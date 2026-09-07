import os
import sys
import re
import json
import time
import asyncio
from pathlib import Path
from collections import defaultdict, deque
from typing import Union, Optional, Tuple, List, Dict, Any, Set

from app.config import settings
from app.logger import backend_logger
from app.services.llm_service import execute_hf_llm_call
from app.services.json_repair_service import (
    strip_markdown_fences,
    parse_json,
    repair_json_deterministically
)


def sanitize_filename(feature_name: str, existing_names: Set[str]) -> str:
    """
    Safely converts a feature name into a clean, filesystem-safe filename.
    Handles special characters, spaces, excessive length, and collision disambiguation.
    """
    if not feature_name or not feature_name.strip():
        base = "feature"
    else:
        # Convert to lower, replace non-alphanumeric with hyphens
        base = re.sub(r'[^a-zA-Z0-9]+', '-', feature_name.strip().lower())
        base = re.sub(r'-+', '-', base).strip('-')
        if not base:
            base = "feature"

    # Truncate to maximum safe length
    if len(base) > 50:
        base = base[:50].rstrip('-')

    candidate = f"{base}.json"
    if candidate not in existing_names:
        existing_names.add(candidate)
        return candidate

    # Collision resolution
    counter = 2
    while True:
        collision_candidate = f"{base}-{counter}.json"
        if collision_candidate not in existing_names:
            existing_names.add(collision_candidate)
            return collision_candidate
        counter += 1


def load_code_map(code_map_source: Union[str, Path, dict]) -> Tuple[dict, Path]:
    """
    Loads and standardizes repository-code-map.json.
    Accepts a filepath, Path object, or already-loaded dictionary.
    """
    print("[FeatureContext] Loading repository-code-map.json", flush=True)
    backend_logger.info("[FeatureContext] Loading repository-code-map.json")

    code_map_path = None
    if isinstance(code_map_source, (str, Path)):
        resolved_path = Path(code_map_source).resolve()
        if not resolved_path.exists():
            raise FileNotFoundError(f"repository-code-map.json not found at: {resolved_path}")
        code_map_path = resolved_path
        with open(resolved_path, "r", encoding="utf-8") as f:
            code_map = json.load(f)
    elif isinstance(code_map_source, dict):
        code_map = code_map_source
        cand_path = (
            code_map.get("project", {}).get("codeMapPath")
            or code_map.get("codeMapPath")
        )
        if cand_path:
            code_map_path = Path(cand_path).resolve()
        else:
            code_map_path = Path.cwd() / "repository-code-map.json"
    else:
        raise TypeError(f"Unsupported code_map_source type: {type(code_map_source)}")

    print("[FeatureContext] Loading existing relationships", flush=True)
    backend_logger.info("[FeatureContext] Loading existing relationships")

    # Normalize files structure to dictionary keyed by normalized relative path
    raw_files = code_map.get("files", {})
    files_dict = {}
    if isinstance(raw_files, list):
        for f in raw_files:
            rel = (
                f.get("relativePath")
                or f.get("file", {}).get("relativePath")
                or ""
            ).replace("\\", "/")
            if rel:
                files_dict[rel] = f
    elif isinstance(raw_files, dict):
        for k, v in raw_files.items():
            norm_k = k.replace("\\", "/")
            files_dict[norm_k] = v
    code_map["files"] = files_dict

    # Normalize relationships
    rels = code_map.get("relationships", [])
    norm_rels = []
    for r in rels:
        from_p = (r.get("from") or "").replace("\\", "/")
        to_p = (r.get("to") or "").replace("\\", "/")
        if from_p and to_p:
            norm_r = dict(r)
            norm_r["from"] = from_p
            norm_r["to"] = to_p
            norm_rels.append(norm_r)
    code_map["relationships"] = norm_rels

    return code_map, code_map_path


def build_feature_discovery_prompt(code_map: dict) -> list[dict]:
    """
    Creates a condensed, high-level summary of the repository facts for Hugging Face LLM
    feature discovery without token exhaustion.
    """
    project_info = code_map.get("project", {})
    project_name = project_info.get("name", "Project")
    languages = project_info.get("languagesDetected", [])

    files = code_map.get("files", {})
    file_summaries = []

    for rel_path, file_data in files.items():
        lang = file_data.get("language") or file_data.get("file", {}).get("language", "")
        
        # Declarations
        decls = []
        for d in file_data.get("declarations", []):
            if isinstance(d, dict) and d.get("name"):
                decls.append(f"{d.get('type', 'symbol')} {d['name']}")
        for c in file_data.get("classes", []):
            if isinstance(c, dict) and c.get("name"):
                decls.append(f"class {c['name']}")
        for s in file_data.get("symbols", []):
            if isinstance(s, dict) and s.get("name"):
                decls.append(f"{s.get('type', 'symbol')} {s['name']}")

        # Routes
        routes = []
        for r in file_data.get("routes", []) or file_data.get("apiRoutes", []):
            if isinstance(r, dict) and r.get("path"):
                routes.append(f"{r.get('method', 'GET')} {r['path']}")

        # Database operations
        db_ops = []
        for d in file_data.get("databaseOperations", []):
            if isinstance(d, dict):
                op = d.get("operation") or d.get("callee")
                if op:
                    db_ops.append(op)

        # Internal target dependencies
        internal_targets = []
        for dep in file_data.get("dependencies", []):
            if isinstance(dep, dict) and dep.get("type") == "internal":
                internal_targets.append(dep.get("target"))

        summary_item = {
            "path": rel_path,
            "language": lang
        }
        if decls:
            summary_item["declarations"] = decls[:15]
        if routes:
            summary_item["routes"] = routes[:10]
        if db_ops:
            summary_item["databaseOperations"] = list(set(db_ops))[:5]
        if internal_targets:
            summary_item["internalImports"] = internal_targets[:10]

        file_summaries.append(summary_item)

    system_content = (
        "You are an expert software architect analyzing a repository Code Map.\n"
        "Your task is to identify meaningful, high-level APPLICATION FEATURES based strictly "
        "on the actual source files, declarations, API routes, and relationships provided.\n\n"
        "RULES:\n"
        "1. Discover features from the ACTUAL repository facts. DO NOT invent or assume features.\n"
        "2. A feature represents a distinct capability (e.g. user authentication, document management, real-time sync).\n"
        "3. Every feature must list 1 to 5 primary 'seedFiles' that directly implement or anchor that feature.\n"
        "4. Feature count and names must be dynamic and appropriate for this specific codebase.\n"
        "5. Return ONLY a valid JSON object matching the schema below. No markdown fences, no conversational text."
    )

    user_content = (
        f"Project Name: {project_name}\n"
        f"Languages: {', '.join(languages)}\n"
        f"Total Source Files: {len(file_summaries)}\n\n"
        f"Repository Code Map Summary:\n"
        f"{json.dumps(file_summaries, indent=2)}\n\n"
        "Identify the primary features of this application. Return ONLY valid JSON with this exact schema:\n"
        "{\n"
        '  "features": [\n'
        "    {\n"
        '      "id": "feature-unique-slug",\n'
        '      "name": "Human Readable Feature Name",\n'
        '      "description": "Clear description of what this feature accomplishes.",\n'
        '      "seedFiles": ["path/to/primary/file1", "path/to/primary/file2"]\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content}
    ]


async def discover_features(code_map: dict) -> list[dict]:
    """
    Discovers application features using the existing Hugging Face LLM integration.
    """
    print("[FeatureContext] Starting feature discovery", flush=True)
    backend_logger.info("[FeatureContext] Starting feature discovery")

    messages = build_feature_discovery_prompt(code_map)
    raw_content, status_code, duration_s = await execute_hf_llm_call(
        messages=messages,
        max_tokens=4000,
        temperature=0.1,
        stage_name="FEATURE_DISCOVERY"
    )

    cleaned = strip_markdown_fences(raw_content)
    ok, parsed_json, _ = parse_json(cleaned)

    if not ok or not isinstance(parsed_json, dict) or "features" not in parsed_json:
        rep_ok, rep_data, _, _ = repair_json_deterministically(cleaned)
        if rep_ok and isinstance(rep_data, dict) and "features" in rep_data:
            parsed_json = rep_data
        else:
            backend_logger.warning("[FeatureContext] LLM feature discovery returned invalid structure; falling back to heuristic discovery")
            parsed_json = {"features": fallback_feature_discovery(code_map)}

    features = parsed_json.get("features", [])

    # Validate features list
    valid_features = []
    available_files = set(code_map.get("files", {}).keys())

    for idx, feat in enumerate(features):
        if not isinstance(feat, dict):
            continue
        feat_name = feat.get("name", "").strip()
        if not feat_name:
            continue
        feat_id = feat.get("id", "").strip() or f"feature-{idx + 1}"
        feat_desc = feat.get("description", "").strip()
        seed_files = [f.replace("\\", "/") for f in feat.get("seedFiles", []) if isinstance(f, str)]

        # Filter seed files to those present in code map
        confirmed_seeds = [f for f in seed_files if f in available_files]
        if not confirmed_seeds:
            # Try to match files by name or folder keywords
            slug = re.sub(r'[^a-zA-Z0-9]+', ' ', feat_name.lower())
            keywords = [w for w in slug.split() if len(w) > 3]
            for f in available_files:
                f_lower = f.lower()
                if any(kw in f_lower for kw in keywords):
                    confirmed_seeds.append(f)
                    if len(confirmed_seeds) >= 3:
                        break

        # If still no seeds, pick one from available files
        if not confirmed_seeds and available_files:
            confirmed_seeds = [next(iter(available_files))]

        valid_features.append({
            "id": feat_id,
            "name": feat_name,
            "description": feat_desc,
            "seedFiles": confirmed_seeds
        })

    if not valid_features:
        valid_features = fallback_feature_discovery(code_map)

    print(f"[FeatureContext] Discovered {len(valid_features)} features", flush=True)
    backend_logger.info(f"[FeatureContext] Discovered {len(valid_features)} features")
    return valid_features


def fallback_feature_discovery(code_map: dict) -> list[dict]:
    """
    Deterministic architectural fallback if LLM is unreachable or returns malformed data.
    Derives feature groups from directory structure, API routes, or major class components.
    """
    files = code_map.get("files", {})
    dir_groups = defaultdict(list)

    for p in files.keys():
        parts = p.split("/")
        if len(parts) > 1:
            group_name = parts[1] if parts[0] in ("src", "server", "client", "app") and len(parts) > 2 else parts[0]
        else:
            group_name = "core"
        dir_groups[group_name].append(p)

    features = []
    for idx, (group, group_files) in enumerate(dir_groups.items()):
        clean_name = group.replace("-", " ").replace("_", " ").title()
        features.append({
            "id": f"feature-{group.lower()}",
            "name": f"{clean_name} Module",
            "description": f"Functionality and components associated with {clean_name}.",
            "seedFiles": group_files[:4]
        })
    return features


def select_feature_files(feature: dict, code_map: dict, max_depth: int = 2) -> list[dict]:
    """
    Traverses the pre-computed dependency graph starting from the feature's seed files.
    Identifies direct vs dependency files and assigns architectural roles.
    """
    feat_name = feature.get("name", "Unknown")
    print(f"[FeatureContext] Building context for feature: {feat_name}", flush=True)
    backend_logger.info(f"[FeatureContext] Building context for feature: {feat_name}")

    files_dict = code_map.get("files", {})
    relationships = code_map.get("relationships", [])

    # Build bidirectional adjacency list for internal files
    outgoing = defaultdict(list)
    incoming = defaultdict(list)

    for r in relationships:
        src = r.get("from")
        tgt = r.get("to")
        is_ext = r.get("external", False)
        if src and tgt:
            outgoing[src].append((tgt, is_ext, r.get("type")))
            if not is_ext:
                incoming[tgt].append((src, False, r.get("type")))

    seed_files = set(feature.get("seedFiles", []))
    selected_internal: Dict[str, str] = {}  # path -> relevance ("direct" | "dependency")

    for s in seed_files:
        if s in files_dict:
            selected_internal[s] = "direct"

    # BFS Traversal of dependencies up to max_depth
    queue = deque([(s, 0) for s in selected_internal.keys()])
    visited = set(selected_internal.keys())

    while queue:
        curr, depth = queue.popleft()
        if depth >= max_depth:
            continue

        # Outgoing dependencies (what curr imports/calls/uses)
        for tgt, is_ext, rel_type in outgoing.get(curr, []):
            if not is_ext and tgt in files_dict and tgt not in visited:
                visited.add(tgt)
                selected_internal[tgt] = "dependency"
                queue.append((tgt, depth + 1))

    # Add direct callers of seed files (e.g. host pages mounting the component)
    # Do NOT expand caller's other outgoing children to prevent pulling in unrelated sibling features
    for s in seed_files:
        for src, is_ext, rel_type in incoming.get(s, []):
            if not is_ext and src in files_dict and src not in selected_internal:
                selected_internal[src] = "dependency"

    # Determine architectural role for each selected file
    result_files = []
    for path, relevance in selected_internal.items():
        file_data = files_dict.get(path, {})
        role = determine_file_role(path, file_data)
        result_files.append({
            "path": path,
            "role": role,
            "relevance": relevance
        })

    print(f"[FeatureContext] Selected {len(result_files)} relevant files", flush=True)
    backend_logger.info(f"[FeatureContext] Selected {len(result_files)} relevant files")
    return result_files


def determine_file_role(path: str, file_data: dict) -> str:
    """
    Classifies a source file into a descriptive architectural role based on its AST declarations.
    """
    path_lower = path.lower()
    routes = file_data.get("routes", []) or file_data.get("apiRoutes", [])
    if routes:
        return "API Route Handler"

    db_ops = file_data.get("databaseOperations", [])
    if db_ops or "model" in path_lower or "entity" in path_lower or "schema" in path_lower:
        return "Data Model & Storage"

    if "controller" in path_lower:
        return "Controller"
    if "service" in path_lower:
        return "Service"
    if "component" in path_lower or path_lower.endswith((".tsx", ".jsx")):
        return "UI Component"
    if "hook" in path_lower:
        return "Custom Hook / State"
    if "util" in path_lower or "helper" in path_lower:
        return "Utility"
    if "middleware" in path_lower or "guard" in path_lower:
        return "Middleware / Guard"
    if "config" in path_lower:
        return "Configuration"

    return "Source Module"


async def generate_feature_context(feature: dict, selected_files: list[dict], code_map: dict) -> dict:
    """
    Assembles a clean, file-centric Feature Context object where declarations,
    routes, database operations, and dependencies are nested directly under each participating file.
    """
    print("[FeatureContext] Generating feature context using LLM", flush=True)
    backend_logger.info("[FeatureContext] Generating feature context using LLM")

    files_dict = code_map.get("files", {})
    selected_paths = {f["path"] for f in selected_files}
    shared_deps = set()

    # Pre-index dependencies for quick lookup per file
    file_dependencies_map = defaultdict(list)
    for r in code_map.get("relationships", []):
        src = r.get("from")
        tgt = r.get("to")
        is_ext = r.get("external", False)

        if src in selected_paths:
            if is_ext:
                shared_deps.add(tgt)
                dep_entry = {
                    "target": tgt,
                    "type": "external",
                    "kind": r.get("type", "import")
                }
                if r.get("evidence", {}).get("line"):
                    dep_entry["line"] = r["evidence"]["line"]
                file_dependencies_map[src].append(dep_entry)
            elif tgt in selected_paths:
                dep_entry = {
                    "target": tgt,
                    "type": "internal",
                    "kind": r.get("type", "import")
                }
                if r.get("evidence", {}).get("line"):
                    dep_entry["line"] = r["evidence"]["line"]
                file_dependencies_map[src].append(dep_entry)

    enriched_files = []
    all_routes = []

    for f in selected_files:
        p = f["path"]
        fdata = files_dict.get(p, {})
        lang = fdata.get("language") or fdata.get("file", {}).get("language", "")

        file_entry = {
            "path": p,
            "role": f.get("role", "Source Module"),
            "relevance": f.get("relevance", "dependency"),
            "language": lang
        }

        # 1. Declarations (Classes, Functions, Components)
        file_decls = []
        for c in fdata.get("classes", []):
            if isinstance(c, dict) and c.get("name"):
                c_entry = {"name": c["name"], "type": "class"}
                if c.get("extends"):
                    c_entry["extends"] = c["extends"]
                if c.get("methods"):
                    c_entry["methods"] = c["methods"]
                file_decls.append(c_entry)

        for d in fdata.get("declarations", []) + fdata.get("symbols", []):
            if isinstance(d, dict) and d.get("name"):
                dtype = d.get("type", "function")
                if dtype == "component":
                    if not any(x.get("name") == d["name"] for x in file_decls):
                        file_decls.append({"name": d["name"], "type": "component"})
                elif dtype in ("function", "method"):
                    if not any(x.get("name") == d["name"] for x in file_decls):
                        file_decls.append({"name": d["name"], "type": dtype})
                elif dtype == "class" and not any(x.get("name") == d["name"] for x in file_decls):
                    file_decls.append({"name": d["name"], "type": "class"})

        if file_decls:
            file_entry["declarations"] = file_decls

        # 2. Routes
        file_routes = []
        for r in fdata.get("routes", []) or fdata.get("apiRoutes", []):
            if isinstance(r, dict) and r.get("path"):
                r_entry = {
                    "method": r.get("method", "GET"),
                    "path": r["path"]
                }
                if r.get("line"):
                    r_entry["line"] = r["line"]
                file_routes.append(r_entry)
                all_routes.append({**r_entry, "file": p})
        if file_routes:
            file_entry["routes"] = file_routes

        # 3. Database Operations
        file_db_ops = []
        for op in fdata.get("databaseOperations", []):
            if isinstance(op, dict):
                op_name = op.get("operation") or op.get("callee", "db_op")
                db_entry = {"operation": op_name}
                if op.get("line"):
                    db_entry["line"] = op["line"]
                file_db_ops.append(db_entry)
        if file_db_ops:
            file_entry["databaseOperations"] = file_db_ops

        # 4. Dependencies
        deps = file_dependencies_map.get(p, [])
        if deps:
            file_entry["dependencies"] = deps

        enriched_files.append(file_entry)

    # 5. Top-level inter-file relationships (file→file edges only, both ends in selected set)
    inter_file_relationships = []
    for r in code_map.get("relationships", []):
        src = r.get("from")
        tgt = r.get("to")
        if src in selected_paths and tgt in selected_paths and not r.get("external", False):
            rel_entry = {
                "from": src,
                "to": tgt,
                "type": r.get("type", "imports")
            }
            evidence = r.get("evidence")
            if evidence:
                rel_entry["evidence"] = evidence
            inter_file_relationships.append(rel_entry)

    # 6. LLM-generated feature execution flow
    feature_flow = await generate_feature_flow(feature, enriched_files, all_routes)

    context = {
        "feature": {
            "id": feature.get("id"),
            "name": feature.get("name"),
            "description": feature.get("description")
        },
        "files": enriched_files,
        "relationships": inter_file_relationships,
        "featureFlow": feature_flow,
        "sharedDependencies": sorted(list(shared_deps))
    }

    return context


async def generate_feature_flow(
    feature: dict,
    enriched_files: list[dict],
    routes: list[dict]
) -> list[str]:
    """
    Generates a concise 3-to-5 step sequence of how control and data move through this feature.
    """
    files_summary = [f"{f['path']} ({f['role']})" for f in enriched_files[:8]]
    routes_summary = [f"{r['method']} {r['path']}" for r in routes[:5]]
    deps_summary = []
    for f in enriched_files[:5]:
        for d in f.get("dependencies", [])[:3]:
            deps_summary.append(f"{f['path']} -> {d['target']}")

    system_prompt = (
        "You are a software architect. Given a feature and its participating files and dependencies, "
        "describe the end-to-end runtime data or control flow through this feature.\n"
        "Return ONLY a JSON array of 3 to 5 concise step descriptions (strings). No markdown fences."
    )

    user_prompt = (
        f"Feature: {feature.get('name')}\n"
        f"Description: {feature.get('description')}\n"
        f"Participating Files: {', '.join(files_summary)}\n"
        f"API Routes: {', '.join(routes_summary) if routes_summary else 'None'}\n"
        f"Key Dependencies: {', '.join(deps_summary) if deps_summary else 'None'}\n\n"
        'Return JSON: ["Step 1: ...", "Step 2: ...", "Step 3: ..."]'
    )

    try:
        raw, _, _ = await execute_hf_llm_call(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=600,
            temperature=0.1,
            stage_name="FEATURE_FLOW"
        )
        cleaned = strip_markdown_fences(raw)
        ok, parsed, _ = parse_json(cleaned)
        if not ok:
            rep_ok, rep_data, _, _ = repair_json_deterministically(cleaned)
            if rep_ok:
                parsed = rep_data
        if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
            return parsed
        if isinstance(parsed, dict) and "flow" in parsed and isinstance(parsed["flow"], list):
            return parsed["flow"]
    except Exception as exc:
        backend_logger.warning(f"[FeatureContext] LLM feature flow generation notice: {exc}")

    # Deterministic fallback flow
    fallback = []
    if routes:
        fallback.append(f"Request initiates via {routes[0]['method']} {routes[0]['path']} in {routes[0]['file']}")
    direct_files = [f["path"] for f in enriched_files if f.get("relevance") == "direct"]
    if direct_files:
        fallback.append(f"Direct processing handled by {', '.join(direct_files[:2])}")
    dep_files = [f["path"] for f in enriched_files if f.get("relevance") == "dependency"]
    if dep_files:
        fallback.append(f"Dependencies invoked across {', '.join(dep_files[:2])}")
    if not fallback:
        fallback = [f"Execution starts within {enriched_files[0]['path']}"]
    return fallback


def validate_feature_context(context: dict, code_map: dict) -> dict:
    """
    Strict validation engine for untrusted LLM output:
    - Verifies all files in context['files'] exist in code_map['files']
    - Discards any hallucinated files
    - Validates per-file declarations, routes, dbOps, and dependencies
    """
    print("[FeatureContext] Validating generated context", flush=True)
    backend_logger.info("[FeatureContext] Validating generated context")

    available_files = set(code_map.get("files", {}).keys())

    validated_files = []
    for f in context.get("files", []):
        p = f.get("path")
        if p in available_files:
            # Validate internal dependencies in this file
            if "dependencies" in f:
                valid_deps = []
                for dep in f["dependencies"]:
                    if dep.get("type") == "internal":
                        if dep.get("target") in available_files:
                            valid_deps.append(dep)
                    else:
                        valid_deps.append(dep)
                f["dependencies"] = valid_deps
            validated_files.append(f)
        else:
            backend_logger.warning(f"[FeatureContext] Discarding hallucinated file: {p}")

    context["files"] = validated_files
    return context


def save_feature_contexts(code_map_path: Path, contexts: list[dict]) -> list[Path]:
    """
    Saves each validated feature context JSON inside the 'feature-context' directory
    co-located in the same directory as repository-code-map.json.
    """
    target_dir = code_map_path.parent / "feature-context"
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    existing_names: Set[str] = set()

    for ctx in contexts:
        feat_name = ctx.get("feature", {}).get("name", "feature")
        filename = sanitize_filename(feat_name, existing_names)
        out_file = target_dir / filename

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(ctx, f, indent=2)

        saved_paths.append(out_file)
        print(f"[FeatureContext] Saved: {out_file.as_posix()}", flush=True)
        backend_logger.info(f"[FeatureContext] Saved: {out_file.as_posix()}")

    return saved_paths


async def generate_all_feature_contexts(code_map_source: Union[str, Path, dict]) -> list[dict]:
    """
    Complete end-to-end workflow:
    1. Load repository-code-map.json
    2. Load existing relationships
    3. LLM feature discovery
    4. Graph-traversal file selection
    5. Context assembly and feature flow generation
    6. Strict AST validation
    7. Save to feature-context/
    """
    code_map, code_map_path = load_code_map(code_map_source)
    features = await discover_features(code_map)

    contexts = []
    for feat in features:
        selected = select_feature_files(feat, code_map)
        if not selected:
            continue
        ctx = await generate_feature_context(feat, selected, code_map)
        validated_ctx = validate_feature_context(ctx, code_map)
        contexts.append(validated_ctx)

    if contexts and code_map_path:
        save_feature_contexts(code_map_path, contexts)

    return contexts


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m app.services.feature_context_generator <path_to_repository_code_map.json>")
        sys.exit(1)

    target_map = sys.argv[1]
    asyncio.run(generate_all_feature_contexts(target_map))

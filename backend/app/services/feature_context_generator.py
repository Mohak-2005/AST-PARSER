import os
import sys
import re
import json
import asyncio
from pathlib import Path
from collections import defaultdict, deque
from typing import Union, Tuple, List, Dict, Any, Set

from app.config import settings
from app.logger import backend_logger
from app.services.llm_service import execute_hf_llm_call
from app.services.json_repair_service import (
    strip_markdown_fences,
    parse_json,
    repair_json_deterministically
)


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def sanitize_filename(feature_name: str, existing_names: Set[str]) -> str:
    """
    Converts a feature name into a clean filesystem-safe filename.
    Handles special characters, spaces, length, and collision disambiguation.
    """
    if not feature_name or not feature_name.strip():
        base = "feature"
    else:
        base = re.sub(r'[^a-zA-Z0-9]+', '-', feature_name.strip().lower())
        base = re.sub(r'-+', '-', base).strip('-')
        if not base:
            base = "feature"

    if len(base) > 50:
        base = base[:50].rstrip('-')

    candidate = f"{base}.json"
    if candidate not in existing_names:
        existing_names.add(candidate)
        return candidate

    counter = 2
    while True:
        c = f"{base}-{counter}.json"
        if c not in existing_names:
            existing_names.add(c)
            return c
        counter += 1


# ---------------------------------------------------------------------------
# Code-map loading and normalization
# ---------------------------------------------------------------------------

def load_code_map(code_map_source: Union[str, Path, dict]) -> Tuple[dict, Path]:
    """
    Loads and standardizes repository-code-map.json.
    Accepts a filepath, Path object, or already-loaded dictionary.
    Normalises both legacy (list) and current (dict) `files` formats.
    Normalises all path separators to forward slashes.
    """
    print("[FeatureContext] Loading repository-code-map.json", flush=True)
    backend_logger.info("[FeatureContext] Loading repository-code-map.json")

    code_map_path = None
    if isinstance(code_map_source, (str, Path)):
        resolved_path = Path(code_map_source).resolve()
        if not resolved_path.exists():
            raise FileNotFoundError(f"repository-code-map.json not found at: {resolved_path}")
        code_map_path = resolved_path
        with open(resolved_path, "r", encoding="utf-8") as fh:
            code_map = json.load(fh)
    elif isinstance(code_map_source, dict):
        code_map = code_map_source
        cand = (
            code_map.get("project", {}).get("codeMapPath")
            or code_map.get("codeMapPath")
        )
        code_map_path = Path(cand).resolve() if cand else Path.cwd() / "repository-code-map.json"
    else:
        raise TypeError(f"Unsupported code_map_source type: {type(code_map_source)}")

    print("[FeatureContext] Loading existing relationships", flush=True)
    backend_logger.info("[FeatureContext] Loading existing relationships")

    # Normalise files → dict keyed by forward-slash relative path
    raw_files = code_map.get("files", {})
    files_dict: Dict[str, dict] = {}
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
            files_dict[k.replace("\\", "/")] = v
    code_map["files"] = files_dict

    # Normalise relationships
    norm_rels = []
    for r in code_map.get("relationships", []):
        frm = (r.get("from") or "").replace("\\", "/")
        to = (r.get("to") or "").replace("\\", "/")
        if frm and to:
            nr = dict(r)
            nr["from"] = frm
            nr["to"] = to
            norm_rels.append(nr)
    code_map["relationships"] = norm_rels

    return code_map, code_map_path


# ---------------------------------------------------------------------------
# Feature discovery — LLM-first, deterministic fallback
# ---------------------------------------------------------------------------

def _build_file_summary(rel_path: str, file_data: dict) -> dict:
    """
    Extracts a concise, architecture-agnostic evidence summary for a single file.
    Reads only what the existing code-map provides; never invents structure.
    """
    lang = file_data.get("language") or file_data.get("file", {}).get("language", "")

    # Declarations — include ALL types the analyzer produces
    decl_names = []
    for item in (
        file_data.get("declarations", [])
        + file_data.get("classes", [])
        + file_data.get("symbols", [])
        + file_data.get("exports", [])
    ):
        if isinstance(item, dict) and item.get("name"):
            dtype = item.get("type", "symbol")
            decl_names.append(f"{dtype} {item['name']}")

    # API routes — only when the code-map actually provides them
    routes = []
    for r in (file_data.get("routes") or file_data.get("apiRoutes") or []):
        if isinstance(r, dict) and r.get("path"):
            routes.append(f"{r.get('method', '').upper()} {r['path']}".strip())

    # Database operations — only when present
    db_ops = []
    for op in (file_data.get("databaseOperations") or []):
        if isinstance(op, dict):
            name = op.get("operation") or op.get("callee")
            if name:
                db_ops.append(name)

    # Internal imports from code-map dependencies
    internal_imports = []
    for dep in (file_data.get("dependencies") or []):
        if isinstance(dep, dict) and dep.get("type") == "internal":
            t = dep.get("target")
            if t:
                internal_imports.append(t)

    summary: dict = {"path": rel_path, "language": lang}
    if decl_names:
        summary["declarations"] = decl_names[:15]
    if routes:
        summary["routes"] = routes[:10]
    if db_ops:
        summary["databaseOperations"] = list(set(db_ops))[:5]
    if internal_imports:
        summary["internalImports"] = internal_imports[:10]
    return summary


def build_feature_discovery_prompt(code_map: dict) -> list:
    """
    Builds the HF LLM messages for feature discovery.
    The prompt is deliberately architecture-neutral; it describes only what
    the code-map actually contains.
    """
    project_info = code_map.get("project", {})
    project_name = project_info.get("name", "Project")
    languages = project_info.get("languagesDetected", [])
    files = code_map.get("files", {})

    file_summaries = [
        _build_file_summary(rel, fdata)
        for rel, fdata in files.items()
    ]

    system_content = (
        "You are an expert software analyst. You will be given a structured code map "
        "of a software repository. Your task is to identify distinct, meaningful "
        "FEATURES of this codebase based ONLY on the actual evidence in the code map.\n\n"
        "RULES:\n"
        "1. Derive features ONLY from the code map facts. Do NOT invent or assume features "
        "that are not supported by the evidence.\n"
        "2. A feature is any coherent unit of behaviour: a data-processing pipeline, "
        "a user-facing capability, a protocol handler, a CLI command, a background job, "
        "a library API surface, or any other logical grouping — whatever fits THIS repository.\n"
        "3. Do NOT assume any particular architecture (e.g. MVC, REST, client-server). "
        "Identify features that exist in the code, not in a template.\n"
        "4. Every feature must list 1–5 'seedFiles': the primary files that directly "
        "implement or anchor that feature.\n"
        "5. Feature count is dynamic: discover as many or as few as the code map supports.\n"
        "6. Return ONLY a valid JSON object. No markdown, no explanation."
    )

    user_content = (
        f"Project Name: {project_name}\n"
        f"Languages: {', '.join(languages) if languages else 'unknown'}\n"
        f"Total Source Files: {len(file_summaries)}\n\n"
        f"Repository Code Map Summary:\n"
        f"{json.dumps(file_summaries, indent=2)}\n\n"
        "Identify the features of this codebase. Return ONLY valid JSON:\n"
        "{\n"
        '  "features": [\n'
        "    {\n"
        '      "id": "feature-unique-slug",\n'
        '      "name": "Human Readable Feature Name",\n'
        '      "description": "What this feature does in this specific codebase.",\n'
        '      "seedFiles": ["relative/path/to/primary/file"]\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content}
    ]


async def discover_features(code_map: dict) -> list:
    """
    Discovers application features using the existing Hugging Face LLM integration.
    Falls back to deterministic graph-clustering if LLM is unavailable.
    """
    print("[FeatureContext] Starting feature discovery", flush=True)
    backend_logger.info("[FeatureContext] Starting feature discovery")

    messages = build_feature_discovery_prompt(code_map)
    raw_content, status_code, _ = await execute_hf_llm_call(
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
            backend_logger.warning(
                "[FeatureContext] LLM feature discovery returned invalid structure; "
                "falling back to graph-cluster discovery"
            )
            parsed_json = {"features": fallback_feature_discovery(code_map)}

    features = parsed_json.get("features", [])
    available_files = set(code_map.get("files", {}).keys())

    valid_features = []
    for idx, feat in enumerate(features):
        if not isinstance(feat, dict):
            continue
        feat_name = feat.get("name", "").strip()
        if not feat_name:
            continue

        feat_id = feat.get("id", "").strip() or f"feature-{idx + 1}"
        feat_desc = feat.get("description", "").strip()
        seed_files = [
            s.replace("\\", "/")
            for s in feat.get("seedFiles", [])
            if isinstance(s, str)
        ]

        # Keep only seeds that actually exist in the code map
        confirmed_seeds = [f for f in seed_files if f in available_files]

        # If none confirmed, try keyword match against file paths
        if not confirmed_seeds:
            slug = re.sub(r'[^a-zA-Z0-9]+', ' ', feat_name.lower())
            keywords = [w for w in slug.split() if len(w) > 3]
            for f in available_files:
                if any(kw in f.lower() for kw in keywords):
                    confirmed_seeds.append(f)
                    if len(confirmed_seeds) >= 3:
                        break

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


def fallback_feature_discovery(code_map: dict) -> list:
    """
    Fully graph-driven deterministic fallback.
    Groups files by connected components in the dependency graph, then
    names each group from its dominant declarations or the deepest common
    directory prefix.

    This approach is architecture-agnostic:
    - Works for monorepos, microservices, CLI tools, libraries, etc.
    - Makes zero assumptions about folder names or language-specific patterns.
    """
    files = code_map.get("files", {})
    relationships = code_map.get("relationships", [])
    available = set(files.keys())

    # Build undirected adjacency for connected-component clustering
    adj: Dict[str, Set[str]] = defaultdict(set)
    for r in relationships:
        frm = r.get("from", "")
        to = r.get("to", "")
        if frm in available and to in available and not r.get("external", False):
            adj[frm].add(to)
            adj[to].add(frm)

    visited: Set[str] = set()
    components: List[List[str]] = []

    for f in available:
        if f in visited:
            continue
        # BFS
        component = []
        queue = deque([f])
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            component.append(node)
            for neighbour in adj.get(node, []):
                if neighbour not in visited:
                    queue.append(neighbour)
        components.append(component)

    # Sort: largest components first, then alphabetically
    components.sort(key=lambda c: (-len(c), c[0]))

    features = []
    for idx, component in enumerate(components):
        # Derive a human-readable name from shared directory prefix or top declarations
        name = _component_name(component, files)
        features.append({
            "id": f"feature-{idx + 1}",
            "name": name,
            "description": f"Functionality implemented across {len(component)} file(s).",
            "seedFiles": component[:4]
        })

    return features or [{"id": "feature-1", "name": "Core Module",
                         "description": "All project files.", "seedFiles": list(available)[:4]}]


def _component_name(component: List[str], files_dict: dict) -> str:
    """
    Derives a short, human-readable name for a connected component.
    Checks (in order):
      1. Dominant declaration names (class/function/component/export)
      2. Deepest common directory prefix
      3. First file path segment that is not a single-letter or numeric-only segment
    """
    # Collect all top-level declaration names across the component
    decl_names: List[str] = []
    for path in component:
        fdata = files_dict.get(path, {})
        for item in (
            fdata.get("declarations", [])
            + fdata.get("classes", [])
            + fdata.get("exports", [])
            + fdata.get("symbols", [])
        ):
            if isinstance(item, dict) and item.get("name"):
                n = item["name"].strip()
                if n and len(n) > 2:
                    decl_names.append(n)

    if decl_names:
        # Pick the most frequently seen name stem
        from collections import Counter
        # Normalise to lower for frequency, but keep original casing
        freq = Counter(n.lower() for n in decl_names)
        best_lower = freq.most_common(1)[0][0]
        original = next((n for n in decl_names if n.lower() == best_lower), best_lower)
        # Convert CamelCase / snake_case to Title Words
        words = re.sub(r'([A-Z])', r' \1', original).replace('_', ' ').strip()
        return words.title()

    # Fall back to deepest common directory prefix
    parts_list = [p.split("/") for p in component]
    min_depth = min(len(p) for p in parts_list)
    common: List[str] = []
    for depth in range(min_depth - 1):  # exclude filename
        segment = parts_list[0][depth]
        if all(p[depth] == segment for p in parts_list):
            if segment and len(segment) > 1 and not segment.isdigit():
                common.append(segment)
        else:
            break

    if common:
        return common[-1].replace("-", " ").replace("_", " ").title() + " Module"

    # Last resort: first meaningful path segment of the first file
    for part in component[0].split("/"):
        if part and len(part) > 1 and not part.isdigit():
            return part.replace("-", " ").replace("_", " ").title() + " Module"

    return "Core Module"


# ---------------------------------------------------------------------------
# Graph-traversal file selection
# ---------------------------------------------------------------------------

def select_feature_files(feature: dict, code_map: dict, max_depth: int = 2) -> list:
    """
    Traverses the pre-computed dependency graph starting from the feature's seed files.

    Strategy (architecture-agnostic):
    - BFS outgoing edges up to max_depth: what the seeds import/call/use
    - Add direct callers of seeds (depth-1 incoming) without expanding their children
      → prevents sibling features from polluting the context

    Assigns relevance: "direct" for seeds, "dependency" for reachable nodes.
    Role is determined by evidence in the code-map (see determine_file_role).
    """
    feat_name = feature.get("name", "Unknown")
    print(f"[FeatureContext] Building context for feature: {feat_name}", flush=True)
    backend_logger.info(f"[FeatureContext] Building context for feature: {feat_name}")

    files_dict = code_map.get("files", {})
    relationships = code_map.get("relationships", [])

    # Build adjacency lists (internal edges only)
    outgoing: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    incoming: Dict[str, List[str]] = defaultdict(list)

    for r in relationships:
        src = r.get("from", "")
        tgt = r.get("to", "")
        is_ext = r.get("external", False)
        rel_type = r.get("type", "imports")
        if src and tgt and not is_ext:
            outgoing[src].append((tgt, rel_type))
            incoming[tgt].append(src)

    seed_files = set(feature.get("seedFiles", []))
    selected: Dict[str, str] = {}  # path → relevance

    for s in seed_files:
        if s in files_dict:
            selected[s] = "direct"

    # BFS outgoing traversal
    queue: deque = deque([(s, 0) for s in selected])
    visited: Set[str] = set(selected.keys())

    while queue:
        curr, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for tgt, _ in outgoing.get(curr, []):
            if tgt in files_dict and tgt not in visited:
                visited.add(tgt)
                selected[tgt] = "dependency"
                queue.append((tgt, depth + 1))

    # Add direct callers of seeds (no expansion)
    for s in seed_files:
        for src in incoming.get(s, []):
            if src in files_dict and src not in selected:
                selected[src] = "dependency"

    result_files = []
    for path, relevance in selected.items():
        fdata = files_dict.get(path, {})
        role = determine_file_role(path, fdata, relationships)
        result_files.append({"path": path, "role": role, "relevance": relevance})

    print(f"[FeatureContext] Selected {len(result_files)} relevant files", flush=True)
    backend_logger.info(f"[FeatureContext] Selected {len(result_files)} relevant files")
    return result_files


# ---------------------------------------------------------------------------
# Evidence-based role classification — architecture/language-agnostic
# ---------------------------------------------------------------------------

def determine_file_role(path: str, file_data: dict, relationships: list = None) -> str:
    """
    Classifies a file's architectural role from CODE-MAP EVIDENCE ONLY.

    Priority order (highest to lowest):
    1. Has API routes declared            → "API Route Handler"
    2. Has database operations declared   → "Data Layer"
    3. Has only/mostly export declarations → "Library / Module API"
    4. Has class declarations with inheritance/implementation → "Domain Model"
    5. Is imported by many others (hub)   → "Shared Utility"
    6. Has no outgoing internal deps (leaf) + declarations → "Implementation Module"
    7. Entry point signals (main, index, __init__, cmd, cli, main.go, etc.)
    8. Default                            → "Source Module"

    NOTE: Does NOT use path name keywords like 'service', 'controller', 'component'
    because those are framework conventions, NOT universal facts.
    """
    if relationships is None:
        relationships = []

    # Evidence 1: API routes
    raw_routes = file_data.get("routes") or file_data.get("apiRoutes") or []
    if raw_routes:
        return "API Route Handler"

    # Evidence 2: Database operations
    if file_data.get("databaseOperations"):
        return "Data Layer"

    # Evidence 3: Class declarations with inheritance/interface information
    classes = file_data.get("classes", [])
    has_class_with_inheritance = any(
        isinstance(c, dict) and (c.get("extends") or c.get("implements"))
        for c in classes
    )
    if has_class_with_inheritance:
        return "Domain Model"

    # Evidence 4: All-exports file (library surface)
    exports = file_data.get("exports", [])
    declarations = file_data.get("declarations", [])
    symbols = file_data.get("symbols", [])
    all_decls = declarations + symbols

    export_names = {e.get("name") for e in exports if isinstance(e, dict) and e.get("name")}
    decl_names = {d.get("name") for d in all_decls if isinstance(d, dict) and d.get("name")}
    if export_names and decl_names and export_names.issuperset(decl_names) and len(export_names) >= 2:
        return "Library / Module API"

    # Evidence 5: Entry point by filename convention (language-agnostic common patterns)
    filename = path.rsplit("/", 1)[-1].lower()
    entry_point_names = {
        "main.py", "main.go", "main.rs", "main.c", "main.cpp", "main.java",
        "main.ts", "main.js", "main.rb", "main.cs", "main.kt", "main.swift",
        "index.js", "index.ts", "index.py",
        "__main__.py", "__init__.py",
        "app.js", "app.ts", "app.py",
        "server.js", "server.ts",
        "cmd.go", "cli.go", "cli.py",
        "manage.py",  # Django
    }
    if filename in entry_point_names:
        return "Entry Point"

    # Evidence 6: High in-degree (many files import this one) → shared utility
    if relationships:
        in_degree = sum(
            1 for r in relationships
            if r.get("to") == path and not r.get("external", False)
        )
        if in_degree >= 4:
            return "Shared Utility"

    # Evidence 7: Has declarations → some form of implementation module
    if all_decls or classes:
        return "Implementation Module"

    return "Source Module"


# ---------------------------------------------------------------------------
# Feature context assembly
# ---------------------------------------------------------------------------

async def generate_feature_context(
    feature: dict,
    selected_files: list,
    code_map: dict
) -> dict:
    """
    Assembles the Feature Context object.

    Schema:
    {
      "feature": { "id", "name", "description" },
      "files": [
        {
          "path", "role", "relevance", "language",
          "declarations"?,      ← from code-map, all types
          "routes"?,            ← when present in code-map
          "databaseOperations"?,← when present in code-map
          "dependencies": [{ "target", "type", "kind", "line"? }]
        }
      ],
      "relationships": [{ "from", "to", "type", "evidence"? }],
      "featureFlow": ["Step 1: ...", ...],
      "sharedDependencies": ["external-pkg", ...]
    }

    All fields are derived from the repository-code-map. Nothing is invented.
    """
    print("[FeatureContext] Generating feature context", flush=True)
    backend_logger.info("[FeatureContext] Generating feature context")

    files_dict = code_map.get("files", {})
    selected_paths = {f["path"] for f in selected_files}
    shared_deps: Set[str] = set()

    # Pre-index file-level dependency list from the relationship graph
    file_deps_map: Dict[str, list] = defaultdict(list)
    for r in code_map.get("relationships", []):
        src = r.get("from", "")
        tgt = r.get("to", "")
        is_ext = r.get("external", False)
        if src not in selected_paths:
            continue
        dep_entry: dict = {
            "target": tgt,
            "type": "external" if is_ext else "internal",
            "kind": r.get("type", "imports")
        }
        evidence = r.get("evidence", {})
        if isinstance(evidence, dict) and evidence.get("line"):
            dep_entry["line"] = evidence["line"]

        if is_ext:
            shared_deps.add(tgt)
            file_deps_map[src].append(dep_entry)
        elif tgt in selected_paths:
            file_deps_map[src].append(dep_entry)

    enriched_files = []
    all_routes = []  # collected for featureFlow context

    for f in selected_files:
        p = f["path"]
        fdata = files_dict.get(p, {})
        lang = fdata.get("language") or fdata.get("file", {}).get("language", "")

        file_entry: dict = {
            "path": p,
            "role": f.get("role", "Source Module"),
            "relevance": f.get("relevance", "dependency"),
            "language": lang
        }

        # Declarations — all types the analyzer produced
        file_decls = []
        seen_decl_names: Set[str] = set()

        for c in fdata.get("classes", []):
            if isinstance(c, dict) and c.get("name") and c["name"] not in seen_decl_names:
                seen_decl_names.add(c["name"])
                entry: dict = {"name": c["name"], "type": "class"}
                if c.get("extends"):
                    entry["extends"] = c["extends"]
                if c.get("implements"):
                    entry["implements"] = c["implements"]
                if c.get("methods"):
                    entry["methods"] = c["methods"]
                file_decls.append(entry)

        for item in (
            fdata.get("declarations", [])
            + fdata.get("symbols", [])
            + fdata.get("exports", [])
        ):
            if isinstance(item, dict) and item.get("name"):
                name = item["name"]
                if name not in seen_decl_names:
                    seen_decl_names.add(name)
                    file_decls.append({
                        "name": name,
                        "type": item.get("type", "symbol")
                    })

        if file_decls:
            file_entry["declarations"] = file_decls

        # Routes — only when code-map provides them (no assumptions)
        file_routes = []
        for r in (fdata.get("routes") or fdata.get("apiRoutes") or []):
            if isinstance(r, dict) and r.get("path"):
                r_entry: dict = {
                    "method": r.get("method", "").upper() or "UNKNOWN",
                    "path": r["path"]
                }
                if r.get("line"):
                    r_entry["line"] = r["line"]
                file_routes.append(r_entry)
                all_routes.append({**r_entry, "file": p})
        if file_routes:
            file_entry["routes"] = file_routes

        # Database operations — only when code-map provides them
        file_db_ops = []
        for op in (fdata.get("databaseOperations") or []):
            if isinstance(op, dict):
                op_name = op.get("operation") or op.get("callee")
                if op_name:
                    db_entry: dict = {"operation": op_name}
                    if op.get("line"):
                        db_entry["line"] = op["line"]
                    file_db_ops.append(db_entry)
        if file_db_ops:
            file_entry["databaseOperations"] = file_db_ops

        # Per-file dependency list (internal + external)
        deps = file_deps_map.get(p, [])
        if deps:
            file_entry["dependencies"] = deps

        enriched_files.append(file_entry)

    # Top-level inter-file relationships (both ends inside selected set)
    inter_file_rels = []
    for r in code_map.get("relationships", []):
        src = r.get("from", "")
        tgt = r.get("to", "")
        if src in selected_paths and tgt in selected_paths and not r.get("external", False):
            rel_entry: dict = {
                "from": src,
                "to": tgt,
                "type": r.get("type", "imports")
            }
            ev = r.get("evidence")
            if ev:
                rel_entry["evidence"] = ev
            inter_file_rels.append(rel_entry)

    # LLM-generated feature flow (with graph-grounded deterministic fallback)
    feature_flow = await generate_feature_flow(feature, enriched_files, inter_file_rels)

    return {
        "feature": {
            "id": feature.get("id"),
            "name": feature.get("name"),
            "description": feature.get("description")
        },
        "files": enriched_files,
        "relationships": inter_file_rels,
        "featureFlow": feature_flow,
        "sharedDependencies": sorted(shared_deps)
    }


# ---------------------------------------------------------------------------
# Feature flow generation
# ---------------------------------------------------------------------------

async def generate_feature_flow(
    feature: dict,
    enriched_files: list,
    relationships: list
) -> list:
    """
    Generates a concise execution/data-flow description for this feature.
    Uses HF LLM grounded in the file list and relationship graph.
    Falls back to a topology-sorted walk through the relationship graph.
    """
    # Prepare a compact representation for the LLM
    file_summaries = [
        f"{f['path']} [{f['role']}]" for f in enriched_files[:10]
    ]
    rel_summaries = [
        f"{r['from']} --{r['type']}--> {r['to']}"
        for r in relationships[:15]
    ]

    system_prompt = (
        "You are a software architect. Given a software feature and its participating "
        "files with their dependency relationships, describe in 3 to 5 steps how "
        "execution or data flows through this feature.\n"
        "Base your steps ONLY on the files and relationships provided. "
        "Do not invent steps or reference files not listed.\n"
        "Return ONLY a JSON array of step strings. No markdown, no extra keys."
    )

    user_prompt = (
        f"Feature: {feature.get('name')}\n"
        f"Description: {feature.get('description')}\n"
        f"Participating Files:\n  " + "\n  ".join(file_summaries) + "\n"
        f"Relationships:\n  " + ("\n  ".join(rel_summaries) if rel_summaries else "  (none)") + "\n\n"
        'Return JSON array: ["Step 1: ...", "Step 2: ...", "Step 3: ..."]'
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
        if isinstance(parsed, dict) and isinstance(parsed.get("flow"), list):
            return parsed["flow"]
    except Exception as exc:
        backend_logger.warning(f"[FeatureContext] LLM feature flow notice: {exc}")

    # Deterministic fallback: topological walk of the relationship graph
    return _graph_based_flow_fallback(enriched_files, relationships)


def _graph_based_flow_fallback(enriched_files: list, relationships: list) -> list:
    """
    Constructs a flow description from the actual relationship graph topology.
    Does not assume REST, frontend, backend, or any framework.
    """
    if not enriched_files:
        return ["No files selected for this feature."]

    # Build a simple directed adjacency for topological ordering
    path_to_role = {f["path"]: f.get("role", "Source Module") for f in enriched_files}
    all_paths = set(path_to_role.keys())

    in_degree: Dict[str, int] = {p: 0 for p in all_paths}
    adj: Dict[str, List[str]] = defaultdict(list)
    for r in relationships:
        frm, to = r.get("from"), r.get("to")
        if frm in all_paths and to in all_paths:
            adj[frm].append(to)
            in_degree[to] = in_degree.get(to, 0) + 1

    # Kahn's topological sort
    queue = deque(p for p in all_paths if in_degree.get(p, 0) == 0)
    topo_order = []
    while queue:
        node = queue.popleft()
        topo_order.append(node)
        for neighbour in adj.get(node, []):
            in_degree[neighbour] -= 1
            if in_degree[neighbour] == 0:
                queue.append(neighbour)

    # Add any remaining (cycle members)
    for p in all_paths:
        if p not in topo_order:
            topo_order.append(p)

    # Build step descriptions from topology
    steps = []
    for i, path in enumerate(topo_order[:5]):
        role = path_to_role.get(path, "Source Module")
        filename = path.rsplit("/", 1)[-1]
        callers = [r["from"] for r in relationships if r.get("to") == path and r.get("from") in all_paths]
        callees = [r["to"] for r in relationships if r.get("from") == path and r.get("to") in all_paths]

        if i == 0 and not callers:
            steps.append(f"Execution begins at {filename} ({role}).")
        elif callees:
            callee_names = ", ".join(c.rsplit("/", 1)[-1] for c in callees[:2])
            steps.append(f"{filename} ({role}) delegates to {callee_names}.")
        else:
            steps.append(f"{filename} ({role}) completes processing.")

    if not steps:
        steps = [f"Feature implemented across {len(enriched_files)} file(s)."]

    return steps


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_feature_context(context: dict, code_map: dict) -> dict:
    """
    Validates the assembled feature context against the code-map ground truth.
    - Removes any file paths not found in the code-map (hallucination guard).
    - Removes internal dependency targets not found in the code-map.
    - Removes relationship entries with invalid endpoints.
    """
    print("[FeatureContext] Validating generated context", flush=True)
    backend_logger.info("[FeatureContext] Validating generated context")

    available = set(code_map.get("files", {}).keys())

    # Validate files
    valid_files = []
    for f in context.get("files", []):
        p = f.get("path")
        if p not in available:
            backend_logger.warning(f"[FeatureContext] Discarding hallucinated file: {p}")
            continue
        # Validate internal dep targets
        if "dependencies" in f:
            f["dependencies"] = [
                d for d in f["dependencies"]
                if d.get("type") != "internal" or d.get("target") in available
            ]
        valid_files.append(f)
    context["files"] = valid_files

    # Validate top-level relationships
    valid_paths = {f["path"] for f in valid_files}
    context["relationships"] = [
        r for r in context.get("relationships", [])
        if r.get("from") in valid_paths and r.get("to") in valid_paths
    ]

    return context


# ---------------------------------------------------------------------------
# Save output
# ---------------------------------------------------------------------------

def save_feature_contexts(code_map_path: Path, contexts: list) -> list:
    """
    Saves each validated feature context JSON to the 'feature-context' directory
    co-located with repository-code-map.json.
    """
    target_dir = code_map_path.parent / "feature-context"
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    existing_names: Set[str] = set()

    for ctx in contexts:
        feat_name = ctx.get("feature", {}).get("name", "feature")
        filename = sanitize_filename(feat_name, existing_names)
        out_file = target_dir / filename
        with open(out_file, "w", encoding="utf-8") as fh:
            json.dump(ctx, fh, indent=2)
        saved_paths.append(out_file)
        print(f"[FeatureContext] Saved: {out_file.as_posix()}", flush=True)
        backend_logger.info(f"[FeatureContext] Saved: {out_file.as_posix()}")

    return saved_paths


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def generate_all_feature_contexts(
    code_map_source: Union[str, Path, dict]
) -> list:
    """
    End-to-end workflow:
    1. Load and normalise repository-code-map.json
    2. LLM feature discovery (fallback: graph-cluster grouping)
    3. Graph traversal file selection per feature
    4. Evidence-based context assembly
    5. Validation against code-map ground truth
    6. Save feature-context/*.json
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


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m app.services.feature_context_generator <path_to_repository_code_map.json>")
        sys.exit(1)
    asyncio.run(generate_all_feature_contexts(sys.argv[1]))

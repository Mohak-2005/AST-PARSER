"""
Feature Context Generator
=========================
Consumes repository-code-map.json (produced by the existing Tree-sitter
analyzer) and generates per-feature context JSON files.

DESIGN CONTRACT
---------------
- Reads ONLY from repository-code-map.json.  No file I/O, no Tree-sitter.
- Language-agnostic:   zero hardcoded language names or extensions.
- Architecture-agnostic: zero hardcoded folder/framework/pattern names.
- Graph-driven:  all structural decisions come from the relationships array.
- Evidence-grounded: all semantic decisions come from declarations, routes,
  databaseOperations, exports, symbols, and classes fields.
- Repository-independent: the same code must work for any project whose
  files and relationships the existing analyzer successfully represents.
"""

import sys
import re
import json
import asyncio
from pathlib import Path
from collections import defaultdict, deque, Counter
from typing import Union, Tuple, List, Dict, Set, Optional

from app.logger import backend_logger
from app.services.llm_service import execute_hf_llm_call
from app.services.json_repair_service import (
    strip_markdown_fences,
    parse_json,
    repair_json_deterministically,
)


# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------

FilePath = str
Relevance = str  # "direct" | "dependency"


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def sanitize_filename(feature_name: str, existing_names: Set[str]) -> str:
    """Converts a feature name into a collision-safe, filesystem-safe filename."""
    base = re.sub(r"[^a-zA-Z0-9]+", "-", (feature_name or "").strip().lower())
    base = re.sub(r"-+", "-", base).strip("-") or "feature"
    if len(base) > 50:
        base = base[:50].rstrip("-")

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
# Code-map loading and normalisation
# ---------------------------------------------------------------------------

def load_code_map(code_map_source: Union[str, Path, dict]) -> Tuple[dict, Path]:
    """
    Loads and normalises repository-code-map.json.

    Accepts a filepath, Path object, or already-loaded dict.
    Handles both legacy (list) and current (dict) `files` formats.
    Normalises all path separators to forward slashes.
    """
    print("[FeatureContext] Loading repository-code-map.json", flush=True)
    backend_logger.info("[FeatureContext] Loading repository-code-map.json")

    code_map_path: Optional[Path] = None
    if isinstance(code_map_source, (str, Path)):
        resolved = Path(code_map_source).resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"repository-code-map.json not found: {resolved}")
        code_map_path = resolved
        with open(resolved, "r", encoding="utf-8") as fh:
            code_map = json.load(fh)
    elif isinstance(code_map_source, dict):
        code_map = code_map_source
        cand = code_map.get("project", {}).get("codeMapPath") or code_map.get("codeMapPath")
        code_map_path = Path(cand).resolve() if cand else Path.cwd() / "repository-code-map.json"
    else:
        raise TypeError(f"Unsupported code_map_source type: {type(code_map_source)}")

    print("[FeatureContext] Normalising code-map structure", flush=True)
    backend_logger.info("[FeatureContext] Normalising code-map structure")

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

    # Normalise relationships — ensure forward-slash paths
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
# Shared graph utilities
# ---------------------------------------------------------------------------

def _build_adjacency(relationships: list, available: Set[str]) -> Tuple[
    Dict[str, List[Tuple[str, str]]],   # outgoing: src → [(tgt, rel_type)]
    Dict[str, List[Tuple[str, str]]],   # incoming: tgt → [(src, rel_type)]
]:
    """Builds directed adjacency lists for internal (non-external) edges only."""
    outgoing: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    incoming: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for r in relationships:
        frm = r.get("from", "")
        to = r.get("to", "")
        rel_type = r.get("type", "imports")
        if frm in available and to in available and not r.get("external", False):
            outgoing[frm].append((to, rel_type))
            incoming[to].append((frm, rel_type))
    return outgoing, incoming


def _bfs_reachable(start: str, adj: Dict[str, List[Tuple[str, str]]],
                   universe: Set[str]) -> Set[str]:
    """BFS: returns all nodes reachable from `start` within `universe`."""
    seen: Set[str] = set()
    queue: deque = deque([start])
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        for nb, _ in adj.get(node, []):
            if nb in universe and nb not in seen:
                queue.append(nb)
    return seen


def _find_connected_components(relationships: list, available: Set[str]) -> List[List[str]]:
    """
    BFS on the undirected projection of the internal relationship graph.
    Returns a list of connected components (each is a list of file paths).
    """
    undirected: Dict[str, Set[str]] = defaultdict(set)
    for r in relationships:
        frm, to = r.get("from", ""), r.get("to", "")
        if frm in available and to in available and not r.get("external", False):
            undirected[frm].add(to)
            undirected[to].add(frm)

    visited: Set[str] = set()
    components: List[List[str]] = []
    for node in available:
        if node in visited:
            continue
        component: List[str] = []
        q: deque = deque([node])
        while q:
            n = q.popleft()
            if n in visited:
                continue
            visited.add(n)
            component.append(n)
            for nb in undirected.get(n, []):
                if nb not in visited:
                    q.append(nb)
        components.append(component)

    components.sort(key=lambda c: (-len(c), c[0]))
    return components


# ---------------------------------------------------------------------------
# Semantic signals extracted from a file's code-map entry
# ---------------------------------------------------------------------------

def _file_signals(fdata: dict) -> dict:
    """
    Extracts normalised semantic signals from a file's code-map entry.
    Returns a dict with boolean/list fields — NO language/framework assumptions.
    All fields may be absent (empty) if the code-map does not provide them.
    """
    routes = fdata.get("routes") or fdata.get("apiRoutes") or []
    db_ops = fdata.get("databaseOperations") or []
    classes = fdata.get("classes") or []
    declarations = fdata.get("declarations") or []
    symbols = fdata.get("symbols") or []
    exports = fdata.get("exports") or []

    # Route prefix groups (e.g. "/api/users" → "users")
    route_prefixes: List[str] = []
    for r in routes:
        if isinstance(r, dict) and r.get("path"):
            parts = r["path"].strip("/").split("/")
            non_empty = [p for p in parts if p and not p.startswith("{") and not p.startswith(":")]
            if non_empty:
                route_prefixes.append(non_empty[0])

    # All declaration names (any type the code-map records)
    decl_names: List[str] = [
        d["name"] for d in (declarations + symbols + exports + classes)
        if isinstance(d, dict) and d.get("name") and len(d["name"]) > 1
    ]

    # Classes with inheritance/implementation evidence
    inherited_classes = [
        c["name"] for c in classes
        if isinstance(c, dict) and c.get("name") and (c.get("extends") or c.get("implements"))
    ]

    # Export names
    export_names: Set[str] = {
        e["name"] for e in exports if isinstance(e, dict) and e.get("name")
    }

    return {
        "has_routes": bool(routes),
        "has_db_ops": bool(db_ops),
        "has_classes": bool(classes),
        "has_declarations": bool(declarations or symbols),
        "has_exports": bool(exports),
        "route_prefixes": route_prefixes,
        "decl_names": decl_names,
        "inherited_classes": inherited_classes,
        "export_names": export_names,
        "export_count": len(export_names),
        "declared_names": {
            d["name"] for d in (declarations + symbols)
            if isinstance(d, dict) and d.get("name")
        },
    }


def _component_signals(component_files: List[str], files_dict: dict) -> dict:
    """Aggregates semantic signals across all files in a component."""
    all_route_prefixes: List[str] = []
    all_decl_names: List[str] = []
    all_inherited: List[str] = []
    has_routes = False
    has_db_ops = False

    for path in component_files:
        sig = _file_signals(files_dict.get(path, {}))
        has_routes = has_routes or sig["has_routes"]
        has_db_ops = has_db_ops or sig["has_db_ops"]
        all_route_prefixes.extend(sig["route_prefixes"])
        all_decl_names.extend(sig["decl_names"])
        all_inherited.extend(sig["inherited_classes"])

    return {
        "has_routes": has_routes,
        "has_db_ops": has_db_ops,
        "route_prefixes": all_route_prefixes,
        "decl_names": all_decl_names,
        "inherited_classes": all_inherited,
    }


# ---------------------------------------------------------------------------
# Candidate naming — multi-signal, evidence-only
# ---------------------------------------------------------------------------

def _name_candidate(component_files: List[str], code_map: dict) -> Tuple[str, str]:
    """
    Derives a human-readable name and description for a feature candidate.

    Signal priority (no framework/folder assumptions):
    1. Route prefix groups   — strongest API evidence
    2. Class names with inheritance  — domain model evidence
    3. Deepest common directory prefix
    4. Most frequent declaration stem
    5. Generic fallback
    """
    files_dict = code_map.get("files", {})
    csig = _component_signals(component_files, files_dict)

    # Signal 1: dominant route prefix
    if csig["route_prefixes"]:
        dominant = Counter(csig["route_prefixes"]).most_common(1)[0][0]
        name = dominant.replace("-", " ").replace("_", " ").title()
        return name, f"Handles routes prefixed by /{dominant}."

    # Signal 2: inherited class names (domain evidence)
    if csig["inherited_classes"]:
        dominant = Counter(csig["inherited_classes"]).most_common(1)[0][0]
        # Split camelCase only at lowercase→uppercase boundary (keeps "JSON", "API" intact)
        words = re.sub(r"(?<=[a-z])([A-Z])", r" \1", dominant).replace("_", " ").strip()
        return words.title(), f"Domain implemented around {dominant}."

    # Signal 3: deepest common directory prefix
    parts_list = [p.split("/") for p in component_files]
    min_depth = min(len(p) for p in parts_list)
    common: List[str] = []
    for depth in range(min_depth - 1):  # exclude filename
        seg = parts_list[0][depth]
        if all(p[depth] == seg for p in parts_list) and seg and len(seg) > 1 and not seg.isdigit():
            common.append(seg)
        else:
            break
    if common:
        label = common[-1].replace("-", " ").replace("_", " ").title()
        return f"{label} Module", f"Functionality under {'/'.join(common)}."

    # Signal 4: most frequent declaration stem
    if csig["decl_names"]:
        freq = Counter(n.lower() for n in csig["decl_names"])
        best_lower, _ = freq.most_common(1)[0]
        original = next((n for n in csig["decl_names"] if n.lower() == best_lower), best_lower)
        # Split camelCase only at lowercase→uppercase boundary
        words = re.sub(r"(?<=[a-z])([A-Z])", r" \1", original).replace("_", " ").strip()
        return words.title(), f"Functionality implemented across {len(component_files)} file(s)."

    return "Core Module", f"Functionality across {len(component_files)} file(s)."


# ---------------------------------------------------------------------------
# Intra-component splitting
# ---------------------------------------------------------------------------

def _split_component(
    component_files: List[str],
    outgoing: Dict[str, List[Tuple[str, str]]],
    incoming: Dict[str, List[Tuple[str, str]]],
    files_dict: dict,
) -> List[Dict]:
    """
    Attempts to identify functionally distinct sub-clusters within a single
    connected component.

    Algorithm:
    1. Find "source nodes" — files with in-degree 0 within the component.
       These are natural candidate entry points for distinct features.
    2. If multiple unconnected sources exist, compute the sub-graph reachable
       from each source.
    3. Measure overlap between sub-graphs.
       - High overlap (≥ 50%) → sources are likely part of the same feature.
       - Low overlap           → sources likely anchor distinct features.
    4. Assign each non-source file to its exclusive owner(s), or mark as shared.

    Returns a list of {"files": [...], "seeds": [...]} dicts.
    """
    if len(component_files) <= 2:
        return [{"files": component_files, "seeds": component_files[:1]}]

    file_set = set(component_files)

    # In-degree within this component only
    local_in: Dict[str, int] = {f: 0 for f in component_files}
    for f in component_files:
        for nb, _ in outgoing.get(f, []):
            if nb in file_set:
                local_in[nb] = local_in.get(nb, 0) + 1

    sources = [f for f in component_files if local_in.get(f, 0) == 0]

    # If 0 or 1 source (or a cycle covering everything), treat as one feature
    if len(sources) <= 1:
        seed = sources[0] if sources else component_files[0]
        return [{"files": component_files, "seeds": [seed]}]

    # Check whether sources are directly connected to each other
    source_set = set(sources)
    cross = sum(
        1 for src in sources
        for nb, _ in outgoing.get(src, [])
        if nb in source_set
    )
    if cross > 0:
        return [{"files": component_files, "seeds": sources[:2]}]

    # Compute reachable sub-graph from each source (within component)
    sub_graphs: Dict[str, Set[str]] = {}
    for src in sources:
        sub_graphs[src] = _bfs_reachable(src, outgoing, file_set)

    # Measure overall overlap
    union_size = len(set().union(*sub_graphs.values()))
    if len(sources) >= 2:
        intersection = sub_graphs[sources[0]].copy()
        for s in sources[1:]:
            intersection &= sub_graphs[s]
        overlap_ratio = len(intersection) / max(union_size, 1)
        if overlap_ratio > 0.5:
            # More than half the component is shared → single feature
            return [{"files": component_files, "seeds": sources[:2]}]

    # Assign files to source groups
    groups: Dict[str, Set[str]] = {src: set() for src in sources}
    shared: Set[str] = set()

    for f in component_files:
        owners = [src for src in sources if f in sub_graphs[src]]
        if len(owners) == 1:
            groups[owners[0]].add(f)
        elif len(owners) > 1:
            shared.add(f)

    # Shared files go into every group that owns them
    for src in sources:
        groups[src].update(shared)

    # Deduplicate groups (identical sets → merge)
    seen_frozen: Set[frozenset] = set()
    result: List[Dict] = []
    for src in sources:
        fs = frozenset(groups[src] or {src})
        if fs not in seen_frozen:
            seen_frozen.add(fs)
            result.append({"files": list(fs), "seeds": [src]})

    return result or [{"files": component_files, "seeds": sources[:1]}]


# ---------------------------------------------------------------------------
# Feature Discovery — LLM-first, semantically-refined deterministic fallback
# ---------------------------------------------------------------------------

def _build_file_summary(rel_path: str, file_data: dict) -> dict:
    """Compact evidence summary of one file for the LLM prompt."""
    sig = _file_signals(file_data)
    lang = file_data.get("language") or file_data.get("file", {}).get("language", "")
    summary: dict = {"path": rel_path, "language": lang}
    if sig["decl_names"]:
        summary["declarations"] = sig["decl_names"][:15]
    if sig["has_routes"]:
        routes = file_data.get("routes") or file_data.get("apiRoutes") or []
        summary["routes"] = [
            f"{r.get('method', '').upper()} {r['path']}"
            for r in routes[:10]
            if isinstance(r, dict) and r.get("path")
        ]
    if sig["has_db_ops"]:
        db_ops = file_data.get("databaseOperations") or []
        summary["databaseOperations"] = list({
            op.get("operation") or op.get("callee")
            for op in db_ops if isinstance(op, dict)
            if op.get("operation") or op.get("callee")
        })[:5]
    deps = file_data.get("dependencies") or []
    internal = [d.get("target") for d in deps if isinstance(d, dict) and d.get("type") == "internal"]
    if internal:
        summary["internalImports"] = internal[:10]
    return summary


def build_feature_discovery_prompt(code_map: dict) -> list:
    """
    Builds the HF LLM messages for feature discovery.
    Deliberately architecture-neutral; describes only what the code-map provides.
    """
    project_info = code_map.get("project", {})
    project_name = project_info.get("name", "Project")
    languages = project_info.get("languagesDetected", [])
    files = code_map.get("files", {})

    file_summaries = [_build_file_summary(p, d) for p, d in files.items()]

    system_content = (
        "You are an expert software analyst. You will receive a structured code map "
        "of a software repository and must identify its meaningful FEATURES.\n\n"
        "RULES — read carefully:\n"
        "1. Derive features ONLY from the code-map evidence. Do NOT invent features "
        "that are not supported by declarations, relationships, routes, or operations.\n"
        "2. A feature is any coherent unit of behaviour in THIS specific repository: "
        "it could be a data-processing pipeline, a user capability, a protocol handler, "
        "a CLI command, a library API surface, a background job, or anything else — "
        "whatever the code-map evidence actually shows.\n"
        "3. Do NOT assume any architecture (MVC, REST, microservices, client-server, etc.). "
        "Identify features as they exist in the code.\n"
        "4. Connected-component grouping is only a starting hint. A large connected "
        "component may contain multiple distinct features; several small components may "
        "form one feature. Use declarations, relationship directions, route groupings, "
        "and database operations to decide.\n"
        "5. Each feature must list 1–5 'seedFiles': the primary files that directly "
        "implement or anchor the feature core.\n"
        "6. Feature count and names are determined by the code-map, not by templates.\n"
        "7. Return ONLY valid JSON. No markdown, no prose."
    )

    user_content = (
        f"Project: {project_name}\n"
        f"Languages: {', '.join(languages) if languages else 'unknown'}\n"
        f"Files: {len(file_summaries)}\n\n"
        f"Code Map Summary:\n{json.dumps(file_summaries, indent=2)}\n\n"
        "Return ONLY this JSON structure:\n"
        "{\n"
        '  "features": [\n'
        "    {\n"
        '      "id": "feature-unique-slug",\n'
        '      "name": "Human Readable Name",\n'
        '      "description": "What this feature does in this specific codebase.",\n'
        '      "seedFiles": ["relative/path/to/file"]\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


async def discover_features(code_map: dict) -> list:
    """
    Discovers features via Hugging Face LLM with a semantic-refinement fallback.
    """
    print("[FeatureContext] Starting feature discovery", flush=True)
    backend_logger.info("[FeatureContext] Starting feature discovery")

    messages = build_feature_discovery_prompt(code_map)
    raw_content, _, _ = await execute_hf_llm_call(
        messages=messages,
        max_tokens=4000,
        temperature=0.1,
        stage_name="FEATURE_DISCOVERY",
    )

    cleaned = strip_markdown_fences(raw_content)
    ok, parsed_json, _ = parse_json(cleaned)

    if not ok or not isinstance(parsed_json, dict) or "features" not in parsed_json:
        rep_ok, rep_data, _, _ = repair_json_deterministically(cleaned)
        if rep_ok and isinstance(rep_data, dict) and "features" in rep_data:
            parsed_json = rep_data
        else:
            backend_logger.warning(
                "[FeatureContext] LLM discovery returned invalid structure; "
                "using deterministic semantic-refinement fallback"
            )
            return fallback_feature_discovery(code_map)

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

        confirmed_seeds = [f for f in seed_files if f in available_files]

        # Keyword fallback for unresolved seeds
        if not confirmed_seeds:
            slug = re.sub(r"[^a-zA-Z0-9]+", " ", feat_name.lower())
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
            "seedFiles": confirmed_seeds,
        })

    if not valid_features:
        return fallback_feature_discovery(code_map)

    print(f"[FeatureContext] Discovered {len(valid_features)} features", flush=True)
    backend_logger.info(f"[FeatureContext] Discovered {len(valid_features)} features")
    return valid_features


def fallback_feature_discovery(code_map: dict) -> list:
    """
    Deterministic fallback with two-phase approach:

    Phase 1 — CANDIDATE GENERATION
      Build connected components of the internal relationship graph.
      Each component is a candidate, NOT a guaranteed final feature.

    Phase 2 — SEMANTIC REFINEMENT
      For each candidate:
        a. Analyse multi-signal evidence (routes, db ops, classes, declarations,
           relationship direction).
        b. Split if multiple independent source-node sub-graphs exist with
           low overlap (< 50% shared files) — these likely anchor distinct features.
        c. Keep as one feature if sources are connected or overlap is high.
      Name each refined candidate from its strongest evidence signal.

    NO architecture, language, or folder-naming assumptions.
    """
    files_dict = code_map.get("files", {})
    relationships = code_map.get("relationships", [])
    available = set(files_dict.keys())

    if not available:
        return [{"id": "feature-1", "name": "Empty Repository",
                 "description": "No files found.", "seedFiles": []}]

    outgoing, incoming = _build_adjacency(relationships, available)
    components = _find_connected_components(relationships, available)

    # Phase 2: refine each component into ≥1 feature candidates
    feature_candidates: List[Dict] = []
    for component in components:
        sub_clusters = _split_component(component, outgoing, incoming, files_dict)
        for cluster in sub_clusters:
            feature_candidates.append(cluster)

    # Generate named features from each candidate
    features = []
    for idx, candidate in enumerate(feature_candidates):
        cluster_files = candidate["files"]
        seeds = candidate["seeds"]
        name, description = _name_candidate(cluster_files, code_map)
        features.append({
            "id": f"feature-{idx + 1}",
            "name": name,
            "description": description,
            "seedFiles": seeds[:4],
        })

    if not features:
        return [{
            "id": "feature-1",
            "name": "Core Module",
            "description": "All project files.",
            "seedFiles": list(available)[:4],
        }]

    print(f"[FeatureContext] Fallback discovered {len(features)} feature candidate(s)", flush=True)
    backend_logger.info(f"[FeatureContext] Fallback discovered {len(features)} feature candidate(s)")
    return features


# ---------------------------------------------------------------------------
# Graph-traversal file selection
# ---------------------------------------------------------------------------

def select_feature_files(
    feature: dict,
    code_map: dict,
    max_depth: int = 2,
) -> list:
    """
    Selects files relevant to a feature by graph traversal.

    Strategy (architecture-agnostic):
    - BFS along outgoing edges from seed files up to max_depth.
    - Add direct callers (depth-1 incoming) of seeds without expanding
      their other outgoing children — prevents sibling features polluting context.

    Assigns relevance: "direct" for seeds, "dependency" for reachable nodes.
    """
    feat_name = feature.get("name", "Unknown")
    print(f"[FeatureContext] Selecting files for: {feat_name}", flush=True)
    backend_logger.info(f"[FeatureContext] Selecting files for: {feat_name}")

    files_dict = code_map.get("files", {})
    relationships = code_map.get("relationships", [])
    available = set(files_dict.keys())

    outgoing, incoming = _build_adjacency(relationships, available)

    seed_files = set(feature.get("seedFiles", []))
    selected: Dict[str, Relevance] = {}

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
        for src, _ in incoming.get(s, []):
            if src in files_dict and src not in selected:
                selected[src] = "dependency"

    result = []
    for path, relevance in selected.items():
        fdata = files_dict.get(path, {})
        role = determine_file_role(path, fdata)
        result.append({"path": path, "role": role, "relevance": relevance})

    print(f"[FeatureContext] Selected {len(result)} file(s)", flush=True)
    backend_logger.info(f"[FeatureContext] Selected {len(result)} file(s)")
    return result


# ---------------------------------------------------------------------------
# Evidence-based role classification — no path-name or language assumptions
# ---------------------------------------------------------------------------

def determine_file_role(path: str, file_data: dict) -> str:
    """
    Classifies a file's architectural role using CODE-MAP EVIDENCE ONLY.

    Evidence is evaluated in this order. The FIRST matching evidence wins;
    no heuristic overrides a higher-priority positive evidence signal.

    1. Has API routes declared           → "API Route Handler"
    2. Has database operations declared  → "Data Layer"
    3. Has class with extends/implements → "Domain Model"
    4. All declared names exported (≥3)  → "Library / Module API"
    5. Filename matches universal entry-point conventions → "Entry Point"
    6. Has any declarations or classes   → "Implementation Module"
    7. (default)                         → "Source Module"

    NOTE:
    - Path names (folder, extension) are NOT used — they are conventions,
      not universal facts.
    - Relationship in-degree is NOT used — connectivity is structural,
      not semantic. A widely-imported file can be any role.
    - Language is NOT used as a trigger — it is metadata only.
    """
    sig = _file_signals(file_data)

    # 1. API routes
    if sig["has_routes"]:
        return "API Route Handler"

    # 2. Database operations
    if sig["has_db_ops"]:
        return "Data Layer"

    # 3. Class with inheritance / interface implementation
    if sig["inherited_classes"]:
        return "Domain Model"

    # 4. Library / Module API surface
    # Conservative: requires ≥3 exports AND every declared name is exported
    if sig["export_count"] >= 3 and sig["declared_names"] and sig["export_names"].issuperset(sig["declared_names"]):
        return "Library / Module API"

    # 5. Entry-point filename (universal cross-language conventions only)
    _ENTRY_POINT_NAMES = {
        "main.py", "main.go", "main.rs", "main.c", "main.cpp", "main.java",
        "main.ts", "main.js", "main.rb", "main.cs", "main.kt", "main.swift",
        "index.js", "index.ts", "index.py",
        "__main__.py", "__init__.py",
        "app.js", "app.ts", "app.py",
        "server.js", "server.ts",
        "cmd.go", "cli.go", "cli.py",
        "manage.py",
    }
    filename = path.rsplit("/", 1)[-1].lower()
    if filename in _ENTRY_POINT_NAMES:
        return "Entry Point"

    # 6. Has declarations or classes → some form of implementation module
    if sig["has_declarations"] or sig["has_classes"]:
        return "Implementation Module"

    # 7. Default
    return "Source Module"


# ---------------------------------------------------------------------------
# Feature context assembly
# ---------------------------------------------------------------------------

async def generate_feature_context(
    feature: dict,
    selected_files: list,
    code_map: dict,
) -> dict:
    """
    Assembles the Feature Context object.

    Output schema:
    {
      "feature":       { "id", "name", "description" },
      "files": [
        {
          "path", "role", "relevance", "language",
          "declarations"?,       ← from code-map (all types present)
          "routes"?,             ← when code-map provides routes
          "databaseOperations"?, ← when code-map provides db ops
          "dependencies": [{ "target", "type", "kind", "line"? }]
        }
      ],
      "relationships": [{ "from", "to", "type", "evidence"? }],
      "featureFlow":   ["Step 1: ...", ...],
      "sharedDependencies": ["external-pkg", ...]
    }

    Every field is sourced from repository-code-map.json.
    Nothing is invented.
    """
    print(f"[FeatureContext] Assembling context for: {feature.get('name')}", flush=True)
    backend_logger.info(f"[FeatureContext] Assembling context for: {feature.get('name')}")

    files_dict = code_map.get("files", {})
    selected_paths = {f["path"] for f in selected_files}
    shared_deps: Set[str] = set()

    # Build per-file dependency list from the relationship graph
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
            "kind": r.get("type", "imports"),
        }
        ev = r.get("evidence", {})
        if isinstance(ev, dict) and ev.get("line"):
            dep_entry["line"] = ev["line"]
        if is_ext:
            shared_deps.add(tgt)
            file_deps_map[src].append(dep_entry)
        elif tgt in selected_paths:
            file_deps_map[src].append(dep_entry)

    enriched_files = []

    for f in selected_files:
        p = f["path"]
        fdata = files_dict.get(p, {})
        lang = fdata.get("language") or fdata.get("file", {}).get("language", "")

        file_entry: dict = {
            "path": p,
            "role": f.get("role", "Source Module"),
            "relevance": f.get("relevance", "dependency"),
            "language": lang,
        }

        # All declarations the code-map recorded (any type)
        file_decls = []
        seen: Set[str] = set()

        for c in (fdata.get("classes") or []):
            if isinstance(c, dict) and c.get("name") and c["name"] not in seen:
                seen.add(c["name"])
                entry: dict = {"name": c["name"], "type": "class"}
                if c.get("extends"):
                    entry["extends"] = c["extends"]
                if c.get("implements"):
                    entry["implements"] = c["implements"]
                if c.get("methods"):
                    entry["methods"] = c["methods"]
                file_decls.append(entry)

        for item in (
            (fdata.get("declarations") or [])
            + (fdata.get("symbols") or [])
            + (fdata.get("exports") or [])
        ):
            if isinstance(item, dict) and item.get("name") and item["name"] not in seen:
                seen.add(item["name"])
                file_decls.append({"name": item["name"], "type": item.get("type", "symbol")})

        if file_decls:
            file_entry["declarations"] = file_decls

        # Routes — only when the code-map provides them
        file_routes = []
        for r in (fdata.get("routes") or fdata.get("apiRoutes") or []):
            if isinstance(r, dict) and r.get("path"):
                r_entry: dict = {
                    "method": (r.get("method") or "").upper() or "UNKNOWN",
                    "path": r["path"],
                }
                if r.get("line"):
                    r_entry["line"] = r["line"]
                file_routes.append(r_entry)
        if file_routes:
            file_entry["routes"] = file_routes

        # Database operations — only when the code-map provides them
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

        deps = file_deps_map.get(p, [])
        if deps:
            file_entry["dependencies"] = deps

        enriched_files.append(file_entry)

    # Top-level inter-file relationships (both ends in selected set, sourced from graph)
    inter_file_rels = []
    for r in code_map.get("relationships", []):
        src = r.get("from", "")
        tgt = r.get("to", "")
        if src in selected_paths and tgt in selected_paths and not r.get("external", False):
            rel_entry: dict = {"from": src, "to": tgt, "type": r.get("type", "imports")}
            ev = r.get("evidence")
            if ev:
                rel_entry["evidence"] = ev
            inter_file_rels.append(rel_entry)

    feature_flow = await generate_feature_flow(feature, enriched_files, inter_file_rels)

    return {
        "feature": {
            "id": feature.get("id"),
            "name": feature.get("name"),
            "description": feature.get("description"),
        },
        "files": enriched_files,
        "relationships": inter_file_rels,
        "featureFlow": feature_flow,
        "sharedDependencies": sorted(shared_deps),
    }


# ---------------------------------------------------------------------------
# Feature flow generation
# ---------------------------------------------------------------------------

async def generate_feature_flow(
    feature: dict,
    enriched_files: list,
    relationships: list,
) -> list:
    """
    Describes how execution or data flows through this feature.

    LLM-first: the prompt supplies the actual relationship types (imports,
    calls, renders, instantiates, etc.) so the model describes the real
    delegation pattern, not a forced request→API→DB template.

    Deterministic fallback: topology-sorted walk of the relationship graph,
    annotated with the actual relationship type on each edge.
    """
    file_summaries = [
        f"{f['path']} [{f['role']}]" for f in enriched_files[:10]
    ]
    rel_summaries = [
        f"{r['from'].rsplit('/', 1)[-1]} --{r['type']}--> {r['to'].rsplit('/', 1)[-1]}"
        for r in relationships[:15]
    ]

    system_prompt = (
        "You are a software architect. Describe in 3–5 steps how execution or data "
        "flows through the given feature.\n\n"
        "RULES:\n"
        "1. Use ONLY the files and relationships provided. Do not reference or invent "
        "anything not in the list.\n"
        "2. Use the relationship TYPE (imports, calls, renders, instantiates, extends, "
        "implements, etc.) to describe HOW files interact — do not use generic verbs.\n"
        "3. Do NOT force a request→API→service→database pattern or any other template. "
        "Describe the flow that is actually visible in the relationships.\n"
        "4. If no clear ordered flow can be established, describe the relationships as "
        "they are (parallel, bidirectional, etc.).\n"
        "5. Return ONLY a JSON array of step strings. No markdown, no extra keys."
    )

    user_prompt = (
        f"Feature: {feature.get('name')}\n"
        f"Description: {feature.get('description')}\n"
        f"Files:\n  " + "\n  ".join(file_summaries) + "\n"
        f"Relationships:\n  " + (
            "\n  ".join(rel_summaries) if rel_summaries else "  (none — isolated feature)"
        ) + "\n\n"
        'Return JSON: ["Step 1: ...", "Step 2: ...", ...]'
    )

    try:
        raw, _, _ = await execute_hf_llm_call(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=600,
            temperature=0.1,
            stage_name="FEATURE_FLOW",
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

    return _graph_based_flow_fallback(enriched_files, relationships)


def _graph_based_flow_fallback(enriched_files: list, relationships: list) -> list:
    """
    Produces a flow description from graph topology alone.

    Uses Kahn's topological sort on the feature's relationship sub-graph.
    Each step describes an actual edge from the graph, annotated with the
    real relationship type (imports, calls, renders, etc.).

    Does NOT assume any architectural pattern.
    If no ordered flow can be determined (cycles, isolated nodes), falls
    back to listing the relationships that exist.
    """
    if not enriched_files:
        return ["No files selected for this feature."]

    path_to_role = {f["path"]: f.get("role", "Source Module") for f in enriched_files}
    all_paths = set(path_to_role.keys())

    # Build adjacency with relationship types
    out_adj: Dict[str, List[Tuple[str, str]]] = defaultdict(list)  # src → [(tgt, type)]
    in_degree: Dict[str, int] = {p: 0 for p in all_paths}

    for r in relationships:
        frm, to = r.get("from", ""), r.get("to", "")
        rel_type = r.get("type", "uses")
        if frm in all_paths and to in all_paths:
            out_adj[frm].append((to, rel_type))
            in_degree[to] = in_degree.get(to, 0) + 1

    # Kahn's topological sort
    queue: deque = deque(p for p in all_paths if in_degree.get(p, 0) == 0)
    topo_order: List[str] = []
    while queue:
        node = queue.popleft()
        topo_order.append(node)
        for nb, _ in out_adj.get(node, []):
            in_degree[nb] -= 1
            if in_degree[nb] == 0:
                queue.append(nb)

    # Add cycle members (any node not reached by Kahn's)
    for p in all_paths:
        if p not in topo_order:
            topo_order.append(p)

    steps: List[str] = []
    callers_of = {
        p: [r.get("from") for r in relationships if r.get("to") == p and r.get("from") in all_paths]
        for p in all_paths
    }

    for i, path in enumerate(topo_order[:5]):
        role = path_to_role.get(path, "Source Module")
        fname = path.rsplit("/", 1)[-1]
        out_edges = out_adj.get(path, [])
        has_callers = bool(callers_of.get(path))

        if not has_callers and out_edges:
            # Entry node: describe its first outgoing edge
            tgt_fname = out_edges[0][0].rsplit("/", 1)[-1]
            rel_type = out_edges[0][1]
            steps.append(f"{fname} ({role}) {rel_type} {tgt_fname}.")
        elif out_edges:
            tgt_fname = out_edges[0][0].rsplit("/", 1)[-1]
            rel_type = out_edges[0][1]
            steps.append(f"{fname} ({role}) {rel_type} {tgt_fname}.")
        else:
            steps.append(f"{fname} ({role}) is a leaf — no further delegation.")

    # If no edges at all, describe the file set
    if not steps:
        if len(enriched_files) == 1:
            f = enriched_files[0]
            steps = [f"{f['path'].rsplit('/', 1)[-1]} ({f['role']}) implements this feature."]
        else:
            steps = [f"Feature implemented across {len(enriched_files)} file(s) with no detected flow order."]

    return steps


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_feature_context(context: dict, code_map: dict) -> dict:
    """
    Validates the assembled context against code-map ground truth.
    - Removes files not present in the code-map (hallucination guard).
    - Removes internal dependency targets not in the code-map.
    - Removes relationship entries whose endpoints were removed.
    """
    print("[FeatureContext] Validating context", flush=True)
    backend_logger.info("[FeatureContext] Validating context")

    available = set(code_map.get("files", {}).keys())

    valid_files = []
    for f in context.get("files", []):
        p = f.get("path")
        if p not in available:
            backend_logger.warning(f"[FeatureContext] Discarding hallucinated file: {p}")
            continue
        if "dependencies" in f:
            f["dependencies"] = [
                d for d in f["dependencies"]
                if d.get("type") != "internal" or d.get("target") in available
            ]
        valid_files.append(f)
    context["files"] = valid_files

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
    Saves each validated feature context JSON to feature-context/ co-located
    with repository-code-map.json.
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
# Existing context detection
# ---------------------------------------------------------------------------

def get_existing_feature_contexts(code_map_source: Union[str, Path, dict]) -> List[Path]:
    """
    Dynamically inspects <directory containing repository-code-map.json>/feature-context/
    to detect any valid, previously generated feature-context JSON files.

    A file is considered valid if:
    - It has a .json extension
    - It parses as valid JSON
    - It contains a 'feature' dict with 'id' or 'name'
    - It contains a 'files' list

    Returns a list of Path objects for all valid feature context files found.
    """
    code_map_path: Optional[Path] = None
    if isinstance(code_map_source, (str, Path)):
        resolved = Path(code_map_source).resolve()
        code_map_path = (
            resolved if resolved.is_file()
            else (resolved / "repository-code-map.json" if (resolved / "repository-code-map.json").exists() else resolved)
        )
    elif isinstance(code_map_source, dict):
        cand = (
            code_map_source.get("project", {}).get("codeMapPath")
            or code_map_source.get("codeMapPath")
        )
        if cand:
            code_map_path = Path(cand).resolve()

    if not code_map_path:
        return []

    target_dir = code_map_path.parent if code_map_path.suffix == ".json" else code_map_path
    fc_dir = target_dir / "feature-context"

    if not fc_dir.exists() or not fc_dir.is_dir():
        return []

    valid_files: List[Path] = []
    for json_file in fc_dir.glob("*.json"):
        if not json_file.is_file():
            continue
        try:
            with open(json_file, "r", encoding="utf-8") as fh:
                content = json.load(fh)
                if (
                    isinstance(content, dict)
                    and "feature" in content
                    and isinstance(content.get("feature"), dict)
                    and (content["feature"].get("name") or content["feature"].get("id"))
                    and "files" in content
                    and isinstance(content.get("files"), list)
                    and len(content["files"]) > 0
                ):
                    valid_files.append(json_file)
        except Exception:
            continue

    return sorted(valid_files, key=lambda p: p.name)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def generate_all_feature_contexts(
    code_map_source: Union[str, Path, dict],
    force: bool = False,
) -> list:
    """
    End-to-end workflow:
    1. Check if valid feature-context/*.json files already exist.
       If they exist and force is False:
         Return existing contexts from disk without calling Hugging Face LLM.
    2. Load and normalise repository-code-map.json
    3. LLM feature discovery  (fallback: semantic graph-cluster refinement)
    4. Graph-traversal file selection per feature
    5. Evidence-based context assembly
    6. Validation against code-map ground truth
    7. Save feature-context/*.json
    """
    if not force:
        existing_files = get_existing_feature_contexts(code_map_source)
        if existing_files:
            print(
                f"[FeatureContext] Found {len(existing_files)} existing valid feature context(s). Skipping LLM generation.",
                flush=True
            )
            backend_logger.info(
                f"[FeatureContext] Found {len(existing_files)} existing valid feature context(s). Skipping LLM generation."
            )
            existing_contexts = []
            for ef in existing_files:
                try:
                    with open(ef, "r", encoding="utf-8") as fh:
                        existing_contexts.append(json.load(fh))
                except Exception:
                    pass
            if existing_contexts:
                return existing_contexts

    code_map, code_map_path = load_code_map(code_map_source)
    features = await discover_features(code_map)

    contexts = []
    for feat in features:
        selected = select_feature_files(feat, code_map)
        if not selected:
            continue
        ctx = await generate_feature_context(feat, selected, code_map)
        validated = validate_feature_context(ctx, code_map)
        contexts.append(validated)

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

"""
Unit Test Case Generator
========================
Consumes repository-code-map.json (and optional feature-context/ files)
to dynamically discover testable units, build focused context, generate
pure unit test cases via the existing Hugging Face LLM integration,
deterministically validate results, and save structured test case data
under <project-directory>/test-cases/.

DESIGN CONTRACT
---------------
- Zero modification to existing analysis systems.
- Reuses existing Hugging Face client (execute_hf_llm_call).
- Language-independent and architecture-independent.
- ONLY generates unit test specifications (testType: "unit").
- Never generates executable code, integration, API, or E2E tests.
- Never hallucinates symbols or requirements.
- Strictly guards existing valid test cases before calling LLM.
- Stores output in test-cases/ co-located with repository-code-map.json.
"""

import os
import re
import sys
import json
import asyncio
from pathlib import Path
from collections import defaultdict
from typing import Union, Tuple, List, Dict, Set, Optional, Any

from app.logger import backend_logger
from app.services.llm_service import execute_hf_llm_call
from app.services.json_repair_service import (
    strip_markdown_fences,
    parse_json,
    repair_json_deterministically,
)
from app.services.feature_context_generator import (
    load_code_map,
    sanitize_filename,
)
from app.services.context_selector import (
    select_context_for_test_case,
    build_grounded_test_prompt,
    resolve_test_type,
    resolve_target,
    extract_actual_source_code,
    resolve_project_root,
    find_source_file_on_disk,
)
from app.services.llm_provider import (
    get_active_provider_name,
    get_provider_config,
)


# ---------------------------------------------------------------------------
# Existing Test Case Output Guard
# ---------------------------------------------------------------------------

def is_valid_test_case(tc: Any, expected_test_type: Optional[str] = None) -> bool:
    """
    Deterministically checks if a test case object satisfies the schema for unit, functional, or API test cases:
    - dict with non-empty testCaseId, title, description, inputs, expectedResult
    - testType matches expected_test_type (if specified), or is one of ('unit', 'functional', 'api', 'e2e', 'integration')
    - unit/target is a dict containing non-empty file, and non-empty symbol or route
    """
    if not isinstance(tc, dict):
        return False
    tc_type = str(tc.get("testType", "")).strip().lower()
    if expected_test_type:
        exp_norm = expected_test_type.strip().lower()
        if exp_norm != "all":
            if exp_norm in ("functional", "e2e"):
                if tc_type not in ("functional", "e2e"):
                    return False
            elif tc_type != exp_norm:
                return False
    elif tc_type not in ("unit", "functional", "api", "integration", "e2e"):
        return False

    if not tc.get("testCaseId") or not isinstance(tc.get("testCaseId"), str):
        return False
    if not tc.get("title") or not isinstance(tc.get("title"), str):
        return False
    if not tc.get("description") or not isinstance(tc.get("description"), str):
        return False
    if "inputs" not in tc or tc.get("inputs") is None:
        return False
    if "expectedResult" not in tc or not str(tc.get("expectedResult", "")).strip():
        return False

    unit_obj = tc.get("unit") or tc.get("target")
    if not isinstance(unit_obj, dict):
        return False
    if not unit_obj.get("file") or not isinstance(unit_obj.get("file"), str):
        return False
    # Require symbol or route/endpoint
    has_target = bool(unit_obj.get("symbol") or unit_obj.get("route") or unit_obj.get("endpoint") or unit_obj.get("path"))
    if not has_target:
        return False

    return True


def is_valid_unit_test_case(tc: Any) -> bool:
    """
    Deterministically checks if a test case object satisfies the minimum unit test schema:
    - dict with non-empty testCaseId, title, description, inputs, expectedResult
    - testType is strictly 'unit'
    - unit is a dict containing non-empty file, symbol, type
    """
    if not isinstance(tc, dict):
        return False
    if str(tc.get("testType", "")).strip().lower() != "unit":
        return False
    if not tc.get("testCaseId") or not isinstance(tc.get("testCaseId"), str):
        return False
    if not tc.get("title") or not isinstance(tc.get("title"), str):
        return False
    if not tc.get("description") or not isinstance(tc.get("description"), str):
        return False
    if "inputs" not in tc or tc.get("inputs") is None:
        return False
    if "expectedResult" not in tc or not str(tc.get("expectedResult", "")).strip():
        return False

    unit_obj = tc.get("unit")
    if not isinstance(unit_obj, dict):
        return False
    if not unit_obj.get("file") or not isinstance(unit_obj.get("file"), str):
        return False
    if not unit_obj.get("symbol") or not isinstance(unit_obj.get("symbol"), str):
        return False
    if not unit_obj.get("type") or not isinstance(unit_obj.get("type"), str):
        return False

    return True


def get_existing_test_cases(code_map_source: Union[str, Path, dict], test_type: Optional[str] = None) -> List[Path]:
    """
    Dynamically inspects <directory containing repository-code-map.json>/test-cases/
    to detect any valid, previously generated test case JSON files matching test_type (or any valid test case if None).

    A file is considered valid if:
    - It has a .json extension
    - It parses as valid JSON
    - It contains at least one valid test case matching the specified test_type (or any valid test case if None)

    Returns a list of Path objects for all valid test case files found.
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
    tc_dir = target_dir / "test-cases"

    if not tc_dir.exists() or not tc_dir.is_dir():
        return []

    valid_files: List[Path] = []
    for json_file in tc_dir.glob("*.json"):
        if not json_file.is_file():
            continue
        try:
            with open(json_file, "r", encoding="utf-8") as fh:
                content = json.load(fh)
                cases: list = []
                if isinstance(content, dict) and isinstance(content.get("testCases"), list):
                    cases = content["testCases"]
                elif isinstance(content, list):
                    cases = content

                # Check for valid test cases matching test_type (or any valid if test_type is None)
                if any(is_valid_test_case(c, expected_test_type=test_type) for c in cases):
                    valid_files.append(json_file)
        except Exception:
            continue

    return sorted(valid_files, key=lambda p: p.name)


# ---------------------------------------------------------------------------
# Feature Context Ingestion (Optional Semantic Context)
# ---------------------------------------------------------------------------

def load_existing_feature_contexts(code_map_path: Path) -> Dict[str, dict]:
    """
    Loads all existing feature-context JSON files from <code_map_path.parent>/feature-context/.
    Returns a dict mapping feature_id -> feature_context.
    """
    fc_dir = code_map_path.parent / "feature-context"
    if not fc_dir.exists() or not fc_dir.is_dir():
        return {}

    feature_contexts: Dict[str, dict] = {}
    for json_file in fc_dir.glob("*.json"):
        if not json_file.is_file():
            continue
        try:
            with open(json_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, dict) and "feature" in data:
                    fid = data["feature"].get("id") or json_file.stem
                    feature_contexts[fid] = data
        except Exception:
            continue

    return feature_contexts


def map_files_to_features(feature_contexts: Dict[str, dict]) -> Dict[str, List[dict]]:
    """
    Creates an index: file_path -> list of feature summaries that touch this file.
    """
    file_to_features: Dict[str, List[dict]] = defaultdict(list)
    for fid, ctx in feature_contexts.items():
        feat = ctx.get("feature", {})
        feat_name = feat.get("name") or fid
        feat_desc = feat.get("description", "")
        flow = ctx.get("featureFlow", [])
        for f in ctx.get("files", []):
            p = f.get("path")
            if p:
                file_to_features[p].append({
                    "id": fid,
                    "name": feat_name,
                    "description": feat_desc,
                    "role": f.get("role"),
                    "relevance": f.get("relevance"),
                    "flow": flow[:3]
                })
    return file_to_features


# ---------------------------------------------------------------------------
# Dynamic Unit Discovery
# ---------------------------------------------------------------------------

EXECUTABLE_DECL_TYPES = {
    "function",
    "async_function",
    "generator_function",
    "arrow_function",
    "method",
    "constructor",
    "initializer",
    "component",
}

NON_TESTABLE_NAMES = {
    "anonymous",
    "unknown",
    "",
    "none",
    "null",
    "undefined",
    "default",
}


def discover_testable_units(
    code_map: dict,
    file_to_features: Optional[Dict[str, List[dict]]] = None
) -> List[dict]:
    """
    Dynamically identifies potential unit-test targets from the existing code-map declarations.
    Every unit corresponds strictly to an actual declaration / method / class in the project.
    Language- and framework-agnostic.
    """
    files_dict = code_map.get("files", {})
    discovered_units: List[dict] = []
    seen_units: Set[Tuple[str, str]] = set()

    for file_path, file_data in files_dict.items():
        if not isinstance(file_data, dict):
            continue

        language = file_data.get("language") or file_data.get("file", {}).get("language", "")
        f_feats = file_to_features.get(file_path, []) if file_to_features else []

        # 1. Inspect declarations (from streamlined format or raw AST)
        declarations = file_data.get("declarations") or []
        for decl in declarations:
            if not isinstance(decl, dict):
                continue
            name = (decl.get("name") or "").strip()
            dtype = (decl.get("type") or "").strip().lower()
            line = decl.get("line")

            if not name or name.lower() in NON_TESTABLE_NAMES:
                continue

            # A. Direct executable functions / components
            if dtype in EXECUTABLE_DECL_TYPES or "func" in dtype:
                key = (file_path, name)
                if key not in seen_units:
                    seen_units.add(key)
                    discovered_units.append({
                        "file": file_path,
                        "symbol": name,
                        "type": "function" if "component" not in dtype else "component",
                        "line": line,
                        "language": language,
                        "features": f_feats,
                    })

            # B. Class declaration with methods or behavior
            elif dtype == "class":
                methods = decl.get("methods") or []
                method_names = []
                for m in methods:
                    m_name = m if isinstance(m, str) else m.get("name") if isinstance(m, dict) else None
                    if m_name and m_name.strip() and m_name.lower() not in NON_TESTABLE_NAMES:
                        method_names.append(m_name.strip())

                if method_names:
                    # Each method is a unit
                    for m_name in method_names:
                        key = (file_path, f"{name}.{m_name}")
                        if key not in seen_units:
                            seen_units.add(key)
                            discovered_units.append({
                                "file": file_path,
                                "symbol": f"{name}.{m_name}",
                                "type": "method",
                                "container": name,
                                "methodName": m_name,
                                "line": line,
                                "language": language,
                                "features": f_feats,
                            })
                else:
                    # Class itself as a behavioral unit
                    key = (file_path, name)
                    if key not in seen_units:
                        seen_units.add(key)
                        discovered_units.append({
                            "file": file_path,
                            "symbol": name,
                            "type": "class",
                            "line": line,
                            "language": language,
                            "features": f_feats,
                        })

        # 2. Inspect symbols (legacy list format or un-streamlined artifacts)
        symbols = file_data.get("symbols") or []
        for sym in symbols:
            if not isinstance(sym, dict):
                continue
            name = (sym.get("name") or "").strip()
            stype = (sym.get("type") or "").strip().lower()
            line = sym.get("line") or (sym.get("location", {}).get("startLine") if isinstance(sym.get("location"), dict) else None)

            if not name or name.lower() in NON_TESTABLE_NAMES:
                continue

            if stype in EXECUTABLE_DECL_TYPES or "func" in stype:
                key = (file_path, name)
                if key not in seen_units:
                    seen_units.add(key)
                    discovered_units.append({
                        "file": file_path,
                        "symbol": name,
                        "type": "function",
                        "line": line,
                        "language": language,
                        "features": f_feats,
                    })

        # 3. Inspect classes field if present separately
        classes = file_data.get("classes") or []
        for cls in classes:
            if not isinstance(cls, dict):
                continue
            cname = (cls.get("name") or "").strip()
            if not cname or cname.lower() in NON_TESTABLE_NAMES:
                continue
            for m in cls.get("methods", []):
                mname = m if isinstance(m, str) else m.get("name") if isinstance(m, dict) else None
                if mname and mname.strip() and mname.lower() not in NON_TESTABLE_NAMES:
                    key = (file_path, f"{cname}.{mname.strip()}")
                    if key not in seen_units:
                        seen_units.add(key)
                        discovered_units.append({
                            "file": file_path,
                            "symbol": f"{cname}.{mname.strip()}",
                            "type": "method",
                            "container": cname,
                            "methodName": mname.strip(),
                            "language": language,
                            "features": f_feats,
                        })

    return discovered_units


def discover_functional_targets(
    code_map: dict,
    file_to_features: Optional[Dict[str, List[dict]]] = None
) -> List[dict]:
    """
    Dynamically discovers functional test targets (UI components, pages, user workflows, feature flows)
    grounded in actual code map declarations, React metadata, and feature contexts.
    Language- and framework-agnostic.
    """
    files_dict = code_map.get("files", {})
    discovered: List[dict] = []
    seen: Set[Tuple[str, str]] = set()

    for file_path, file_data in files_dict.items():
        if not isinstance(file_data, dict):
            continue
        language = file_data.get("language") or file_data.get("file", {}).get("language", "")
        f_feats = file_to_features.get(file_path, []) if file_to_features else []

        # 1. React components and hooks from parser metadata
        react_meta = file_data.get("react") or {}
        if isinstance(react_meta, dict):
            components = react_meta.get("components") or []
            for comp in components:
                c_name = comp if isinstance(comp, str) else comp.get("name") if isinstance(comp, dict) else None
                if c_name and c_name.lower() not in NON_TESTABLE_NAMES:
                    key = (file_path, c_name)
                    if key not in seen:
                        seen.add(key)
                        discovered.append({
                            "file": file_path,
                            "symbol": c_name,
                            "type": "component",
                            "language": language,
                            "features": f_feats,
                            "props": comp.get("props", []) if isinstance(comp, dict) else [],
                            "hooks": react_meta.get("hooks", []),
                        })

        # 2. Declarations with component / page / view type or naming convention
        declarations = file_data.get("declarations") or file_data.get("symbols") or []
        for decl in declarations:
            if not isinstance(decl, dict):
                continue
            name = (decl.get("name") or "").strip()
            dtype = (decl.get("type") or "").strip().lower()
            line = decl.get("line")
            if not name or name.lower() in NON_TESTABLE_NAMES:
                continue

            is_functional_candidate = (
                dtype in ("component", "page", "view", "screen", "controller", "dialog", "modal", "workflow")
                or name.endswith(("Page", "Screen", "View", "Component", "Dialog", "Modal", "Form", "Workflow"))
                or (language in ("javascript", "typescript", "jsx", "tsx") and name[0].isupper() and file_path.endswith((".jsx", ".tsx", ".vue", ".svelte")))
            )
            if is_functional_candidate:
                key = (file_path, name)
                if key not in seen:
                    seen.add(key)
                    discovered.append({
                        "file": file_path,
                        "symbol": name,
                        "type": "component" if dtype not in ("page", "view", "controller") else dtype,
                        "line": line,
                        "language": language,
                        "features": f_feats,
                    })

    # If no UI components were discovered (e.g. backend-only project), fall back to public executable functions/workflows
    if not discovered:
        for file_path, file_data in files_dict.items():
            if not isinstance(file_data, dict):
                continue
            language = file_data.get("language") or file_data.get("file", {}).get("language", "")
            f_feats = file_to_features.get(file_path, []) if file_to_features else []
            declarations = file_data.get("declarations") or file_data.get("symbols") or []
            for decl in declarations:
                if not isinstance(decl, dict):
                    continue
                name = (decl.get("name") or "").strip()
                dtype = (decl.get("type") or "").strip().lower()
                line = decl.get("line")
                if not name or name.lower() in NON_TESTABLE_NAMES:
                    continue
                if dtype in EXECUTABLE_DECL_TYPES or "func" in dtype:
                    key = (file_path, name)
                    if key not in seen:
                        seen.add(key)
                        discovered.append({
                            "file": file_path,
                            "symbol": name,
                            "type": "workflow",
                            "line": line,
                            "language": language,
                            "features": f_feats,
                        })

    return discovered


def discover_api_endpoints(
    code_map: dict,
    file_to_features: Optional[Dict[str, List[dict]]] = None
) -> List[dict]:
    """
    Dynamically discovers API route and endpoint targets from code map declarations,
    routes metadata, decorators, and controllers.
    Language- and framework-agnostic.
    """
    files_dict = code_map.get("files", {})
    discovered: List[dict] = []
    seen: Set[Tuple[str, str, str]] = set()

    for file_path, file_data in files_dict.items():
        if not isinstance(file_data, dict):
            continue
        language = file_data.get("language") or file_data.get("file", {}).get("language", "")
        f_feats = file_to_features.get(file_path, []) if file_to_features else []

        # 1. Inspect explicit 'routes' array in file_data
        routes = file_data.get("routes") or []
        for r in routes:
            if not isinstance(r, dict):
                continue
            r_path = r.get("path") or r.get("route") or r.get("endpoint") or ""
            r_method = (r.get("method") or r.get("httpMethod") or "GET").strip().upper()
            r_handler = r.get("handler") or r.get("symbol") or r.get("name") or r_path
            if r_path:
                key = (file_path, r_method, r_path)
                if key not in seen:
                    seen.add(key)
                    discovered.append({
                        "file": file_path,
                        "symbol": r_handler,
                        "route": r_path,
                        "httpMethod": r_method,
                        "type": "route",
                        "line": r.get("line"),
                        "language": language,
                        "features": f_feats,
                    })

        # 2. Inspect declarations for route decorators or route-like patterns
        declarations = file_data.get("declarations") or file_data.get("symbols") or []
        for decl in declarations:
            if not isinstance(decl, dict):
                continue
            name = (decl.get("name") or "").strip()
            dtype = (decl.get("type") or "").strip().lower()
            line = decl.get("line")
            if not name or name.lower() in NON_TESTABLE_NAMES:
                continue

            decorators = decl.get("decorators") or []
            has_route_decorator = False
            route_path = ""
            http_method = "GET"

            for dec in decorators:
                dec_str = dec if isinstance(dec, str) else dec.get("text") or dec.get("name") or ""
                m_route = re.search(r"@(app|router|blueprint|bp)\.(get|post|put|delete|patch|route)\s*\(\s*['\"]([^'\"]+)['\"]", dec_str, re.IGNORECASE)
                if m_route:
                    has_route_decorator = True
                    dec_method = m_route.group(2).upper()
                    if dec_method != "ROUTE":
                        http_method = dec_method
                    route_path = m_route.group(3)
                    break
                m_spring = re.search(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\s*(?:\(\s*(?:value\s*=\s*)?['\"]([^'\"]+)['\"])?", dec_str, re.IGNORECASE)
                if m_spring:
                    has_route_decorator = True
                    http_method = m_spring.group(1).upper()
                    if http_method == "REQUEST":
                        http_method = "GET"
                    route_path = m_spring.group(2) or f"/{name.lower()}"
                    break

            # Check if file is in routes/controllers/api directory
            norm_f = file_path.replace("\\", "/").lower()
            is_route_file = any(seg in norm_f for seg in ("/routes/", "/controllers/", "/api/", "/endpoints/"))
            
            if has_route_decorator:
                key = (file_path, http_method, route_path or name)
                if key not in seen:
                    seen.add(key)
                    discovered.append({
                        "file": file_path,
                        "symbol": name,
                        "route": route_path or f"/{name.lower()}",
                        "httpMethod": http_method,
                        "type": "route",
                        "line": line,
                        "language": language,
                        "features": f_feats,
                    })
            elif is_route_file and (dtype in EXECUTABLE_DECL_TYPES or "func" in dtype or dtype == "method"):
                # Infer HTTP method from handler name if possible
                inferred_method = "GET"
                for m_candidate in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                    if name.upper().startswith(m_candidate) or name.upper().endswith(m_candidate):
                        inferred_method = m_candidate
                        break
                inferred_route = f"/api/{name.lower().replace('_', '-')}"
                key = (file_path, inferred_method, inferred_route)
                if key not in seen:
                    seen.add(key)
                    discovered.append({
                        "file": file_path,
                        "symbol": name,
                        "route": inferred_route,
                        "httpMethod": inferred_method,
                        "type": "route",
                        "line": line,
                        "language": language,
                        "features": f_feats,
                    })

    return discovered



# ---------------------------------------------------------------------------
# Unit Grouping for Bounded LLM Context
# ---------------------------------------------------------------------------

def group_units_for_generation(
    units: List[dict],
    feature_contexts: Optional[Dict[str, dict]] = None
) -> List[dict]:
    """
    Groups units logically to prevent sending large or unrelated contexts to the LLM.
    If feature contexts exist, groups by feature where appropriate;
    otherwise groups by cohesive file or module.
    """
    if not units:
        return []

    groups_map: Dict[str, dict] = {}

    for unit in units:
        f_feats = unit.get("features", [])
        if f_feats and feature_contexts:
            # Group under the primary feature context
            feat = f_feats[0]
            fid = feat["id"]
            if fid not in groups_map:
                groups_map[fid] = {
                    "id": fid,
                    "name": feat["name"],
                    "description": feat.get("description", ""),
                    "featureContext": feature_contexts.get(fid),
                    "units": [],
                    "files": set(),
                }
            groups_map[fid]["units"].append(unit)
            groups_map[fid]["files"].add(unit["file"])
        else:
            # Group by file/module
            file_key = unit["file"]
            base_id = re.sub(r"[^a-zA-Z0-9]+", "-", file_key.rsplit(".", 1)[0]).strip("-") or "module"
            if base_id not in groups_map:
                fname = Path(file_key).name
                groups_map[base_id] = {
                    "id": base_id,
                    "name": f"Module {fname}",
                    "description": f"Unit tests for {file_key}",
                    "featureContext": None,
                    "units": [],
                    "files": set(),
                }
            groups_map[base_id]["units"].append(unit)
            groups_map[base_id]["files"].add(unit["file"])

    result: List[dict] = []
    for g in groups_map.values():
        g["files"] = sorted(list(g["files"]))
        result.append(g)

    return result


# ---------------------------------------------------------------------------
# Focused Context Construction
# ---------------------------------------------------------------------------

def _read_source_snippet_if_available(
    file_path: str,
    code_map_path: Optional[Path],
    line: Optional[int],
    project_root: Optional[Path] = None,
    code_map: Optional[dict] = None
) -> Optional[str]:
    """Safely extracts a small line snippet (up to 30 lines) if the source file is on disk."""
    if not line:
        return None

    disk_path = find_source_file_on_disk(
        file_rel_path=file_path,
        project_root=project_root,
        code_map_path=code_map_path,
        code_map=code_map
    )
    if not disk_path or not disk_path.exists():
        return None

    try:
        with open(disk_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
            start = max(0, line - 2)
            end = min(len(lines), line + 28)
            snippet = "".join(lines[start:end]).strip()
            if snippet:
                return snippet[:1200]
    except Exception:
        pass
    return None


def build_focused_unit_context(
    group: dict,
    code_map: dict,
    code_map_path: Optional[Path] = None,
    project_root: Optional[Path] = None
) -> dict:
    """
    Constructs a tightly focused context containing ONLY information relevant to the group's units:
    - Target file, language, and line count
    - Target symbols (methods/functions)
    - Direct dependencies of these files only
    - Callers and callees touching these files
    - Semantic feature summary (if available)
    - Implementation snippet (if source is accessible)
    Never sends the entire repository or entire code map.
    """
    resolved_root = resolve_project_root(code_map, code_map_path, project_root)
    files_dict = code_map.get("files", {})
    all_relationships = code_map.get("relationships", [])
    group_files = set(group["files"])

    # Extract relevant direct relationships (callers / callees of group files only)
    relevant_relationships = []
    for r in all_relationships:
        frm = r.get("from", "")
        to = r.get("to", "")
        if frm in group_files or to in group_files:
            relevant_relationships.append({
                "from": frm,
                "to": to,
                "type": r.get("type", "uses"),
                "evidence": r.get("evidence")
            })

    # Build per-file unit details
    file_contexts = []
    for fp in group["files"]:
        fdata = files_dict.get(fp, {})
        file_units = [u for u in group["units"] if u["file"] == fp]
        unit_summaries = []

        for u in file_units:
            usum = {
                "symbol": u["symbol"],
                "type": u["type"],
            }
            if u.get("line"):
                usum["line"] = u["line"]
            if u.get("container"):
                usum["container"] = u["container"]

            # Attach authoritative source code snippet if available on disk
            s_status, s_data = extract_actual_source_code(
                file_rel_path=fp,
                target_symbol=u.get("symbol"),
                decl_line=u.get("line"),
                language=fdata.get("language") or "",
                code_map_path=code_map_path,
                project_root=resolved_root,
                code_map=code_map
            )
            if s_data and s_data.get("sourceCode"):
                usum["codeSnippet"] = s_data["sourceCode"]
            else:
                snippet = _read_source_snippet_if_available(
                    fp, code_map_path, u.get("line"),
                    project_root=resolved_root, code_map=code_map
                )
                if snippet:
                    usum["codeSnippet"] = snippet

            unit_summaries.append(usum)

        # File direct dependencies
        direct_deps = []
        for d in (fdata.get("dependencies") or fdata.get("imports") or []):
            if isinstance(d, dict):
                target = d.get("target") or d.get("source")
                if target:
                    direct_deps.append({"target": target, "type": d.get("type", "internal")})

        file_contexts.append({
            "file": fp,
            "language": fdata.get("language") or fdata.get("file", {}).get("language", "unknown"),
            "lineCount": fdata.get("lineCount") or fdata.get("file", {}).get("lineCount", 0),
            "unitsToTest": unit_summaries,
            "directDependencies": direct_deps[:10],
        })

    focused = {
        "groupName": group["name"],
        "targetFiles": file_contexts,
        "relationships": relevant_relationships[:15],
    }

    if group.get("featureContext"):
        fc = group["featureContext"]
        focused["featureContext"] = {
            "name": fc.get("feature", {}).get("name"),
            "description": fc.get("feature", {}).get("description"),
            "flow": fc.get("featureFlow", [])[:3]
        }

    # Dynamically select minimal context for the group's primary target
    first_u = group["units"][0] if group.get("units") else {}
    if first_u:
        focused["dynamicContext"] = select_context_for_test_case(
            target={"file": first_u.get("file"), "symbol": first_u.get("symbol"), "type": first_u.get("type"), "line": first_u.get("line")},
            test_type="unit",
            code_map=code_map,
            code_map_path=code_map_path,
            project_root=resolved_root,
            feature_contexts={fc["id"]: group["featureContext"]} if (group.get("featureContext") and "id" in group["featureContext"]) else None
        )

    return focused


# ---------------------------------------------------------------------------
# LLM Prompt Construction & Execution
# ---------------------------------------------------------------------------

UNIT_TEST_SCHEMA = {
    "testCases": [
        {
            "testCaseId": "TC-001",
            "unit": {
                "file": "relative/path/to/file",
                "symbol": "functionOrMethodName",
                "type": "function|method|class"
            },
            "title": "Clear concise unit test title",
            "description": "Explains what specific isolated behavior of the unit is tested",
            "preconditions": ["Optional setup or mock specification"],
            "inputs": {"argName": "value"},
            "expectedResult": "Expected return value, state modification, or raised error",
            "testType": "unit",
            "priority": "high|medium|low",
            "rationale": "Why this isolated unit test is necessary based on actual code evidence"
        }
    ]
}


def build_generation_messages(focused_context: dict) -> list:
    """
    Builds the system and user messages for Hugging Face LLM.
    Strictly forbids integration/E2E/API tests and executable code.
    """
    system_content = (
        "You are an expert software verification engineer specializing in UNIT TESTING.\n\n"
        "Your task is to generate structured UNIT TEST CASE SPECIFICATIONS based ONLY on the provided code evidence.\n\n"
        "============================================================\n"
        "SOURCE-GROUNDING GUARDRAILS (CRITICAL)\n"
        "============================================================\n"
        "1. ONLY UNIT TEST CASES are permitted. Every test case MUST have 'testType': 'unit'.\n"
        "2. NEVER infer behavior from a function, class, method, route, or component name alone. A symbol name is only an identifier/location signal, NOT evidence of behavior.\n"
        "3. ACTUAL SOURCE CODE is authoritative. All assertions, branches, and expected results MUST be grounded in the provided implementation source code.\n"
        "4. DO NOT invent: parameters, parameter names, parameter types, return types, return values, database operations, API calls, validation rules, error handling, side effects, state changes, UI behavior, business rules, file operations, authentication behavior, authorization behavior unless supported by the provided evidence.\n"
        "5. DO NOT infer an operation merely because its name suggests it (e.g. 'delete_transaction' does NOT prove that the function deletes a database record).\n"
        "6. DO NOT infer return types from naming conventions (e.g. 'get_categories' does NOT automatically mean the function returns a list).\n"
        "7. DO NOT invent test inputs. Inputs must be derived from actual function parameters, actual source behavior, explicit Test Case information, or relevant structural metadata.\n"
        "8. DO NOT invent database schemas, table names, columns, API endpoints, routes, or external services.\n"
        "9. If the source shows a behavior that contradicts the Test Case intent, DO NOT silently accept the Test Case assumption. The generated Test Case must reflect the ACTUAL IMPLEMENTATION.\n"
        "10. If sufficient evidence does not exist to determine a behavior: DO NOT GUESS. Either omit that behavioral claim or mark the case as requiring additional context.\n"
        "11. Every test case must test ONLY one of the specified units in complete isolation.\n"
        "12. If a unit interacts with an external dependency or file, specify a MOCK/STUB in preconditions or inputs.\n"
        "13. DO NOT generate integration tests, API tests, E2E tests, UI tests, database tests, or system workflows.\n"
        "14. DO NOT generate executable test scripts or test implementation code (no jest, pytest, junit scripts).\n"
        "15. Every 'symbol' and 'file' under 'unit' MUST exactly match the provided unit targets.\n\n"
        "============================================================\n"
        "EVIDENCE HIERARCHY\n"
        "============================================================\n"
        "1. Actual source code\n"
        "2. Explicit Test Case information\n"
        "3. repository-code-map.json\n"
        "4. Validated Dependency Graph\n"
        "5. Feature Context\n"
        "6. General programming conventions (MUST NEVER OVERRIDE PROJECT-SPECIFIC EVIDENCE)\n\n"
        "============================================================\n"
        "FINAL RULE\n"
        "============================================================\n"
        "GENERATE TEST CASES FROM EVIDENCE, NOT FROM NAMES.\n"
        "Return ONLY valid JSON matching the schema below. No markdown, no prose."
    )

    user_content = (
        f"Focused Unit Context:\n{json.dumps(focused_context, indent=2)}\n\n"
        f"Required JSON Output Schema:\n{json.dumps(UNIT_TEST_SCHEMA, indent=2)}\n\n"
        "Return ONLY the JSON object with the 'testCases' array."
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


FUNCTIONAL_TEST_SCHEMA = {
    "testCases": [
        {
            "testCaseId": "TC-001",
            "unit": {
                "file": "relative/path/to/file",
                "symbol": "ComponentNameOrWorkflow",
                "type": "component|workflow"
            },
            "title": "Clear concise functional test scenario title",
            "description": "Explains user workflow, interactions, state transitions, and expected outcome",
            "preconditions": ["Prerequisites or initial component/page state"],
            "inputs": {"userActions": ["action 1", "action 2"], "propsOrState": {}},
            "expectedResult": "Expected DOM rendered state, callback invocations, or workflow transition",
            "testType": "functional",
            "priority": "high|medium|low",
            "rationale": "Why this functional behavior verification is necessary based on code evidence"
        }
    ]
}


def build_functional_generation_messages(focused_context: dict) -> list:
    """
    Builds the system and user messages for Functional Test Case Generation.
    Focuses on user interactions, page/component workflows, state transitions, and DOM outcomes.
    Strictly forbids inventing arbitrary CSS selectors or ungrounded behaviors.
    """
    system_content = (
        "You are an expert software verification engineer specializing in FUNCTIONAL & USER WORKFLOW TESTING.\n\n"
        "Your task is to generate structured FUNCTIONAL TEST CASE SPECIFICATIONS based ONLY on the provided code evidence.\n\n"
        "============================================================\n"
        "SOURCE-GROUNDING GUARDRAILS (CRITICAL)\n"
        "============================================================\n"
        "1. ONLY FUNCTIONAL TEST CASES are permitted. Every test case MUST have 'testType': 'functional'.\n"
        "2. Ground tests in actual UI components, event handlers, props, state transitions, and user journeys.\n"
        "3. DO NOT invent arbitrary CSS selectors, XPath expressions, or ungrounded DOM elements.\n"
        "4. DO NOT invent business logic, validation rules, or navigation flows not supported by the provided code.\n"
        "5. Every test case must reference an actual component, page, or workflow target from the provided context.\n"
        "6. If the evidence is insufficient to verify a complete workflow, report the specific gap instead of guessing.\n\n"
        "Return ONLY valid JSON matching the schema below. No markdown, no prose."
    )

    user_content = (
        f"Focused Functional Context:\n{json.dumps(focused_context, indent=2)}\n\n"
        f"Required JSON Output Schema:\n{json.dumps(FUNCTIONAL_TEST_SCHEMA, indent=2)}\n\n"
        "Return ONLY the JSON object with the 'testCases' array."
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


API_TEST_SCHEMA = {
    "testCases": [
        {
            "testCaseId": "TC-001",
            "unit": {
                "file": "relative/path/to/file",
                "symbol": "handlerSymbol",
                "route": "/api/endpoint/path",
                "httpMethod": "GET|POST|PUT|DELETE|PATCH",
                "type": "route"
            },
            "title": "Clear concise API test scenario title",
            "description": "Explains the endpoint request variation and expected response behavior",
            "preconditions": ["Optional server state or authentication token requirement"],
            "inputs": {"params": {}, "headers": {}, "body": {}},
            "expectedResult": "Expected HTTP status code (e.g. 200, 201, 400, 404), response schema, or error message",
            "testType": "api",
            "priority": "high|medium|low",
            "rationale": "Why this API contract/endpoint test is necessary based on route and handler evidence"
        }
    ]
}


def build_api_generation_messages(focused_context: dict) -> list:
    """
    Builds the system and user messages for API Test Case Generation.
    Focuses on HTTP endpoints, methods, parameters, request bodies, status codes, and response shapes.
    Strictly forbids fabricating endpoints or ungrounded response schemas.
    """
    system_content = (
        "You are an expert software verification engineer specializing in API & REST ENDPOINT TESTING.\n\n"
        "Your task is to generate structured API TEST CASE SPECIFICATIONS based ONLY on the provided code evidence.\n\n"
        "============================================================\n"
        "SOURCE-GROUNDING GUARDRAILS (CRITICAL)\n"
        "============================================================\n"
        "1. ONLY API TEST CASES are permitted. Every test case MUST have 'testType': 'api'.\n"
        "2. Ground tests in actual route paths, HTTP methods (GET, POST, PUT, DELETE, PATCH), and handler declarations.\n"
        "3. DO NOT invent endpoints, route paths, HTTP methods, or headers not grounded in the provided code.\n"
        "4. Assert realistic HTTP status codes (e.g. 200/201 for success, 400/404 for client errors, 500 for server errors) grounded in handler logic.\n"
        "5. Include positive, negative, and boundary scenarios when supported by parameter validation or branch logic.\n"
        "6. If the evidence is insufficient to determine an endpoint contract, report the specific gap instead of guessing.\n\n"
        "Return ONLY valid JSON matching the schema below. No markdown, no prose."
    )

    user_content = (
        f"Focused API Context:\n{json.dumps(focused_context, indent=2)}\n\n"
        f"Required JSON Output Schema:\n{json.dumps(API_TEST_SCHEMA, indent=2)}\n\n"
        "Return ONLY the JSON object with the 'testCases' array."
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def build_type_aware_generation_messages(focused_context: dict, test_type: str = "unit") -> list:
    """Dispatches to the appropriate prompt generator based on test_type."""
    t_norm = (test_type or "unit").strip().lower()
    if t_norm in ("functional", "e2e"):
        return build_functional_generation_messages(focused_context)
    elif t_norm == "api":
        return build_api_generation_messages(focused_context)
    return build_generation_messages(focused_context)


async def generate_test_cases_for_group(focused_context: dict, test_type: str = "unit") -> list:
    """
    Calls the existing Hugging Face model using execute_hf_llm_call and parses the JSON response.
    Supports 'unit', 'functional', and 'api' test generation.
    """
    messages = build_type_aware_generation_messages(focused_context, test_type=test_type)
    stage_name = "FUNCTIONAL_TEST_GENERATION" if test_type in ("functional", "e2e") else ("API_TEST_GENERATION" if test_type == "api" else "UNIT_TEST_GENERATION")
    raw_content, _, _ = await execute_hf_llm_call(
        messages=messages,
        max_tokens=3000,
        temperature=0.1,
        stage_name=stage_name,
    )

    cleaned = strip_markdown_fences(raw_content)
    ok, parsed, _ = parse_json(cleaned)

    if not ok or not isinstance(parsed, dict) or "testCases" not in parsed:
        rep_ok, rep_data, _, _ = repair_json_deterministically(cleaned)
        if rep_ok and isinstance(rep_data, dict) and "testCases" in rep_data:
            parsed = rep_data
        elif rep_ok and isinstance(rep_data, list):
            parsed = {"testCases": rep_data}
        else:
            backend_logger.warning(f"[{stage_name}] LLM output was not valid JSON.")
            return []

    return parsed.get("testCases", [])


# ---------------------------------------------------------------------------
# Deterministic Validation (10 Criteria)
# ---------------------------------------------------------------------------

FORBIDDEN_TEST_TERMS = {
    "playwright",
    "selenium",
    "cypress",
    "browser",
    "e2e",
    "end to end",
    "end-to-end",
    "integration test",
    "api endpoint",
    "http request",
    "real database",
    "network call",
    "ui interaction",
}


def validate_test_cases(
    raw_cases: list,
    valid_units_by_file: Dict[str, Set[str]],
    code_map: dict,
    expected_test_type: Optional[str] = "unit"
) -> List[dict]:
    """
    Deterministically validates test cases against criteria:
    1. Valid JSON (already parsed)
    2. Required fields exist
    3. testType matches expected_test_type (defaults to 'unit')
    4. Referenced file exists in repository-code-map.json
    5. Referenced symbol exists in analyzed declarations
    6. Test cases correspond to a selected unit
    7. Duplicate test cases are removed
    8. Rejects forbidden terms when expected_test_type is 'unit'
    9. No fabricated symbols
    10. Output conforms to schema
    """
    available_files = set(code_map.get("files", {}).keys())
    validated: List[dict] = []
    seen_signatures: Set[str] = set()
    norm_expected_type = expected_test_type.strip().lower() if expected_test_type else None

    for idx, tc in enumerate(raw_cases):
        if not isinstance(tc, dict):
            continue

        # 3. testType validation
        test_type = str(tc.get("testType", "")).strip().lower()
        if norm_expected_type == "unit":
            if test_type != "unit":
                backend_logger.warning(f"[UnitTestGenerator] Rejecting non-unit test: {tc.get('title')} ({test_type})")
                continue
        elif norm_expected_type in ("integration", "functional", "e2e"):
            if test_type not in ("functional", "e2e"):
                backend_logger.warning(f"[UnitTestGenerator] Rejecting mismatch test type: {tc.get('title')} ({test_type} != {norm_expected_type})")
                continue
        elif norm_expected_type == "api":
            if test_type != "api":
                backend_logger.warning(f"[UnitTestGenerator] Rejecting non-api test: {tc.get('title')} ({test_type})")
                continue
        elif norm_expected_type not in (None, "all") and test_type != norm_expected_type:
            continue

        # 2. Required fields
        unit_info = tc.get("unit") or tc.get("target")
        if not isinstance(unit_info, dict):
            continue

        file_ref = (unit_info.get("file") or "").replace("\\", "/").strip()
        symbol_ref = (unit_info.get("symbol") or "").strip()
        route_ref = (unit_info.get("route") or unit_info.get("endpoint") or "").strip()
        unit_type = (unit_info.get("type") or ("route" if route_ref else "function")).strip()

        title = str(tc.get("title") or "").strip()
        desc = str(tc.get("description") or "").strip()
        expected = str(tc.get("expectedResult") or "").strip()
        inputs = tc.get("inputs")

        if not file_ref or (not symbol_ref and not route_ref) or not title or not desc or not expected or inputs is None:
            continue

        # 4. Referenced file must exist in code-map
        if file_ref not in available_files:
            backend_logger.warning(f"[UnitTestGenerator] Rejecting test for non-existent file: {file_ref}")
            continue

        # 5. & 9. Symbol/Target must exist in analyzed declarations (no fabrication)
        allowed_symbols = valid_units_by_file.get(file_ref, set())
        if allowed_symbols and symbol_ref:
            symbol_matches = (
                symbol_ref in allowed_symbols
                or symbol_ref.split(".")[-1] in allowed_symbols
                or any(s.endswith(f".{symbol_ref}") for s in allowed_symbols)
            )
            if not symbol_matches and not route_ref:
                backend_logger.warning(f"[UnitTestGenerator] Rejecting fabricated symbol: {symbol_ref} in {file_ref}")
                continue

        # 8. Check for forbidden integration / E2E / UI terms if unit test
        if norm_expected_type == "unit" or test_type == "unit":
            text_to_check = f"{title} {desc}".lower()
            if any(term in text_to_check for term in FORBIDDEN_TEST_TERMS):
                backend_logger.warning(f"[UnitTestGenerator] Rejecting integration/E2E test: {title}")
                continue

        # 7. Deduplication
        inputs_str = json.dumps(inputs, sort_keys=True) if isinstance(inputs, (dict, list)) else str(inputs)
        sig = f"{file_ref}::{symbol_ref or route_ref}::{title}::{inputs_str}"
        if sig in seen_signatures:
            continue
        seen_signatures.add(sig)

        # Build clean validated test case
        tc_id = tc.get("testCaseId") or f"TC-{len(validated) + 1:03d}"
        priority = str(tc.get("priority", "medium")).strip().lower()
        if priority not in ("high", "medium", "low"):
            priority = "medium"

        preconditions = tc.get("preconditions", [])
        if not isinstance(preconditions, list):
            preconditions = [str(preconditions)] if preconditions else []

        actual_type = test_type if test_type in ("unit", "functional", "api", "integration", "e2e") else (norm_expected_type or "unit")
        target_label = symbol_ref or route_ref or "target"
        rationale = str(tc.get("rationale") or f"{actual_type.capitalize()} test verifying behavior of {target_label}.").strip()

        target_dict = {
            "file": file_ref,
            "symbol": symbol_ref,
            "type": unit_type,
        }
        if route_ref:
            target_dict["route"] = route_ref
        http_method = unit_info.get("httpMethod") or unit_info.get("method")
        if http_method:
            target_dict["httpMethod"] = str(http_method).upper()

        validated.append({
            "testCaseId": tc_id,
            "unit": target_dict,
            "target": target_dict,
            "title": title,
            "description": desc,
            "preconditions": preconditions,
            "inputs": inputs,
            "expectedResult": expected,
            "testType": actual_type,
            "priority": priority,
            "rationale": rationale,
        })

    return validated


# ---------------------------------------------------------------------------
# Output Storage
# ---------------------------------------------------------------------------

def save_test_cases(
    code_map_path: Path,
    group_name: str,
    test_cases: List[dict],
    existing_names: Set[str]
) -> Path:
    """
    Saves validated test cases into <directory-containing-repository-code-map>/test-cases/
    using dynamic, collision-safe filename derivation.
    """
    target_dir = code_map_path.parent / "test-cases"
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = sanitize_filename(group_name, existing_names)
    out_file = target_dir / filename

    content = {
        "group": group_name,
        "testCases": test_cases
    }

    with open(out_file, "w", encoding="utf-8") as fh:
        json.dump(content, fh, indent=2)

    print(f"[UnitTestGenerator] Saved: {out_file.as_posix()} ({len(test_cases)} tests)", flush=True)
    backend_logger.info(f"[UnitTestGenerator] Saved: {out_file.as_posix()} ({len(test_cases)} tests)")
    return out_file


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def generate_all_unit_test_cases(
    code_map_source: Union[str, Path, dict],
    force: bool = False
) -> Dict[str, Any]:
    """
    End-to-end Unit Test Case Generation pipeline:
    1. Guard check: Check if valid test cases already exist.
       If so and force=False -> return immediately without calling Hugging Face.
    2. Load code map and normalize paths.
    3. Ingest existing feature contexts (if present).
    4. Dynamically discover testable units.
    5. Group units into bounded batches.
    6. Construct focused context per group.
    7. Generate test cases via existing Hugging Face LLM.
    8. Deterministically validate each test case.
    9. Save to <project-directory>/test-cases/.
    """
    # 1. Existing Output Guard Check BEFORE calling HF
    if not force:
        existing_files = get_existing_test_cases(code_map_source)
        if existing_files:
            msg = "Unit test cases already generated."
            print(f"[UnitTestGenerator] {msg} Found {len(existing_files)} file(s). Skipping LLM generation.", flush=True)
            backend_logger.info(f"[UnitTestGenerator] {msg} Found {len(existing_files)} file(s). Skipping LLM generation.")

            total_cases = 0
            for ef in existing_files:
                try:
                    with open(ef, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                        cases = data.get("testCases", []) if isinstance(data, dict) else data
                        total_cases += len(cases) if isinstance(cases, list) else 0
                except Exception:
                    pass

            return {
                "status": "already_exists",
                "alreadyExists": True,
                "savedFiles": [f.name for f in existing_files],
                "testCaseCount": total_cases,
                "unitCount": total_cases,
                "message": msg,
            }

    # 2. Load and normalize code map
    code_map, code_map_path = load_code_map(code_map_source)
    if not code_map or not code_map.get("files"):
        raise ValueError("repository-code-map.json is missing or contains no files.")

    resolved_project_root = resolve_project_root(code_map, code_map_path)

    # 3. Ingest existing feature contexts (supplementary semantic context)
    feature_contexts = load_existing_feature_contexts(code_map_path) if code_map_path else {}
    file_to_features = map_files_to_features(feature_contexts)

    # 4. Dynamic Unit Discovery
    units = discover_testable_units(code_map, file_to_features)
    if not units:
        raise ValueError("No testable units found in repository code map.")

    print(f"[UnitTestGenerator] Discovered {len(units)} testable unit(s)", flush=True)
    backend_logger.info(f"[UnitTestGenerator] Discovered {len(units)} testable unit(s)")

    # Build lookup of allowed symbols per file for deterministic validation
    valid_units_by_file: Dict[str, Set[str]] = defaultdict(set)
    for u in units:
        valid_units_by_file[u["file"]].add(u["symbol"])
        if u.get("methodName"):
            valid_units_by_file[u["file"]].add(u["methodName"])
        if u.get("container"):
            valid_units_by_file[u["file"]].add(u["container"])

    # 5. Group units into bounded batches
    groups = group_units_for_generation(units, feature_contexts)

    # 6. Generate and validate per group
    all_saved_files: List[Path] = []
    total_test_cases = 0
    existing_filenames: Set[str] = set()
    generation_failures: List[str] = []

    for group in groups:
        focused = build_focused_unit_context(group, code_map, code_map_path, project_root=resolved_project_root)
        try:
            raw_test_cases = await generate_test_cases_for_group(focused)
        except Exception as exc:
            err_str = str(exc)
            generation_failures.append(f"Group '{group['name']}' API call failed: {err_str}")
            backend_logger.error(f"[UnitTestGenerator] Group '{group['name']}' generation error: {err_str}")
            raw_test_cases = []

        if not raw_test_cases:
            generation_failures.append(f"Group '{group['name']}' returned no parsed test cases")
            continue

        validated = validate_test_cases(raw_test_cases, valid_units_by_file, code_map)
        if not validated:
            generation_failures.append(f"Group '{group['name']}' test cases rejected by validation")

        if validated and code_map_path:
            out_file = save_test_cases(code_map_path, group["name"], validated, existing_filenames)
            all_saved_files.append(out_file)
            total_test_cases += len(validated)

    if not all_saved_files and not total_test_cases:
        active_provider = get_active_provider_name()
        try:
            pcfg = get_provider_config(active_provider, require_api_key=False)
            model_name = pcfg.get("model", "unknown")
        except Exception:
            model_name = "unknown"

        detail_suffix = f": {'; '.join(set(generation_failures))}" if generation_failures else ""
        raise ValueError(f"LLM provider {active_provider} ({model_name}) did not produce valid unit test cases{detail_suffix}.")

    return {
        "status": "success",
        "alreadyExists": False,
        "savedFiles": [f.name for f in all_saved_files],
        "testCaseCount": total_test_cases,
        "unitCount": len(units),
        "message": f"Unit test cases generated successfully ({total_test_cases} test cases across {len(all_saved_files)} file(s)).",
    }


async def generate_all_test_cases(
    code_map_source: Union[str, Path, dict],
    force: bool = False,
    test_type: str = "unit"
) -> Dict[str, Any]:
    """
    Unified entry point for Test Case Generation supporting:
    - 'unit': isolated functions, methods, classes (delegates to generate_all_unit_test_cases)
    - 'functional': user workflows, component interactions, state transitions
    - 'api': endpoints, routes, methods, payloads, status codes
    - 'all': generates across unit, functional, and api test types
    """
    norm_type = (test_type or "unit").strip().lower()

    if norm_type == "unit":
        res = await generate_all_unit_test_cases(code_map_source, force=force)
        res["testType"] = "unit"
        return res

    code_map, code_map_path = load_code_map(code_map_source)
    if not code_map or not code_map.get("files"):
        raise ValueError("repository-code-map.json is missing or contains no files.")

    resolved_project_root = resolve_project_root(code_map, code_map_path)
    feature_contexts = load_existing_feature_contexts(code_map_path) if code_map_path else {}
    file_to_features = map_files_to_features(feature_contexts)

    # 1. Existing Output Guard Check
    if not force:
        existing_files = get_existing_test_cases(code_map_source, test_type=norm_type)
        if existing_files:
            msg = f"{norm_type.capitalize()} test cases already generated."
            backend_logger.info(f"[TestCaseGenerator] {msg} Found {len(existing_files)} file(s). Skipping LLM generation.")
            total_cases = 0
            for ef in existing_files:
                try:
                    with open(ef, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                        cases = data.get("testCases", []) if isinstance(data, dict) else data
                        total_cases += len(cases) if isinstance(cases, list) else 0
                except Exception:
                    pass
            return {
                "status": "already_exists",
                "alreadyExists": True,
                "savedFiles": [f.name for f in existing_files],
                "testCaseCount": total_cases,
                "unitCount": total_cases,
                "message": msg,
                "testType": norm_type,
            }

    if norm_type == "all":
        # Run unit, functional, and api
        results = []
        all_saved = []
        total_tc = 0
        for sub_type in ("unit", "functional", "api"):
            try:
                res = await generate_all_test_cases(code_map_source, force=force, test_type=sub_type)
                if res.get("status") in ("success", "already_exists"):
                    results.append(res)
                    all_saved.extend(res.get("savedFiles", []))
                    total_tc += res.get("testCaseCount", 0)
            except Exception as e:
                backend_logger.warning(f"[TestCaseGenerator] sub-type '{sub_type}' generation notice: {e}")
        return {
            "status": "success",
            "alreadyExists": False,
            "savedFiles": list(set(all_saved)),
            "testCaseCount": total_tc,
            "unitCount": total_tc,
            "message": f"Generated {total_tc} test cases across {len(set(all_saved))} files.",
            "testType": "all",
            "breakdown": {r.get("testType", "unknown"): r.get("testCaseCount", 0) for r in results}
        }

    # Discover targets based on test type
    if norm_type in ("functional", "e2e"):
        targets = discover_functional_targets(code_map, file_to_features)
        prefix = "functional"
    elif norm_type == "api":
        targets = discover_api_endpoints(code_map, file_to_features)
        prefix = "api"
    else:
        targets = discover_testable_units(code_map, file_to_features)
        prefix = "unit"

    if not targets:
        raise ValueError(f"No testable {norm_type} targets found in repository code map.")

    backend_logger.info(f"[TestCaseGenerator] Discovered {len(targets)} {norm_type} target(s)")

    # Build symbol lookup for validation
    valid_units_by_file: Dict[str, Set[str]] = defaultdict(set)
    for u in targets:
        if u.get("file") and u.get("symbol"):
            valid_units_by_file[u["file"]].add(u["symbol"])
        if u.get("route"):
            valid_units_by_file[u["file"]].add(u["route"])

    groups = group_units_for_generation(targets, feature_contexts)
    all_saved_files: List[Path] = []
    total_test_cases = 0
    existing_filenames: Set[str] = set()
    generation_failures: List[str] = []

    for group in groups:
        focused = build_focused_unit_context(group, code_map, code_map_path, project_root=resolved_project_root)
        try:
            raw_test_cases = await generate_test_cases_for_group(focused, test_type=norm_type)
        except Exception as exc:
            err_str = str(exc)
            generation_failures.append(f"Group '{group['name']}' API call failed: {err_str}")
            backend_logger.error(f"[TestCaseGenerator] Group '{group['name']}' generation error: {err_str}")
            raw_test_cases = []

        if not raw_test_cases:
            generation_failures.append(f"Group '{group['name']}' returned no parsed test cases")
            continue

        validated = validate_test_cases(raw_test_cases, valid_units_by_file, code_map, expected_test_type=norm_type)
        if not validated:
            generation_failures.append(f"Group '{group['name']}' test cases rejected by validation")

        if validated and code_map_path:
            group_save_name = f"{prefix}-{group['name']}" if not group["name"].startswith(prefix) else group["name"]
            out_file = save_test_cases(code_map_path, group_save_name, validated, existing_filenames)
            all_saved_files.append(out_file)
            total_test_cases += len(validated)

    if not all_saved_files and not total_test_cases:
        active_provider = get_active_provider_name()
        detail_suffix = f": {'; '.join(set(generation_failures))}" if generation_failures else ""
        raise ValueError(f"LLM provider {active_provider} did not produce valid {norm_type} test cases{detail_suffix}.")

    return {
        "status": "success",
        "alreadyExists": False,
        "savedFiles": [f.name for f in all_saved_files],
        "testCaseCount": total_test_cases,
        "unitCount": len(targets),
        "message": f"{norm_type.capitalize()} test cases generated successfully ({total_test_cases} test cases across {len(all_saved_files)} file(s)).",
        "testType": norm_type,
    }


# ---------------------------------------------------------------------------
# Dynamic Test-Type Test Case Generation
# ---------------------------------------------------------------------------

async def generate_test_cases_with_dynamic_context(
    code_map_source: Union[str, Path, dict],
    target: dict,
    test_type: Optional[str] = None,
    test_case: Optional[dict] = None,
    graph_data: Optional[dict] = None,
    feature_contexts: Optional[Dict[str, dict]] = None,
    schema_spec: Optional[dict] = None
) -> Dict[str, Any]:
    """
    Generates test cases using dynamic, test-type-aware minimum context selection.
    Prioritizes actual source code as authoritative, enforces non-hallucination,
    and returns validated test cases matching the required schema.
    """
    code_map, code_map_path = load_code_map(code_map_source)
    if not code_map or not code_map.get("files"):
        raise ValueError("repository-code-map.json is missing or contains no files.")

    # Load feature contexts if not explicitly provided
    if feature_contexts is None and code_map_path:
        feature_contexts = load_existing_feature_contexts(code_map_path)

    resolved_project_root = resolve_project_root(code_map, code_map_path)

    # 1. Dynamically select compact grounded context
    compact_context = select_context_for_test_case(
        test_case=test_case,
        target=target,
        test_type=test_type,
        code_map=code_map,
        code_map_path=code_map_path,
        project_root=resolved_project_root,
        graph_data=graph_data,
        feature_contexts=feature_contexts
    )

    # If target not found, DO NOT GUESS: Return structured failure state
    res_status = compact_context.get("resolutionStatus")
    if res_status in ("TARGET_NOT_FOUND", "INSUFFICIENT_CONTEXT"):
        backend_logger.warning(f"[ContextSelector] Generation aborted: {res_status} for target {target}")
        return {
            "status": res_status,
            "testCases": [],
            "warnings": compact_context.get("warnings", []),
            "context": compact_context
        }

    # 2. Build grounded prompt emphasizing source code authority
    messages = build_grounded_test_prompt(compact_context, schema_spec)

    # 3. Call LLM
    raw_content, _, _ = await execute_hf_llm_call(
        messages=messages,
        max_tokens=3000,
        temperature=0.1,
        stage_name=f"{compact_context['testType'].upper()}_TEST_GENERATION"
    )

    # 4. Parse response
    cleaned = strip_markdown_fences(raw_content)
    ok, parsed, _ = parse_json(cleaned)
    if not ok or not isinstance(parsed, dict) or "testCases" not in parsed:
        rep_ok, rep_data, _, _ = repair_json_deterministically(cleaned)
        if rep_ok and isinstance(rep_data, dict) and "testCases" in rep_data:
            parsed = rep_data
        elif rep_ok and isinstance(rep_data, list):
            parsed = {"testCases": rep_data}
        else:
            backend_logger.warning("[UnitTestGenerator] Dynamic LLM output was not valid JSON.")
            return {
                "status": "RETRIEVAL_ERROR",
                "testCases": [],
                "warnings": ["LLM response was not valid JSON"],
                "context": compact_context
            }

    raw_cases = parsed.get("testCases", [])

    # 5. Deterministic Validation
    # Build lookup of allowed symbols per file
    valid_units = discover_testable_units(code_map)
    valid_units_by_file: Dict[str, Set[str]] = defaultdict(set)
    for u in valid_units:
        valid_units_by_file[u["file"]].add(u["symbol"])
        if u.get("methodName"):
            valid_units_by_file[u["file"]].add(u["methodName"])
        if u.get("container"):
            valid_units_by_file[u["file"]].add(u["container"])

    # If target has a symbol, ensure target symbol is allowed
    resolved_t = compact_context.get("target", {})
    if resolved_t.get("file") and resolved_t.get("symbol"):
        valid_units_by_file[resolved_t["file"]].add(resolved_t["symbol"])

    validated = validate_test_cases(
        raw_cases=raw_cases,
        valid_units_by_file=valid_units_by_file,
        code_map=code_map,
        expected_test_type=compact_context["testType"]
    )

    return {
        "status": "SUCCESS" if validated else "INSUFFICIENT_CONTEXT",
        "testCases": validated,
        "warnings": compact_context.get("warnings", []),
        "context": compact_context
    }


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m app.services.unit_test_generator <path_to_repository_code_map.json>")
        sys.exit(1)
    asyncio.run(generate_all_unit_test_cases(sys.argv[1]))

"""
Dynamic Test-Type-Aware Context Selector
=========================================
Implements minimum relevant context selection for LLM test case generation
based on test type (unit, integration, functional/e2e).

DESIGN & SOURCE-OF-TRUTH CONTRACT
----------------------------------
1. ACTUAL SOURCE CODE is authoritative for actual implementation behavior.
2. repository-code-map.json is authoritative for structural information.
3. Dependency Graph is authoritative for validated cross-file relationships.
4. Feature Context is supporting feature-level grouping/context.
5. LLM-generated assumptions must NEVER be treated as source-of-truth.

Zero hardcoding: all paths, symbols, languages, and relationships are resolved
dynamically from existing artifacts and source code on disk.
Zero SQLite: standard file system and in-memory evaluation only.
Provider-independent: works identically with Hugging Face/Qwen, Groq, Mistral, OpenRouter.
"""

import os
import re
import json
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple, Any, Union
from collections import defaultdict

from app.logger import backend_logger

# ---------------------------------------------------------------------------
# Resolution Status Constants
# ---------------------------------------------------------------------------
STATUS_RESOLVED = "RESOLVED"
STATUS_PARTIALLY_RESOLVED = "PARTIALLY_RESOLVED"
STATUS_AMBIGUOUS = "AMBIGUOUS"
STATUS_TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
STATUS_SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
STATUS_INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
STATUS_RETRIEVAL_ERROR = "RETRIEVAL_ERROR"

# Compiled / binary artifact extensions that must NEVER be parsed or used as source code
COMPILED_OR_BINARY_EXTENSIONS = {
    ".class", ".pyc", ".pyo", ".pyd", ".o", ".obj",
    ".dll", ".exe", ".so", ".dylib", ".bin", ".wasm",
    ".jar", ".war", ".ear"
}


# ---------------------------------------------------------------------------
# Path Normalization Helper
# ---------------------------------------------------------------------------
def normalize_path(p: str) -> str:
    """Normalizes file paths to standard relative forward-slash format."""
    if not p:
        return ""
    clean = str(p).replace("\\", "/").strip()
    clean = re.sub(r"^\./", "", clean)
    clean = re.sub(r"^/+", "", clean)
    return clean


# ---------------------------------------------------------------------------
# Test Type Strategy Resolver
# ---------------------------------------------------------------------------
def resolve_test_type(
    test_case: Optional[dict] = None,
    target: Optional[dict] = None,
    default: str = "unit"
) -> Tuple[str, bool]:
    """
    Determines the test type dynamically from available test case metadata.
    Returns (test_type, is_explicit).
    Supported normalized types: 'unit', 'integration', 'functional'.
    Safely infers without inventing if explicit type is missing.
    """
    candidate = None
    if test_case and isinstance(test_case, dict):
        candidate = (
            test_case.get("testType")
            or test_case.get("test_type")
            or test_case.get("type")
        )
    if not candidate and target and isinstance(target, dict):
        candidate = target.get("testType") or target.get("test_type")

    if candidate and isinstance(candidate, str):
        c_lower = candidate.strip().lower()
        if c_lower in ("unit", "isolated", "component_unit"):
            return "unit", True
        if c_lower in ("integration", "integration_test", "contract"):
            return "integration", True
        if c_lower in ("functional", "e2e", "end-to-end", "workflow", "system", "scenario"):
            return "functional", True
        if c_lower in ("api", "endpoint", "rest", "api_test", "http", "route"):
            return "api", True

    # Safely infer from metadata if not explicitly specified
    if test_case and isinstance(test_case, dict):
        text_corpus = " ".join([
            str(test_case.get("title") or ""),
            str(test_case.get("description") or ""),
            str(test_case.get("rationale") or ""),
            str(test_case.get("expectedResult") or "")
        ]).lower()

        if any(term in text_corpus for term in ("api", "endpoint", "status code", "http method", "request body", "query param", "route", "post /", "get /", "put /", "delete /", "patch /")):
            return "api", False
        if any(term in text_corpus for term in ("e2e", "end-to-end", "user flow", "feature flow", "journey", "landing", "page view", "user click")):
            return "functional", False
        if any(term in text_corpus for term in ("integration", "database roundtrip", "controller to service", "cross-file", "pipeline")):
            return "integration", False

    if target and isinstance(target, dict):
        t_type = str(target.get("type") or "").lower()
        if t_type in ("route", "api_route", "endpoint", "controller"):
            return "api", False
        if t_type in ("page", "view", "component", "screen", "workflow"):
            return "functional", False

    return default, False


# ---------------------------------------------------------------------------
# Dynamic Target Resolution
# ---------------------------------------------------------------------------
def resolve_target(
    target: dict,
    code_map: dict,
    code_map_path: Optional[Path] = None
) -> Tuple[str, Optional[dict], Optional[dict], List[str]]:
    """
    Resolves target.file + target.symbol against existing Code Map declarations.
    DO NOT GUESS if target file exists but symbol does not exist.
    Preserves ambiguity if multiple symbols match.
    
    Returns:
    (status, resolved_target_dict, code_map_file_entry, warnings)
    """
    warnings: List[str] = []
    if not target or not isinstance(target, dict):
        return STATUS_INSUFFICIENT_CONTEXT, None, None, ["No target information provided"]

    target_file_raw = target.get("file") or ""
    target_symbol = (target.get("symbol") or "").strip()
    target_type = target.get("type") or "function"

    backend_logger.info("TARGET_RESOLUTION_STARTED")
    backend_logger.info(f"targetFile={target_file_raw}")
    backend_logger.info(f"targetSymbol={target_symbol}")

    if not target_file_raw:
        return STATUS_TARGET_NOT_FOUND, None, None, ["Target file path is empty"]

    norm_target_file = normalize_path(target_file_raw)
    files_dict = code_map.get("files", {})

    # Match file in code map (exact or case-insensitive fallback)
    code_map_entry = None
    matched_file_key = None

    if norm_target_file in files_dict:
        matched_file_key = norm_target_file
        code_map_entry = files_dict[norm_target_file]
    else:
        norm_lower = norm_target_file.lower()
        for fkey, fval in files_dict.items():
            if normalize_path(fkey).lower() == norm_lower:
                matched_file_key = fkey
                code_map_entry = fval
                break

    if not code_map_entry:
        return STATUS_TARGET_NOT_FOUND, None, None, [f"Target file not found in Code Map: {target_file_raw}"]

    # If no symbol specified, resolved at file level
    if not target_symbol:
        resolved_target = {
            "file": matched_file_key,
            "symbol": "",
            "type": target_type or "file",
            "language": code_map_entry.get("language") or code_map_entry.get("file", {}).get("language", ""),
            "line": None
        }
        return STATUS_RESOLVED, resolved_target, code_map_entry, warnings

    # Search for symbol in declarations, symbols, and classes
    matching_decls = []

    # 1. Declarations
    for decl in code_map_entry.get("declarations", []):
        if not isinstance(decl, dict):
            continue
        dname = (decl.get("name") or "").strip()
        if dname == target_symbol:
            matching_decls.append(decl)
        elif "." in target_symbol and target_symbol.endswith(f".{dname}"):
            matching_decls.append(decl)
        # Check methods in class declaration
        for m in decl.get("methods", []):
            mname = m if isinstance(m, str) else m.get("name") if isinstance(m, dict) else ""
            if mname and (mname == target_symbol or f"{dname}.{mname}" == target_symbol):
                matching_decls.append({
                    "name": target_symbol,
                    "type": "method",
                    "container": dname,
                    "line": decl.get("line")
                })

    # 2. Symbols field fallback
    if not matching_decls:
        for sym in code_map_entry.get("symbols", []):
            if not isinstance(sym, dict):
                continue
            sname = (sym.get("name") or "").strip()
            if sname == target_symbol or (target_symbol.endswith(f".{sname}") if "." in target_symbol else False):
                matching_decls.append(sym)

    # 3. Classes field fallback
    if not matching_decls:
        for cls in code_map_entry.get("classes", []):
            if not isinstance(cls, dict):
                continue
            cname = (cls.get("name") or "").strip()
            if cname == target_symbol:
                matching_decls.append(cls)
            for m in cls.get("methods", []):
                mname = m if isinstance(m, str) else m.get("name") if isinstance(m, dict) else ""
                if mname and (mname == target_symbol or f"{cname}.{mname}" == target_symbol):
                    matching_decls.append({
                        "name": target_symbol,
                        "type": "method",
                        "container": cname,
                        "line": cls.get("line")
                    })

    # Evaluation & Disambiguation
    if len(matching_decls) > 1:
        # Disambiguation heuristic 1: target line match
        target_line = target.get("line")
        if target_line:
            line_matched = [
                d for d in matching_decls
                if d.get("line") == target_line
                or (isinstance(d.get("location"), dict) and d["location"].get("startLine") == target_line)
            ]
            if len(line_matched) == 1:
                matching_decls = line_matched

        # Disambiguation heuristic 2: symbol type match
        if len(matching_decls) > 1 and target_type:
            t_type_norm = str(target_type).strip().lower()
            if t_type_norm == "class":
                type_matched = [d for d in matching_decls if str(d.get("type", "")).lower() == "class"]
                if len(type_matched) == 1:
                    matching_decls = type_matched
            elif t_type_norm in ("function", "method", "constructor"):
                type_matched = [d for d in matching_decls if str(d.get("type", "")).lower() in ("function", "method", "constructor")]
                if len(type_matched) == 1:
                    matching_decls = type_matched

    if len(matching_decls) > 1:
        # Ambiguous: multiple matching declarations
        warnings.append(f"Ambiguous symbol: found {len(matching_decls)} declarations matching '{target_symbol}'")
        first_line = (
            matching_decls[0].get("line")
            or (matching_decls[0].get("location", {}).get("startLine") if isinstance(matching_decls[0].get("location"), dict) else None)
        )
        resolved_target = {
            "file": matched_file_key,
            "symbol": target_symbol,
            "type": target_type,
            "language": code_map_entry.get("language") or code_map_entry.get("file", {}).get("language", ""),
            "line": first_line
        }
        return STATUS_AMBIGUOUS, resolved_target, code_map_entry, warnings

    if len(matching_decls) == 0:
        # DO NOT GUESS: Return TARGET_NOT_FOUND
        warnings.append(f"Symbol '{target_symbol}' not found in file declarations of {matched_file_key}")
        return STATUS_TARGET_NOT_FOUND, None, code_map_entry, warnings

    matched = matching_decls[0]
    line = (
        matched.get("line")
        or (matched.get("location", {}).get("startLine") if isinstance(matched.get("location"), dict) else None)
    )

    resolved_target = {
        "file": matched_file_key,
        "symbol": target_symbol,
        "type": matched.get("type") or target_type,
        "container": matched.get("container"),
        "line": line,
        "language": code_map_entry.get("language") or code_map_entry.get("file", {}).get("language", "")
    }

    return STATUS_RESOLVED, resolved_target, code_map_entry, warnings


# ---------------------------------------------------------------------------
# Authoritative Source Code Extraction (Balanced AST / Block Extractor)
# ---------------------------------------------------------------------------
def _extract_balanced_code_block(lines: List[str], start_line: int, language: str = "") -> Tuple[str, int, int]:
    """
    Extracts a balanced code block for a symbol starting at start_line (1-indexed).
    Uses indentation analysis for Python and brace-depth counting for C-like languages.
    """
    if not lines:
        return "", 1, 1

    idx = max(0, start_line - 1)
    if idx >= len(lines):
        return "\n".join(lines), 1, len(lines)

    lang = (language or "").lower()
    is_indentation_based = lang in ("python", "py")

    if is_indentation_based:
        decl_line = lines[idx]
        indent_match = re.match(r"^(\s*)", decl_line)
        base_indent = len(indent_match.group(1)) if indent_match else 0

        end_idx = idx + 1
        while end_idx < len(lines):
            line = lines[end_idx]
            if line.strip() == "" or line.strip().startswith("#"):
                end_idx += 1
                continue
            cur_indent = len((re.match(r"^(\s*)", line) or [""])[0])
            if cur_indent <= base_indent:
                break
            end_idx += 1
        return "\n".join(lines[idx:end_idx]), idx + 1, end_idx

    # Brace-based extraction
    open_braces = 0
    has_brace = False
    end_idx = idx

    for i in range(idx, len(lines)):
        line = lines[i]
        opens = line.count("{")
        closes = line.count("}")

        if opens > 0:
            has_brace = True
        open_braces += (opens - closes)
        end_idx = i + 1

        if has_brace and open_braces <= 0:
            break

    if not has_brace:
        # Arrow function or single statement
        end_idx = min(len(lines), idx + 15)
        for i in range(idx, end_idx):
            if ";" in lines[i] or lines[i].strip() == "":
                end_idx = i + 1
                break

    return "\n".join(lines[idx:end_idx]), idx + 1, end_idx


# ---------------------------------------------------------------------------
# Authoritative Source Path & Project Root Resolution
# ---------------------------------------------------------------------------
def resolve_project_root(
    code_map: Optional[dict] = None,
    code_map_path: Optional[Path] = None,
    explicit_project_root: Optional[Union[str, Path]] = None
) -> Optional[Path]:
    """
    Dynamically determines the authoritative project root directory from available artifacts:
    1. explicit_project_root parameter if provided and exists
    2. code_map['project']['path'] or code_map['projectPath'] / code_map['rootPath']
    3. Authoritative absolute paths recorded in code_map['files']
    4. Directory containing code_map_path (if code_map is collocated with the project)
    """
    if explicit_project_root:
        exp_p = Path(explicit_project_root).resolve()
        if exp_p.exists() and exp_p.is_dir():
            return exp_p

    if code_map and isinstance(code_map, dict):
        proj_dict = code_map.get("project")
        cand_paths = []
        if isinstance(proj_dict, dict):
            if proj_dict.get("path"):
                cand_paths.append(proj_dict.get("path"))
            if proj_dict.get("rootPath"):
                cand_paths.append(proj_dict.get("rootPath"))
        if code_map.get("projectPath"):
            cand_paths.append(code_map.get("projectPath"))
        if code_map.get("rootPath"):
            cand_paths.append(code_map.get("rootPath"))

        for cp in cand_paths:
            if cp:
                p = Path(cp).resolve()
                if p.exists() and p.is_dir():
                    return p

        # Check files in code_map for authoritative absolute paths
        files_dict = code_map.get("files", {})
        file_items = (
            files_dict.values() if isinstance(files_dict, dict)
            else (files_dict if isinstance(files_dict, list) else [])
        )
        for item in file_items:
            if not isinstance(item, dict):
                continue
            abs_cand = item.get("path") or item.get("file", {}).get("path")
            rel_cand = item.get("relativePath") or item.get("file", {}).get("relativePath")
            if abs_cand and Path(abs_cand).exists():
                abs_path = Path(abs_cand).resolve()
                if rel_cand:
                    rel_parts = Path(normalize_path(rel_cand)).parts
                    root_cand = abs_path
                    for _ in range(len(rel_parts)):
                        root_cand = root_cand.parent
                    if root_cand.exists() and root_cand.is_dir():
                        return root_cand
                return abs_path.parent

    if code_map_path:
        cmp = Path(code_map_path).resolve()
        base_dir = cmp.parent if cmp.is_file() else cmp
        if base_dir.exists() and base_dir.is_dir():
            return base_dir

    return None


def find_source_file_on_disk(
    file_rel_path: str,
    project_root: Optional[Path] = None,
    code_map_path: Optional[Path] = None,
    code_map: Optional[dict] = None
) -> Optional[Path]:
    """
    Dynamically locates an analyzable source file on disk.
    Ensures compiled binary artifacts (e.g. .class, .pyc) are rejected.
    Resolves relative paths against the project root and code map metadata.
    """
    if not file_rel_path:
        return None

    norm_rel = normalize_path(file_rel_path)
    if not norm_rel:
        return None

    # Guard: reject binary or compiled artifact extensions
    suffix = Path(norm_rel).suffix.lower()
    if suffix in COMPILED_OR_BINARY_EXTENSIONS:
        backend_logger.warning(f"[ContextSelector] Skipping compiled/binary artifact: {norm_rel}")
        return None

    # 1. Check authoritative absolute path recorded in code_map entry
    if code_map and isinstance(code_map, dict):
        files_dict = code_map.get("files", {})
        entry = None
        if isinstance(files_dict, dict):
            entry = files_dict.get(norm_rel) or files_dict.get(file_rel_path)
            if not entry:
                for k, v in files_dict.items():
                    if normalize_path(k) == norm_rel:
                        entry = v
                        break
        elif isinstance(files_dict, list):
            for it in files_dict:
                if isinstance(it, dict):
                    r = it.get("relativePath") or it.get("file", {}).get("relativePath")
                    if r and normalize_path(r) == norm_rel:
                        entry = it
                        break

        if entry and isinstance(entry, dict):
            cand_abs = entry.get("path") or entry.get("file", {}).get("path")
            if cand_abs:
                p_abs = Path(cand_abs).resolve()
                if p_abs.exists() and p_abs.is_file() and p_abs.suffix.lower() not in COMPILED_OR_BINARY_EXTENSIONS:
                    return p_abs

    # 2. Check project_root / norm_rel
    resolved_root = project_root or resolve_project_root(code_map, code_map_path)
    if resolved_root:
        cand = (resolved_root / norm_rel).resolve()
        if cand.exists() and cand.is_file() and cand.suffix.lower() not in COMPILED_OR_BINARY_EXTENSIONS:
            return cand

    # 3. Check code_map_path base directory
    if code_map_path:
        cmp = Path(code_map_path).resolve()
        base_dir = cmp.parent if cmp.is_file() else cmp
        cand = (base_dir / norm_rel).resolve()
        if cand.exists() and cand.is_file() and cand.suffix.lower() not in COMPILED_OR_BINARY_EXTENSIONS:
            return cand

    # 4. Check relative to current working directory
    cand_cwd = Path(norm_rel).resolve()
    if cand_cwd.exists() and cand_cwd.is_file() and cand_cwd.suffix.lower() not in COMPILED_OR_BINARY_EXTENSIONS:
        return cand_cwd

    # 5. Search by filename inside resolved_root as a fallback
    if resolved_root and resolved_root.exists() and resolved_root.is_dir():
        fname = Path(norm_rel).name
        for root, dirs, files in os.walk(resolved_root):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("target", "build", "dist", "out", "node_modules", "bin")]
            if fname in files:
                cand_found = Path(root, fname).resolve()
                if cand_found.suffix.lower() not in COMPILED_OR_BINARY_EXTENSIONS:
                    return cand_found

    return None


def extract_actual_source_code(
    file_rel_path: str,
    target_symbol: Optional[str] = None,
    decl_line: Optional[int] = None,
    language: str = "",
    code_map_path: Optional[Path] = None,
    project_root: Optional[Path] = None,
    code_map: Optional[dict] = None,
    max_budget_chars: int = 4000
) -> Tuple[str, Optional[dict]]:
    """
    Extracts actual implementation source code from disk.
    Prioritizes actual implementation behavior over metadata.
    
    Returns:
    (status, source_data_dict)
    where source_data_dict contains:
    - path: repository-relative path
    - symbol: target symbol
    - sourceCode: authoritative source content
    - startLine: 1-indexed
    - endLine: 1-indexed
    - isFullFile: bool
    """
    norm_path = normalize_path(file_rel_path)
    backend_logger.info("SOURCE_PATH_RESOLUTION_STARTED")

    disk_path = find_source_file_on_disk(
        file_rel_path=norm_path,
        project_root=project_root,
        code_map_path=code_map_path,
        code_map=code_map
    )

    if not disk_path or not disk_path.exists() or not disk_path.is_file():
        backend_logger.info("SOURCE_PATH_RESOLVED")
        backend_logger.info(f"path={norm_path}")
        backend_logger.info("exists=false")
        return STATUS_SOURCE_NOT_FOUND, None

    backend_logger.info("SOURCE_PATH_RESOLVED")
    backend_logger.info(f"path={norm_path}")
    backend_logger.info("exists=true")

    try:
        with open(disk_path, "r", encoding="utf-8", errors="replace") as fh:
            raw_content = fh.read()
    except Exception as exc:
        backend_logger.warning(f"[ContextSelector] Could not read source file {disk_path}: {exc}")
        return STATUS_SOURCE_NOT_FOUND, None

    lines = raw_content.splitlines()
    total_lines = len(lines)

    # Budgeting Rule: If file <= 120 lines, return full source for maximum grounding
    if total_lines <= 120:
        clipped = raw_content[:max_budget_chars]
        return STATUS_RESOLVED, {
            "path": norm_path,
            "symbol": target_symbol or "",
            "sourceCode": clipped,
            "startLine": 1,
            "endLine": total_lines,
            "isFullFile": True,
            "lineCount": total_lines
        }

    # Otherwise extract import headers + target symbol block
    start_line = max(1, decl_line) if decl_line else 1

    # If decl_line is unknown or symbol not found at line, search lines for symbol definition
    if target_symbol and (not decl_line or start_line > total_lines):
        sym_pattern = re.compile(rf"\b(function|def|class|const|let|var)\s+{re.escape(target_symbol)}\b")
        for i, line in enumerate(lines):
            if sym_pattern.search(line):
                start_line = i + 1
                break

    block_text, block_start, block_end = _extract_balanced_code_block(lines, start_line, language)

    # Extract top import header (first 30 lines max)
    import_lines = []
    max_imports = min(30, start_line - 1)
    for i in range(max_imports):
        l = lines[i]
        tl = l.strip()
        if (
            tl.startswith("import ")
            or tl.startswith("from ")
            or (tl.startswith("const ") and "require(" in tl)
            or (tl.startswith("let ") and "require(" in tl)
            or (tl.startswith("var ") and "require(" in tl)
            or tl.startswith("#include")
            or tl.startswith("package ")
            or tl.startswith("use ")
        ):
            import_lines.append(l)

    assembled_parts = []
    if import_lines:
        assembled_parts.append("\n".join(import_lines))
        assembled_parts.append("// ... [Target implementation extracted below] ...")
    assembled_parts.append(block_text)

    assembled_source = "\n\n".join(assembled_parts)
    clipped_source = assembled_source[:max_budget_chars]

    return STATUS_RESOLVED, {
        "path": norm_path,
        "symbol": target_symbol or "",
        "sourceCode": clipped_source,
        "startLine": block_start,
        "endLine": block_end,
        "isFullFile": False,
        "lineCount": total_lines
    }


# ---------------------------------------------------------------------------
# Code Map Relevance Filter
# ---------------------------------------------------------------------------
def clean_code_map_entry(raw_entry: dict, rel_path: str = "") -> dict:
    """
    Extracts only essential structural facts without full AST coordinate trees.
    Deterministic and compact.
    """
    cleaned = {
        "relativePath": normalize_path(rel_path or raw_entry.get("relativePath") or raw_entry.get("file", {}).get("relativePath", "")),
        "language": raw_entry.get("language") or raw_entry.get("file", {}).get("language", ""),
        "lineCount": raw_entry.get("lineCount") or raw_entry.get("file", {}).get("lineCount", 0),
    }

    # Declarations / Symbols
    decls = []
    for d in (raw_entry.get("declarations") or raw_entry.get("symbols") or []):
        if not isinstance(d, dict):
            continue
        d_name = d.get("name")
        d_type = d.get("type")
        if d_name:
            item = {"name": d_name, "type": d_type}
            if d.get("line"):
                item["line"] = d["line"]
            if d.get("methods"):
                item["methods"] = [
                    m if isinstance(m, str) else m.get("name")
                    for m in d["methods"] if (m if isinstance(m, str) else m.get("name"))
                ]
            decls.append(item)
    if decls:
        cleaned["declarations"] = decls[:15]

    # Imports / Dependencies (internal targets prioritized)
    deps = []
    for dep in (raw_entry.get("dependencies") or raw_entry.get("imports") or []):
        if isinstance(dep, dict):
            tgt = dep.get("target") or dep.get("source") or dep.get("modulePath")
            if tgt:
                item = {"target": normalize_path(tgt)}
                if dep.get("type"):
                    item["type"] = dep["type"]
                deps.append(item)
    if deps:
        cleaned["dependencies"] = deps[:10]

    # Exports
    exports = []
    for e in raw_entry.get("exports", []):
        ename = e if isinstance(e, str) else e.get("name") or e.get("text") if isinstance(e, dict) else None
        if ename:
            exports.append(ename)
    if exports:
        cleaned["exports"] = exports[:10]

    # Routes (for API test context)
    routes = []
    for r in raw_entry.get("routes", []):
        if isinstance(r, dict):
            r_path = r.get("path") or r.get("route") or r.get("endpoint")
            r_method = r.get("method") or r.get("httpMethod") or "GET"
            if r_path:
                routes.append({"path": r_path, "method": str(r_method).upper()})
    if routes:
        cleaned["routes"] = routes[:10]

    # React / UI Components (for Functional test context)
    react_meta = raw_entry.get("react")
    if isinstance(react_meta, dict) and react_meta:
        cleaned_react = {}
        if react_meta.get("components"):
            cleaned_react["components"] = react_meta["components"][:5]
        if react_meta.get("hooks"):
            cleaned_react["hooks"] = react_meta["hooks"][:5]
        if cleaned_react:
            cleaned["react"] = cleaned_react

    return cleaned


def select_relevant_code_map_entries(
    target_files: Set[str],
    code_map: dict,
    include_depth_1: bool = False
) -> List[dict]:
    """
    Selects Code Map entries strictly relevant to target_files.
    NEVER sends the complete code map.
    """
    files_dict = code_map.get("files", {})
    selected_files: Set[str] = set()
    norm_targets = {normalize_path(f) for f in target_files}

    for norm_t in norm_targets:
        if norm_t in files_dict:
            selected_files.add(norm_t)
        else:
            for k in files_dict.keys():
                if normalize_path(k) == norm_t:
                    selected_files.add(k)
                    break

    # If depth 1 expansion requested, add direct internal dependencies
    if include_depth_1:
        for fkey in list(selected_files):
            fdata = files_dict.get(fkey, {})
            for d in (fdata.get("dependencies") or fdata.get("imports") or []):
                if isinstance(d, dict):
                    dtype = str(d.get("type") or d.get("kind") or "").lower()
                    target_dep = normalize_path(d.get("target") or d.get("source") or "")
                    if "internal" in dtype or any(target_dep.endswith(ext) for ext in (".ts", ".js", ".py", ".tsx", ".jsx")):
                        # Check if internal dependency exists in code map
                        for cand_k in files_dict.keys():
                            if normalize_path(cand_k) == target_dep or cand_k.endswith(target_dep):
                                selected_files.add(cand_k)
                                break

    entries = []
    for fpath in sorted(list(selected_files)):
        fdata = files_dict.get(fpath)
        if fdata:
            entries.append(clean_code_map_entry(fdata, fpath))

    return entries


# ---------------------------------------------------------------------------
# Dependency Graph Relevance Filter
# ---------------------------------------------------------------------------
def select_relevant_graph_edges(
    target_files: Set[str],
    graph_data: Optional[dict],
    code_map: Optional[dict] = None
) -> List[dict]:
    """
    Selects only graph edges connecting target files to their direct collaborating components.
    NEVER sends the complete Dependency Graph.
    """
    if not target_files:
        return []

    norm_targets = {normalize_path(f) for f in target_files}
    relevant_edges: List[dict] = []
    seen_edges: Set[Tuple[str, str, str]] = set()

    # 1. Inspect PostgreSQL/GraphData edges if available
    if graph_data and isinstance(graph_data, dict):
        nodes = graph_data.get("nodes", [])
        edges = graph_data.get("edges", [])

        # Build node_id -> file_path lookup
        node_file_map: Dict[str, str] = {}
        for n in nodes:
            nid = n.get("id") or ""
            fp = normalize_path(n.get("filePath") or "")
            if not fp and ":" in nid:
                fp = normalize_path(nid.split(":", 1)[1])
            if nid and fp:
                node_file_map[nid] = fp

        for edge in edges:
            src = edge.get("source") or edge.get("source_node_id") or ""
            tgt = edge.get("target") or edge.get("target_node_id") or ""
            rel = edge.get("relationship") or "references"

            src_file = node_file_map.get(src, "")
            tgt_file = node_file_map.get(tgt, "")

            # Keep edge if at least one side is a target file
            if (src_file in norm_targets or tgt_file in norm_targets):
                edge_key = (src, tgt, rel)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    relevant_edges.append({
                        "source": src,
                        "target": tgt,
                        "relationship": rel,
                        "confidence": edge.get("confidence", 1.0),
                        "evidence": edge.get("evidence", {})
                    })

    # 2. Inspect Code Map relationships if graph edges were empty
    if not relevant_edges and code_map and isinstance(code_map, dict):
        for r in code_map.get("relationships", []):
            frm = normalize_path(r.get("from", ""))
            to = normalize_path(r.get("to", ""))
            rel_type = r.get("type", "uses")

            if (frm in norm_targets or to in norm_targets):
                edge_key = (frm, to, rel_type)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    relevant_edges.append({
                        "source": frm,
                        "target": to,
                        "relationship": rel_type,
                        "confidence": 1.0,
                        "evidence": r.get("evidence", {})
                    })

    return relevant_edges[:15]


# ---------------------------------------------------------------------------
# Feature Context Relevance Filter
# ---------------------------------------------------------------------------
def select_relevant_feature_context(
    target_files: Set[str],
    feature_contexts: Optional[Dict[str, dict]],
    feature_id: Optional[str] = None
) -> Optional[dict]:
    """
    Selects only the Feature Context section directly relevant to the target or feature_id.
    NEVER sends all feature contexts.
    """
    if not feature_contexts:
        return None

    # If explicit feature_id provided and exists
    if feature_id and feature_id in feature_contexts:
        ctx = feature_contexts[feature_id]
        return {
            "id": feature_id,
            "name": ctx.get("feature", {}).get("name") or feature_id,
            "description": ctx.get("feature", {}).get("description", ""),
            "flow": ctx.get("featureFlow", [])[:5],
            "relevantFiles": [
                f.get("path") for f in ctx.get("files", [])
                if f.get("path")
            ][:6]
        }

    # Match by target files
    norm_targets = {normalize_path(f) for f in target_files}
    for fid, ctx in feature_contexts.items():
        ctx_files = {normalize_path(f.get("path", "")) for f in ctx.get("files", [])}
        if norm_targets.intersection(ctx_files):
            return {
                "id": fid,
                "name": ctx.get("feature", {}).get("name") or fid,
                "description": ctx.get("feature", {}).get("description", ""),
                "flow": ctx.get("featureFlow", [])[:5],
                "relevantFiles": list(ctx_files)[:6]
            }

    return None


# ---------------------------------------------------------------------------
# Core Dynamic Context Selector
# ---------------------------------------------------------------------------
def select_context_for_test_case(
    test_case: Optional[dict] = None,
    target: Optional[dict] = None,
    test_type: Optional[str] = None,
    code_map: Optional[dict] = None,
    code_map_path: Optional[Path] = None,
    project_root: Optional[Path] = None,
    graph_data: Optional[dict] = None,
    feature_contexts: Optional[Dict[str, dict]] = None,
    force_depth: Optional[int] = None
) -> Dict[str, Any]:
    """
    Main dynamic context selection entry point.
    Determines test type, resolves target, extracts authoritative source code,
    and applies test-type-aware relevance strategy.

    Produces compact, grounded context matching Section 15.
    """
    code_map = code_map or {}
    t_obj = target or (test_case.get("unit") or test_case.get("target") if test_case else {}) or {}

    # 1. Determine test type
    effective_test_type, is_explicit = resolve_test_type(test_case, t_obj, default="unit")
    if test_type and test_type.strip():
        effective_test_type = test_type.strip().lower()

    target_file_str = t_obj.get("file") or ""
    target_symbol_str = t_obj.get("symbol") or ""
    log_target = f"{target_file_str}::{target_symbol_str}" if target_symbol_str else target_file_str

    # Log START (Section 17 requirement)
    backend_logger.info("CONTEXT_SELECTION_STARTED")
    backend_logger.info(f"testType={effective_test_type}")
    backend_logger.info(f"target={log_target}")

    # 2. Dynamic Target Resolution
    resolution_status, resolved_target, file_entry, warnings = resolve_target(t_obj, code_map, code_map_path)
    final_target = resolved_target or t_obj

    # Resolve project root dynamically
    project_root = resolve_project_root(code_map, code_map_path, project_root)

    involved_files: Set[str] = set()
    if final_target.get("file"):
        involved_files.add(normalize_path(final_target["file"]))

    # 3. Apply Test-Type Context Strategy
    dependency_depth = 0
    selected_code_map: List[dict] = []
    selected_graph_edges: List[dict] = []
    selected_feature_ctx: Optional[dict] = None
    selected_sources: List[dict] = []

    # -----------------------------------------------------------------------
    # 5A. UNIT TEST STRATEGY
    # -----------------------------------------------------------------------
    if effective_test_type == "unit":
        # Required: target file Code Map entry + actual target source
        # Omit Dependency Graph and Feature Context by default
        dependency_depth = force_depth if force_depth is not None else 0

        # Check if depth 1 is genuinely required for internal dependency
        if dependency_depth == 0 and file_entry:
            internal_deps = [
                d for d in (file_entry.get("dependencies") or file_entry.get("imports") or [])
                if isinstance(d, dict) and "internal" in str(d.get("type", "")).lower()
            ]
            # Only expand to depth 1 if test case preconditions specifically require mocking an internal dep
            if internal_deps and test_case and any("mock" in str(p).lower() for p in test_case.get("preconditions", [])):
                dependency_depth = 1

        selected_code_map = select_relevant_code_map_entries(
            involved_files,
            code_map,
            include_depth_1=(dependency_depth > 0)
        )

        # Retrieve actual target source code
        if final_target.get("file"):
            backend_logger.info("SOURCE_RETRIEVAL_STARTED")
            s_status, s_data = extract_actual_source_code(
                file_rel_path=final_target["file"],
                target_symbol=final_target.get("symbol"),
                decl_line=final_target.get("line"),
                language=final_target.get("language") or "",
                code_map_path=code_map_path,
                project_root=project_root,
                code_map=code_map
            )
            if s_data:
                selected_sources.append(s_data)
            elif resolution_status == STATUS_RESOLVED:
                resolution_status = STATUS_SOURCE_NOT_FOUND
            backend_logger.info("SOURCE_RETRIEVAL_COMPLETED")
            backend_logger.info(f"sourceFiles={len(selected_sources)}")

        # Graph and Feature Context remain OMITTED for Unit tests

    # -----------------------------------------------------------------------
    # 5B. INTEGRATION TEST STRATEGY
    # -----------------------------------------------------------------------
    elif effective_test_type == "integration":
        # Required: Target file + collaborating components + relevant graph edges + involved source
        dependency_depth = force_depth if force_depth is not None else 1

        # Identify collaborating files from direct dependencies & relationships
        if file_entry:
            for d in (file_entry.get("dependencies") or file_entry.get("imports") or []):
                if isinstance(d, dict):
                    dep_tgt = normalize_path(d.get("target") or d.get("source") or "")
                    if dep_tgt and dep_tgt in code_map.get("files", {}):
                        involved_files.add(dep_tgt)

        selected_code_map = select_relevant_code_map_entries(
            involved_files,
            code_map,
            include_depth_1=True
        )

        # Select only graph edges connecting involved files
        selected_graph_edges = select_relevant_graph_edges(involved_files, graph_data, code_map)

        # Extract source for involved files (budgeted)
        backend_logger.info("SOURCE_RETRIEVAL_STARTED")
        for fp in list(involved_files)[:3]:
            s_status, s_data = extract_actual_source_code(
                file_rel_path=fp,
                target_symbol=final_target.get("symbol") if fp == normalize_path(final_target.get("file", "")) else None,
                decl_line=final_target.get("line") if fp == normalize_path(final_target.get("file", "")) else None,
                language="",
                code_map_path=code_map_path,
                project_root=project_root,
                code_map=code_map,
                max_budget_chars=2500
            )
            if s_data:
                selected_sources.append(s_data)
        backend_logger.info("SOURCE_RETRIEVAL_COMPLETED")
        backend_logger.info(f"sourceFiles={len(selected_sources)}")

    # -----------------------------------------------------------------------
    # 5C. FUNCTIONAL / E2E TEST STRATEGY
    # -----------------------------------------------------------------------
    else:  # functional / e2e
        # Required: Relevant Feature Context + involved Code Map entries + flow source
        dependency_depth = force_depth if force_depth is not None else 0

        # Select relevant feature context
        selected_feature_ctx = select_relevant_feature_context(
            involved_files,
            feature_contexts,
            feature_id=test_case.get("featureId") if test_case else None
        )

        if selected_feature_ctx:
            for rf in selected_feature_ctx.get("relevantFiles", []):
                involved_files.add(normalize_path(rf))

        selected_code_map = select_relevant_code_map_entries(
            involved_files,
            code_map,
            include_depth_1=False
        )

        # Optional graph edges for feature flow if needed
        selected_graph_edges = select_relevant_graph_edges(involved_files, graph_data, code_map)[:8]

        # Extract source for the top 2 files in the flow
        backend_logger.info("SOURCE_RETRIEVAL_STARTED")
        for fp in list(involved_files)[:2]:
            s_status, s_data = extract_actual_source_code(
                file_rel_path=fp,
                language="",
                code_map_path=code_map_path,
                project_root=project_root,
                code_map=code_map,
                max_budget_chars=2000
            )
            if s_data:
                selected_sources.append(s_data)
        backend_logger.info("SOURCE_RETRIEVAL_COMPLETED")
        backend_logger.info(f"sourceFiles={len(selected_sources)}")

    # 4. Build Structured Internal Context Object (Section 15)
    context_body: Dict[str, Any] = {
        "codeMap": selected_code_map,
        "sourceCode": selected_sources
    }
    if selected_graph_edges:
        context_body["dependencyGraph"] = selected_graph_edges
    if selected_feature_ctx:
        context_body["featureContext"] = selected_feature_ctx

    result: Dict[str, Any] = {
        "testCase": test_case or {},
        "target": final_target,
        "testType": effective_test_type,
        "resolutionStatus": resolution_status,
        "warnings": warnings,
        "context": context_body,
        "metrics": {
            "codeMapEntries": len(selected_code_map),
            "graphEdges": len(selected_graph_edges),
            "featureContextSections": 1 if selected_feature_ctx else 0,
            "sourceFiles": len(selected_sources),
            "dependencyDepth": dependency_depth,
            "inputCharacters": len(json.dumps(context_body, separators=(',', ':')))
        }
    }

    # Log COMPLETION (Section 17 requirement)
    backend_logger.info("CONTEXT_SELECTION_COMPLETED")
    backend_logger.info(f"codeMapEntries={result['metrics']['codeMapEntries']}")
    backend_logger.info(f"graphEdges={result['metrics']['graphEdges']}")
    backend_logger.info(f"featureContextSections={result['metrics']['featureContextSections']}")
    backend_logger.info(f"sourceFiles={result['metrics']['sourceFiles']}")
    backend_logger.info(f"dependencyDepth={result['metrics']['dependencyDepth']}")
    backend_logger.info("LLM_CONTEXT_BUILT")
    backend_logger.info(f"sourceFiles={result['metrics']['sourceFiles']}")
    backend_logger.info(f"inputCharacters={result['metrics']['inputCharacters']}")

    return result


# ---------------------------------------------------------------------------
# Grounded Prompt Builder (Section 14)
# ---------------------------------------------------------------------------
def build_grounded_test_prompt(
    compact_context: dict,
    schema_spec: Optional[dict] = None
) -> List[dict]:
    """
    Constructs provider-independent LLM prompt asserting source code authority,
    strict non-hallucination, and exact schema compliance.
    """
    target = compact_context.get("target", {})
    test_type = compact_context.get("testType", "unit")
    ctx = compact_context.get("context", {})

    target_file = target.get("file", "unknown")
    target_symbol = target.get("symbol", "unknown")
    target_type = target.get("type", "function")

    system_prompt = (
        f"You are an expert test engineer generating structured {test_type.upper()} TEST CASES.\n\n"
        f"Target under test: '{target_symbol}' ({target_type}) in '{target_file}'.\n"
        f"Every test case MUST have 'testType': '{test_type}'.\n\n"
        "============================================================\n"
        "SOURCE-GROUNDING GUARDRAILS (CRITICAL)\n"
        "============================================================\n"
        "1. NEVER infer behavior from a function, class, method, route, or component name alone.\n"
        "2. A symbol name is only an identifier/location signal. It is NOT evidence of behavior.\n"
        "3. Before generating a Test Case, inspect the supplied actual source code for the target symbol whenever source is available.\n"
        "4. Use repository-code-map.json to establish: target file, target symbol, symbol type, imports, exports, dependencies, calls, and other structural information actually present.\n"
        "5. Use the Dependency Graph only to establish validated relationships between relevant project files.\n"
        "6. Use Feature Context only as supporting contextual information about feature-level relationships.\n"
        "7. ACTUAL SOURCE CODE is authoritative and has priority over inferred behavior. All assertions, branches, and expected results MUST be grounded in the provided implementation source code.\n"
        "8. DO NOT invent: parameters, parameter names, parameter types, return types, return values, database operations, API calls, validation rules, error handling, side effects, state changes, UI behavior, business rules, file operations, authentication behavior, authorization behavior unless supported by the provided evidence.\n"
        "9. DO NOT infer an operation merely because its name suggests it. (e.g. 'delete_transaction' does NOT prove that the function deletes a database record; the actual source must establish that behavior).\n"
        "10. DO NOT infer return types from naming conventions. (e.g. 'get_categories' does NOT automatically mean the function returns a list).\n"
        "11. DO NOT invent test inputs. Inputs must be derived from: actual function parameters, actual source behavior, explicit Test Case information, or relevant structural metadata.\n"
        "12. DO NOT invent database schemas, table names, columns, API endpoints, routes, or external services.\n"
        "13. If the source shows a behavior that contradicts the Test Case intent, DO NOT silently accept the Test Case assumption.\n"
        "14. The generated Test Case must reflect the ACTUAL IMPLEMENTATION.\n"
        "15. If sufficient evidence does not exist to determine a behavior: DO NOT GUESS. Either omit that behavioral claim or mark the case as requiring additional context using the existing generation/failure mechanism.\n\n"
        "============================================================\n"
        "EVIDENCE HIERARCHY\n"
        "============================================================\n"
        "For every important behavioral claim, use this priority:\n"
        "1. Actual source code\n"
        "2. Explicit Test Case information\n"
        "3. repository-code-map.json\n"
        "4. Validated Dependency Graph\n"
        "5. Feature Context\n"
        "6. General programming conventions\n"
        "GENERAL PROGRAMMING CONVENTIONS MUST NEVER OVERRIDE PROJECT-SPECIFIC EVIDENCE.\n\n"
        "============================================================\n"
        "BEHAVIORAL CLAIM VALIDATION\n"
        "============================================================\n"
        "Before finalizing each Test Case, internally verify:\n"
        "- Is the target symbol actually present?\n"
        "- Are the parameters supported by the source?\n"
        "- Is the expected return/result supported by the source?\n"
        "- Are the described side effects supported by the source?\n"
        "- Are database/API/file operations supported by the source?\n"
        "- Are error conditions supported by the source?\n"
        "- Are the inputs compatible with the actual implementation?\n"
        "- Is the expected result actually observable from the implementation?\n"
        "If any answer is NO or UNKNOWN: DO NOT fabricate the missing information.\n\n"
        "============================================================\n"
        "FUNCTION-NAME GUARDRAIL\n"
        "============================================================\n"
        "Function/class/method names may be used to LOCATE the target. They MUST NOT be used as the primary evidence for determining behavior.\n"
        "BAD: function name = 'get_transactions' -> therefore: 'returns a list of transactions'\n"
        "GOOD: source code executes the transaction query, collects the returned rows, and returns the rows as a list -> therefore: 'returns the retrieved transaction records.'\n"
        "BAD: function name = 'recalculate_summary' -> therefore: 'stores the recalculated summary in the database.'\n"
        "GOOD: source code explicitly performs the database update -> therefore the Test Case may assert that database update.\n\n"
        "============================================================\n"
        "FINAL RULE\n"
        "============================================================\n"
        "GENERATE TEST CASES FROM EVIDENCE, NOT FROM NAMES.\n"
        "The purpose of repository-code-map.json, Dependency Graph, Feature Context, and actual source retrieval is to GROUND the Test Case generation.\n"
        "More context is NOT automatically better. Use the smallest amount of relevant evidence necessary to establish the behavior correctly.\n"
        "Return ONLY valid JSON matching the exact required schema. No markdown, no conversation."
    )

    user_payload: Dict[str, Any] = {
        "target": {
            "file": target_file,
            "symbol": target_symbol,
            "type": target_type
        },
        "testType": test_type,
        "context": ctx
    }

    if compact_context.get("testCase"):
        user_payload["baseTestCase"] = compact_context["testCase"]

    default_schema = {
        "testCases": [
            {
                "testCaseId": "TC-001",
                "unit": {
                    "file": target_file,
                    "symbol": target_symbol,
                    "type": target_type
                },
                "title": "Clear behavioral test title",
                "description": "Specific behavior grounded in actual source implementation",
                "preconditions": ["Optional setup or mocks"],
                "inputs": {"argName": "value"},
                "expectedResult": "Expected outcome verified against actual source code",
                "testType": test_type,
                "priority": "high",
                "rationale": "Evidence-grounded rationale"
            }
        ]
    }
    active_schema = schema_spec or default_schema

    user_prompt = (
        f"Grounded Context:\n{json.dumps(user_payload, indent=2)}\n\n"
        f"Required JSON Schema:\n{json.dumps(active_schema, indent=2)}\n\n"
        "Generate test cases strictly adhering to actual source behavior and the schema above."
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

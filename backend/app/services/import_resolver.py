"""
Dynamic Import Resolver & Pre-Execution Import Validator
=========================================================
Classifies module imports, resolves project-local source imports across multi-language
codebases (including nested monorepos and relative script directories), and validates
import integrity before script execution.
"""

import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

# Common Python standard library modules
PYTHON_STDLIB: Set[str] = {
    "abc", "argparse", "array", "ast", "asyncio", "base64", "binascii", "bisect",
    "builtins", "bz2", "calendar", "cmath", "collections", "concurrent", "configparser",
    "contextlib", "copy", "csv", "ctypes", "dataclasses", "datetime", "decimal",
    "difflib", "dis", "distutils", "doctest", "email", "enum", "errno", "faulthandler",
    "fcntl", "filecmp", "fileinput", "fnmatch", "fractions", "functools", "gc",
    "getopt", "getpass", "gettext", "glob", "gzip", "hashlib", "heapq", "hmac",
    "html", "http", "imaplib", "imghdr", "importlib", "inspect", "io", "ipaddress",
    "itertools", "json", "linecache", "locale", "logging", "lzma", "mailbox",
    "mailcap", "marshal", "math", "mimetypes", "mmap", "modulefinder", "multiprocessing",
    "netrc", "nntplib", "numbers", "operator", "optparse", "os", "pathlib", "pdb",
    "pickle", "pipes", "pkgutil", "platform", "plistlib", "poplib", "posixpath",
    "pprint", "profile", "pstats", "pty", "pwd", "queue", "quopri", "random", "re",
    "readline", "resource", "rlcompleter", "sched", "secrets", "select", "selectors",
    "shelve", "shlex", "shutil", "signal", "site", "smtpd", "smtplib", "sndhdr",
    "socket", "socketserver", "sqlite3", "ssl", "stat", "statistics", "string",
    "stringprep", "struct", "subprocess", "sunau", "symbol", "symtable", "sys",
    "sysconfig", "tabnanny", "tarfile", "telnetlib", "tempfile", "termios", "test",
    "textwrap", "threading", "time", "timeit", "tkinter", "token", "tokenize",
    "trace", "traceback", "tracemalloc", "tty", "types", "typing", "unicodedata",
    "unittest", "urllib", "uu", "uuid", "venv", "warnings", "wave", "weakref",
    "webbrowser", "wsgiref", "xdrlib", "xml", "xmlrpc", "zipapp", "zipfile",
    "zipimport", "zlib", "_thread",
}

# Node.js built-in modules
NODE_STDLIB: Set[str] = {
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console",
    "constants", "crypto", "dgram", "diagnostics_channel", "dns", "domain",
    "events", "fs", "fs/promises", "http", "http2", "https", "inspector",
    "module", "net", "os", "path", "path/posix", "path/win32", "perf_hooks",
    "process", "punycode", "querystring", "readline", "repl", "stream",
    "stream/promises", "stream/web", "string_decoder", "sys", "timers",
    "timers/promises", "tls", "trace_events", "tty", "url", "util", "util/types",
    "v8", "vm", "wasi", "worker_threads", "zlib",
}

# Known test frameworks by language family
TEST_FRAMEWORK_MODULES: Dict[str, Set[str]] = {
    "javascript": {
        "@jest/globals", "jest", "vitest", "mocha", "chai", "@testing-library/react",
        "@testing-library/react-hooks", "@testing-library/jest-dom", "@testing-library/user-event",
        "supertest", "sinon", "nock", "cypress", "playwright", "puppeteer", "ava", "tape", "jasmine",
    },
    "typescript": {
        "@jest/globals", "jest", "vitest", "mocha", "chai", "@testing-library/react",
        "@testing-library/react-hooks", "@testing-library/jest-dom", "@testing-library/user-event",
        "supertest", "sinon", "nock", "cypress", "playwright", "puppeteer", "ava", "tape", "jasmine",
    },
    "python": {
        "pytest", "unittest", "unittest.mock", "mock", "nose", "hypothesis", "tox",
    },
    "java": {
        "org.junit", "org.testng", "org.mockito", "org.assertj", "org.hamcrest",
        "org.springframework.boot.test", "org.springframework.test",
    },
    "go": {
        "testing", "github.com/stretchr/testify",
    },
}


def detect_subproject_root(
    target_file_or_dir: Union[str, Path],
    project_root: Optional[Path] = None
) -> Optional[Path]:
    """
    Locates the closest enclosing subproject directory containing a project descriptor
    (package.json, pom.xml, pyproject.toml, requirements.txt, setup.py, go.mod, Cargo.toml)
    between target_file_or_dir and project_root.
    """
    p = Path(target_file_or_dir).resolve()
    if p.is_file():
        p = p.parent

    config_names = [
        "package.json", "pom.xml", "pyproject.toml", "requirements.txt",
        "setup.py", "go.mod", "Cargo.toml", "build.gradle"
    ]

    curr = p
    root_resolved = Path(project_root).resolve() if project_root else None

    while True:
        for cfg in config_names:
            if (curr / cfg).exists():
                return curr

        if root_resolved and curr == root_resolved:
            break
        if curr.parent == curr:
            break
        curr = curr.parent

    return None


def detect_js_module_system(
    target_file_path: Optional[Path] = None,
    project_root: Optional[Path] = None,
    source_code: str = ""
) -> str:
    """
    Detects whether the JavaScript/TypeScript context uses ECMAScript Modules (ESM)
    or CommonJS (CJS). Returns 'esm' or 'commonjs'.
    """
    if target_file_path:
        suffix = target_file_path.suffix.lower()
        if suffix == ".mjs":
            return "esm"
        if suffix == ".cjs":
            return "commonjs"

    # Check closest package.json
    search_dir = target_file_path.parent if (target_file_path and target_file_path.exists()) else project_root
    if search_dir:
        sub_root = detect_subproject_root(search_dir, project_root) or search_dir
        pkg_file = sub_root / "package.json"
        if pkg_file.exists():
            try:
                pkg_data = json.loads(pkg_file.read_text(encoding="utf-8", errors="ignore"))
                pkg_type = pkg_data.get("type", "").lower()
                if pkg_type == "module":
                    return "esm"
                if pkg_type == "commonjs":
                    return "commonjs"
            except Exception:
                pass

    # Inspect source code markers
    if source_code:
        # ESM exports take precedence
        if re.search(r"^\s*export\s+(?:default|const|let|var|function|class|type|interface)\b", source_code, re.MULTILINE):
            return "esm"
        if re.search(r"^\s*import\s+.*?from\s+['\"]", source_code, re.MULTILINE):
            return "esm"
        # CommonJS markers
        if re.search(r"\bmodule\.exports\b|\bexports\.[a-zA-Z0-9_$]+\s*=", source_code):
            return "commonjs"
        if re.search(r"\brequire\s*\(\s*['\"][^'\"]+['\"]\s*\)", source_code):
            return "commonjs"

    # Default to ESM if TypeScript, else CommonJS
    if target_file_path and target_file_path.suffix.lower() in [".ts", ".tsx"]:
        return "esm"
    return "commonjs"


def classify_import(
    import_specifier: str,
    language: str,
    project_root: Optional[Path] = None
) -> str:
    """
    Classifies a module import into one of four categories:
    - 'standard_library'
    - 'test_framework'
    - 'project_local'
    - 'external_dependency'
    """
    lang = (language or "").lower()
    spec = (import_specifier or "").strip()

    if not spec:
        return "external_dependency"

    # Normalize specifier
    norm_spec = spec.replace("\\", "/")

    # JS/TS Family
    if lang in ["javascript", "typescript", "jsx", "tsx", "js", "ts"]:
        # Relative or root-relative paths are project-local
        if norm_spec.startswith(".") or norm_spec.startswith("/"):
            return "project_local"
        if norm_spec.startswith("@/") or norm_spec.startswith("~/") or norm_spec.startswith("src/"):
            return "project_local"

        # Node standard library
        base_node = norm_spec[5:] if norm_spec.startswith("node:") else norm_spec
        base_mod = base_node.split("/")[0]
        if base_node in NODE_STDLIB or base_mod in NODE_STDLIB:
            return "standard_library"

        # Test frameworks
        js_fw = TEST_FRAMEWORK_MODULES.get("javascript", set())
        if norm_spec in js_fw or base_mod in js_fw:
            return "test_framework"
        if any(norm_spec.startswith(f"{fw}/") for fw in js_fw):
            return "test_framework"

        # If project_root is available, check if the module specifier directly resolves to a local dir/file
        if project_root and project_root.exists():
            cand = project_root / norm_spec
            if cand.exists() or (project_root / f"{norm_spec}.ts").exists() or (project_root / f"{norm_spec}.js").exists():
                return "project_local"

        return "external_dependency"

    # Python Family
    elif lang == "python":
        # Relative Python imports (e.g. from . import foo)
        if norm_spec.startswith("."):
            return "project_local"

        top_mod = norm_spec.split(".")[0]

        # Test frameworks
        py_fw = TEST_FRAMEWORK_MODULES.get("python", set())
        if norm_spec in py_fw or top_mod in py_fw:
            return "test_framework"

        # Standard library
        if top_mod in PYTHON_STDLIB:
            return "standard_library"
        if hasattr(sys, "stdlib_module_names") and top_mod in sys.stdlib_module_names:
            return "standard_library"

        # Check if matches local file or folder in project root or src
        if project_root and project_root.exists():
            cand_file = project_root / f"{top_mod}.py"
            cand_dir = project_root / top_mod
            cand_src_file = project_root / "src" / f"{top_mod}.py"
            cand_src_dir = project_root / "src" / top_mod
            if cand_file.exists() or cand_dir.is_dir() or cand_src_file.exists() or cand_src_dir.is_dir():
                return "project_local"

        return "external_dependency"

    # Java Family
    elif lang == "java":
        if norm_spec.startswith("java.") or norm_spec.startswith("javax.") or norm_spec.startswith("jakarta."):
            return "standard_library"

        for fw_prefix in TEST_FRAMEWORK_MODULES.get("java", set()):
            if norm_spec.startswith(fw_prefix):
                return "test_framework"

        # If it doesn't match standard or known external prefixes, and starts with project-like packages
        if norm_spec.startswith("org.apache.") or norm_spec.startswith("com.google.") or norm_spec.startswith("com.fasterxml."):
            return "external_dependency"

        return "project_local"

    # Go Family
    elif lang == "go":
        if norm_spec in TEST_FRAMEWORK_MODULES.get("go", set()):
            return "test_framework"
        if norm_spec.startswith(".") or norm_spec.startswith("/"):
            return "project_local"
        if "/" not in norm_spec or "." not in norm_spec.split("/")[0]:
            return "standard_library"
        return "external_dependency"

    return "external_dependency"


def extract_imports_from_script(
    script_content: str,
    language: str
) -> List[Dict[str, Any]]:
    """
    Extracts all import specifications from a script using language-specific parsing.
    Returns list of dicts: {"specifier": str, "symbols": List[str], "statement": str, "line": int}
    """
    imports: List[Dict[str, Any]] = []
    lang = (language or "").lower()

    if not script_content:
        return imports

    # Python AST extraction
    if lang == "python":
        try:
            tree = ast.parse(script_content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append({
                            "specifier": alias.name,
                            "symbols": [alias.asname or alias.name],
                            "statement": f"import {alias.name}",
                            "line": getattr(node, "lineno", 1),
                        })
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    level = node.level or 0
                    prefix = "." * level
                    full_mod = f"{prefix}{mod}" if prefix else mod
                    symbols = [a.name for a in node.names]
                    imports.append({
                        "specifier": full_mod,
                        "symbols": symbols,
                        "statement": f"from {full_mod} import {', '.join(symbols)}",
                        "line": getattr(node, "lineno", 1),
                    })
            return imports
        except Exception:
            # Fallback to regex if syntax error in incomplete script
            pass

    # JS/TS Regex extraction
    if lang in ["javascript", "typescript", "jsx", "tsx", "js", "ts"]:
        # ESM: import ... from 'specifier' or import 'specifier'
        esm_matches = re.finditer(
            r"""^\s*import\s+(?:(?:\*\s+as\s+[\w$]+|\{[^}]*\}|[\w$]+)\s*,?\s*)*(?:\{[^}]*\})?\s*from\s*['"]([^'"]+)['"]|^\s*import\s*['"]([^'"]+)['"]""",
            script_content,
            re.MULTILINE
        )
        for m in esm_matches:
            spec = m.group(1) or m.group(2)
            stmt = m.group(0).strip()
            line_no = script_content[:m.start()].count("\n") + 1
            imports.append({
                "specifier": spec,
                "symbols": [],
                "statement": stmt,
                "line": line_no,
            })

        # CommonJS: require('specifier')
        cjs_matches = re.finditer(
            r"""\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)""",
            script_content
        )
        for m in cjs_matches:
            spec = m.group(1)
            stmt = m.group(0).strip()
            line_no = script_content[:m.start()].count("\n") + 1
            imports.append({
                "specifier": spec,
                "symbols": [],
                "statement": stmt,
                "line": line_no,
            })
        return imports

    # Java Regex extraction
    if lang == "java":
        java_matches = re.finditer(r"^\s*import\s+(?:static\s+)?([a-zA-Z0-9_.*]+)\s*;", script_content, re.MULTILINE)
        for m in java_matches:
            spec = m.group(1)
            line_no = script_content[:m.start()].count("\n") + 1
            imports.append({
                "specifier": spec,
                "symbols": [spec.split(".")[-1]],
                "statement": m.group(0).strip(),
                "line": line_no,
            })
        return imports

    # Go Regex extraction
    if lang == "go":
        go_matches = re.finditer(r"""import\s*\(\s*([^)]+)\)|import\s*['"]([^'"]+)['"]""", script_content)
        for m in go_matches:
            if m.group(2):
                imports.append({"specifier": m.group(2), "symbols": [], "statement": m.group(0), "line": 1})
            elif m.group(1):
                block = m.group(1)
                for line in block.splitlines():
                    sub = re.search(r"""['"]([^'"]+)['"]""", line)
                    if sub:
                        imports.append({"specifier": sub.group(1), "symbols": [], "statement": line.strip(), "line": 1})
        return imports

    return imports


def resolve_project_local_import(
    import_spec: str,
    script_path: Path,
    project_root: Optional[Path] = None,
    language: str = "javascript",
    target_file: Optional[str] = None
) -> Dict[str, Any]:
    """
    Attempts to resolve a project-local import specifier to an existing file on disk.
    Tries relative paths from script directory, project root, and subproject roots,
    with file extensions and index files.
    """
    lang = (language or "").lower()
    spec = (import_spec or "").strip()
    candidate_paths: List[Path] = []

    if not spec:
        return {
            "resolved": False,
            "resolved_path": None,
            "candidate_paths": [],
            "reason": "Empty import specifier."
        }

    script_dir = script_path.parent.resolve()
    p_root = Path(project_root).resolve() if project_root else None

    # Determine potential subproject root
    sub_root = None
    if target_file and p_root:
        sub_root = detect_subproject_root(p_root / target_file, p_root)
    if not sub_root and p_root:
        sub_root = detect_subproject_root(script_dir, p_root)

    # Search base directories in order of relevance
    search_bases: List[Path] = [script_dir]
    if sub_root and sub_root not in search_bases:
        search_bases.append(sub_root)
    if p_root and p_root not in search_bases:
        search_bases.append(p_root)
        if (p_root / "src").exists() and (p_root / "src") not in search_bases:
            search_bases.append(p_root / "src")

    norm_spec = spec.replace("\\", "/")

    # JS/TS resolution
    if lang in ["javascript", "typescript", "jsx", "tsx", "js", "ts"]:
        clean_spec = norm_spec
        if clean_spec.startswith("@/") or clean_spec.startswith("~/"):
            clean_spec = clean_spec[2:]

        extensions = ["", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx", "/index.js", "/index.jsx"]

        for base in search_bases:
            cand_base = (base / clean_spec).resolve()
            for ext in extensions:
                p_cand = Path(str(cand_base) + ext)
                candidate_paths.append(p_cand)
                if p_cand.is_file():
                    return {
                        "resolved": True,
                        "resolved_path": p_cand,
                        "candidate_paths": candidate_paths,
                        "reason": None
                    }

        # Also check if target_file itself matches the stem or leaf of spec
        if target_file and p_root:
            target_full = (p_root / target_file).resolve()
            candidate_paths.append(target_full)
            if target_full.is_file():
                stem_match = (
                    Path(clean_spec).stem == target_full.stem
                    or clean_spec.endswith(target_full.stem)
                    or target_full.stem in clean_spec
                )
                if stem_match:
                    return {
                        "resolved": True,
                        "resolved_path": target_full,
                        "candidate_paths": candidate_paths,
                        "reason": None
                    }

        return {
            "resolved": False,
            "resolved_path": None,
            "candidate_paths": candidate_paths,
            "reason": f"Cannot find local module '{spec}' among candidate paths."
        }

    # Python resolution
    elif lang == "python":
        # Turn dotted import (e.g. app.services.my_module or ..utils) into relative path
        rel_parts = []
        is_rel = False
        if norm_spec.startswith("."):
            is_rel = True
            dots = len(norm_spec) - len(norm_spec.lstrip("."))
            spec_rest = norm_spec.lstrip(".")
            # dots=1 -> current dir, dots=2 -> parent dir
            curr = script_dir
            for _ in range(dots - 1):
                curr = curr.parent
            rel_parts = spec_rest.split(".") if spec_rest else []
            py_bases = [curr]
        else:
            rel_parts = norm_spec.split(".")
            py_bases = search_bases

        rel_path_str = "/".join(rel_parts)
        for base in py_bases:
            cand1 = (base / f"{rel_path_str}.py").resolve()
            cand2 = (base / rel_path_str / "__init__.py").resolve()
            candidate_paths.extend([cand1, cand2])
            if cand1.is_file():
                return {"resolved": True, "resolved_path": cand1, "candidate_paths": candidate_paths, "reason": None}
            if cand2.is_file():
                return {"resolved": True, "resolved_path": cand2, "candidate_paths": candidate_paths, "reason": None}

        # Check target_file fallback
        if target_file and p_root:
            target_full = (p_root / target_file).resolve()
            candidate_paths.append(target_full)
            if target_full.is_file():
                if target_full.stem in norm_spec.split("."):
                    return {"resolved": True, "resolved_path": target_full, "candidate_paths": candidate_paths, "reason": None}

        return {
            "resolved": False,
            "resolved_path": None,
            "candidate_paths": candidate_paths,
            "reason": f"Cannot find Python module '{spec}' on disk."
        }

    # Java resolution
    elif lang == "java":
        # Strip trailing .* if present
        clean_spec = re.sub(r"\.\*$", "", norm_spec)
        class_path = clean_spec.replace(".", "/") + ".java"

        java_subfolders = [
            "src/main/java", "src/test/java", "src", "."
        ]
        for base in search_bases:
            for sub in java_subfolders:
                cand = (base / sub / class_path).resolve()
                candidate_paths.append(cand)
                if cand.is_file():
                    return {"resolved": True, "resolved_path": cand, "candidate_paths": candidate_paths, "reason": None}

        # Check target_file
        if target_file and p_root:
            target_full = (p_root / target_file).resolve()
            candidate_paths.append(target_full)
            if target_full.is_file():
                return {"resolved": True, "resolved_path": target_full, "candidate_paths": candidate_paths, "reason": None}

        return {
            "resolved": False,
            "resolved_path": None,
            "candidate_paths": candidate_paths,
            "reason": f"Cannot find Java class source '{spec}'."
        }

    # Default fallback
    return {
        "resolved": True,
        "resolved_path": None,
        "candidate_paths": candidate_paths,
        "reason": None
    }


def validate_script_imports(
    script_path: Path,
    project_root: Optional[Path] = None,
    language: str = "",
    project_env: Optional[Any] = None,
    script_content: Optional[str] = None,
    target_file: Optional[str] = None
) -> Dict[str, Any]:
    """
    Pre-execution validation of a test script's imports.
    Extracts all imports, classifies them, and ensures that any project-local imports
    actually resolve to real files on disk.
    If any project-local import fails to resolve, returns structured diagnostic error.
    """
    content = script_content
    if content is None:
        if not script_path.exists():
            return {
                "valid": False,
                "failureType": "SCRIPT_NOT_FOUND",
                "original_import": None,
                "script": str(script_path),
                "candidate_paths": [],
                "reason": f"Script file '{script_path}' does not exist on disk."
            }
        try:
            content = script_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return {
                "valid": False,
                "failureType": "SCRIPT_NOT_FOUND",
                "original_import": None,
                "script": str(script_path),
                "candidate_paths": [],
                "reason": f"Could not read script file '{script_path}': {e}"
            }

    detected_lang = language
    if not detected_lang and project_env and hasattr(project_env, "language"):
        detected_lang = project_env.language
    if not detected_lang:
        ext_map = {".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".java": "java", ".go": "go"}
        detected_lang = ext_map.get(script_path.suffix.lower(), "javascript")

    p_root = Path(project_root).resolve() if project_root else None
    imports = extract_imports_from_script(content, detected_lang)

    for imp in imports:
        spec = imp["specifier"]
        category = classify_import(spec, detected_lang, project_root=p_root)

        if category == "project_local":
            res = resolve_project_local_import(
                import_spec=spec,
                script_path=script_path,
                project_root=p_root,
                language=detected_lang,
                target_file=target_file
            )
            if not res["resolved"]:
                cand_strs = [str(c) for c in res["candidate_paths"]]
                return {
                    "valid": False,
                    "failureType": "IMPORT_RESOLUTION_ERROR",
                    "original_import": spec,
                    "script": str(script_path),
                    "candidate_paths": cand_strs,
                    "reason": f"Project-local import '{spec}' in script '{script_path.name}' could not be resolved to any source file on disk."
                }

    return {
        "valid": True,
        "failureType": None,
        "original_import": None,
        "script": str(script_path),
        "candidate_paths": [],
        "reason": None
    }

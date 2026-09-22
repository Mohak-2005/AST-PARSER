from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class TargetDescriptor:
    target_symbol: str = ""
    target_file: str = ""
    target_type: str = "unit"
    exported_symbols: List[str] = field(default_factory=list)
    observed_props_or_params: List[str] = field(default_factory=list)
    observed_methods: List[str] = field(default_factory=list)
    import_targets: List[str] = field(default_factory=list)
    is_symbol_found: bool = False
    source_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_symbol": self.target_symbol,
            "target_file": self.target_file,
            "target_type": self.target_type,
            "exported_symbols": self.exported_symbols,
            "observed_props_or_params": self.observed_props_or_params,
            "observed_methods": self.observed_methods,
            "import_targets": self.import_targets,
            "is_symbol_found": self.is_symbol_found,
        }


def normalize_target_symbol(target_symbol: str) -> Tuple[str, str, List[str]]:
    """
    Normalizes target symbol names:
    - Strips leading decorators/annotations ('@app.route' -> 'app.route')
    - Resolves qualified identifiers ('Parent.child', 'pkg::func', 'module/target')
    Returns: (raw_symbol, base_symbol, parts)
    """
    raw = (target_symbol or "").strip()
    clean = raw.lstrip("@").strip()
    if "::" in clean:
        parts = [p.strip() for p in clean.split("::") if p.strip()]
    elif "/" in clean and not clean.startswith((".", "/")):
        parts = [p.strip() for p in clean.split("/") if p.strip()]
    elif "." in clean:
        parts = [p.strip() for p in clean.split(".") if p.strip()]
    else:
        parts = [clean] if clean else []
    base = parts[-1] if parts else clean
    return raw, base, parts


def _is_identifier_in_source(source_code: str, identifier: str) -> bool:
    """Checks if an exact identifier token exists within source code with word boundaries."""
    if not identifier:
        return False
    return bool(re.search(rf"\b{re.escape(identifier)}\b", source_code))


def _extract_js_ts_info(source_code: str, target_symbol: str) -> TargetDescriptor:
    desc = TargetDescriptor(target_symbol=target_symbol)
    if not source_code:
        return desc

    raw_sym, base_sym, parts = normalize_target_symbol(target_symbol)
    candidates = {s for s in (raw_sym, base_sym) if s}
    search_sym = base_sym if base_sym else raw_sym

    # 1. Exported symbols
    exports: Set[str] = set()
    for m in re.finditer(r"export\s+default\s+(?:(?:async\s+)?function\s+|class\s+)?([a-zA-Z0-9_$]+)", source_code):
        exports.add(m.group(1))
    for m in re.finditer(r"export\s+(?:const|let|var|function|class|type|interface|enum)\s+([a-zA-Z0-9_$]+)", source_code):
        exports.add(m.group(1))
    for m in re.finditer(r"export\s*\{\s*([^}]+)\s*\}", source_code):
        for part in m.group(1).split(","):
            part = part.strip().split(" as ")[0].strip()
            if part:
                exports.add(part)
    desc.exported_symbols = sorted(list(exports))

    # 2. Imports
    imports: Set[str] = set()
    for m in re.finditer(r"from\s+['\"]([^'\"]+)['\"]", source_code):
        imports.add(m.group(1))
    for m in re.finditer(r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", source_code):
        imports.add(m.group(1))
    desc.import_targets = sorted(list(imports))

    props_or_params: Set[str] = set()
    methods: Set[str] = set()

    # 3. Match function declarations / arrow functions / components
    pattern_func = re.compile(
        rf"(?:(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+{re.escape(search_sym)}|(?:const|let|var)\s+{re.escape(search_sym)}\s*=\s*(?:(?:memo|forwardRef)\s*\(\s*)?(?:(?:async\s+)?function)?)\s*\(\s*\{{?([^)]*?)\}}?\s*\)",
        re.DOTALL
    )
    m_func = pattern_func.search(source_code)
    if m_func:
        desc.is_symbol_found = True
        param_str = m_func.group(1)
        for token in re.split(r"[,;]", param_str):
            token = token.strip().split(":")[0].strip().split("=")[0].strip()
            if token and re.match(r"^[a-zA-Z_$][a-zA-Z0-9_$]*$", token) and token not in ["props", "any"]:
                props_or_params.add(token)

    # Match interface/type for TargetProps with balanced braces
    pattern_props_start = re.compile(
        rf"(?:interface|type)\s+(?:{re.escape(search_sym)}Props|Props)\s*(?:=\s*)?\{{",
        re.DOTALL
    )
    m_props_start = pattern_props_start.search(source_code)
    if m_props_start:
        start_idx = m_props_start.end()
        depth = 1
        pos = start_idx
        while pos < len(source_code) and depth > 0:
            if source_code[pos] == "{":
                depth += 1
            elif source_code[pos] == "}":
                depth -= 1
            pos += 1
        props_block = source_code[start_idx:pos - 1] if depth == 0 else source_code[start_idx:]
        for line in props_block.splitlines():
            line = line.strip()
            if line and not line.startswith("//") and not line.startswith("/*"):
                prop_m = re.match(r"^([a-zA-Z_$][a-zA-Z0-9_$]*)\s*\??\s*:", line)
                if prop_m:
                    props_or_params.add(prop_m.group(1))

    # Class methods if target is class or member
    class_target = parts[0] if parts else search_sym
    pattern_class = re.compile(
        rf"class\s+{re.escape(class_target)}[^{{]*\{{([^}}]+)\}}",
        re.DOTALL
    )
    m_class = pattern_class.search(source_code)
    if m_class:
        if search_sym == class_target:
            desc.is_symbol_found = True
        class_body = m_class.group(1)
        for m_meth in re.finditer(r"(?:async\s+)?([a-zA-Z_$][a-zA-Z0-9_$]*)\s*\([^)]*\)\s*\{", class_body):
            name = m_meth.group(1)
            if name != "constructor":
                methods.add(name)
                if search_sym == name or (parts and parts[-1] == name):
                    desc.is_symbol_found = True

    # Check existence
    if not target_symbol:
        desc.is_symbol_found = True
    elif not desc.is_symbol_found:
        if search_sym in exports or search_sym in methods:
            desc.is_symbol_found = True
        elif _is_identifier_in_source(source_code, search_sym):
            if re.search(rf"\b(?:function|class|const|let|var|type|interface)\s+{re.escape(search_sym)}\b", source_code):
                desc.is_symbol_found = True
            elif any(s in exports for s in candidates):
                desc.is_symbol_found = True

    desc.observed_props_or_params = sorted(list(props_or_params))
    desc.observed_methods = sorted(list(methods))
    return desc


def _extract_java_info(source_code: str, target_symbol: str) -> TargetDescriptor:
    desc = TargetDescriptor(target_symbol=target_symbol)
    if not source_code:
        return desc

    raw_sym, base_sym, parts = normalize_target_symbol(target_symbol)
    search_sym = base_sym if base_sym else raw_sym

    # 1. Classes / interfaces / records / enums (including annotations)
    classes: Set[str] = set()
    for m in re.finditer(r"(?:@[\w.]+(?:\([^)]*\))?\s+)*(?:public|protected|private|static|final|abstract|\s)*\b(class|interface|record|enum)\s+([a-zA-Z0-9_$]+)", source_code):
        classes.add(m.group(2))
    desc.exported_symbols = sorted(list(classes))

    # 2. Imports
    imports: Set[str] = set()
    for m in re.finditer(r"import\s+(?:static\s+)?([a-zA-Z0-9_$.*]+);", source_code):
        imports.add(m.group(1))
    desc.import_targets = sorted(list(imports))

    props_or_params: Set[str] = set()
    methods: Set[str] = set()

    # Match class block
    class_target = parts[0] if parts else search_sym
    class_pattern = re.compile(
        rf"(?:@[\w.]+(?:\([^)]*\))?\s+)*(?:public|protected|private|static|final|abstract|\s)*\b(?:class|interface|record|enum)\s+{re.escape(class_target)}[^{{]*\{{",
        re.DOTALL
    )
    m_cls = class_pattern.search(source_code)
    if m_cls:
        if search_sym == class_target:
            desc.is_symbol_found = True
        start_pos = m_cls.end()
        depth = 1
        pos = start_pos
        while pos < len(source_code) and depth > 0:
            if source_code[pos] == "{":
                depth += 1
            elif source_code[pos] == "}":
                depth -= 1
            pos += 1
        class_body = source_code[start_pos:pos - 1] if depth == 0 else source_code[start_pos:]

        # Constructor
        m_ctor = re.search(rf"{re.escape(class_target)}\s*\(([^)]*)\)", class_body)
        if m_ctor:
            for p in m_ctor.group(1).split(","):
                p_parts = p.strip().split()
                if p_parts:
                    param_name = p_parts[-1].strip()
                    if re.match(r"^[a-zA-Z_$][a-zA-Z0-9_$]*$", param_name):
                        props_or_params.add(param_name)

        # Methods (including annotated methods e.g. @GetMapping, @Test)
        for m_meth in re.finditer(r"(?:@[\w.]+(?:\([^)]*\))?\s+)*(?:public|protected|private|static|final|synchronized|\s)+[\w<>\[\],?]+\s+([a-zA-Z0-9_$]+)\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\{", class_body):
            m_name = m_meth.group(1)
            if m_name != class_target:
                methods.add(m_name)
                if search_sym == m_name or (parts and parts[-1] == m_name):
                    desc.is_symbol_found = True

    # Also extract methods across entire source code if target is method-level
    for m_meth in re.finditer(r"(?:@[\w.]+(?:\([^)]*\))?\s+)*(?:public|protected|private|static|final|synchronized|\s)+[\w<>\[\],?]+\s+([a-zA-Z0-9_$]+)\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\{", source_code):
        m_name = m_meth.group(1)
        if m_name not in classes:
            methods.add(m_name)
            if search_sym == m_name or (parts and parts[-1] == m_name):
                desc.is_symbol_found = True

    if not target_symbol:
        desc.is_symbol_found = True
    elif not desc.is_symbol_found:
        if search_sym in classes or search_sym in methods:
            desc.is_symbol_found = True
        elif _is_identifier_in_source(source_code, search_sym):
            if re.search(rf"\b(?:class|interface|record|enum)\s+{re.escape(search_sym)}\b", source_code):
                desc.is_symbol_found = True

    desc.observed_props_or_params = sorted(list(props_or_params))
    desc.observed_methods = sorted(list(methods))
    return desc


def _extract_python_info(source_code: str, target_symbol: str) -> TargetDescriptor:
    desc = TargetDescriptor(target_symbol=target_symbol)
    if not source_code:
        return desc

    raw_sym, base_sym, parts = normalize_target_symbol(target_symbol)
    candidates = {s for s in (raw_sym, base_sym, raw_sym.lstrip("@")) if s}

    props_or_params: Set[str] = set()
    methods: Set[str] = set()
    classes: Set[str] = set()
    imports: Set[str] = set()

    # 1. Try standard library AST parsing first
    parsed_via_ast = False
    try:
        # Convert any JS-style comments from retrieval snippets into Python comments
        sanitized = re.sub(r"^\s*//", "#", source_code, flags=re.MULTILINE)
        tree = ast.parse(sanitized)
        parsed_via_ast = True

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                classes.add(node.name)
                # Check if this function matches target
                if not target_symbol or node.name in candidates or (parts and parts[-1] == node.name):
                    desc.is_symbol_found = True
                    for a in node.args.args:
                        if a.arg not in ("self", "cls"):
                            props_or_params.add(a.arg)
                    for a in getattr(node.args, "kwonlyargs", []):
                        props_or_params.add(a.arg)
                    for a in getattr(node.args, "posonlyargs", []):
                        if a.arg not in ("self", "cls"):
                            props_or_params.add(a.arg)

            elif isinstance(node, ast.ClassDef):
                classes.add(node.name)
                is_target_class = (not target_symbol) or (node.name in candidates) or (parts and parts[0] == node.name)
                if node.name in candidates and (not parts or len(parts) == 1):
                    desc.is_symbol_found = True

                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if item.name != "__init__":
                            methods.add(item.name)
                        else:
                            if is_target_class:
                                for a in item.args.args:
                                    if a.arg not in ("self", "cls"):
                                        props_or_params.add(a.arg)
                        # If target matches this method
                        if (parts and parts[-1] == item.name) or item.name in candidates:
                            desc.is_symbol_found = True
                            if parts and len(parts) > 1 and parts[-1] == item.name:
                                for a in item.args.args:
                                    if a.arg not in ("self", "cls"):
                                        props_or_params.add(a.arg)

            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    imports.add(f"{mod}.{alias.name}" if mod else alias.name)

    except Exception:
        parsed_via_ast = False

    # 2. Regex extraction fallback or supplement
    if not parsed_via_ast or not desc.is_symbol_found:
        # Functions and Classes regex
        for m in re.finditer(r"^(?:class|(?:async\s+)?def)\s+([a-zA-Z0-9_]+)", source_code, re.MULTILINE):
            classes.add(m.group(1))

        # Imports regex
        for m in re.finditer(r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.,\s]+))", source_code, re.MULTILINE):
            imp = m.group(1) or m.group(2)
            if imp:
                imports.add(imp.strip())

        # Check function match: def target_symbol(...) or async def target_symbol(...)
        search_sym = base_sym if base_sym else raw_sym
        pattern_func = re.compile(
            rf"(?:async\s+)?def\s+{re.escape(search_sym)}\s*\((.*?)\)(?:\s*->\s*[^:]+)?\s*:",
            re.DOTALL
        )
        m_func = pattern_func.search(source_code)
        if m_func:
            desc.is_symbol_found = True
            param_content = m_func.group(1)
            for p in param_content.split(","):
                p_name = p.strip().split(":")[0].split("=")[0].strip()
                if p_name and p_name not in ["self", "cls"]:
                    props_or_params.add(p_name)

        # Check class match
        class_name = parts[0] if parts else search_sym
        pattern_class = re.compile(rf"class\s+{re.escape(class_name)}\b[^{{]*?:", re.MULTILINE)
        m_cls = pattern_class.search(source_code)
        if m_cls:
            if search_sym == class_name:
                desc.is_symbol_found = True
            lines = source_code[m_cls.end():].splitlines()
            for line in lines:
                if line.strip() and not line.startswith(" ") and not line.startswith("\t"):
                    break
                m_m = re.match(r"^\s+(?:async\s+)?def\s+([a-zA-Z0-9_]+)\s*\((.*?)\)(?:\s*->\s*[^:]+)?\s*:", line)
                if m_m:
                    m_name = m_m.group(1)
                    if m_name == "__init__":
                        if search_sym == class_name:
                            for p in m_m.group(2).split(","):
                                p_name = p.strip().split(":")[0].split("=")[0].strip()
                                if p_name and p_name not in ["self", "cls"]:
                                    props_or_params.add(p_name)
                    else:
                        methods.add(m_name)
                        if search_sym == m_name or (parts and parts[-1] == m_name):
                            desc.is_symbol_found = True

    # Final presence check
    if not target_symbol:
        desc.is_symbol_found = True
    elif not desc.is_symbol_found:
        if base_sym in classes or base_sym in methods:
            desc.is_symbol_found = True
        elif _is_identifier_in_source(source_code, base_sym) and (
            re.search(rf"\bdef\s+{re.escape(base_sym)}\b", source_code)
            or re.search(rf"\bclass\s+{re.escape(base_sym)}\b", source_code)
        ):
            desc.is_symbol_found = True

    desc.exported_symbols = sorted(list(classes))
    desc.import_targets = sorted(list(imports))
    desc.observed_props_or_params = sorted(list(props_or_params))
    desc.observed_methods = sorted(list(methods))
    return desc


def _extract_go_info(source_code: str, target_symbol: str) -> TargetDescriptor:
    desc = TargetDescriptor(target_symbol=target_symbol)
    if not source_code:
        return desc

    raw_sym, base_sym, parts = normalize_target_symbol(target_symbol)
    search_sym = base_sym if base_sym else raw_sym

    # 1. Functions and types (both exported TitleCase and package-private lowercase)
    symbols: Set[str] = set()
    for m in re.finditer(r"\bfunc\s+(?:\([^)]*\)\s+)?([a-zA-Z0-9_]+)", source_code):
        symbols.add(m.group(1))
    for m in re.finditer(r"\btype\s+([a-zA-Z0-9_]+)\s+(?:struct|interface)", source_code):
        symbols.add(m.group(1))
    desc.exported_symbols = sorted(list(symbols))

    # 2. Imports
    imports: Set[str] = set()
    for m in re.finditer(r"import\s+['\"]([^'\"]+)['\"]", source_code):
        imports.add(m.group(1))
    for m in re.finditer(r"import\s*\(([^)]+)\)", source_code, re.DOTALL):
        for line in m.group(1).splitlines():
            im = re.search(r"['\"]([^'\"]+)['\"]", line)
            if im:
                imports.add(im.group(1))
    desc.import_targets = sorted(list(imports))

    props_or_params: Set[str] = set()
    methods: Set[str] = set()

    # Struct fields if target is struct
    pattern_struct = re.compile(rf"type\s+{re.escape(search_sym)}\s+struct\s*\{{([^}}]*)\}}", re.DOTALL)
    m_struct = pattern_struct.search(source_code)
    if m_struct:
        desc.is_symbol_found = True
        for line in m_struct.group(1).splitlines():
            line = line.strip()
            if line and not line.startswith("//"):
                tokens = line.split()
                if tokens and re.match(r"^[a-zA-Z0-9_]+$", tokens[0]):
                    props_or_params.add(tokens[0])

    # Receiver methods for target struct
    pattern_method = re.compile(rf"func\s*\([^)]*\b{re.escape(search_sym)}\b[^)]*\)\s*([a-zA-Z0-9_]+)\s*\(", re.DOTALL)
    for m_meth in pattern_method.finditer(source_code):
        methods.add(m_meth.group(1))

    # Function params if target is function
    pattern_func = re.compile(rf"func\s+{re.escape(search_sym)}\s*\(([^)]*)\)")
    m_func = pattern_func.search(source_code)
    if m_func:
        desc.is_symbol_found = True
        for p in m_func.group(1).split(","):
            tokens = p.strip().split()
            if tokens and re.match(r"^[a-zA-Z0-9_]+$", tokens[0]):
                props_or_params.add(tokens[0])

    if not target_symbol:
        desc.is_symbol_found = True
    elif not desc.is_symbol_found:
        if search_sym in symbols or search_sym in methods:
            desc.is_symbol_found = True
        elif _is_identifier_in_source(source_code, search_sym):
            desc.is_symbol_found = True

    desc.observed_props_or_params = sorted(list(props_or_params))
    desc.observed_methods = sorted(list(methods))
    return desc


def _extract_rust_info(source_code: str, target_symbol: str) -> TargetDescriptor:
    desc = TargetDescriptor(target_symbol=target_symbol)
    if not source_code:
        return desc

    raw_sym, base_sym, parts = normalize_target_symbol(target_symbol)
    search_sym = base_sym if base_sym else raw_sym

    # 1. Pub and non-pub symbols
    symbols: Set[str] = set()
    for m in re.finditer(r"\b(?:pub(?:\([^)]*\))?\s+)?(?:fn|struct|enum|trait)\s+([a-zA-Z0-9_]+)", source_code):
        symbols.add(m.group(1))
    desc.exported_symbols = sorted(list(symbols))

    # 2. Uses
    imports: Set[str] = set()
    for m in re.finditer(r"\buse\s+([^;]+);", source_code):
        imports.add(m.group(1).strip())
    desc.import_targets = sorted(list(imports))

    props_or_params: Set[str] = set()
    methods: Set[str] = set()

    # Struct fields
    pattern_struct = re.compile(rf"(?:pub(?:\([^)]*\))?\s+)?struct\s+{re.escape(search_sym)}\s*\{{([^}}]*)\}}", re.DOTALL)
    m_struct = pattern_struct.search(source_code)
    if m_struct:
        desc.is_symbol_found = True
        for line in m_struct.group(1).splitlines():
            line = line.strip()
            if line and not line.startswith("//"):
                m_f = re.match(r"^(?:pub(?:\([^)]*\))?\s+)?([a-zA-Z0-9_]+)\s*:", line)
                if m_f:
                    props_or_params.add(m_f.group(1))

    # Impl methods: impl Target { pub fn method(...) }
    pattern_impl = re.compile(rf"impl\s+(?:[^{{]*\s+for\s+)?{re.escape(search_sym)}\s*\{{", re.DOTALL)
    m_impl = pattern_impl.search(source_code)
    if m_impl:
        desc.is_symbol_found = True
        start = m_impl.end()
        depth = 1
        pos = start
        while pos < len(source_code) and depth > 0:
            if source_code[pos] == "{":
                depth += 1
            elif source_code[pos] == "}":
                depth -= 1
            pos += 1
        impl_body = source_code[start:pos - 1] if depth == 0 else source_code[start:]
        for m_meth in re.finditer(r"(?:pub(?:\([^)]*\))?\s+)?fn\s+([a-zA-Z0-9_]+)", impl_body):
            methods.add(m_meth.group(1))

    # Standalone function
    pattern_fn = re.compile(rf"(?:pub(?:\([^)]*\))?\s+)?fn\s+{re.escape(search_sym)}\s*\(([^)]*)\)")
    m_fn = pattern_fn.search(source_code)
    if m_fn:
        desc.is_symbol_found = True
        for p in m_fn.group(1).split(","):
            p_clean = p.strip().split(":")[0].strip()
            if p_clean and p_clean not in ["self", "&self", "&mut self"]:
                props_or_params.add(p_clean)

    if not target_symbol:
        desc.is_symbol_found = True
    elif not desc.is_symbol_found:
        if search_sym in symbols or search_sym in methods:
            desc.is_symbol_found = True
        elif _is_identifier_in_source(source_code, search_sym):
            desc.is_symbol_found = True

    desc.observed_props_or_params = sorted(list(props_or_params))
    desc.observed_methods = sorted(list(methods))
    return desc


def _extract_csharp_info(source_code: str, target_symbol: str) -> TargetDescriptor:
    desc = TargetDescriptor(target_symbol=target_symbol)
    if not source_code:
        return desc

    raw_sym, base_sym, parts = normalize_target_symbol(target_symbol)
    search_sym = base_sym if base_sym else raw_sym

    # 1. Classes / interfaces / records / structs
    classes: Set[str] = set()
    for m in re.finditer(r"(?:\[[\w.]+(?:\([^)]*\))?\]\s*)*(?:public|internal|private|protected)?\s*(?:static\s+|partial\s+|abstract\s+)?(?:class|interface|struct|record)\s+([a-zA-Z0-9_]+)", source_code):
        classes.add(m.group(1))
    desc.exported_symbols = sorted(list(classes))

    # 2. Usings
    usings: Set[str] = set()
    for m in re.finditer(r"using\s+([a-zA-Z0-9_.]+);", source_code):
        usings.add(m.group(1))
    desc.import_targets = sorted(list(usings))

    props_or_params: Set[str] = set()
    methods: Set[str] = set()

    pattern_class = re.compile(rf"(?:\[[\w.]+(?:\([^)]*\))?\]\s*)*(?:public|internal|private|protected)?\s*(?:static\s+|partial\s+|abstract\s+)?(?:class|interface|struct|record)\s+{re.escape(search_sym)}[^{{]*\{{", re.DOTALL)
    m_cls = pattern_class.search(source_code)
    if m_cls:
        desc.is_symbol_found = True
        start = m_cls.end()
        depth = 1
        pos = start
        while pos < len(source_code) and depth > 0:
            if source_code[pos] == "{":
                depth += 1
            elif source_code[pos] == "}":
                depth -= 1
            pos += 1
        class_body = source_code[start:pos - 1] if depth == 0 else source_code[start:]

        # Properties
        for m_p in re.finditer(r"(?:public|internal)\s+[\w<>\[\],\s]+\s+([A-Z][a-zA-Z0-9_]*)\s*\{\s*get;", class_body):
            props_or_params.add(m_p.group(1))
        # Methods
        for m_m in re.finditer(r"(?:\[[\w.]+(?:\([^)]*\))?\]\s*)*(?:public|internal|private|protected)\s+(?:async\s+)?(?:static\s+|virtual\s+|override\s+)?[\w<>\[\],\s]+\s+([A-Z][a-zA-Z0-9_]*)\s*\([^)]*\)", class_body):
            if m_m.group(1) != search_sym:
                methods.add(m_m.group(1))

    # Also extract methods across entire source code
    for m_m in re.finditer(r"(?:\[[\w.]+(?:\([^)]*\))?\]\s*)*(?:public|internal|private|protected)\s+(?:async\s+)?(?:static\s+|virtual\s+|override\s+)?[\w<>\[\],\s]+\s+([A-Z][a-zA-Z0-9_]*)\s*\([^)]*\)", source_code):
        if m_m.group(1) not in classes:
            methods.add(m_m.group(1))
            if search_sym == m_m.group(1) or (parts and parts[-1] == m_m.group(1)):
                desc.is_symbol_found = True

    if not target_symbol:
        desc.is_symbol_found = True
    elif not desc.is_symbol_found:
        if search_sym in classes or search_sym in methods:
            desc.is_symbol_found = True
        elif _is_identifier_in_source(source_code, search_sym):
            desc.is_symbol_found = True

    desc.observed_props_or_params = sorted(list(props_or_params))
    desc.observed_methods = sorted(list(methods))
    return desc


def inspect_source_target(
    source_code: str,
    target_symbol: str,
    target_file: str,
    language: str,
) -> TargetDescriptor:
    """
    Deterministically inspects retrieved source code for a specific target symbol,
    extracting its exported symbols, observed parameters/props, and methods.
    Supports all AST-Parser languages in an additive, language-agnostic manner.
    """
    lang_lower = (language or "").lower()
    if lang_lower in ["javascript", "typescript", "jsx", "tsx", "js", "ts"]:
        desc = _extract_js_ts_info(source_code, target_symbol)
    elif lang_lower == "java":
        desc = _extract_java_info(source_code, target_symbol)
    elif lang_lower == "python":
        desc = _extract_python_info(source_code, target_symbol)
    elif lang_lower == "go":
        desc = _extract_go_info(source_code, target_symbol)
    elif lang_lower == "rust":
        desc = _extract_rust_info(source_code, target_symbol)
    elif lang_lower in ["c_sharp", "cs", "csharp"]:
        desc = _extract_csharp_info(source_code, target_symbol)
    else:
        # Generic regex scan
        desc = TargetDescriptor(target_symbol=target_symbol)
        raw_sym, base_sym, _ = normalize_target_symbol(target_symbol)
        if not target_symbol or _is_identifier_in_source(source_code, base_sym or raw_sym):
            desc.is_symbol_found = True

    desc.target_file = target_file
    desc.target_symbol = target_symbol
    return desc


def validate_target_identity(
    test_case: dict,
    retrieved_data: dict,
    descriptor: TargetDescriptor,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validates that the Test Case target matches the retrieved code artifact:
    - Target file matches retrieved primary file or source files (inspecting test_case first).
    - Target symbol exists in retrieved source.
    - Test Case does not assert behavior or methods contradicting the local source.
    Returns (is_valid, failure_type, explanation).
    """
    target = test_case.get("target") or test_case.get("unit") or {}
    tc_file = target.get("file", "").strip()
    tc_symbol = target.get("symbol", "").strip()

    # 1. Validate target file
    # Check test_case specific retrieval and source first, then fallback to retrieved_data
    tc_retrieval = test_case.get("retrieval") or {}
    rd_retrieval = retrieved_data.get("retrieval") or {}
    primary_file = tc_retrieval.get("primaryFile", "").strip() or rd_retrieval.get("primaryFile", "").strip()

    tc_source_files = test_case.get("source", {}).get("files", [])
    rd_source_files = retrieved_data.get("source", {}).get("files", [])

    available_file_paths = []
    if primary_file:
        available_file_paths.append(primary_file)
    for sf in (tc_source_files + rd_source_files):
        if isinstance(sf, dict) and sf.get("path"):
            available_file_paths.append(sf.get("path"))
    available_file_paths = list(dict.fromkeys(available_file_paths))

    if tc_file:
        tc_base = Path(tc_file).name.lower()
        file_matched = any(
            tc_base == Path(af).name.lower() or tc_file.lower() in af.lower() or af.lower() in tc_file.lower()
            for af in available_file_paths if af
        )
        if not file_matched and available_file_paths and any(available_file_paths):
            return (
                False,
                "TEST_CASE_SOURCE_MISMATCH",
                f"Test Case target file '{tc_file}' does not match retrieved source file(s): {available_file_paths}"
            )

    # 2. Validate target symbol
    if tc_symbol and not descriptor.is_symbol_found:
        return (
            False,
            "TARGET_SYMBOL_NOT_FOUND",
            f"Target symbol '{tc_symbol}' could not be located in retrieved source code for '{tc_file}'"
        )

    # 3. Validate target method if explicitly asserted in test case target
    tc_method = target.get("method") or test_case.get("targetMethod")
    if tc_method and descriptor.observed_methods and tc_method not in descriptor.observed_methods:
        return (
            False,
            "TEST_CASE_SOURCE_MISMATCH",
            f"Test Case target method '{tc_method}' does not exist on '{tc_symbol}' in '{tc_file}' (observed methods: {descriptor.observed_methods})"
        )

    return True, None, None

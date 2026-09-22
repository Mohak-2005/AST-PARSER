from __future__ import annotations

import ast
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Collection, Dict, List, Optional, Set, Tuple, Union

from app.logger import backend_logger, log_stage, log_error
from app.services.filename_sanitizer import (
    sanitize_test_title_to_filename,
    resolve_unique_script_filename,
)
from app.services.framework_detector import ProjectEnv, detect_project_environment
from app.services.json_repair_service import strip_markdown_fences
from app.services.llm_service import execute_hf_llm_call
from app.services.source_target_inspector import (
    TargetDescriptor,
    inspect_source_target,
    validate_target_identity,
)

logger = logging.getLogger("test_script_generator")


def _clean_code_output(raw: str) -> str:
    """
    Strips markdown code fences, leading/trailing whitespace, and language tags.
    """
    if not raw:
        return ""
    stripped = raw.strip()
    # Match ```java ... ``` or ```python ... ``` or ``` ... ```
    m = re.match(r"^```[a-zA-Z0-9_-]*\s*\n(.*)\n```\s*$", stripped, re.DOTALL)
    if m:
        return m.group(1).strip()

    # Sometimes trailing ``` is on its own or there are multiple blocks
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]

    return "\n".join(lines).strip()


def resolve_target_import(
    target_file: str,
    target_symbol: str,
    script_output_dir: Union[str, Path],
    project_root: Optional[Union[str, Path]] = None,
    descriptor: Optional[TargetDescriptor] = None,
    language: str = "javascript",
    source_code: str = "",
) -> Dict[str, Any]:
    """
    Deterministically resolves the exact import specifier and statement for a target symbol
    from the target source file to the script output directory.
    Uses platform-safe path resolution and language-appropriate module conventions.
    """
    if not target_file or not target_symbol:
        return {
            "resolved": False,
            "import_specifier": "",
            "import_statement": "",
            "target_file_path": "",
            "is_default_export": False,
            "is_named_export": False,
            "error": f"Target file or symbol is missing (file='{target_file}', symbol='{target_symbol}').",
        }

    lang_lower = (language or "").lower()
    script_dir_abs = Path(script_output_dir).resolve()

    # Step 1: Locate target file
    source_file_abs: Optional[Path] = None
    if Path(target_file).is_absolute() and Path(target_file).exists():
        source_file_abs = Path(target_file).resolve()
    elif project_root:
        p_root = Path(project_root).resolve()
        cand = (p_root / target_file).resolve()
        if cand.exists():
            source_file_abs = cand
        else:
            # Search for filename under project_root if it exists
            target_name = Path(target_file).name
            found = None
            if p_root.exists():
                for p in p_root.glob(f"**/{target_name}"):
                    if not any(part in ["node_modules", ".git", "dist", "build", "__pycache__", "venv", "target"] for part in p.parts):
                        found = p
                        break
            if found:
                source_file_abs = found.resolve()
            else:
                source_file_abs = cand
    else:
        # Fallback: check relative to script_dir_abs or script_dir_abs.parent.parent
        cand1 = (script_dir_abs / target_file).resolve()
        cand2 = (script_dir_abs.parent.parent / target_file).resolve()
        if cand1.exists():
            source_file_abs = cand1
        elif cand2.exists():
            source_file_abs = cand2
        elif Path(target_file).exists():
            source_file_abs = Path(target_file).resolve()
        else:
            source_file_abs = cand2

    if not source_file_abs:
        return {
            "resolved": False,
            "import_specifier": "",
            "import_statement": "",
            "target_file_path": "",
            "is_default_export": False,
            "is_named_export": False,
            "error": f"Target file '{target_file}' could not be located in project root.",
        }

    # Step 2: Language-specific resolution
    if lang_lower in ["javascript", "typescript", "jsx", "tsx", "js", "ts"]:
        try:
            rel = os.path.relpath(source_file_abs, script_dir_abs)
        except ValueError:
            return {
                "resolved": False,
                "import_specifier": "",
                "import_statement": "",
                "target_file_path": str(source_file_abs),
                "is_default_export": False,
                "is_named_export": False,
                "error": f"Cannot resolve relative import between different drive letters: {source_file_abs} and {script_dir_abs}.",
            }

        rel_posix = rel.replace("\\", "/")
        if not rel_posix.startswith("."):
            rel_posix = f"./{rel_posix}"

        # Strip standard extensions for JS/TS import specifiers
        import_specifier = re.sub(r"\.(tsx|ts|jsx|js)$", "", rel_posix)

        is_default = False
        is_named = False
        if descriptor:
            if "default" in descriptor.exported_symbols or (source_code and re.search(rf"export\s+default\s+(?:function\s+)?{re.escape(target_symbol)}\b", source_code)):
                is_default = True
            if target_symbol in descriptor.exported_symbols:
                is_named = True
            if source_code and re.search(rf"export\s+default\s+{re.escape(target_symbol)}\b", source_code):
                is_default = True

        if not is_default and not is_named:
            if target_symbol and target_symbol[0].isupper():
                is_default = True
            else:
                is_named = True

        if is_default:
            stmt = f"import {target_symbol} from '{import_specifier}';"
        else:
            stmt = f"import {{ {target_symbol} }} from '{import_specifier}';"

        return {
            "resolved": True,
            "import_specifier": import_specifier,
            "import_statement": stmt,
            "target_file_path": str(source_file_abs),
            "is_default_export": is_default,
            "is_named_export": is_named,
            "error": None,
        }

    elif lang_lower == "python":
        if project_root:
            try:
                rel = os.path.relpath(source_file_abs, Path(project_root).resolve())
            except ValueError:
                rel = target_file
        else:
            rel = target_file
        rel = rel.replace("\\", "/")
        rel = re.sub(r"^\./", "", rel)
        rel = re.sub(r"\.py$", "", rel)
        mod_name = rel.replace("/", ".")
        stmt = f"from {mod_name} import {target_symbol}"
        return {
            "resolved": True,
            "import_specifier": mod_name,
            "import_statement": stmt,
            "target_file_path": str(source_file_abs),
            "is_default_export": False,
            "is_named_export": True,
            "error": None,
        }

    elif lang_lower == "java":
        pkg = ""
        if source_code:
            m = re.search(r"package\s+([a-zA-Z0-9_.]+);", source_code)
            if m:
                pkg = m.group(1)
        specifier = f"{pkg}.{target_symbol}" if pkg else target_symbol
        stmt = f"import {pkg}.{target_symbol};" if pkg else ""
        return {
            "resolved": True,
            "import_specifier": specifier,
            "import_statement": stmt,
            "target_file_path": str(source_file_abs),
            "is_default_export": False,
            "is_named_export": True,
            "error": None,
        }

    elif lang_lower == "go":
        pkg = ""
        if source_code:
            m = re.search(r"\bpackage\s+([a-zA-Z0-9_]+)", source_code)
            if m:
                pkg = m.group(1)
        specifier = pkg or target_symbol
        stmt = f'import "{pkg}"' if pkg and pkg != "main" else ""
        return {
            "resolved": True,
            "import_specifier": specifier,
            "import_statement": stmt,
            "target_file_path": str(source_file_abs),
            "is_default_export": False,
            "is_named_export": True,
            "error": None,
        }

    elif lang_lower == "rust":
        stmt = f"use crate::{target_symbol};"
        return {
            "resolved": True,
            "import_specifier": target_symbol,
            "import_statement": stmt,
            "target_file_path": str(source_file_abs),
            "is_default_export": False,
            "is_named_export": True,
            "error": None,
        }

    elif lang_lower in ["c_sharp", "cs", "csharp"]:
        ns = ""
        if source_code:
            m = re.search(r"\bnamespace\s+([a-zA-Z0-9_.]+)", source_code)
            if m:
                ns = m.group(1)
        stmt = f"using {ns};" if ns else ""
        return {
            "resolved": True,
            "import_specifier": ns or target_symbol,
            "import_statement": stmt,
            "target_file_path": str(source_file_abs),
            "is_default_export": False,
            "is_named_export": True,
            "error": None,
        }

    else:
        # Generic language fallback: preserved
        return {
            "resolved": True,
            "import_specifier": target_symbol,
            "import_statement": "",
            "target_file_path": str(source_file_abs),
            "is_default_export": False,
            "is_named_export": True,
            "error": None,
        }


def determine_script_extension(
    language: str,
    target_file: str,
    raw_code: str = "",
    project_env: Optional[ProjectEnv] = None,
) -> str:
    """
    Dynamically determines the appropriate script extension from target file,
    generated syntax, and project tooling.
    Supports all AST-Parser languages in an additive, language-agnostic manner.
    """
    lang = (language or "").lower()
    if lang == "java":
        return ".java"
    if lang == "python":
        return ".py"
    if lang == "go":
        return ".go"
    if lang == "rust":
        return ".rs"
    if lang in ["c_sharp", "cs", "csharp"]:
        return ".cs"
    if lang in ["cpp", "cxx", "cc"]:
        return ".cpp"
    if lang == "c":
        return ".c"
    if lang == "ruby":
        return ".rb"
    if lang == "php":
        return ".php"
    if lang == "kotlin":
        return ".kt"
    if lang == "swift":
        return ".swift"
    if lang == "scala":
        return ".scala"
    if lang == "dart":
        return ".dart"

    if lang in ["javascript", "typescript", "jsx", "tsx", "js", "ts"]:
        target_ext = Path(target_file).suffix.lower() if target_file else ""
        has_ts = (
            (project_env.has_typescript if project_env else False)
            or (project_env.language == "typescript" if project_env else False)
            or target_ext in [".ts", ".tsx"]
        )

        has_jsx = target_ext in [".tsx", ".jsx"]
        if raw_code:
            has_jsx_in_code = bool(
                re.search(
                    r"<[A-Z][a-zA-Z0-9_]*(\s+[^>]*)?(?:/>|>)|</[a-zA-Z0-9_]+>|<(div|span|button|p|h[1-6]|ul|li|input|form|section|article)\b[^>]*>",
                    raw_code,
                )
            ) or bool(re.search(r"```tsx\b", raw_code))
            has_jsx = has_jsx or has_jsx_in_code
            has_ts_syntax = bool(
                re.search(
                    r"\bas\s+(?:any|string|number|boolean|unknown|never|[A-Z][a-zA-Z0-9_]*)\b|:\s*(?:React\.[A-Z]|string|number|boolean|any|Record<|Array<)|\binterface\s+[A-Z]|\btype\s+[A-Z][a-zA-Z0-9_]*\s*=",
                    raw_code,
                )
            )
            if has_ts_syntax:
                has_ts = True
        else:
            has_jsx = (
                target_ext in [".tsx", ".jsx"]
                or (project_env.has_jsx if project_env else False)
            )

        if has_jsx:
            return ".tsx" if has_ts else ".jsx"
        else:
            return ".ts" if has_ts else ".js"

    if project_env and project_env.file_extension:
        return project_env.file_extension
    return ".txt"


def build_script_generation_prompt(
    test_case: dict,
    retrieved_data: dict,
    project_env: ProjectEnv,
    source_code: str = "",
    descriptor: Optional[TargetDescriptor] = None,
    resolved_import: Optional[Dict[str, Any]] = None,
    expected_extension: Optional[str] = None,
) -> list[dict]:
    """
    Constructs provider-independent prompt strictly enforcing local-source privacy,
    test framework conventions, real imports, and real symbol signatures.
    CRITICAL PRIVACY: The user's actual source code is NEVER sent in this prompt.
    Only permitted structural metadata (symbol names, prop/param names, method names)
    and Test Case specifications are transmitted to the cloud LLM.
    """
    tc_id = test_case.get("testCaseId", "TC-001")
    title = test_case.get("title", "")
    description = test_case.get("description", "")
    target = test_case.get("target") or test_case.get("unit") or {}
    target_file = target.get("file", "")
    target_symbol = target.get("symbol", "")
    target_type = target.get("type", "unit")
    inputs = test_case.get("inputs", {})
    expected_result = test_case.get("expectedResult", "")

    lang = project_env.language
    framework = project_env.test_framework
    ext = project_env.file_extension

    # Use descriptor if provided, or inspect locally
    if not descriptor:
        descriptor = inspect_source_target(source_code, target_symbol, target_file, lang)

    system_prompt = (
        "You are an expert software test automation engineer.\n"
        "Your task is to generate a single, complete, executable, and strictly source-grounded test script "
        f"for the target symbol '{target_symbol}' in '{target_file}'.\n\n"
        "STRICT EVIDENCE HIERARCHY:\n"
        "1. TEST CASE SPECIFICATION (Target symbol, preconditions, inputs, expected results)\n"
        "2. DETERMINISTIC TARGET STRUCTURAL METADATA (Observed parameters/props, methods, exports)\n"
        "3. EXACT RESOLVED IMPORT CONVENTIONS\n"
        "4. PROJECT TEST CONFIGURATION & CONVENTIONS (Use detected framework: " f"{framework}, extension: {ext})\n"
        "5. GENERAL PROGRAMMING CONVENTIONS\n\n"
        "CRITICAL ANTI-SUBSTITUTION & INTEGRITY RULES:\n"
        f"- Target Symbol: '{target_symbol}'. You MUST test this exact target and ONLY this target.\n"
        "- DO NOT substitute or switch to any other component, class, function, or file under any circumstances.\n"
        "- DO NOT invent props, parameters, methods, return values, database queries, or CSS selectors not present in the source.\n"
        f"- Observed Parameters/Props for target: {descriptor.observed_props_or_params if descriptor.observed_props_or_params else 'None observed'}\n"
        f"- Observed Methods for target: {descriptor.observed_methods if descriptor.observed_methods else 'None observed'}\n"
        "- Construct the smallest valid inputs required for the test based strictly on these observed parameters/props.\n"
        "- Generate ONLY pure, executable test code. No explanations, no markdown intro, no outro.\n"
        "- Enforce ONE TEST CASE → ONE CLEAN STANDALONE TEST SCRIPT.\n"
        f"- Preserve the Test Case metadata at the very top of the script:\n"
        f"  Test Case ID: {tc_id}\n"
        f"  Test Case Title: {title}\n"
        f"  Target: {target_file}::{target_symbol}\n"
    )

    if resolved_import and resolved_import.get("import_statement"):
        system_prompt += (
            f"\nEXACT IMPORT REQUIREMENT:\n"
            f"You MUST import the target symbol '{target_symbol}' using the exact resolved import:\n"
            f"{resolved_import['import_statement']}\n"
            f"DO NOT guess relative paths like './{target_symbol}' or '../{target_symbol}'.\n"
        )

    if lang == "java":
        system_prompt += (
            "\nJAVA SPECIFIC RULES:\n"
            f"- Use {framework.upper()} annotations and assertions (e.g. {project_env.test_framework_import}).\n"
            "- Filename is a human-readable title. Because Java class names cannot have spaces or hyphens, "
            f"declare a package-private test class: 'class {re.sub(r'[^a-zA-Z0-9_]', '', target_symbol or 'Unit')}Test' "
            f"or 'class {re.sub(r'[^a-zA-Z0-9_]', '', tc_id)}Test'.\n"
            "- DO NOT use 'public class' because the class name will differ from the title-based file name. A package-private 'class ClassName' compiles cleanly in any file.\n"
            "- Include required imports for all tested classes and JUnit assertions.\n"
        )
    elif lang == "python":
        system_prompt += (
            "\nPYTHON SPECIFIC RULES:\n"
            f"- Use {framework} test conventions ({project_env.test_framework_import}).\n"
            f"- Define test function as 'def test_{re.sub(r'[^a-zA-Z0-9_]', '_', tc_id).lower()}_{re.sub(r'[^a-zA-Z0-9_]', '_', target_symbol).lower()}():'.\n"
            "- Use standard Python assert statements or framework assertions.\n"
        )
    elif lang == "go":
        system_prompt += (
            "\nGO SPECIFIC RULES:\n"
            f"- Use standard Go testing package ({project_env.test_framework_import}).\n"
            f"- Define test function as 'func Test{re.sub(r'[^a-zA-Z0-9_]', '', target_symbol or tc_id)}(t *testing.T)'.\n"
            "- Use t.Errorf, t.Fatalf, or t.Run for assertions.\n"
        )
    elif lang == "rust":
        system_prompt += (
            "\nRUST SPECIFIC RULES:\n"
            f"- Use Rust built-in test framework ({project_env.test_framework_import}).\n"
            f"- Annotate test function with #[test]: 'fn test_{re.sub(r'[^a-zA-Z0-9_]', '_', target_symbol or tc_id).lower()}()'.\n"
            "- Use assert!, assert_eq!, or assert_ne! for assertions.\n"
        )
    elif lang in ["c_sharp", "cs", "csharp"]:
        system_prompt += (
            "\nC# SPECIFIC RULES:\n"
            "- Use NUnit, xUnit, or MSTest conventions (e.g. [Test] / [Fact] / [TestMethod]).\n"
            "- Define a public test class and test method.\n"
            "- Use Assert.AreEqual / Assert.True / Assert.Equal.\n"
        )
    elif lang in ["javascript", "typescript"]:
        actual_ext = expected_extension or ext
        system_prompt += (
            f"\nJAVASCRIPT/TYPESCRIPT SPECIFIC RULES:\n"
            f"- Use {framework} test syntax (describe, it/test, expect).\n"
            f"- Target test file extension is '{actual_ext}'.\n"
            f"- Describe block or test title should reference '{tc_id}: {title}'.\n"
            f"- Target component/function to test is '{target_symbol}'.\n"
        )
        if actual_ext == ".js":
            system_prompt += "- DO NOT use TypeScript syntax (e.g. 'as any', type annotations). Write pure JavaScript.\n"
        elif actual_ext in [".tsx", ".jsx"]:
            system_prompt += f"- Render component using JSX syntax with {framework} and React Testing Library conventions.\n"

    user_content = (
        f"=== TEST CASE SPECIFICATION ===\n"
        f"Test Case ID: {tc_id}\n"
        f"Title: {title}\n"
        f"Description: {description}\n"
        f"Target File: {target_file}\n"
        f"Target Symbol: {target_symbol} ({target_type})\n"
        f"Inputs: {json.dumps(inputs) if inputs else 'None'}\n"
        f"Expected Result: {expected_result}\n\n"
        f"=== DETERMINISTIC TARGET IDENTITY & STRUCTURAL METADATA ===\n"
        f"Target File: {target_file}\n"
        f"Target Symbol: {target_symbol}\n"
        f"Exported Symbols: {descriptor.exported_symbols}\n"
        f"Observed Parameters/Props: {descriptor.observed_props_or_params}\n"
        f"Observed Methods: {descriptor.observed_methods}\n\n"
        f"Generate the complete, executable {framework} test script for {tc_id} now. "
        "Output ONLY the test script code."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    # Critical Privacy Enforcement: verify that raw source code is NEVER present in outgoing prompt messages
    if source_code and len(source_code.strip()) > 30:
        for msg in messages:
            content = msg.get("content", "")
            if source_code in content:
                raise ValueError("CRITICAL PRIVACY VIOLATION: Source code was detected in outgoing LLM prompt payload.")

    return messages


def _check_delimiter_balance(code: str, language: str = "") -> Optional[str]:
    """
    Validates delimiter balance (parentheses, brackets, curly braces)
    while ignoring comments and string literals.
    Returns an error message if unbalanced, or None if balanced.
    """
    no_comments = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    lang_lower = (language or "").lower()
    if lang_lower == "python":
        no_comments = re.sub(r"#.*$", "", no_comments, flags=re.MULTILINE)
    else:
        no_comments = re.sub(r"//.*$", "", no_comments, flags=re.MULTILINE)
        if lang_lower in ["ruby", "bash", "sh"]:
            no_comments = re.sub(r"#.*$", "", no_comments, flags=re.MULTILINE)

    # Replace string literals so their contents don't trip delimiter checks
    no_strings = re.sub(r'"(?:\\.|[^"\\])*"', '""', no_comments)
    no_strings = re.sub(r"'(?:\\.|[^'\\])*'", "''", no_strings)
    no_strings = re.sub(r"`(?:\\.|[^`\\])*`", "``", no_strings)

    stack: List[Tuple[str, int]] = []
    matching = {")": "(", "]": "[", "}": "{"}
    pairs = {"(": ")", "[": "]", "{": "}"}
    lines = no_strings.splitlines()

    for line_no, line in enumerate(lines, start=1):
        for ch in line:
            if ch in pairs:
                stack.append((ch, line_no))
            elif ch in matching:
                if not stack:
                    return f"Unmatched closing delimiter '{ch}' at line {line_no}."
                open_ch, open_line = stack.pop()
                if open_ch != matching[ch]:
                    return f"Mismatched delimiter at line {line_no}: expected '{pairs[open_ch]}' to close '{open_ch}' from line {open_line}, but found '{ch}'."

    if stack:
        open_ch, open_line = stack[-1]
        return f"Unclosed opening delimiter '{open_ch}' from line {open_line} (missing closing '{pairs[open_ch]}')."

    return None


def _detect_trivial_assertions(code: str, language: str = "") -> bool:
    """
    Detects if the script contains trivial / tautological assertions
    (e.g., assert True, expect(true).toBe(true), assertEquals(1, 1)).
    """
    trivial_patterns = [
        r"^\s*assert\s+(?:True|1\s*==\s*1|0\s*==\s*0|\"\" == \"\"|'test'\s*==\s*'test'|False\s*==\s*False)\s*(?:,\s*.*)?$",
        r"\bexpect\s*\(\s*(?:true|1|'test'|\"test\")\s*\)\.(?:toBe|toEqual)\s*\(\s*(?:true|1|'test'|\"test\")\s*\)",
        r"\bexpect\s*\(\s*true\s*\)\.toBeTruthy\s*\(\s*\)",
        r"\bassertEquals\s*\(\s*(?:1\s*,\s*1|\"test\"\s*,\s*\"test\"|true\s*,\s*true)\s*\)",
        r"\bassertTrue\s*\(\s*true\s*\)",
        r"\bAssert\.(?:True\s*\(\s*true\s*\)|AreEqual\s*\(\s*(?:1\s*,\s*1|true\s*,\s*true)\s*\))",
        r"\bassert!\s*\(\s*true\s*\)",
        r"\bassert_eq!\s*\(\s*(?:1\s*,\s*1|true\s*,\s*true)\s*\)",
    ]
    for line in code.splitlines():
        clean_line = line.strip()
        for pat in trivial_patterns:
            if re.search(pat, clean_line):
                return True
    return False


def _has_substantive_assertions(code: str, language: str = "") -> bool:
    """
    Checks whether the script contains at least one substantive assertion
    verifying target execution, return values, state, or exceptions.
    """
    lang = (language or "").lower()
    lines = code.splitlines()

    # Substantive exception checks
    if re.search(r"\b(pytest\.raises|assertRaises|assertThrows|toThrow|rejects\.toThrow|Assert\.Throws|should_panic)\b", code):
        return True

    for line in lines:
        cl = line.strip()
        if _detect_trivial_assertions(cl, lang):
            continue

        if lang == "python":
            if cl.startswith("assert ") or "self.assert" in cl:
                return True
        elif lang in ["javascript", "typescript"]:
            if re.search(r"\bexpect\s*\(.+\)\s*\.", cl) or re.search(r"\bassert\b", cl):
                return True
        elif lang == "java":
            if re.search(r"\bassert(Equals|True|False|Null|NotNull|Same|NotSame|Throws|ArrayEquals|That)\s*\(", cl):
                return True
        elif lang == "go":
            if re.search(r"\bt\.(Error|Fatal|Fail)", cl):
                return True
        elif lang == "rust":
            if re.search(r"\bassert(_eq|_ne)?!\s*\(", cl):
                return True
        elif lang in ["c_sharp", "cs", "csharp"]:
            if re.search(r"\bAssert\.", cl):
                return True
        else:
            if "assert" in cl.lower() or "expect" in cl.lower():
                return True

    return False


def _check_expected_result_alignment(code: str, test_case: dict, language: str = "") -> Optional[str]:
    """
    Verifies that if the Test Case specifies an error/exception scenario or expectedResult,
    the generated test script actually asserts an error, exception, or failure.
    """
    expected_result = (test_case.get("expectedResult") or "").strip()
    scenario_type = (test_case.get("scenarioType") or test_case.get("type") or "").lower()
    is_error_scenario = (
        bool(re.search(r"\b(raises?|throws?|throw|exception|error\b|rejected|rejects)\b", expected_result, re.IGNORECASE))
        and not bool(re.search(r"\b(no\s+error|without\s+error|should\s+not\s+throw|valid)\b", expected_result, re.IGNORECASE))
    ) or scenario_type in ["negative", "error"]

    if is_error_scenario:
        error_assertion_patterns = [
            r"\bpytest\.raises\b",
            r"\bassertRaises\b",
            r"\bassertThrows\b",
            r"\bAssert\.Throws\b",
            r"\btoThrow\b",
            r"\brejects\b",
            r"\bshould_panic\b",
            r"\b(is_err|unwrap_err)\b",
            r"\bt\.(Error|Fatal)\b",
            r"\b(status_code|status|statusCode)\s*(?:==|===|\.toBe|\.toEqual|\.is)\s*(?:4\d\d|5\d\d)\b",
            r"\bexpect\s*\([^)]+\)\.(?:rejects|toThrow)\b",
            r"\bexcept\s+[A-Za-z0-9_.]+\s*:",
            r"\bcatch\s*\(",
        ]
        has_error_assert = any(re.search(p, code) for p in error_assertion_patterns)
        if not has_error_assert:
            return f"Expected result specifies error/exception behavior ('{expected_result}'), but script does not assert any error, exception, or failure status."
    return None


def _check_unit_test_isolation(code: str, test_case: dict) -> Optional[str]:
    """
    Ensures unit tests do not make unmocked live external network or database calls.
    """
    test_type = (test_case.get("testType") or test_case.get("target", {}).get("type") or "unit").lower()
    if test_type != "unit":
        return None

    live_call_patterns = [
        r"\brequests\.(get|post|put|delete|patch)\s*\(",
        r"\burllib\.request\.urlopen\s*\(",
        r"\bhttp\.client\.HTTPConnection\s*\(",
        r"\baiohttp\.ClientSession\s*\(",
        r"\bmysql\.connector\.connect\s*\(",
        r"\bpsycopg2\.connect\s*\(",
        r"\bpymongo\.MongoClient\s*\(",
        r"\baxios\.(get|post|put|delete|patch)\s*\(",
    ]
    mock_patterns = [
        r"\b(mock|patch|mocker|responses|httpretty|vcr|wiremock|mockito|spyOn|fake|dummy|test_client|TestClient|client)\b"
    ]
    has_mock = any(re.search(p, code, re.IGNORECASE) for p in mock_patterns)
    for pat in live_call_patterns:
        m = re.search(pat, code)
        if m and not has_mock:
            return f"Unit test appears to make unmocked live external call ('{m.group(0).strip()}'). Unit tests must be isolated and mock external network/database dependencies."
    return None


def _check_test_type_compatibility(code: str, test_case: dict) -> Optional[str]:
    """
    Enforces test type boundaries (e.g. unit tests must not use browser automation).
    """
    test_type = (test_case.get("testType") or test_case.get("target", {}).get("type") or "unit").lower()
    if test_type == "unit":
        e2e_patterns = [
            r"\b(from\s+playwright|import\s+playwright|require\s*\(\s*['\"]playwright)\b",
            r"\b(from\s+selenium|import\s+selenium|require\s*\(\s*['\"]selenium)\b",
            r"\b(puppeteer|cypress)\b",
        ]
        for pat in e2e_patterns:
            m = re.search(pat, code, re.IGNORECASE)
            if m:
                return f"Unit test contains browser automation tool ('{m.group(0).strip()}'). Browser automation is reserved for end-to-end (e2e) tests."
    return None


def _check_mock_target_grounding(code: str, source_code: str, descriptor: Optional[TargetDescriptor]) -> Optional[str]:
    """
    Verifies that mocks target symbols actually present in the retrieved module or descriptor.
    """
    patch_matches = re.finditer(r'(?:@patch|mock\.patch)\s*\(\s*["\']([^"\']+)["\']', code)
    for pm in patch_matches:
        patch_path = pm.group(1)
        if any(patch_path.startswith(prefix) for prefix in ["builtins.", "sys.", "os.", "requests.", "urllib.", "datetime.", "time."]):
            continue
        parts = patch_path.split(".")
        if len(parts) >= 2:
            leaf_symbol = parts[-1]
            in_source = bool(source_code and leaf_symbol.lower() in source_code.lower())
            in_methods = bool(descriptor and any(m.lower() == leaf_symbol.lower() for m in descriptor.observed_methods))
            in_exports = bool(descriptor and any(e.lower() == leaf_symbol.lower() for e in descriptor.exported_symbols))
            in_params = bool(descriptor and any(p.lower() == leaf_symbol.lower() for p in descriptor.observed_props_or_params))
            if not (in_source or in_methods or in_exports or in_params):
                return f"Mock target '{patch_path}' patches symbol '{leaf_symbol}' which does not exist in the retrieved source code or target descriptor."
    return None


def _check_test_case_conflicts(code: str, tc_id: str, retrieved_data: dict) -> Optional[str]:
    """
    Detects conflicting Test Case IDs in multi-case retrieved groups.
    """
    test_cases = retrieved_data.get("testCases", [])
    if len(test_cases) > 1:
        other_tc_ids = {
            tc.get("testCaseId").strip()
            for tc in test_cases
            if tc.get("testCaseId") and tc.get("testCaseId").strip() != tc_id
        }
        m_hdr = re.search(r"(?://|#)\s*Test Case ID:\s*([A-Za-z0-9_-]+)", code)
        if m_hdr:
            declared_id = m_hdr.group(1).strip()
            if declared_id in other_tc_ids and declared_id != tc_id:
                return f"Conflicting Test Case ID: script header declares '{declared_id}' but current test case is '{tc_id}'."
        clean_current = re.sub(r"[^a-zA-Z0-9]", "", tc_id).lower()
        if clean_current not in code.lower():
            for other_id in other_tc_ids:
                clean_other = re.sub(r"[^a-zA-Z0-9]", "", other_id).lower()
                if clean_other in code.lower():
                    return f"Conflicting Test Case ID: script targets '{other_id}' instead of '{tc_id}'."
    return None


def _check_prop_grounding(code: str, target_symbol: str, descriptor: Optional[TargetDescriptor], source_code: str) -> Optional[str]:
    """
    Verifies that JSX components are not passed ungrounded props that do not exist in source.
    """
    if not descriptor or not descriptor.observed_props_or_params:
        return None
    observed = {p.lower() for p in descriptor.observed_props_or_params}
    if any(p in observed for p in ["props", "...rest", "rest", "args", "*args", "**kwargs"]):
        return None

    jsx_pattern = re.compile(rf"<{re.escape(target_symbol)}\b([^>]*)/?>")
    for m in jsx_pattern.finditer(code):
        attrs_str = m.group(1)
        passed_props = re.findall(r"\b([a-zA-Z0-9_-]+)\s*=", attrs_str)
        standard_props = {
            "key", "classname", "style", "id", "children", "ref",
            "data-testid", "aria-label", "onclick", "onchange", "onsubmit", "disabled", "title"
        }
        for prop in passed_props:
            prop_l = prop.lower()
            if prop_l not in observed and prop_l not in standard_props:
                if source_code and prop_l in source_code.lower():
                    continue
                return f"Component '{target_symbol}' is passed ungrounded prop '{prop}' that is not defined in the source code or observed props ({descriptor.observed_props_or_params})."
    return None


def validate_generated_script(
    raw_code: str,
    test_case: dict,
    retrieved_data: dict,
    project_env: ProjectEnv,
    descriptor: Optional[TargetDescriptor] = None,
    script_dir: Optional[Path] = None,
    resolved_import: Optional[Dict[str, Any]] = None,
    script_extension: Optional[str] = None,
    source_code: str = "",
) -> Tuple[bool, str, List[str], Optional[str]]:
    """
    Deterministic local cross-validation of generated test scripts against Test Case and Retrieved Code:
    1. Test Case Mapping: Preserves testCaseId, scenario/intent alignment, guards against conflicting testCaseIds.
    2. Target Mapping: Verifies target symbol presence, local import resolution, and guards against cross-target substitution.
    3. Source Grounding: Grounding for parameters, props, instance methods, and return shapes.
    4. Assertions & Expected Result: Rejects trivial/tautological assertions; enforces alignment with expectedResult.
    5. Test Isolation & Mock Grounding: Unit tests must not make live unmocked external calls; mocks must target grounded symbols.
    6. Syntax and Imports: Python ast.parse syntax check; multi-language delimiter balancing; extension-syntax compatibility.
    7. Framework Compatibility: Annotations and test functions match project_env.test_framework for all supported languages.
    8. Test-Type Compatibility: Unit tests must not use browser automation.

    Returns: (is_valid, cleaned_code, errors, failure_type)
    """
    errors: List[str] = []
    cleaned = _clean_code_output(raw_code)
    failure_type: Optional[str] = None
    lang = project_env.language.lower()

    # 1. Non-empty check
    if not cleaned or len(cleaned) < 30:
        errors.append(f"Script content is too short or empty ({len(cleaned)} chars).")
        return False, cleaned, errors, "SCRIPT_SYNTAX_ERROR"

    # 2. Markdown fences cleanup
    if "```" in cleaned:
        cleaned = cleaned.replace("```", "")

    # 3. No TODO / empty placeholders
    if re.search(r"//\s*TODO\s*:\s*implement", cleaned, re.IGNORECASE) or "throw new UnsupportedOperationException" in cleaned:
        errors.append("Script contains unresolved TODO or UnsupportedOperationException placeholder.")
        failure_type = "UNSUPPORTED_ASSUMPTION"

    # 4. Deterministic Syntax Validation
    # Python: exact ast.parse syntax verification with line numbers
    if lang == "python":
        try:
            ast.parse(cleaned)
        except SyntaxError as e:
            errors.append(f"Python script contains syntax error at line {e.lineno}: {e.msg}")
            failure_type = failure_type or "SCRIPT_SYNTAX_ERROR"

    # Multi-language delimiter balance check (parentheses, brackets, curly braces)
    delim_err = _check_delimiter_balance(cleaned, lang)
    if delim_err:
        errors.append(f"Syntax error: {delim_err}")
        failure_type = failure_type or "SCRIPT_SYNTAX_ERROR"

    # 5. Test Case Mapping & Conflicting IDs
    tc_id = test_case.get("testCaseId", "").strip()
    cleaned_lower = cleaned.lower()
    tc_id_clean = re.sub(r"[^a-zA-Z0-9]", "", tc_id).lower()
    if tc_id and (tc_id.lower() not in cleaned_lower and tc_id_clean not in cleaned_lower):
        errors.append(f"Test Case ID '{tc_id}' was not preserved in comments, class name, or test name.")
        failure_type = failure_type or "TEST_CASE_SOURCE_MISMATCH"

    conflict_err = _check_test_case_conflicts(cleaned, tc_id, retrieved_data)
    if conflict_err:
        errors.append(conflict_err)
        failure_type = failure_type or "TEST_CASE_SOURCE_MISMATCH"

    # 6. Target Mapping & Cross-Target Substitution Guard
    target = test_case.get("target") or test_case.get("unit") or {}
    target_symbol = target.get("symbol", "").strip()
    target_file = target.get("file", "").strip()

    symbol_found = False
    if target_symbol:
        symbol_found = target_symbol.lower() in cleaned_lower

    if not symbol_found and descriptor and descriptor.exported_symbols:
        symbol_found = any(exp.lower() in cleaned_lower for exp in descriptor.exported_symbols)

    if target_symbol and not symbol_found:
        errors.append(f"Target symbol '{target_symbol}' was not found in generated script.")
        failure_type = failure_type or "TARGET_MISMATCH"

    if descriptor and descriptor.target_symbol:
        if not symbol_found:
            errors.append(f"Cross-target substitution detected: target is '{descriptor.target_symbol}', but script does not test it.")
            failure_type = "TARGET_MISMATCH"

    # Check local import resolution & extension syntax compatibility (JS/TS)
    if lang in ["javascript", "typescript"]:
        if resolved_import and not resolved_import.get("resolved"):
            errors.append(f"Target import resolution failed: {resolved_import.get('error')}")
            failure_type = failure_type or "IMPORT_RESOLUTION_ERROR"
        elif target_symbol and target_file:
            pattern_import = re.compile(
                rf"(?:import\s+(?:(?:\*\s+as\s+\w+|\{{[^}}]*\b{re.escape(target_symbol)}\b[^}}]*\}}|{re.escape(target_symbol)})\s*,?\s*)*(?:\{{[^}}]*\b{re.escape(target_symbol)}\b[^}}]*\}})?\s*from\s*['\"]([^'\"]+)['\"]|require\s*\(\s*['\"]([^'\"]+)['\"]\s*\))"
            )
            m_imp = pattern_import.search(cleaned)
            if resolved_import and resolved_import.get("resolved"):
                expected_spec = resolved_import.get("import_specifier", "")
                if m_imp:
                    actual_imp = m_imp.group(1) or m_imp.group(2)
                    if actual_imp.startswith("."):
                        norm_act = re.sub(r"\.(tsx|ts|jsx|js)$", "", actual_imp.replace("\\", "/").rstrip("/"))
                        norm_exp = re.sub(r"\.(tsx|ts|jsx|js)$", "", expected_spec.replace("\\", "/").rstrip("/"))
                        if norm_act != norm_exp and script_dir:
                            test_cand = (Path(script_dir) / actual_imp).resolve()
                            cand_matched = False
                            for ext_try in ["", ".tsx", ".ts", ".jsx", ".js", "/index.tsx", "/index.ts", "/index.jsx", "/index.js"]:
                                if Path(str(test_cand) + ext_try).exists():
                                    cand_matched = True
                                    break
                            if not cand_matched:
                                errors.append(
                                    f"Import path '{actual_imp}' for target symbol '{target_symbol}' does not resolve to the target source file (expected '{expected_spec}')."
                                )
                                failure_type = failure_type or "IMPORT_RESOLUTION_ERROR"

        effective_ext = script_extension or project_env.file_extension
        has_jsx_in_code = bool(
            re.search(
                r"<[A-Z][a-zA-Z0-9_]*(\s+[^>]*)?(?:/>|>)|</[a-zA-Z0-9_]+>|<(div|span|button|p|h[1-6]|ul|li|input|form)\b[^>]*>",
                cleaned,
            )
        )
        has_ts_in_code = bool(
            re.search(
                r"\bas\s+any\b|\bas\s+(string|number|boolean|unknown|never|[A-Z][a-zA-Z0-9_]*)\b|:\s*(React\.[A-Z]|string|number|boolean|Record<|Array<)|\binterface\s+[A-Z]|\btype\s+[A-Z][a-zA-Z0-9_]*\s*=",
                cleaned,
            )
        )

        if effective_ext == ".js":
            if has_jsx_in_code and not (project_env and project_env.has_jsx and getattr(project_env, "supports_jsx_in_js", False)):
                errors.append("Script contains JSX syntax but file extension is .js. JSX must be saved in .jsx or .tsx files unless tooling explicitly supports it.")
                failure_type = failure_type or "SCRIPT_SYNTAX_ERROR"
            if has_ts_in_code:
                errors.append("Script contains TypeScript-only syntax ('as any' or type annotations) in an ordinary JavaScript (.js) script.")
                failure_type = failure_type or "INCOMPATIBLE_SYNTAX"

        elif effective_ext == ".ts":
            if has_jsx_in_code:
                errors.append("Script contains JSX syntax but file extension is .ts. TSX files must use .tsx extension.")
                failure_type = failure_type or "SCRIPT_SYNTAX_ERROR"

    # 7. Source Grounding: Props / Parameters & Instance Methods
    prop_err = _check_prop_grounding(cleaned, target_symbol, descriptor, source_code)
    if prop_err:
        errors.append(prop_err)
        failure_type = failure_type or "SOURCE_BEHAVIOR_NOT_FOUND"

    if descriptor and (descriptor.observed_methods or source_code):
        check_symbol = (descriptor.target_symbol or target_symbol).strip()
        if check_symbol:
            known_instances = {
                check_symbol.lower(),
                "instance", "target", "obj", "subject"
            }
            if len(check_symbol) >= 4:
                known_instances.add(check_symbol[:4].lower())
                known_instances.add(check_symbol[:3].lower())

            for m_inst in re.finditer(rf"\b([a-zA-Z0-9_$]+)\s*=\s*new\s+{re.escape(check_symbol)}\b|\b{re.escape(check_symbol)}\s+([a-zA-Z0-9_$]+)\s*=", cleaned):
                var_name = m_inst.group(1) or m_inst.group(2)
                if var_name:
                    known_instances.add(var_name.lower())

            common_safe_methods = {
                "tostring", "equals", "hashcode", "get", "set", "valueof", "format",
                "length", "size", "split", "strip", "lower", "upper", "render", "click",
                "change", "tobe", "toequal", "tohavelength", "tohavebeencalled", "expect",
                "assert", "assertequals", "asserttrue", "assertfalse", "assertnull",
                "assertnotnull", "assertthat", "assertthrows", "setup", "teardown",
                "create", "build", "new", "init", "close", "run", "call", "apply"
            }
            for m_call in re.finditer(r"\b([a-zA-Z0-9_$]+)\s*\.\s*([a-zA-Z0-9_$]+)\s*\(", cleaned):
                recv = m_call.group(1).lower()
                meth = m_call.group(2)
                meth_lower = meth.lower()
                if meth_lower in common_safe_methods:
                    continue
                if recv in known_instances or any(inst in recv for inst in known_instances):
                    method_in_observed = any(m.lower() == meth_lower for m in descriptor.observed_methods)
                    method_in_source = bool(source_code and meth_lower in source_code.lower())
                    if not method_in_observed and not method_in_source:
                        errors.append(
                            f"Generated script calls ungrounded method '{meth}' on target '{check_symbol}' that is not supported by the local source implementation."
                        )
                        failure_type = failure_type or "SOURCE_BEHAVIOR_NOT_FOUND"

    # 8. Assertions & Stated Expected Result Alignment
    if _detect_trivial_assertions(cleaned, lang) and not _has_substantive_assertions(cleaned, lang):
        errors.append("Script contains trivial or tautological assertion (e.g. 'assert True' or 'expect(true).toBe(true)') without substantive verification of target behavior.")
        failure_type = failure_type or "TRIVIAL_ASSERTION_DETECTED"

    expected_res_err = _check_expected_result_alignment(cleaned, test_case, lang)
    if expected_res_err:
        errors.append(expected_res_err)
        failure_type = failure_type or "EXPECTED_RESULT_MISMATCH"

    # 9. Test Isolation & Mock Grounding
    isolation_err = _check_unit_test_isolation(cleaned, test_case)
    if isolation_err:
        errors.append(isolation_err)
        failure_type = failure_type or "LIVE_DEPENDENCY_IN_UNIT_TEST"

    mock_target_err = _check_mock_target_grounding(cleaned, source_code, descriptor)
    if mock_target_err:
        errors.append(mock_target_err)
        failure_type = failure_type or "UNGROUNDED_MOCK_TARGET"

    # 10. Test-Type Compatibility
    test_type_err = _check_test_type_compatibility(cleaned, test_case)
    if test_type_err:
        errors.append(test_type_err)
        failure_type = failure_type or "TEST_TYPE_MISMATCH"

    # 11. Multi-Language Framework & Assertion Checks
    if lang == "java":
        if "@Test" not in cleaned and "org.junit" not in cleaned and "junit" not in cleaned.lower() and "testng" not in cleaned.lower():
            errors.append("Java test script does not contain @Test annotation or JUnit/TestNG imports.")
            failure_type = failure_type or "FRAMEWORK_MISMATCH"
        if not re.search(r"\bassert(Equals|True|False|Null|NotNull|Same|NotSame|Throws|ArrayEquals|That)?\s*\(", cleaned):
            errors.append("Java test script does not contain any JUnit/TestNG assertion calls.")
            failure_type = failure_type or "SOURCE_BEHAVIOR_NOT_FOUND"
        if re.search(r"\bpublic\s+class\s+", cleaned):
            errors.append("Java script declares a public class in a title-named file. Use package-private 'class ClassName' instead.")
            failure_type = failure_type or "SCRIPT_SYNTAX_ERROR"

    elif lang == "python":
        if "def test_" not in cleaned and "Test" not in cleaned:
            errors.append("Python test script does not define a test function (def test_*) or Test class.")
            failure_type = failure_type or "FRAMEWORK_MISMATCH"
        if "assert" not in cleaned and "self.assert" not in cleaned and "pytest.raises" not in cleaned:
            errors.append("Python test script does not contain any assertions.")
            failure_type = failure_type or "SOURCE_BEHAVIOR_NOT_FOUND"

    elif lang == "go":
        if "testing.T" not in cleaned and "func Test" not in cleaned:
            errors.append("Go test script does not define a test function (func Test*(t *testing.T)).")
            failure_type = failure_type or "FRAMEWORK_MISMATCH"
        if not re.search(r"\bt\.(Error|Fatal|Run|Fail)", cleaned):
            errors.append("Go test script does not contain any test assertions (t.Error / t.Fatal).")
            failure_type = failure_type or "SOURCE_BEHAVIOR_NOT_FOUND"

    elif lang == "rust":
        if "#[test]" not in cleaned:
            errors.append("Rust test script does not contain #[test] annotation.")
            failure_type = failure_type or "FRAMEWORK_MISMATCH"
        if "assert" not in cleaned:
            errors.append("Rust test script does not contain any assertions (assert!).")
            failure_type = failure_type or "SOURCE_BEHAVIOR_NOT_FOUND"

    elif lang in ["c_sharp", "cs", "csharp"]:
        if not re.search(r"\[(Test|Fact|TestMethod)\]", cleaned):
            errors.append("C# test script does not contain test annotations ([Test] or [Fact]).")
            failure_type = failure_type or "FRAMEWORK_MISMATCH"
        if "Assert." not in cleaned:
            errors.append("C# test script does not contain assertions (Assert.*).")
            failure_type = failure_type or "SOURCE_BEHAVIOR_NOT_FOUND"

    elif lang in ["javascript", "typescript"]:
        if not re.search(r"\b(test|it|describe)\s*\(", cleaned):
            errors.append("JS/TS test script does not contain describe/test/it blocks.")
            failure_type = failure_type or "FRAMEWORK_MISMATCH"
        if not re.search(r"\b(expect|assert)\b", cleaned):
            errors.append("JS/TS test script does not contain assertions (expect/assert).")
            failure_type = failure_type or "SOURCE_BEHAVIOR_NOT_FOUND"

    is_valid = len(errors) == 0
    return is_valid, cleaned, errors, failure_type


async def generate_script_for_single_test_case(
    test_case: dict,
    retrieved_data: dict,
    project_env: ProjectEnv,
    target_output_dir: Path,
    analysis_id: Optional[str] = None,
    force: bool = False,
    existing_filenames: Optional[Collection[str]] = None,
) -> dict:
    """
    Generates a single test script for a test case and saves to:
    `<target_output_dir>/<Sanitized Title>.<ext>`
    Emits all required diagnostic logs.
    """
    tc_id = test_case.get("testCaseId", "TC-001")
    title = test_case.get("title", "") or f"Test_{tc_id}"
    ext = project_env.file_extension
    target = test_case.get("target") or test_case.get("unit") or {}
    target_file = target.get("file", "")
    target_symbol = target.get("symbol", "")

    # DIAGNOSTIC LOG 1: SCRIPT_GENERATION_STARTED
    backend_logger.info(
        f"SCRIPT_GENERATION_STARTED: Generating test script for {tc_id} ('{title}') "
        f"[target: {target_file}::{target_symbol}]"
    )
    log_stage("SCRIPT_GENERATION", "START", analysis_id=analysis_id, extra={"testCaseId": tc_id, "title": title})

    # Extract source code: check test_case specific source files first, fallback to retrieved_data
    tc_source_files = test_case.get("source", {}).get("files", [])
    rd_source_files = retrieved_data.get("source", {}).get("files", [])
    source_files = tc_source_files if (tc_source_files and isinstance(tc_source_files, list)) else rd_source_files

    target_base = Path(target_file).name.lower() if target_file else ""
    source_code = ""
    if source_files and isinstance(source_files, list):
        # 1. Match by target file path or filename
        for sf in source_files:
            if isinstance(sf, dict):
                sf_path = sf.get("path", "")
                if sf_path and (sf_path == target_file or Path(sf_path).name.lower() == target_base):
                    source_code = sf.get("sourceCode", "")
                    if source_code:
                        break
        # 2. Match by primary_target reason if no exact file match
        if not source_code:
            for sf in source_files:
                if isinstance(sf, dict) and sf.get("reason") == "primary_target":
                    source_code = sf.get("sourceCode", "")
                    if source_code:
                        break
        # 3. Fallback to first file in source_files
        if not source_code and len(source_files) > 0 and isinstance(source_files[0], dict):
            source_code = source_files[0].get("sourceCode", "")

    descriptor = inspect_source_target(source_code, target_symbol, target_file, project_env.language)

    # DIAGNOSTIC LOG 2: SCRIPT_TARGET_RESOLVED / SCRIPT_TARGET_UNRESOLVED
    if descriptor.is_symbol_found:
        backend_logger.info(
            f"SCRIPT_TARGET_RESOLVED: Resolved target '{target_symbol}' in '{target_file}' "
            f"(exported={descriptor.exported_symbols}, props={descriptor.observed_props_or_params}, methods={descriptor.observed_methods})"
        )
    else:
        backend_logger.warning(
            f"SCRIPT_TARGET_UNRESOLVED: Target symbol '{target_symbol}' not located in retrieved source for '{target_file}'"
        )

    # Compute deterministic human-readable filename
    existing_set = set(existing_filenames) if existing_filenames else set()
    out_filename = resolve_unique_script_filename(title, tc_id, ext, existing_set, current_tc_id=tc_id)
    out_filepath = target_output_dir / out_filename

    # DIAGNOSTIC LOG 3: SCRIPT_SOURCE_VALIDATION_STARTED
    backend_logger.info(f"SCRIPT_SOURCE_VALIDATION_STARTED: Pre-validating target identity for {tc_id}")
    is_target_valid, mismatch_type, mismatch_explanation = validate_target_identity(
        test_case, retrieved_data, descriptor
    )

    if not is_target_valid:
        # DIAGNOSTIC LOG: SCRIPT_VALIDATION_FAILED (Pre-generation)
        backend_logger.warning(
            f"SCRIPT_VALIDATION_FAILED for {tc_id}: {mismatch_type} - {mismatch_explanation}"
        )
        log_stage("SCRIPT_VALIDATION", "FAILURE", analysis_id=analysis_id, extra={"testCaseId": tc_id, "failureType": mismatch_type, "error": mismatch_explanation})
        return {
            "testCaseId": tc_id,
            "testCaseTitle": title,
            "status": "VALIDATION_FAILED",
            "failureType": mismatch_type,
            "filename": out_filename,
            "filePath": str(out_filepath),
            "script": "",
            "errors": [mismatch_explanation],
            "mismatches": [mismatch_explanation],
        }

    # DIAGNOSTIC LOG 4: SCRIPT_SOURCE_VALIDATION_COMPLETED
    backend_logger.info(f"SCRIPT_SOURCE_VALIDATION_COMPLETED: Pre-validation passed for {tc_id}")

    # Step 4: Resolve import dynamically before prompt or execution
    project_root = project_env.project_root or (target_output_dir.parent.parent if target_output_dir else None)
    import_res = resolve_target_import(
        target_file=target_file,
        target_symbol=target_symbol,
        script_output_dir=target_output_dir,
        project_root=project_root,
        descriptor=descriptor,
        language=project_env.language,
        source_code=source_code,
    )
    if not import_res.get("resolved"):
        err_msg = import_res.get("error") or f"Could not resolve target import for '{target_symbol}' in '{target_file}'."
        backend_logger.warning(f"SCRIPT_VALIDATION_FAILED for {tc_id}: IMPORT_RESOLUTION_ERROR - {err_msg}")
        log_stage("SCRIPT_VALIDATION", "FAILURE", analysis_id=analysis_id, extra={"testCaseId": tc_id, "failureType": "IMPORT_RESOLUTION_ERROR", "error": err_msg})
        return {
            "testCaseId": tc_id,
            "testCaseTitle": title,
            "status": "VALIDATION_FAILED",
            "failureType": "IMPORT_RESOLUTION_ERROR",
            "filename": out_filename,
            "filePath": str(out_filepath),
            "script": "",
            "errors": [err_msg],
            "mismatches": [err_msg],
        }

    # Recompute extension dynamically based on target file and project tooling
    ext = determine_script_extension(project_env.language, target_file, project_env=project_env)
    out_filename = resolve_unique_script_filename(title, tc_id, ext, existing_set, current_tc_id=tc_id)
    out_filepath = target_output_dir / out_filename

    # Check if already exists and valid
    if not force and out_filepath.exists():
        existing_code = out_filepath.read_text(encoding="utf-8", errors="ignore")
        is_val, cleaned, errs, _ = validate_generated_script(
            existing_code, test_case, retrieved_data, project_env, descriptor,
            script_dir=target_output_dir, resolved_import=import_res, script_extension=ext,
            source_code=source_code,
        )
        if is_val:
            backend_logger.info(f"SCRIPT_ALREADY_EXISTS: {out_filepath}")
            return {
                "testCaseId": tc_id,
                "testCaseTitle": title,
                "status": "success",
                "filename": out_filename,
                "filePath": str(out_filepath),
                "script": cleaned,
                "alreadyExists": True,
                "errors": [],
            }

    # Build prompt with resolved import and expected extension
    messages = build_script_generation_prompt(
        test_case, retrieved_data, project_env, source_code, descriptor,
        resolved_import=import_res, expected_extension=ext
    )

    # DIAGNOSTIC LOG 5: SCRIPT_LLM_GENERATION_STARTED
    backend_logger.info(f"SCRIPT_LLM_GENERATION_STARTED: Calling LLM for {tc_id} ({title})")
    t0 = time.time()

    raw_response = ""
    try:
        raw_response, status_code, duration_s = await execute_hf_llm_call(
            messages=messages,
            max_tokens=4000,
            temperature=0.1,
            analysis_id=analysis_id,
            stage_name="SCRIPT_GENERATION_REQUEST",
        )
    except Exception as e:
        backend_logger.error(f"SCRIPT_GENERATION_FAILED: LLM call error for {tc_id}: {str(e)}")
        log_error("SCRIPT_GENERATION", type(e).__name__, str(e), analysis_id=analysis_id)
        return {
            "testCaseId": tc_id,
            "testCaseTitle": title,
            "status": "VALIDATION_FAILED",
            "failureType": "LLM_REQUEST_ERROR",
            "filename": out_filename,
            "filePath": str(out_filepath),
            "script": "",
            "errors": [f"LLM request failed: {str(e)}"],
        }

    # DIAGNOSTIC LOG 6: SCRIPT_LLM_GENERATION_COMPLETED
    backend_logger.info(
        f"SCRIPT_LLM_GENERATION_COMPLETED: Received {len(raw_response)} chars in {time.time() - t0:.2f}s for {tc_id}"
    )

    # Recompute script extension dynamically based on actual generated syntax
    final_ext = determine_script_extension(project_env.language, target_file, raw_code=raw_response, project_env=project_env)
    if final_ext != ext:
        ext = final_ext
        out_filename = resolve_unique_script_filename(title, tc_id, ext, existing_set, current_tc_id=tc_id)
        out_filepath = target_output_dir / out_filename

    # Reconcile naive relative imports in generated script if LLM made a naive `./<target_symbol>` assumption
    if import_res.get("resolved") and project_env.language.lower() in ["javascript", "typescript", "jsx", "tsx", "js", "ts"]:
        imp_spec = import_res["import_specifier"]
        stem = Path(target_file).stem
        for naive_target in [target_symbol, stem]:
            if naive_target:
                raw_response = re.sub(
                    rf"(['\"])\./{re.escape(naive_target)}(?:\.(?:tsx|ts|jsx|js))?(['\"])",
                    rf"\1{imp_spec}\2",
                    raw_response
                )

    # DIAGNOSTIC LOG 7: SCRIPT_VALIDATION_STARTED
    backend_logger.info(f"SCRIPT_VALIDATION_STARTED: Validating generated script for {tc_id}")
    is_valid, cleaned_script, validation_errors, failure_type = validate_generated_script(
        raw_response, test_case, retrieved_data, project_env, descriptor,
        script_dir=target_output_dir, resolved_import=import_res, script_extension=ext,
        source_code=source_code,
    )

    # If missing header metadata was the only minor defect, prepend standard metadata comment
    comment_marker = "//" if project_env.language in ["java", "javascript", "typescript", "go", "rust", "c_sharp", "cpp"] else "#"
    header_comment = (
        f"{comment_marker} Test Case ID: {tc_id}\n"
        f"{comment_marker} Test Case Title: {title}\n"
        f"{comment_marker} Target: {target_file}::{target_symbol}\n\n"
    )

    if not is_valid and len(validation_errors) == 1 and "Test Case ID" in validation_errors[0]:
        cleaned_script = header_comment + cleaned_script
        is_valid, cleaned_script, validation_errors, failure_type = validate_generated_script(
            cleaned_script, test_case, retrieved_data, project_env, descriptor,
            script_dir=target_output_dir, resolved_import=import_res, script_extension=ext,
            source_code=source_code,
        )

    if is_valid:
        # Prepend header if not already there
        if tc_id not in cleaned_script[:200]:
            cleaned_script = header_comment + cleaned_script

        # DIAGNOSTIC LOG 8: SCRIPT_VALIDATION_COMPLETED
        backend_logger.info(f"SCRIPT_VALIDATION_COMPLETED: Script passed all checks for {tc_id}")
        log_stage("SCRIPT_VALIDATION", "SUCCESS", analysis_id=analysis_id, extra={"testCaseId": tc_id})

        # Save to disk
        target_output_dir.mkdir(parents=True, exist_ok=True)
        out_filepath.write_text(cleaned_script, encoding="utf-8")

        # Clean up any previously saved stale file with a conflicting extension for this test case
        for old_ext in [".js", ".ts", ".jsx", ".tsx"]:
            if old_ext != ext:
                old_file = target_output_dir / f"{Path(out_filename).stem}{old_ext}"
                if old_file.exists():
                    try:
                        old_file.unlink()
                    except Exception:
                        pass

        # DIAGNOSTIC LOG 9: SCRIPT_SAVED
        backend_logger.info(f"SCRIPT_SAVED: Test script successfully saved to {out_filepath}")
        log_stage("SCRIPT_SAVED", "SUCCESS", analysis_id=analysis_id, extra={"filePath": str(out_filepath)})

        return {
            "testCaseId": tc_id,
            "testCaseTitle": title,
            "status": "success",
            "filename": out_filename,
            "filePath": str(out_filepath),
            "script": cleaned_script,
            "errors": [],
        }
    else:
        # DIAGNOSTIC LOG: SCRIPT_VALIDATION_FAILED
        backend_logger.warning(
            f"SCRIPT_VALIDATION_FAILED for {tc_id} ({failure_type}): {validation_errors}"
        )
        log_stage("SCRIPT_VALIDATION", "FAILURE", analysis_id=analysis_id, extra={"testCaseId": tc_id, "failureType": failure_type, "errors": validation_errors})
        
        # DO NOT save the script as valid!
        return {
            "testCaseId": tc_id,
            "testCaseTitle": title,
            "status": "VALIDATION_FAILED",
            "failureType": failure_type or "VALIDATION_FAILED",
            "filename": out_filename,
            "filePath": str(out_filepath),
            "script": "",
            "errors": validation_errors,
            "mismatches": validation_errors,
        }


def get_existing_test_scripts(code_map_source: Union[str, Path, dict]) -> List[Dict[str, Any]]:
    """
    Discovers all existing test scripts under `<analysis-output-dir>/test scripts/`.
    Cross-validates every script on disk against corresponding Test Case and Retrieved Code.
    Returns list of discovered scripts with rich human-readable metadata, validation status, and errors.
    """
    target_dir: Optional[Path] = None
    code_map_dict: Optional[dict] = None
    if isinstance(code_map_source, (str, Path)):
        p = Path(code_map_source)
        target_dir = p if p.is_dir() else p.parent
        cmp = target_dir / "repository-code-map.json"
        if cmp.exists():
            try:
                code_map_dict = json.loads(cmp.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                code_map_dict = None
    elif isinstance(code_map_source, dict):
        code_map_dict = code_map_source
        cand = code_map_source.get("project", {}).get("path")
        if cand and Path(cand).exists():
            target_dir = Path(cand).parent

    if not target_dir:
        return []

    scripts_root = target_dir / "test scripts"
    if not scripts_root.exists() or not scripts_root.is_dir():
        return []

    project_root_str = code_map_dict.get("project", {}).get("path") if code_map_dict else str(target_dir)
    project_env = detect_project_environment(project_root_str, code_map_dict)

    # Preload retrieved code artifacts by group name
    retrieved_dir = target_dir / "code retrieved"
    retrieved_data_by_group: Dict[str, dict] = {}
    if retrieved_dir.exists() and retrieved_dir.is_dir():
        for rf in retrieved_dir.glob("*.json"):
            try:
                retrieved_data_by_group[rf.stem] = json.loads(rf.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                pass

    results: List[Dict[str, Any]] = []
    # Walk subfolders
    for group_dir in scripts_root.iterdir():
        if group_dir.is_dir():
            group_name = group_dir.name
            ret_data = retrieved_data_by_group.get(group_name) or {}
            test_cases = ret_data.get("testCases", [])

            # Check if manifest exists
            manifest_file = group_dir / "manifest.json"
            manifest_by_filename: Dict[str, dict] = {}
            if manifest_file.exists():
                try:
                    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8", errors="ignore"))
                    for s in manifest_data.get("scripts", []):
                        fn = s.get("filename")
                        if fn:
                            manifest_by_filename[fn] = s
                    manifest_tc_ids = {s.get("testCaseId") for s in manifest_data.get("scripts", []) if s.get("testCaseId")}
                except Exception:
                    pass

            for script_file in group_dir.iterdir():
                if script_file.is_file() and script_file.name not in ["manifest.json", "summary.json"]:
                    # If manifest exists and this file is legacy TC-xxx superseded by manifest, skip it
                    if manifest_file.exists() and script_file.name not in manifest_by_filename:
                        manifest_tc_ids = {s.get("testCaseId") for s in manifest_data.get("scripts", []) if s.get("testCaseId")}
                        if script_file.stem in manifest_tc_ids:
                            continue

                    try:
                        content = script_file.read_text(encoding="utf-8", errors="ignore")
                        fn_entry = manifest_by_filename.get(script_file.name, {})

                        # Extract metadata from header comments or manifest
                        tc_id = fn_entry.get("testCaseId")
                        tc_title = fn_entry.get("testCaseTitle")

                        if not tc_id:
                            m_id = re.search(r"(?://|#)\s*Test Case ID:\s*([A-Za-z0-9_-]+)", content)
                            tc_id = m_id.group(1).strip() if m_id else script_file.stem

                        if not tc_title:
                            m_title = re.search(r"(?://|#)\s*Test Case Title:\s*(.+)", content)
                            tc_title = m_title.group(1).strip() if m_title else script_file.stem

                        # Find corresponding test case
                        matched_tc = None
                        if test_cases:
                            matched_tc = next((tc for tc in test_cases if tc.get("testCaseId") == tc_id), None)
                            if not matched_tc:
                                matched_tc = next((tc for tc in test_cases if sanitize_test_title_to_filename(tc.get("title", "")) == script_file.stem), None)

                        if matched_tc:
                            # Extract source code for target
                            tc_source_files = matched_tc.get("source", {}).get("files", [])
                            rd_source_files = ret_data.get("source", {}).get("files", [])
                            source_files = tc_source_files if (tc_source_files and isinstance(tc_source_files, list)) else rd_source_files

                            target = matched_tc.get("target") or matched_tc.get("unit") or {}
                            target_file = target.get("file", "")
                            target_symbol = target.get("symbol", "")
                            target_base = Path(target_file).name.lower() if target_file else ""
                            source_code = ""
                            if source_files and isinstance(source_files, list):
                                for sf in source_files:
                                    if isinstance(sf, dict):
                                        sf_path = sf.get("path", "")
                                        if sf_path and (sf_path == target_file or Path(sf_path).name.lower() == target_base):
                                            source_code = sf.get("sourceCode", "")
                                            if source_code:
                                                break
                                if not source_code:
                                    for sf in source_files:
                                        if isinstance(sf, dict) and sf.get("reason") == "primary_target":
                                            source_code = sf.get("sourceCode", "")
                                            if source_code:
                                                break
                                if not source_code and len(source_files) > 0 and isinstance(source_files[0], dict):
                                    source_code = source_files[0].get("sourceCode", "")

                            descriptor = inspect_source_target(source_code, target_symbol, target_file, project_env.language)
                            import_res = resolve_target_import(
                                target_file=target_file,
                                target_symbol=target_symbol,
                                script_output_dir=group_dir,
                                project_root=project_env.project_root,
                                descriptor=descriptor,
                                language=project_env.language,
                                source_code=source_code,
                            )
                            is_val, cleaned, errs, failure_type = validate_generated_script(
                                raw_code=content,
                                test_case=matched_tc,
                                retrieved_data=ret_data,
                                project_env=project_env,
                                descriptor=descriptor,
                                script_dir=group_dir,
                                resolved_import=import_res,
                                script_extension=script_file.suffix,
                                source_code=source_code,
                            )
                            status = "success" if is_val else "VALIDATION_FAILED"
                        else:
                            # Fallback to manifest metadata or generic validation
                            status = fn_entry.get("status", "success")
                            failure_type = fn_entry.get("failureType")
                            errs = list(fn_entry.get("errors", []))

                            delim_err = _check_delimiter_balance(content, project_env.language)
                            if delim_err:
                                status = "VALIDATION_FAILED"
                                failure_type = failure_type or "SCRIPT_SYNTAX_ERROR"
                                if delim_err not in errs:
                                    errs.append(delim_err)

                            if _detect_trivial_assertions(content, project_env.language) and not _has_substantive_assertions(content, project_env.language):
                                status = "VALIDATION_FAILED"
                                failure_type = failure_type or "TRIVIAL_ASSERTION_DETECTED"
                                triv_msg = "Script contains trivial or tautological assertion without substantive verification."
                                if triv_msg not in errs:
                                    errs.append(triv_msg)

                        results.append({
                            "group": group_name,
                            "testCaseId": tc_id,
                            "testCaseTitle": tc_title,
                            "fileName": script_file.name,
                            "filePath": str(script_file),
                            "content": content,
                            "extension": script_file.suffix,
                            "lineCount": len(content.splitlines()),
                            "status": status,
                            "failureType": failure_type,
                            "errors": errs,
                        })
                    except Exception as e:
                        logger.warning("Could not read or validate test script %s: %s", script_file, e)

    return results


async def generate_all_test_scripts(
    code_map_source: Union[str, Path, dict],
    force: bool = False,
    analysis_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Reads existing `code retrieved/` artifacts, detects project test environment,
    generates 1:1 test scripts for all test cases using human-readable filenames,
    and stores them under `<analysis-output-root>/test scripts/<retrieved-code-name>/`.
    """
    target_dir: Optional[Path] = None
    code_map_path: Optional[Path] = None
    code_map_dict: Optional[dict] = None

    if isinstance(code_map_source, (str, Path)):
        p = Path(code_map_source)
        if p.is_file():
            code_map_path = p
            target_dir = p.parent
        else:
            target_dir = p
            code_map_path = p / "repository-code-map.json"

        if code_map_path.exists():
            try:
                code_map_dict = json.loads(code_map_path.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                code_map_dict = None
    elif isinstance(code_map_source, dict):
        code_map_dict = code_map_source
        cand = code_map_source.get("project", {}).get("path")
        if cand:
            target_dir = Path(cand).parent

    if not target_dir:
        raise ValueError("Could not resolve target artifact directory from code_map_source.")

    retrieved_dir = target_dir / "code retrieved"
    if not retrieved_dir.exists() or not retrieved_dir.is_dir():
        return {
            "status": "not_found",
            "message": f"No 'code retrieved' directory found at {retrieved_dir}. Please run Code Retrieval first.",
            "generatedCount": 0,
            "scripts": [],
        }

    retrieved_files = list(retrieved_dir.glob("*.json"))
    if not retrieved_files:
        return {
            "status": "not_found",
            "message": f"No retrieved code JSON files found in {retrieved_dir}.",
            "generatedCount": 0,
            "scripts": [],
        }

    # Detect project environment
    project_root_str = code_map_dict.get("project", {}).get("path") if code_map_dict else None
    project_env = detect_project_environment(project_root_str, code_map_dict)
    backend_logger.info(
        f"DETECTED ENVIRONMENT: Language={project_env.language}, Framework={project_env.test_framework}, "
        f"Ext={project_env.file_extension}, BuildTool={project_env.build_tool}"
    )

    scripts_root = target_dir / "test scripts"
    scripts_root.mkdir(parents=True, exist_ok=True)

    all_generated: List[Dict[str, Any]] = []
    total_cases = 0

    for ret_file in retrieved_files:
        ret_stem = ret_file.stem
        group_output_dir = scripts_root / ret_stem
        group_output_dir.mkdir(parents=True, exist_ok=True)

        try:
            ret_data = json.loads(ret_file.read_text(encoding="utf-8", errors="ignore"))
        except Exception as e:
            backend_logger.warning(f"Failed to read retrieved code file {ret_file}: {e}")
            continue

        test_cases = ret_data.get("testCases", [])
        if not test_cases:
            test_cases = [ret_data]

        group_scripts = []
        # Track existing filenames in this group to avoid collisions
        existing_filenames_in_group: Set[str] = set()

        # First populate existing files on disk
        for existing_file in group_output_dir.iterdir():
            if existing_file.is_file() and existing_file.name not in ["manifest.json", "summary.json"]:
                existing_filenames_in_group.add(existing_file.name)

        for tc in test_cases:
            total_cases += 1
            res = await generate_script_for_single_test_case(
                test_case=tc,
                retrieved_data=ret_data,
                project_env=project_env,
                target_output_dir=group_output_dir,
                analysis_id=analysis_id,
                force=force,
                existing_filenames=existing_filenames_in_group,
            )
            res["group"] = ret_stem
            if res.get("filename"):
                existing_filenames_in_group.add(res["filename"])
            group_scripts.append(res)
            all_generated.append(res)

        # Write group manifest
        manifest_path = group_output_dir / "manifest.json"
        try:
            manifest_data = {
                "group": ret_stem,
                "projectEnvironment": project_env.to_dict(),
                "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "totalTestCases": len(test_cases),
                "successful": sum(1 for s in group_scripts if s.get("status") == "success"),
                "failed": sum(1 for s in group_scripts if s.get("status") != "success"),
                "scripts": [
                    {
                        "testCaseId": s.get("testCaseId"),
                        "testCaseTitle": s.get("testCaseTitle"),
                        "filename": s.get("filename"),
                        "status": s.get("status"),
                        "failureType": s.get("failureType"),
                        "filePath": s.get("filePath"),
                        "errors": s.get("errors", []),
                    }
                    for s in group_scripts
                ],
            }
            manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
        except Exception as e:
            backend_logger.warning(f"Could not save manifest at {manifest_path}: {e}")

    # Build final summary
    successful_count = sum(1 for s in all_generated if s.get("status") == "success")
    return {
        "status": "success",
        "generatedCount": len(all_generated),
        "successfulCount": successful_count,
        "failedCount": len(all_generated) - successful_count,
        "projectEnvironment": project_env.to_dict(),
        "scriptsDir": str(scripts_root),
        "scripts": all_generated,
        "message": f"Successfully generated {successful_count}/{total_cases} test scripts.",
    }

"""
Dynamic Test Execution Runner Service
======================================
Safely executes validated test scripts using the project's detected test runner,
capturing real execution stdout, stderr, exit codes, and durations.
Never fabricates results.
"""

import os
import re
import sys
import json
import time
import shutil
import asyncio
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union

from app.logger import backend_logger
from app.services.feature_context_generator import load_code_map
from app.services.context_selector import resolve_project_root, normalize_path
from app.services.framework_detector import detect_project_environment
from app.services.import_resolver import (
    detect_subproject_root,
    validate_script_imports,
)
from app.services.service_manager import (
    detect_application_startup,
    detect_application_port,
    is_port_in_use,
    start_service_if_needed,
    stop_background_service,
    redact_secrets,
    RunningServiceContext,
)

DEFAULT_TEST_TIMEOUT_S: float = float(os.getenv("TEST_EXECUTION_TIMEOUT_SECONDS", "300.0"))


def categorize_execution_failure(
    exit_code: int,
    stdout: str,
    stderr: str,
    runner: str = "",
    stage: Optional[str] = None,
    exception: Optional[Exception] = None,
) -> str:
    """
    Categorizes the execution failure into one of the canonical 19 categories:
    - SCRIPT_NOT_FOUND
    - INVALID_SCRIPT
    - IMPORT_RESOLUTION_ERROR
    - RUNNER_NOT_CONFIGURED
    - RUNNER_NOT_FOUND
    - INVALID_RUNNER_INVOCATION
    - INVALID_WORKING_DIRECTORY
    - DEPENDENCY_ERROR
    - ENVIRONMENT_ERROR
    - APPLICATION_STARTUP_ERROR
    - APPLICATION_NOT_READY
    - API_CONNECTION_ERROR
    - TEST_DISCOVERY_ERROR
    - TEST_COLLECTION_ERROR
    - TEST_SETUP_ERROR
    - ASSERTION_FAILURE
    - TIMEOUT
    - PROCESS_LAUNCH_ERROR
    - EXECUTION_ERROR
    """
    if exit_code == 0:
        return ""

    if exit_code == 124:
        return "TIMEOUT"

    combined = f"{stdout}\n{stderr}".lower()

    # 1. Timeout detection
    if "timed out" in combined or "timeoutexpired" in combined or stage == "timeout":
        return "TIMEOUT"

    # 2. Stage-based application startup / readiness
    if stage == "startup":
        if "did not become ready" in combined or "timeout" in combined:
            return "APPLICATION_NOT_READY"
        return "APPLICATION_STARTUP_ERROR"

    if stage == "readiness" or "did not become ready on" in combined or "application not ready" in combined:
        return "APPLICATION_NOT_READY"

    # 3. Exceptions
    if exception is not None:
        if isinstance(exception, (PermissionError, OSError)):
            return "PROCESS_LAUNCH_ERROR"
        if isinstance(exception, FileNotFoundError):
            return "RUNNER_NOT_FOUND"

    # 4. Process launch errors
    if (
        "permissionerror" in combined
        or "access is denied" in combined
        or "process_launch_error" in combined
        or "failed to launch process" in combined
    ):
        return "PROCESS_LAUNCH_ERROR"

    # 5. Invalid working directory
    if (
        "effective working directory" in combined
        or "invalid working directory" in combined
        or "working directory does not exist" in combined
        or ("no such file or directory" in combined and "directory" in combined)
    ):
        return "INVALID_WORKING_DIRECTORY"

    # 6. Script not found or invalid
    if "script_not_found" in combined or ("script file" in combined and "does not exist" in combined):
        return "SCRIPT_NOT_FOUND"
    if "invalid_script" in combined or "script file is empty" in combined:
        return "INVALID_SCRIPT"

    # 7. Runner not configured
    if (
        "runner not configured" in combined
        or "no test runner configured" in combined
        or "npm err! missing script: test" in combined
        or "no test script specified" in combined
    ):
        return "RUNNER_NOT_CONFIGURED"

    # 8. Runner missing / not found
    if (
        "is not recognized as an internal or external command" in combined
        or "cannot find the path specified" in combined
        or "command not found" in combined
        or "executable not found" in combined
        or "runner not available" in combined
        or ("runner executable" in combined and "not found" in combined)
        or ("no such file or directory" in combined and any(r in combined for r in ["mvn", "vitest", "jest", "pytest", "cargo", "go", "npm", "yarn", "pnpm"]))
    ):
        return "RUNNER_NOT_FOUND"

    # 9. Invalid runner invocation
    if (
        "unrecognized argument" in combined
        or "unrecognized arguments" in combined
        or "unknown option" in combined
        or "invalid option" in combined
        or "error: no such option" in combined
        or "usage: pytest" in combined
        or "usage: jest" in combined
        or "usage: vitest" in combined
        or "invalid flag" in combined
    ):
        return "INVALID_RUNNER_INVOCATION"

    # 10. Environment error
    if (
        ("java_home" in combined and ("not set" in combined or "not defined" in combined or "is not" in combined))
        or "no java runtime present" in combined
        or ("environment variable" in combined and ("missing" in combined or "not defined" in combined or "not set" in combined))
        or ("python version" in combined and ("unsupported" in combined or "not supported" in combined or "requires" in combined))
    ):
        return "ENVIRONMENT_ERROR"

    # 11. Application startup or readiness failures in output
    if "failed to launch application service" in combined or "application startup error" in combined:
        return "APPLICATION_STARTUP_ERROR"

    # 12. API Connection errors (live server refused / unreachable)
    if (
        "econnrefused" in combined
        or "connectionrefusederror" in combined
        or "connection refused" in combined
        or "failed to establish a new connection" in combined
        or "max retries exceeded with url" in combined
        or "connect: connection refused" in combined
        or "cannot connect to" in combined
        or "newconnectionerror" in combined
        or "econnreset" in combined
        or "socket hang up" in combined
        or "winerror 10061" in combined
        or "actively refused" in combined
        or "connectionerror" in combined
    ):
        return "API_CONNECTION_ERROR"

    # 13. Test Setup / Fixture failures
    if (
        ("fixture" in combined and "failed" in combined)
        or "error at fixture" in combined
        or "@beforeeach" in combined
        or "@beforeall" in combined
        or "beforeall failed" in combined
        or "beforeeach failed" in combined
        or "setup_method failed" in combined
        or "error during test setup" in combined
    ):
        return "TEST_SETUP_ERROR"

    # 14. Dependency missing (third-party package)
    if (
        "modulenotfounderror: no module named" in combined
        or "modulenotfounderror" in combined
        or "cannot find module" in combined
        or "err_module_not_found" in combined
        or ("package" in combined and "does not exist" in combined)
        or "failed to resolve import" in combined
        or "cannot find symbol" in combined
    ):
        return "DEPENDENCY_ERROR"

    # 15. Import resolution error (project-local imports)
    if (
        "importerror: cannot import name" in combined
        or "cannot import name" in combined
        or "import_resolution_error" in combined
        or "failed to resolve local import" in combined
        or "cannot resolve project-local import" in combined
        or "unresolved local import" in combined
        or "importerror" in combined
    ):
        return "IMPORT_RESOLUTION_ERROR"

    # 16. Test collection / compilation error
    if (
        "collection error" in combined
        or "errors during collection" in combined
        or "collecting ... error" in combined
        or "error collecting" in combined
        or "compilation failure" in combined
        or "compilation error" in combined
        or "missingprojectexception" in combined
    ):
        return "TEST_COLLECTION_ERROR"

    # 17. Test discovery error (runner ran, but found 0 tests to run)
    if (
        "no tests to run" in combined
        or "no tests found" in combined
        or "0 tests collected" in combined
        or "collected 0 items" in combined
        or "no test classes found" in combined
        or "no tests were found" in combined
        or "empty test suite" in combined
        or "no sources to compile" in combined
        or "no tests ran" in combined
    ):
        return "TEST_DISCOVERY_ERROR"

    # 18. Assertion failure
    if (
        "assertionerror" in combined
        or ("assert " in combined and any(op in combined for op in ["==", "!=", ">", "<", " is ", " in "]))
        or "failed (failures=" in combined
        or "failures:" in combined
        or "comparisonfailure" in combined
        or ("expect(" in combined and any(e in combined for e in ["tobe", "toequal"]))
        or ("expected" in combined and "received" in combined)
        or "fail:" in combined
        or "[fail]" in combined
        or "fail" in combined
    ):
        return "ASSERTION_FAILURE"

    # 19. Unhandled runtime exception in test
    return "EXECUTION_ERROR"


def extract_failure_evidence(stdout: str, stderr: str, failure_type: str) -> Optional[str]:
    """
    Extracts a concise, 1-3 line evidence snippet directly explaining the failure.
    """
    raw_lines = (stderr + "\n" + stdout).splitlines()
    lines = []
    for l in raw_lines:
        s = l.strip()
        if not s:
            continue
        # Strip pytest 'E   ' prefix if present
        if s.startswith("E   ") or s.startswith("E  "):
            s = s[4:].strip()
        lines.append(s)

    if not lines:
        return None

    evidence_patterns = {
        "API_CONNECTION_ERROR": [r"(?i)connection.*refused", r"(?i)failed to establish.*connection", r"(?i)econnrefused", r"(?i)max retries exceeded", r"(?i)actively refused", r"(?i)winerror 10061"],
        "ASSERTION_FAILURE": [r"(?i)assertionerror.*", r"(?i)assert\s+.*", r"(?i)expected:?.*received:?.*", r"(?i)comparisonfailure.*", r"(?i)failures:\s*\d+"],
        "IMPORT_RESOLUTION_ERROR": [r"(?i)cannot import name.*", r"(?i)cannot resolve.*import", r"(?i)importerror:.*"],
        "DEPENDENCY_ERROR": [r"(?i)modulenotfounderror:?.*", r"(?i)cannot find module.*", r"(?i)package\s+.*does not exist", r"(?i)no module named\s+.*"],
        "APPLICATION_STARTUP_ERROR": [r"(?i)failed to launch application.*", r"(?i)process exited prematurely.*", r"(?i)startup.*error"],
        "APPLICATION_NOT_READY": [r"(?i)service did not become ready.*", r"(?i)port.*timed out"],
        "RUNNER_NOT_FOUND": [r"(?i)is not recognized as an internal.*", r"(?i)command not found.*", r"(?i)executable not found.*", r"(?i)test runner executable.*not found"],
        "RUNNER_NOT_CONFIGURED": [r"(?i)no test runner configured.*", r"(?i)no test script specified.*"],
        "TEST_SETUP_ERROR": [r"(?i)fixture.*failed", r"(?i)before(each|all).*failed", r"(?i)setup_method.*failed"],
        "TEST_DISCOVERY_ERROR": [r"(?i)no tests to run.*", r"(?i)no tests found.*", r"(?i)0 tests collected.*", r"(?i)no test classes found.*", r"(?i)no tests ran.*"],
        "TEST_COLLECTION_ERROR": [r"(?i)collection error.*", r"(?i)compilation failure.*", r"(?i)error collecting.*"],
        "TIMEOUT": [r"(?i)timed out.*", r"(?i)timeout.*expired"],
    }

    patterns = evidence_patterns.get(failure_type, [])
    for pat in patterns:
        for line in lines:
            if re.search(pat, line):
                return line[:300]

    err_lines = [l.strip() for l in stderr.splitlines() if l.strip()]
    if err_lines:
        return err_lines[-1][:300]
    return lines[-1][:300]


def detect_if_script_needs_server(
    script_path: Optional[Path] = None,
    script_content: str = "",
    test_type: str = "unit"
) -> bool:
    """
    Determines whether a test script requires a live application server to be running.
    - Unit tests: Always False (in-memory, direct code execution).
    - Functional / E2E tests: True if browser automation or HTTP/network requests are made.
    - API tests:
        - True if live network calls are made (http://localhost, http://127.0.0.1, requests, httpx, fetch, axios to URL).
        - False if in-process client is used (TestClient(app), app.test_client(), supertest(app) with imported app).
    """
    stype = (test_type or "unit").lower()
    if stype == "unit":
        return False

    content = script_content
    if not content and script_path and script_path.exists():
        try:
            content = script_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass

    content_lower = content.lower()

    # Browser automation always requires live server or running target
    if any(b in content_lower for b in ["playwright", "puppeteer", "selenium", "cypress"]):
        return True

    # Check for live server URL targets
    live_target_regexes = [
        r"https?://localhost",
        r"https?://127\.0\.0\.1",
        r"https?://0\.0\.0\.0",
        r"localhost:\d{2,5}",
        r"127\.0\.0\.1:\d{2,5}",
    ]
    has_live_target = any(re.search(rx, content_lower) for rx in live_target_regexes)

    # In-process client checks
    in_process_indicators = [
        "testclient(",
        "test_client()",
        "client = testclient",
        "app.test_client",
    ]
    has_supertest_inprocess = bool(re.search(r"request\s*\(\s*app\s*\)", content)) or bool(re.search(r"supertest\s*\(\s*app\s*\)", content))

    has_in_process_client = any(ind in content_lower for ind in in_process_indicators) or has_supertest_inprocess

    if has_live_target:
        return True

    if stype in ["functional", "e2e"]:
        return not has_in_process_client

    if stype == "api":
        if has_in_process_client:
            return False
        if any(cli in content_lower for cli in ["requests.", "httpx.", "axios.", "fetch(", "http.client", "urllib.request"]):
            return True

    return False


def detect_test_runner(
    project_root: Optional[Path] = None,
    language: str = "",
    framework: str = "",
    target_file: Optional[str] = None,
    cwd: Optional[Path] = None,
) -> Tuple[Optional[str], Optional[List[str]], Optional[str]]:
    """
    Detects the appropriate test runner command for the project.
    Returns (runner_name, command_args, error_reason).
    """
    lang_lower = (language or "").lower()
    fw_lower = (framework or "").lower()

    if lang_lower == "python" or not lang_lower:
        # Check if pytest is available in current python environment
        try:
            res = subprocess.run([sys.executable, "-m", "pytest", "--version"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                return "pytest", [sys.executable, "-m", "pytest", "-v"], None
        except Exception:
            pass

        # Check system / PATH pytest binary
        pytest_bin = shutil.which("pytest") or shutil.which("pytest.exe")
        if pytest_bin:
            return "pytest", [pytest_bin, "-v"], None

        return "unittest", [sys.executable, "-m", "unittest", "discover"], None

    elif lang_lower in ["javascript", "typescript", "jsx", "tsx", "js", "ts"]:
        # Check node tooling in project
        npx_bin = shutil.which("npx") or shutil.which("npx.cmd")
        npm_bin = shutil.which("npm") or shutil.which("npm.cmd")
        yarn_bin = shutil.which("yarn") or shutil.which("yarn.cmd")
        pnpm_bin = shutil.which("pnpm") or shutil.which("pnpm.cmd")

        # Determine effective search directory (check subprojects e.g. backend/ or frontend/)
        search_dirs: List[Path] = []
        if cwd and Path(cwd).exists():
            search_dirs.append(Path(cwd))
        if project_root and Path(project_root).exists():
            p_root = Path(project_root)
            if target_file:
                sub = detect_subproject_root(p_root / target_file, p_root)
                if sub and sub not in search_dirs:
                    search_dirs.insert(0, sub)
            if p_root not in search_dirs:
                search_dirs.append(p_root)
            # Also check direct child directories with package.json
            for child in p_root.iterdir():
                if child.is_dir() and (child / "package.json").exists() and child not in search_dirs:
                    search_dirs.append(child)

        # Inspect candidate directories for package.json and local binaries
        for s_dir in search_dirs:
            pkg_file = s_dir / "package.json"
            if not pkg_file.exists():
                continue

            try:
                pkg_data = json.loads(pkg_file.read_text(encoding="utf-8", errors="ignore"))
                all_deps = {
                    **pkg_data.get("dependencies", {}),
                    **pkg_data.get("devDependencies", {})
                }
                scripts = pkg_data.get("scripts", {})

                # Check lockfile preference
                is_pnpm = (s_dir / "pnpm-lock.yaml").exists() and pnpm_bin
                is_yarn = (s_dir / "yarn.lock").exists() and yarn_bin

                # Check local node_modules/.bin binaries
                vitest_bin = shutil.which("vitest", path=str(s_dir / "node_modules" / ".bin"))
                jest_bin = shutil.which("jest", path=str(s_dir / "node_modules" / ".bin"))

                if "vitest" in all_deps or "vitest" in scripts.get("test", ""):
                    if vitest_bin:
                        return "vitest", [vitest_bin, "run"], None
                    if npx_bin:
                        return "vitest", [npx_bin, "vitest", "run"], None
                if "jest" in all_deps or "jest" in scripts.get("test", ""):
                    if jest_bin:
                        return "jest", [jest_bin], None
                    if npx_bin:
                        return "jest", [npx_bin, "jest"], None
                if "test" in scripts:
                    if is_pnpm:
                        return "pnpm test", [pnpm_bin, "test", "--"], None
                    if is_yarn:
                        return "yarn test", [yarn_bin, "test"], None
                    if npm_bin:
                        return "npm test", [npm_bin, "test", "--"], None
            except Exception:
                pass

        # If package.json exists in some search_dir but no test runner or script is configured:
        if search_dirs and any((d / "package.json").exists() for d in search_dirs):
            # Check if vitest or jest is globally available
            global_vitest = shutil.which("vitest")
            if global_vitest:
                return "vitest", [global_vitest, "run"], None
            global_jest = shutil.which("jest")
            if global_jest:
                return "jest", [global_jest], None

            return None, None, "No test runner configured in package.json (no vitest/jest dependencies or test script). Install vitest/jest or configure test script to execute tests."

        # If no package.json at all:
        return None, None, "No package.json found in project or subprojects to run JS/TS tests."

    elif lang_lower == "go":
        go_bin = shutil.which("go")
        if go_bin:
            return "go test", [go_bin, "test", "-v"], None
        return None, None, "Go compiler/test runner ('go') is not installed or not in PATH."

    elif lang_lower == "rust":
        cargo_bin = shutil.which("cargo")
        if cargo_bin:
            return "cargo test", [cargo_bin, "test"], None
        return None, None, "Rust package manager ('cargo') is not installed or not in PATH."

    elif lang_lower == "java":
        mvn_bin = shutil.which("mvn") or shutil.which("mvn.cmd")
        if mvn_bin:
            return "mvn test", [mvn_bin, "test"], None
        gradle_bin = shutil.which("gradle") or shutil.which("gradle.cmd")
        if gradle_bin:
            return "gradle test", [gradle_bin, "test"], None
        return None, None, "Java test runner ('mvn' or 'gradle') is not installed or not in PATH."

    return None, None, f"No supported test runner found for language '{language}'."


def resolve_maven_test_source_dir(pom_dir: Path) -> Path:
    """
    Dynamically determines the test source directory for a Maven project.
    Inspects pom.xml for <testSourceDirectory>, or defaults to src/test/java.
    """
    pom_file = pom_dir / "pom.xml"
    if pom_file.exists():
        try:
            content = pom_file.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"<testSourceDirectory\s*>(.*?)</testSourceDirectory\s*>", content, re.DOTALL)
            if m:
                raw_path = m.group(1).strip()
                raw_path = raw_path.replace("${project.basedir}", str(pom_dir)).replace("${basedir}", str(pom_dir))
                p = Path(raw_path)
                if not p.is_absolute():
                    p = pom_dir / p
                return p
        except Exception:
            pass
    return pom_dir / "src" / "test" / "java"


def extract_java_test_class_name(content: str, fallback_stem: str = "") -> str:
    """
    Extracts the actual test class name from Java source content.
    Prioritizes classes ending in Test/Tests/TestCase or starting with Test.
    """
    classes = re.findall(r"\bclass\s+([A-Za-z0-9_$]+)\b", content)
    test_classes = [c for c in classes if re.search(r"(?:Test|Tests|TestCase)$|^Test", c)]
    if test_classes:
        return test_classes[0]
    if classes:
        return classes[0]
    clean_stem = re.sub(r"[^a-zA-Z0-9_]", "", fallback_stem)
    return clean_stem or "*"


def stage_maven_test_script(script_path: Path, pom_dir: Path, class_name: str, content: str) -> Optional[Path]:
    """
    Safely stages a Java test script into the Maven project's test source directory.
    Extracts package declaration if present to place it in the matching subfolder.
    Only stages .java files, leaving original generated artifacts unmodified.
    """
    if script_path.suffix.lower() != ".java":
        return None
    if not class_name or class_name == "*":
        return None

    test_src_dir = resolve_maven_test_source_dir(pom_dir)

    # Don't stage if script is already inside the project's test source directory
    try:
        script_path.relative_to(test_src_dir)
        return script_path
    except ValueError:
        pass

    # Check for package declaration in Java source
    m_pkg = re.search(r"^\s*package\s+([a-zA-Z0-9_.]+)\s*;", content, re.MULTILINE)
    if m_pkg:
        pkg_parts = m_pkg.group(1).split(".")
        dest_dir = test_src_dir.joinpath(*pkg_parts)
    else:
        dest_dir = test_src_dir

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        target_file = dest_dir / f"{class_name}.java"
        shutil.copyfile(script_path, target_file)
        backend_logger.info(f"[TestExecutor] Staged Java test script {script_path.name} -> {target_file}")
        return target_file
    except Exception as exc:
        backend_logger.warning(f"[TestExecutor] Failed to stage Maven test script {script_path} to {dest_dir}: {exc}")
        return None


def execute_single_test_script(
    script_path: Path,
    runner_cmd: List[str],
    cwd: Path,
    timeout_s: float = DEFAULT_TEST_TIMEOUT_S,
    target_file: Optional[str] = None,
    language: str = "",
    runner_name: str = "",
    test_type: str = "unit",
    service_ctx: Optional[RunningServiceContext] = None,
) -> Dict[str, Any]:
    """
    Safely executes a single test script using subprocess, capturing real output.
    Pre-validates script existence, working directory, runner executable, and local imports.
    Sets necessary environment variables (PYTHONPATH, NODE_PATH).
    Categorizes failures with structured failureType and extracts evidence.
    """
    t0 = time.time()
    effective_cwd = cwd

    # 1. Pre-execution Validation: Base working directory existence
    if cwd and not cwd.exists():
        msg = f"Effective working directory '{cwd}' does not exist."
        return {
            "exitCode": 1,
            "status": "error",
            "failureType": "INVALID_WORKING_DIRECTORY",
            "stdout": "",
            "stderr": msg,
            "durationMs": 0.0,
            "runner": runner_name or (runner_cmd[0] if runner_cmd else "none"),
            "command": None,
            "workingDirectory": str(cwd),
            "projectRoot": str(cwd),
            "startupCommand": None,
            "stage": "validation",
            "evidence": msg,
        }

    # 2. Dynamically locate subproject working directory if applicable
    if target_file and cwd:
        sub = detect_subproject_root(cwd / target_file, cwd)
        if sub and sub.exists():
            effective_cwd = sub
    elif cwd:
        sub = detect_subproject_root(script_path.parent, cwd)
        if sub and sub.exists():
            effective_cwd = sub

    # 3. Pre-execution Validation: Script file existence
    if not script_path.exists():
        msg = f"Script file '{script_path}' does not exist on disk."
        return {
            "exitCode": 1,
            "status": "error",
            "failureType": "SCRIPT_NOT_FOUND",
            "stdout": "",
            "stderr": msg,
            "durationMs": 0.0,
            "runner": runner_name or (runner_cmd[0] if runner_cmd else "none"),
            "command": None,
            "workingDirectory": str(effective_cwd),
            "projectRoot": str(cwd),
            "startupCommand": None,
            "stage": "validation",
            "evidence": msg,
        }

    # 4. Pre-execution Validation: Script emptiness / invalid script
    try:
        if script_path.stat().st_size == 0:
            msg = f"Script file '{script_path.name}' is empty (0 bytes)."
            return {
                "exitCode": 1,
                "status": "error",
                "failureType": "INVALID_SCRIPT",
                "stdout": "",
                "stderr": msg,
                "durationMs": 0.0,
                "runner": runner_name or (runner_cmd[0] if runner_cmd else "none"),
                "command": None,
                "workingDirectory": str(effective_cwd),
                "projectRoot": str(cwd),
                "startupCommand": None,
                "stage": "validation",
                "evidence": msg,
            }
    except Exception:
        pass

    # 5. Pre-execution Validation: Effective working directory existence
    if not effective_cwd.exists():
        msg = f"Effective working directory '{effective_cwd}' does not exist."
        return {
            "exitCode": 1,
            "status": "error",
            "failureType": "INVALID_WORKING_DIRECTORY",
            "stdout": "",
            "stderr": msg,
            "durationMs": 0.0,
            "runner": runner_name or (runner_cmd[0] if runner_cmd else "none"),
            "command": None,
            "workingDirectory": str(effective_cwd),
            "projectRoot": str(cwd),
            "startupCommand": None,
            "stage": "validation",
            "evidence": msg,
        }

    # 6. Pre-execution Validation: Runner executable existence
    runner_bin = runner_cmd[0] if runner_cmd else None
    if not runner_bin or (not Path(runner_bin).exists() and not shutil.which(runner_bin)):
        msg = f"Test runner executable '{runner_bin}' not found on disk or in PATH."
        return {
            "exitCode": 1,
            "status": "error",
            "failureType": "RUNNER_NOT_FOUND",
            "stdout": "",
            "stderr": msg,
            "durationMs": 0.0,
            "runner": runner_name or runner_bin or "none",
            "command": None,
            "workingDirectory": str(effective_cwd),
            "projectRoot": str(cwd),
            "startupCommand": None,
            "stage": "validation",
            "evidence": msg,
        }

    # 7. Pre-execution Validation: Local imports resolution
    val_imp = validate_script_imports(
        script_path=script_path,
        project_root=cwd,
        language=language,
        target_file=target_file,
    )
    if not val_imp["valid"] and val_imp.get("failureType") == "IMPORT_RESOLUTION_ERROR":
        cand_str = ("\n  - " + "\n  - ".join(val_imp.get("candidate_paths", [])[:5])) if val_imp.get("candidate_paths") else ""
        return {
            "exitCode": 1,
            "status": "failed",
            "failureType": "IMPORT_RESOLUTION_ERROR",
            "stdout": "",
            "stderr": f"{val_imp['reason']}{cand_str}",
            "durationMs": 0.0,
            "runner": runner_name or (runner_cmd[0] if runner_cmd else "none"),
            "command": None,
            "workingDirectory": str(effective_cwd),
            "projectRoot": str(cwd),
            "startupCommand": None,
            "stage": "validation",
            "evidence": val_imp.get("reason"),
        }

    # 8. Server Requirements Check: Live server needed?
    needs_server = detect_if_script_needs_server(script_path=script_path, test_type=test_type)
    local_service_ctx: Optional[RunningServiceContext] = None
    active_ctx = service_ctx
    startup_cmd_str: Optional[str] = None

    if active_ctx and active_ctx.cmd:
        startup_cmd_str = redact_secrets(" ".join(active_ctx.cmd))

    if needs_server and not active_ctx:
        # Start service locally for this single script run
        s_ctx, ftype, fmsg = start_service_if_needed(cwd, effective_cwd, language)
        if not s_ctx:
            return {
                "exitCode": 1,
                "status": "error",
                "failureType": ftype or "APPLICATION_STARTUP_ERROR",
                "stdout": "",
                "stderr": fmsg or "Application failed to start.",
                "durationMs": 0.0,
                "runner": runner_name or (runner_cmd[0] if runner_cmd else "none"),
                "command": None,
                "workingDirectory": str(effective_cwd),
                "projectRoot": str(cwd),
                "startupCommand": None,
                "stage": "startup",
                "evidence": fmsg.splitlines()[0] if fmsg else None,
            }
        local_service_ctx = s_ctx
        active_ctx = s_ctx
        if s_ctx.cmd:
            startup_cmd_str = redact_secrets(" ".join(s_ctx.cmd))

    # 9. Build command line
    cmd = list(runner_cmd)
    is_maven = any("mvn" in part.lower() for part in runner_cmd)
    if is_maven:
        pom_dir = effective_cwd
        if not (pom_dir / "pom.xml").exists():
            for parent in [effective_cwd.parent, *effective_cwd.parents]:
                if (parent / "pom.xml").exists():
                    pom_dir = parent
                    break
            if not (pom_dir / "pom.xml").exists():
                for sub in cwd.glob("**/pom.xml"):
                    pom_dir = sub.parent
                    break
        effective_cwd = pom_dir

        class_name = None
        content = ""
        try:
            content = script_path.read_text(encoding="utf-8", errors="ignore")
            class_name = extract_java_test_class_name(content, script_path.stem)
        except Exception:
            clean_stem = re.sub(r"[^a-zA-Z0-9_]", "", script_path.stem)
            class_name = clean_stem or "*"

        cmd.append(f"-Dtest={class_name}")

        staged = stage_maven_test_script(script_path, effective_cwd, class_name, content)
        if not staged:
            cmd.append(f"-Dtest.src.dir={script_path.parent}")

    elif runner_name == "unittest" or (len(runner_cmd) >= 3 and "unittest" in runner_cmd[2]):
        cmd = [sys.executable, "-m", "unittest", "discover", "-s", str(script_path.parent), "-p", script_path.name]

    else:
        # Standard file path target for pytest, vitest, jest, etc.
        try:
            rel_script = script_path.relative_to(effective_cwd)
            cmd.append(str(rel_script))
        except ValueError:
            cmd.append(str(script_path))

    cmd_display = redact_secrets(" ".join(cmd))

    # 10. Build environment variables
    env = os.environ.copy()
    py_paths = [str(effective_cwd), str(effective_cwd / "src"), str(cwd), str(script_path.parent)]
    if env.get("PYTHONPATH"):
        py_paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join([p for p in py_paths if p])

    node_paths = [str(effective_cwd / "node_modules"), str(cwd / "node_modules")]
    if env.get("NODE_PATH"):
        node_paths.append(env["NODE_PATH"])
    env["NODE_PATH"] = os.pathsep.join([p for p in node_paths if p])

    # 11. Execute process
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(effective_cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s
        )
        dur_ms = (time.time() - t0) * 1000
        if proc.returncode == 0:
            return {
                "exitCode": 0,
                "status": "passed",
                "failureType": None,
                "stdout": proc.stdout or "",
                "stderr": proc.stderr or "",
                "durationMs": round(dur_ms, 2),
                "runner": runner_name or runner_cmd[0],
                "command": cmd_display,
                "workingDirectory": str(effective_cwd),
                "projectRoot": str(cwd),
                "startupCommand": startup_cmd_str,
                "stage": "execution",
                "evidence": None,
            }
        else:
            ftype = categorize_execution_failure(
                proc.returncode,
                proc.stdout or "",
                proc.stderr or "",
                runner_name,
                stage="execution"
            )
            ev = extract_failure_evidence(proc.stdout or "", proc.stderr or "", ftype)
            return {
                "exitCode": proc.returncode,
                "status": "failed",
                "failureType": ftype,
                "stdout": proc.stdout or "",
                "stderr": proc.stderr or "",
                "durationMs": round(dur_ms, 2),
                "runner": runner_name or runner_cmd[0],
                "command": cmd_display,
                "workingDirectory": str(effective_cwd),
                "projectRoot": str(cwd),
                "startupCommand": startup_cmd_str,
                "stage": "execution",
                "evidence": ev,
            }
    except subprocess.TimeoutExpired as te:
        dur_ms = (time.time() - t0) * 1000
        msg = f"Execution timed out after {timeout_s}s."
        return {
            "exitCode": 124,
            "status": "error",
            "failureType": "TIMEOUT",
            "stdout": te.stdout or "" if hasattr(te, "stdout") else "",
            "stderr": msg,
            "durationMs": round(dur_ms, 2),
            "runner": runner_name or runner_cmd[0],
            "command": cmd_display,
            "workingDirectory": str(effective_cwd),
            "projectRoot": str(cwd),
            "startupCommand": startup_cmd_str,
            "stage": "timeout",
            "evidence": msg,
        }
    except Exception as exc:
        dur_ms = (time.time() - t0) * 1000
        ftype = categorize_execution_failure(1, "", str(exc), runner_name, stage="execution", exception=exc)
        return {
            "exitCode": 1,
            "status": "error",
            "failureType": ftype or "PROCESS_LAUNCH_ERROR",
            "stdout": "",
            "stderr": f"Execution failed to launch: {str(exc)}",
            "durationMs": round(dur_ms, 2),
            "runner": runner_name or runner_cmd[0],
            "command": cmd_display,
            "workingDirectory": str(effective_cwd),
            "projectRoot": str(cwd),
            "startupCommand": startup_cmd_str,
            "stage": "execution",
            "evidence": str(exc)[:300],
        }
    finally:
        if local_service_ctx:
            stop_background_service(local_service_ctx)


def run_project_test_execution(
    code_map_source: Union[str, Path, dict],
    test_type: Optional[str] = None,
    script_files: Optional[List[str]] = None,
    timeout_s: float = DEFAULT_TEST_TIMEOUT_S
) -> Dict[str, Any]:
    """
    Finds existing test scripts, detects runner, executes scripts,
    saves execution artifact under <artifact-dir>/test execution/, and returns results.
    """
    code_map, code_map_path = load_code_map(code_map_source)
    if not code_map or not code_map_path:
        return {
            "status": "error",
            "message": "Project Code Map could not be loaded or located on disk.",
            "totalTests": 0,
            "passed": 0,
            "failed": 0,
            "durationMs": 0.0,
            "runner": "none",
            "results": []
        }

    target_dir = code_map_path.parent if code_map_path.suffix == ".json" else code_map_path
    scripts_dir = target_dir / "test scripts"
    if not scripts_dir.exists() or not scripts_dir.is_dir():
        return {
            "status": "not_found",
            "message": "No 'test scripts' directory found. Generate test scripts first.",
            "totalTests": 0,
            "passed": 0,
            "failed": 0,
            "durationMs": 0.0,
            "runner": "none",
            "results": []
        }

    # Resolve project root and environment using code_map metadata
    project_root = resolve_project_root(code_map, code_map_path) or target_dir
    project_env = detect_project_environment(project_root, code_map=code_map)

    expected_ext = (project_env.file_extension or "").lower()
    valid_extensions = {".java", ".py", ".ts", ".js", ".jsx", ".tsx", ".go", ".rs"}
    if expected_ext:
        valid_extensions.add(expected_ext)
    ignored_extensions = {".json", ".md", ".txt", ".class", ".pyc", ".log", ".lock"}

    # Top-level manifest if present
    top_manifest_file = scripts_dir / "manifest.json"
    top_manifest_entries = []
    if top_manifest_file.exists():
        try:
            m_data = json.loads(top_manifest_file.read_text(encoding="utf-8", errors="ignore"))
            top_manifest_entries = m_data.get("scripts", [])
        except Exception:
            pass

    # Find all valid test script files across groups
    all_scripts: List[Dict[str, Any]] = []
    for grp_dir in sorted(scripts_dir.iterdir()):
        if not grp_dir.is_dir():
            continue

        # Load group-level manifest if present
        grp_manifest_file = grp_dir / "manifest.json"
        manifest_by_filename: Dict[str, dict] = {}
        manifest_tc_ids: set = set()

        combined_entries = list(top_manifest_entries)
        if grp_manifest_file.exists():
            try:
                g_data = json.loads(grp_manifest_file.read_text(encoding="utf-8", errors="ignore"))
                combined_entries.extend(g_data.get("scripts", []))
            except Exception:
                pass

        for s in combined_entries:
            fn = s.get("filename")
            tid = s.get("testCaseId")
            if fn:
                manifest_by_filename[fn] = s
            if tid:
                manifest_tc_ids.add(tid)

        for s_file in sorted(grp_dir.iterdir()):
            if not s_file.is_file():
                continue
            suffix = s_file.suffix.lower()
            if suffix in ignored_extensions:
                continue

            # If manifest exists and this file is legacy TC-xxx superseded by manifest, skip it
            if manifest_by_filename and s_file.name not in manifest_by_filename:
                if s_file.stem in manifest_tc_ids:
                    continue

            # Check validity of extension
            if expected_ext and suffix != expected_ext and suffix not in valid_extensions:
                continue
            elif not expected_ext and suffix not in valid_extensions:
                continue

            # Read content preview to find class name, testCaseId, etc.
            content_preview = ""
            try:
                content_preview = s_file.read_text(encoding="utf-8", errors="ignore")[:1000]
            except Exception:
                pass

            m_match = manifest_by_filename.get(s_file.name)
            tc_id = m_match.get("testCaseId") if m_match else None
            stype = (m_match.get("testType") if m_match else None)

            if not tc_id:
                m_id = re.search(r"(?://|#)\s*Test Case ID:\s*([A-Za-z0-9_-]+)", content_preview)
                tc_id = m_id.group(1).strip() if m_id else s_file.stem

            if not stype:
                stype = "api" if "api" in s_file.name.lower() else "unit"
            stype = stype.lower()

            if test_type and test_type.lower() not in ("all", "") and stype != test_type.lower():
                continue

            if script_files:
                allowed = set(script_files)
                cls_name = extract_java_test_class_name(content_preview, s_file.stem) if suffix == ".java" else s_file.stem
                if not (
                    s_file.name in allowed
                    or str(s_file) in allowed
                    or tc_id in allowed
                    or s_file.stem in allowed
                    or cls_name in allowed
                ):
                    continue

            m_target = None
            if m_match:
                m_target = m_match.get("targetFile") or (m_match.get("target", {}).get("file") if isinstance(m_match.get("target"), dict) else None)
            if not m_target:
                m_tf = re.search(r"(?://|#)\s*Target:\s*([^:\r\n]+)", content_preview)
                if m_tf:
                    m_target = m_tf.group(1).strip()

            needs_server = detect_if_script_needs_server(s_file, content_preview, stype)

            all_scripts.append({
                "file": s_file,
                "filename": s_file.name,
                "testCaseId": tc_id,
                "testType": stype,
                "targetFile": m_target,
                "needsServer": needs_server,
            })

    if not all_scripts:
        return {
            "status": "not_found",
            "message": f"No matching test scripts found for execution (testType='{test_type or 'all'}').",
            "totalTests": 0,
            "passed": 0,
            "failed": 0,
            "durationMs": 0.0,
            "runner": "none",
            "results": []
        }

    # Detect test runner using first target file's subproject context if available
    first_target = None
    for s in all_scripts:
        if s.get("targetFile"):
            first_target = s["targetFile"]
            break

    runner_name, runner_cmd, err_reason = detect_test_runner(
        project_root=project_root,
        language=project_env.language,
        framework=project_env.test_framework,
        target_file=first_target,
        cwd=project_root,
    )

    if not runner_cmd:
        failure_type = "RUNNER_NOT_CONFIGURED" if (err_reason and "not configured" in err_reason.lower()) else "RUNNER_NOT_FOUND"
        return {
            "status": "error",
            "message": err_reason or f"No compatible test runner found for {project_env.language}.",
            "totalTests": len(all_scripts),
            "passed": 0,
            "failed": len(all_scripts),
            "durationMs": 0.0,
            "runner": "none",
            "results": [
                {
                    "testCaseId": s["testCaseId"],
                    "scriptFile": s["filename"],
                    "testType": s["testType"],
                    "status": "error",
                    "failureType": failure_type,
                    "exitCode": 1,
                    "stdout": "",
                    "stderr": err_reason or "Runner not available",
                    "durationMs": 0.0,
                    "runner": "none",
                    "command": None,
                    "workingDirectory": str(project_root),
                    "projectRoot": str(project_root),
                    "startupCommand": None,
                    "stage": "validation",
                    "evidence": err_reason or "Runner not available",
                }
                for s in all_scripts
            ]
        }

    # Check if any script requires a live application server
    service_ctx: Optional[RunningServiceContext] = None
    active_service_failed = False
    service_fail_type: Optional[str] = None
    service_fail_msg: Optional[str] = None
    detected_startup_cmd_str: Optional[str] = None

    scripts_needing_server = [s for s in all_scripts if s.get("needsServer")]
    if scripts_needing_server:
        sub_srv_cwd = None
        for s in scripts_needing_server:
            if s.get("targetFile"):
                cand_sub = detect_subproject_root(project_root / s["targetFile"], project_root)
                if cand_sub:
                    sub_srv_cwd = cand_sub
                    break

        st_cmd, exp_port, s_reason = detect_application_startup(project_root, sub_srv_cwd, project_env.language)
        if st_cmd:
            detected_startup_cmd_str = redact_secrets(" ".join(st_cmd))

        ctx, ftype, fmsg = start_service_if_needed(project_root, sub_srv_cwd, project_env.language)
        if ctx:
            service_ctx = ctx
            if ctx.cmd:
                detected_startup_cmd_str = redact_secrets(" ".join(ctx.cmd))
        else:
            active_service_failed = True
            service_fail_type = ftype or "APPLICATION_STARTUP_ERROR"
            service_fail_msg = fmsg or "Application failed to start."

    # Execute scripts sequentially
    t_start = time.time()
    results = []
    passed_count = 0
    failed_count = 0

    try:
        for item in all_scripts:
            if item.get("needsServer") and active_service_failed:
                failed_count += 1
                ev = service_fail_msg.splitlines()[0] if service_fail_msg else None
                results.append({
                    "testCaseId": item["testCaseId"],
                    "scriptFile": item["filename"],
                    "testType": item["testType"],
                    "status": "error",
                    "failureType": service_fail_type,
                    "exitCode": 1,
                    "stdout": "",
                    "stderr": service_fail_msg or "Application failed to start.",
                    "durationMs": 0.0,
                    "runner": runner_name or "none",
                    "command": None,
                    "workingDirectory": str(project_root),
                    "projectRoot": str(project_root),
                    "startupCommand": detected_startup_cmd_str,
                    "stage": "startup",
                    "evidence": ev,
                })
                continue

            res = execute_single_test_script(
                script_path=item["file"],
                runner_cmd=runner_cmd,
                cwd=project_root,
                timeout_s=timeout_s,
                target_file=item.get("targetFile"),
                language=project_env.language,
                runner_name=runner_name or "",
                test_type=item["testType"],
                service_ctx=service_ctx,
            )
            if res["status"] == "passed":
                passed_count += 1
            else:
                failed_count += 1

            results.append({
                "testCaseId": item["testCaseId"],
                "scriptFile": item["filename"],
                "testType": item["testType"],
                "status": res["status"],
                "failureType": res.get("failureType"),
                "exitCode": res["exitCode"],
                "stdout": res["stdout"],
                "stderr": res["stderr"],
                "durationMs": res["durationMs"],
                "runner": res.get("runner") or runner_name,
                "command": res.get("command"),
                "workingDirectory": res.get("workingDirectory"),
                "projectRoot": res.get("projectRoot"),
                "startupCommand": res.get("startupCommand"),
                "stage": res.get("stage"),
                "evidence": res.get("evidence"),
            })
    finally:
        if service_ctx:
            stop_background_service(service_ctx)

    total_dur_ms = round((time.time() - t_start) * 1000, 2)

    # Save execution summary artifact
    exec_dir = target_dir / "test execution"
    exec_dir.mkdir(parents=True, exist_ok=True)
    summary_path = exec_dir / "execution-summary.json"

    summary_data = {
        "status": "success",
        "runner": runner_name,
        "totalTests": len(results),
        "passed": passed_count,
        "failed": failed_count,
        "durationMs": total_dur_ms,
        "executedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": results
    }

    try:
        summary_path.write_text(json.dumps(summary_data, indent=2), encoding="utf-8")
        backend_logger.info(f"[TestExecutor] Saved execution summary to {summary_path}")
    except Exception as exc:
        backend_logger.warning(f"[TestExecutor] Could not save execution summary: {exc}")

    return {
        "status": "success",
        "totalTests": len(results),
        "passed": passed_count,
        "failed": failed_count,
        "durationMs": total_dur_ms,
        "runner": runner_name,
        "results": results,
        "message": f"Executed {len(results)} test(s) via {runner_name}: {passed_count} passed, {failed_count} failed ({total_dur_ms}ms).",
        "summaryPath": str(summary_path)
    }


def get_existing_execution_results(code_map_source: Union[str, Path, dict]) -> Optional[Dict[str, Any]]:
    """Reads existing execution summary if present on disk."""
    code_map, code_map_path = load_code_map(code_map_source)
    if not code_map or not code_map_path:
        return None
    target_dir = code_map_path.parent if code_map_path.suffix == ".json" else code_map_path
    summary_path = target_dir / "test execution" / "execution-summary.json"
    if summary_path.exists() and summary_path.is_file():
        try:
            return json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None

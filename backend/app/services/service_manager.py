"""
Service Manager for Functional and API Test Execution
======================================================
Manages application startup detection, port availability, background process lifecycle,
readiness verification, secret redaction, and reliable cleanup for Functional and API testing.
Never hardcodes project names, commands, ports, or endpoints.
"""

import os
import re
import sys
import json
import time
import socket
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from app.logger import backend_logger


def redact_secrets(cmd_or_text: Union[str, List[str]]) -> str:
    """
    Redacts sensitive tokens, passwords, and keys from commands and log output.
    """
    if isinstance(cmd_or_text, list):
        text = " ".join(cmd_or_text)
    else:
        text = str(cmd_or_text)

    # Redact common credential patterns
    patterns = [
        (r"(?i)(password|passwd|secret|token|api[_-]?key|auth|bearer)\s*[:=]\s*['\"]?([^'\"\s]+)['\"]?", r"\1=***REDACTED***"),
        (r"(?i)(--password|--token|--key|--api-key)\s+['\"]?([^'\"\s]+)['\"]?", r"\1 ***REDACTED***"),
        (r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{15,}", r"Bearer ***REDACTED***"),
    ]
    redacted = text
    for pat, repl in patterns:
        redacted = re.sub(pat, repl, redacted)
    return redacted


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """
    Checks if a TCP port is currently open and listening on the given host.
    """
    if not port or port <= 0 or port > 65535:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect((host, port))
            return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False


def detect_application_port(
    project_root: Path,
    subproject_root: Optional[Path] = None,
    language: str = ""
) -> int:
    """
    Dynamically inspects project configuration and source files to detect the application port.
    Returns detected port or language-appropriate default.
    """
    search_dirs = [subproject_root, project_root] if subproject_root else [project_root]
    port_regexes = [
        r"""(?:PORT|port)\s*[:=]\s*['"]?(\d{2,5})['"]?""",
        r"""app\.listen\s*\(\s*(\d{2,5})""",
        r"""process\.env\.PORT\s*\|\|\s*(\d{2,5})""",
        r"""--port\s+(\d{2,5})""",
        r"""server\.port\s*=\s*(\d{2,5})""",
        r"""uvicorn\.run\([^)]*port\s*=\s*(\d{2,5})""",
    ]

    for d in search_dirs:
        if not d or not d.exists():
            continue

        # 1. Check package.json scripts / config
        pkg_file = d / "package.json"
        if pkg_file.exists():
            try:
                pkg_data = json.loads(pkg_file.read_text(encoding="utf-8", errors="ignore"))
                scripts_str = json.dumps(pkg_data.get("scripts", {}))
                for rx in port_regexes:
                    m = re.search(rx, scripts_str)
                    if m:
                        p = int(m.group(1))
                        if 1024 <= p <= 65535:
                            return p
            except Exception:
                pass

        # 2. Check application properties / yml for Java
        for prop_name in ["application.properties", "application.yml", "application.yaml"]:
            for cand in [d / prop_name, d / "src" / "main" / "resources" / prop_name]:
                if cand.exists():
                    try:
                        content = cand.read_text(encoding="utf-8", errors="ignore")
                        m = re.search(r"server\.port\s*[:=]\s*(\d{2,5})", content)
                        if m:
                            p = int(m.group(1))
                            if 1024 <= p <= 65535:
                                return p
                    except Exception:
                        pass

        # 3. Scan common entry files
        entry_candidates = [
            "server.js", "app.js", "index.js", "src/server.ts", "src/index.ts",
            "src/app.ts", "app.py", "main.py", "server.py", "wsgi.py"
        ]
        for ec in entry_candidates:
            cand = d / ec
            if cand.exists():
                try:
                    content = cand.read_text(encoding="utf-8", errors="ignore")
                    for rx in port_regexes:
                        m = re.search(rx, content)
                        if m:
                            p = int(m.group(1))
                            if 1024 <= p <= 65535:
                                return p
                except Exception:
                    pass

    # Language/Framework defaults
    lang_lower = (language or "").lower()
    if lang_lower in ["javascript", "typescript", "jsx", "tsx", "js", "ts"]:
        return 3000
    if lang_lower == "python":
        return 8000
    if lang_lower == "java":
        return 8080
    return 8000


def detect_application_startup(
    project_root: Path,
    subproject_root: Optional[Path] = None,
    language: str = ""
) -> Tuple[Optional[List[str]], Optional[int], Optional[str]]:
    """
    Detects the application startup command and port from real project configuration.
    Returns (startup_cmd, expected_port, detection_reason).
    Never hardcodes project paths or names.
    """
    search_dirs = [subproject_root, project_root] if subproject_root else [project_root]
    lang_lower = (language or "").lower()

    npm_bin = shutil.which("npm") or shutil.which("npm.cmd")
    yarn_bin = shutil.which("yarn") or shutil.which("yarn.cmd")
    pnpm_bin = shutil.which("pnpm") or shutil.which("pnpm.cmd")
    node_bin = shutil.which("node") or shutil.which("node.exe")

    # 1. JavaScript / TypeScript projects
    if lang_lower in ["javascript", "typescript", "jsx", "tsx", "js", "ts"]:
        for d in search_dirs:
            if not d or not d.exists():
                continue
            pkg_file = d / "package.json"
            if not pkg_file.exists():
                continue

            try:
                pkg_data = json.loads(pkg_file.read_text(encoding="utf-8", errors="ignore"))
                scripts = pkg_data.get("scripts", {})
                port = detect_application_port(project_root, d, language)

                is_pnpm = (d / "pnpm-lock.yaml").exists() and pnpm_bin
                is_yarn = (d / "yarn.lock").exists() and yarn_bin

                # Priority: start -> dev -> server -> serve
                for s_key in ["start", "dev", "server", "serve"]:
                    if s_key in scripts:
                        if is_pnpm:
                            return [pnpm_bin, "run", s_key], port, f"package.json script '{s_key}' via pnpm"
                        if is_yarn:
                            return [yarn_bin, s_key], port, f"package.json script '{s_key}' via yarn"
                        if npm_bin:
                            return [npm_bin, "run", s_key], port, f"package.json script '{s_key}' via npm"

                # Check main entry file in package.json
                main_file = pkg_data.get("main")
                if main_file and (d / main_file).exists() and node_bin:
                    return [node_bin, main_file], port, f"package.json main file '{main_file}'"

                # Check standard entry files
                for cand_file in ["server.js", "app.js", "index.js"]:
                    if (d / cand_file).exists() and node_bin:
                        return [node_bin, cand_file], port, f"Standard Node entry file '{cand_file}'"

            except Exception as e:
                backend_logger.debug(f"[ServiceManager] Could not parse package.json in {d}: {e}")

    # 2. Python projects
    elif lang_lower == "python" or not lang_lower:
        py_bin = sys.executable
        for d in search_dirs:
            if not d or not d.exists():
                continue

            port = detect_application_port(project_root, d, "python")

            # Check Django manage.py
            if (d / "manage.py").exists():
                return [py_bin, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"], port, "Django manage.py runserver"

            # Check FastAPI / Uvicorn in common files
            for fname in ["app.py", "main.py", "server.py"]:
                fpath = d / fname
                if fpath.exists():
                    try:
                        content = fpath.read_text(encoding="utf-8", errors="ignore")
                        mod_name = fname[:-3]
                        if "FastAPI(" in content or "uvicorn.run(" in content:
                            uvicorn_bin = shutil.which("uvicorn") or shutil.which("uvicorn.exe")
                            if uvicorn_bin:
                                return [uvicorn_bin, f"{mod_name}:app", "--host", "127.0.0.1", "--port", str(port)], port, f"FastAPI app in {fname}"
                            return [py_bin, "-m", "uvicorn", f"{mod_name}:app", "--host", "127.0.0.1", "--port", str(port)], port, f"FastAPI app in {fname}"
                        elif "Flask(" in content:
                            return [py_bin, "-m", "flask", "run", "--host", "127.0.0.1", "--port", str(port)], port, f"Flask app in {fname}"
                        elif "__main__" in content and "app.run(" in content:
                            return [py_bin, fname], port, f"Python app entry in {fname}"
                    except Exception:
                        pass

    # 3. Java projects (Spring Boot)
    elif lang_lower == "java":
        mvn_bin = shutil.which("mvn") or shutil.which("mvn.cmd")
        gradle_bin = shutil.which("gradle") or shutil.which("gradle.cmd")
        for d in search_dirs:
            if not d or not d.exists():
                continue
            port = detect_application_port(project_root, d, "java")
            if (d / "pom.xml").exists() and mvn_bin:
                try:
                    pom_content = (d / "pom.xml").read_text(encoding="utf-8", errors="ignore")
                    if "spring-boot" in pom_content:
                        return [mvn_bin, "spring-boot:run"], port, "Maven Spring Boot application"
                except Exception:
                    pass
            if (d / "build.gradle").exists() and gradle_bin:
                return [gradle_bin, "bootRun"], port, "Gradle Spring Boot application"

    return None, None, f"No supported application startup command detected for {language}."


class RunningServiceContext:
    """Represents a running background application service started for testing."""
    def __init__(
        self,
        process: subprocess.Popen,
        cmd: List[str],
        cwd: Path,
        port: int,
        started_by_us: bool = True
    ):
        self.process = process
        self.cmd = cmd
        self.cwd = cwd
        self.port = port
        self.started_by_us = started_by_us
        self.pid = process.pid if process else None

    def is_alive(self) -> bool:
        if not self.process:
            return False
        return self.process.poll() is None


def wait_for_service_ready(
    process: subprocess.Popen,
    port: int,
    timeout_s: float = 20.0,
    host: str = "127.0.0.1"
) -> Tuple[bool, Optional[str]]:
    """
    Polls the target port and checks process health until the service is ready
    or timeout expires.
    """
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        # Check if process terminated prematurely
        ret = process.poll()
        if ret is not None:
            stdout_data, stderr_data = "", ""
            try:
                out, err = process.communicate(timeout=1.0)
                stdout_data = out.decode("utf-8", errors="ignore") if isinstance(out, bytes) else str(out or "")
                stderr_data = err.decode("utf-8", errors="ignore") if isinstance(err, bytes) else str(err or "")
            except Exception:
                pass
            return False, f"Process exited prematurely with exit code {ret}.\nStderr: {stderr_data}\nStdout: {stdout_data}"

        # Check port connection
        if is_port_in_use(port, host):
            return True, None

        time.sleep(0.3)

    return False, f"Service did not become ready on {host}:{port} within {timeout_s}s timeout."


def start_service_if_needed(
    project_root: Path,
    subproject_root: Optional[Path] = None,
    language: str = "",
    timeout_s: float = 20.0
) -> Tuple[Optional[RunningServiceContext], Optional[str], Optional[str]]:
    """
    Determines if the application server is already running. If not, starts it
    in the background and waits for readiness.
    Returns (service_ctx, failure_type, error_message).
    """
    startup_cmd, port, reason = detect_application_startup(project_root, subproject_root, language)
    if not startup_cmd or not port:
        return None, "APPLICATION_STARTUP_ERROR", f"Could not detect application startup command: {reason}"

    # Check if service is already running on this port
    if is_port_in_use(port):
        backend_logger.info(f"[ServiceManager] Service already active on port {port}. Reusing running instance.")
        dummy_ctx = RunningServiceContext(
            process=None,
            cmd=startup_cmd,
            cwd=subproject_root or project_root,
            port=port,
            started_by_us=False
        )
        return dummy_ctx, None, None

    # Start the process in the background
    working_dir = subproject_root or project_root
    backend_logger.info(
        f"[ServiceManager] Starting application service via: {redact_secrets(startup_cmd)} "
        f"(cwd={working_dir}, port={port})"
    )

    try:
        env = os.environ.copy()
        if (working_dir / "node_modules").exists():
            env["NODE_PATH"] = str(working_dir / "node_modules")
        env["PYTHONPATH"] = os.pathsep.join([str(working_dir), str(project_root), env.get("PYTHONPATH", "")])

        proc = subprocess.Popen(
            startup_cmd,
            cwd=str(working_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=(os.name == "nt")  # Shell needed on Windows for npm/yarn scripts
        )

        ready, err_msg = wait_for_service_ready(proc, port, timeout_s=timeout_s)
        if not ready:
            stop_background_service(proc)
            return None, "APPLICATION_NOT_READY", err_msg

        ctx = RunningServiceContext(
            process=proc,
            cmd=startup_cmd,
            cwd=working_dir,
            port=port,
            started_by_us=True
        )
        return ctx, None, None

    except FileNotFoundError as fnf:
        return None, "APPLICATION_STARTUP_ERROR", f"Startup executable not found: {fnf}"
    except Exception as exc:
        return None, "APPLICATION_STARTUP_ERROR", f"Failed to launch application service: {exc}"


def stop_background_service(proc_or_ctx: Union[subprocess.Popen, RunningServiceContext, None]):
    """
    Safely terminates only the background service and its child processes started by us.
    Preserves all unrelated user processes.
    """
    if not proc_or_ctx:
        return

    if isinstance(proc_or_ctx, RunningServiceContext):
        if not proc_or_ctx.started_by_us or not proc_or_ctx.process:
            return
        proc = proc_or_ctx.process
    else:
        proc = proc_or_ctx

    if proc.poll() is not None:
        return

    pid = proc.pid
    backend_logger.info(f"[ServiceManager] Terminating background service PID {pid}")

    try:
        if os.name == "nt":
            # On Windows, kill process tree
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=5
            )
        else:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception as e:
        backend_logger.warning(f"[ServiceManager] Error terminating background PID {pid}: {e}")

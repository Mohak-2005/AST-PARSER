import os
import sys
import json
import socket
import unittest
import tempfile
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.services.service_manager import (
    redact_secrets,
    is_port_in_use,
    detect_application_port,
    detect_application_startup,
    start_service_if_needed,
    stop_background_service,
    RunningServiceContext,
)
from app.services.test_executor import (
    categorize_execution_failure,
    extract_failure_evidence,
    detect_if_script_needs_server,
    execute_single_test_script,
    run_project_test_execution,
)


class TestServiceManager(unittest.TestCase):
    """Unit tests for service manager lifecycle, startup detection, port detection, and cleanup."""

    def test_redact_secrets(self):
        cmd = ["python", "-m", "uvicorn", "app:app", "--password", "supersecret123", "--token=xyz789token"]
        redacted = redact_secrets(cmd)
        self.assertNotIn("supersecret123", redacted)
        self.assertNotIn("xyz789token", redacted)
        self.assertIn("***REDACTED***", redacted)

        text = "Authorization: Bearer mysecrettokenwithmorethan15chars"
        self.assertIn("Bearer ***REDACTED***", redact_secrets(text))

    def test_is_port_in_use(self):
        # Bind ephemeral port
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.listen(1)

        self.assertTrue(is_port_in_use(port))
        s.close()
        # Once closed, should not be in use
        self.assertFalse(is_port_in_use(port))

    def test_detect_application_port(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Node package.json with script containing PORT=4000
            pkg = root / "package.json"
            pkg.write_text(json.dumps({"scripts": {"start": "PORT=4000 node server.js"}}), encoding="utf-8")
            self.assertEqual(detect_application_port(root, language="javascript"), 4000)

        with tempfile.TemporaryDirectory() as td_py:
            # Python default in clean directory
            self.assertEqual(detect_application_port(Path(td_py), language="python"), 8000)

        with tempfile.TemporaryDirectory() as td_java:
            # Java default in clean directory
            self.assertEqual(detect_application_port(Path(td_java), language="java"), 8080)

    def test_detect_application_startup_python_fastapi(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            app_file = root / "main.py"
            app_file.write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")

            cmd, port, reason = detect_application_startup(root, language="python")
            self.assertIsNotNone(cmd)
            self.assertIn("uvicorn", " ".join(cmd))
            self.assertEqual(port, 8000)
            self.assertIn("FastAPI app", reason)

    def test_detect_application_startup_nodejs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "package.json"
            pkg.write_text(json.dumps({
                "name": "my-app",
                "scripts": {"start": "node server.js"}
            }), encoding="utf-8")

            cmd, port, reason = detect_application_startup(root, language="javascript")
            self.assertIsNotNone(cmd)
            self.assertIn("start", cmd)
            self.assertIn("package.json script", reason)

    def test_start_service_if_needed_reusing_running(self):
        # Open port with sufficient backlog
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.listen(10)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "package.json"
            pkg.write_text(json.dumps({
                "scripts": {"start": f"PORT={port} node server.js"}
            }), encoding="utf-8")

            ctx, ftype, fmsg = start_service_if_needed(root, language="javascript")
            self.assertIsNotNone(ctx)
            self.assertFalse(ctx.started_by_us)
            self.assertIsNone(ftype)

            # Stopping context not started by us must be a no-op
            stop_background_service(ctx)
            # Context remains not alive since process=None
            self.assertFalse(ctx.is_alive())

        s.close()


class Test19CanonicalFailureCategories(unittest.TestCase):
    """Unit tests verifying all 19 canonical execution failure categories."""

    def test_canonical_failure_types(self):
        # 1. SCRIPT_NOT_FOUND
        self.assertEqual(
            categorize_execution_failure(1, "", "Script file 'test_app.py' does not exist on disk."),
            "SCRIPT_NOT_FOUND"
        )
        # 2. INVALID_SCRIPT
        self.assertEqual(
            categorize_execution_failure(1, "", "Script file is empty (0 bytes)."),
            "INVALID_SCRIPT"
        )
        # 3. IMPORT_RESOLUTION_ERROR
        self.assertEqual(
            categorize_execution_failure(1, "", "ImportError: cannot import name 'calculate_tax' from 'app.tax'"),
            "IMPORT_RESOLUTION_ERROR"
        )
        # 4. RUNNER_NOT_CONFIGURED
        self.assertEqual(
            categorize_execution_failure(1, "", "No test runner configured in package.json"),
            "RUNNER_NOT_CONFIGURED"
        )
        # 5. RUNNER_NOT_FOUND
        self.assertEqual(
            categorize_execution_failure(1, "", "'pytest' is not recognized as an internal or external command"),
            "RUNNER_NOT_FOUND"
        )
        # 6. INVALID_RUNNER_INVOCATION
        self.assertEqual(
            categorize_execution_failure(1, "", "pytest: error: unrecognized arguments: --invalid-flag"),
            "INVALID_RUNNER_INVOCATION"
        )
        # 7. INVALID_WORKING_DIRECTORY
        self.assertEqual(
            categorize_execution_failure(1, "", "Effective working directory 'C:/invalid/dir' does not exist."),
            "INVALID_WORKING_DIRECTORY"
        )
        # 8. DEPENDENCY_ERROR
        self.assertEqual(
            categorize_execution_failure(1, "", "ModuleNotFoundError: No module named 'fastapi'"),
            "DEPENDENCY_ERROR"
        )
        # 9. ENVIRONMENT_ERROR
        self.assertEqual(
            categorize_execution_failure(1, "", "The JAVA_HOME environment variable is not defined correctly"),
            "ENVIRONMENT_ERROR"
        )
        # 10. APPLICATION_STARTUP_ERROR
        self.assertEqual(
            categorize_execution_failure(1, "", "Failed to launch application service: executable not found", stage="startup"),
            "APPLICATION_STARTUP_ERROR"
        )
        # 11. APPLICATION_NOT_READY
        self.assertEqual(
            categorize_execution_failure(1, "", "Service did not become ready on 127.0.0.1:8000 within 20.0s timeout.", stage="readiness"),
            "APPLICATION_NOT_READY"
        )
        # 12. API_CONNECTION_ERROR
        self.assertEqual(
            categorize_execution_failure(1, "", "requests.exceptions.ConnectionError: [WinError 10061] No connection could be made because the target machine actively refused it"),
            "API_CONNECTION_ERROR"
        )
        self.assertEqual(
            categorize_execution_failure(1, "", "connect ECONNREFUSED 127.0.0.1:3000"),
            "API_CONNECTION_ERROR"
        )
        # 13. TEST_DISCOVERY_ERROR
        self.assertEqual(
            categorize_execution_failure(1, "", "collected 0 items / 0 tests collected"),
            "TEST_DISCOVERY_ERROR"
        )
        # 14. TEST_COLLECTION_ERROR
        self.assertEqual(
            categorize_execution_failure(1, "", "ERROR collecting tests/test_math.py: compilation failure"),
            "TEST_COLLECTION_ERROR"
        )
        # 15. TEST_SETUP_ERROR
        self.assertEqual(
            categorize_execution_failure(1, "", "fixture 'db_session' failed during setup"),
            "TEST_SETUP_ERROR"
        )
        # 16. ASSERTION_FAILURE
        self.assertEqual(
            categorize_execution_failure(1, "AssertionError: Expected 200 but received 404", ""),
            "ASSERTION_FAILURE"
        )
        # 17. TIMEOUT
        self.assertEqual(
            categorize_execution_failure(124, "", "Execution timed out after 300.0s."),
            "TIMEOUT"
        )
        # 18. PROCESS_LAUNCH_ERROR
        self.assertEqual(
            categorize_execution_failure(1, "", "PermissionError: [WinError 5] Access is denied"),
            "PROCESS_LAUNCH_ERROR"
        )
        # 19. EXECUTION_ERROR
        self.assertEqual(
            categorize_execution_failure(1, "", "ZeroDivisionError: division by zero"),
            "EXECUTION_ERROR"
        )


class TestLiveServerDetectionAndEvidence(unittest.TestCase):
    """Tests for live server detection and evidence extraction."""

    def test_detect_if_script_needs_server_unit_test(self):
        # Unit test always False
        self.assertFalse(detect_if_script_needs_server(None, "import requests\nrequests.get('http://localhost:8000')", "unit"))

    def test_detect_if_script_needs_server_in_process_api(self):
        # In-process TestClient(app) does NOT need live server
        script = "from fastapi.testclient import TestClient\nfrom app.main import app\nclient = TestClient(app)\ndef test_h(): client.get('/health')"
        self.assertFalse(detect_if_script_needs_server(None, script, "api"))

        # In-process supertest(app) does NOT need live server
        script_js = "const request = require('supertest');\nconst app = require('./app');\nit('works', () => request(app).get('/health'))"
        self.assertFalse(detect_if_script_needs_server(None, script_js, "api"))

    def test_detect_if_script_needs_server_live_api(self):
        # Live requests to localhost needs server
        script = "import requests\ndef test_api(): res = requests.get('http://localhost:8000/api/users')"
        self.assertTrue(detect_if_script_needs_server(None, script, "api"))

        # Live Playwright test needs server
        script_pw = "const { chromium } = require('playwright'); await page.goto('http://localhost:3000')"
        self.assertTrue(detect_if_script_needs_server(None, script_pw, "functional"))

    def test_extract_failure_evidence(self):
        stderr = "Traceback (most recent call last):\n  File 'test.py', line 10\nrequests.exceptions.ConnectionError: Failed to establish a new connection: [WinError 10061] No connection could be made because the target machine actively refused it"
        ev = extract_failure_evidence("", stderr, "API_CONNECTION_ERROR")
        self.assertIn("actively refused it", ev)

        stdout = "test_math.py::test_add FAILED\nAssertionError: assert 5 == 10\n"
        ev = extract_failure_evidence(stdout, "", "ASSERTION_FAILURE")
        self.assertIn("AssertionError", ev)


class TestSingleScriptExecutionDiagnostics(unittest.TestCase):
    """Tests for execute_single_test_script returning rich diagnostics."""

    def test_execute_empty_script_returns_invalid_script(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = root / "empty_test.py"
            script.write_text("", encoding="utf-8")

            res = execute_single_test_script(
                script_path=script,
                runner_cmd=[sys.executable, "-m", "unittest"],
                cwd=root,
                language="python"
            )
            self.assertEqual(res["status"], "error")
            self.assertEqual(res["failureType"], "INVALID_SCRIPT")
            self.assertEqual(res["stage"], "validation")
            self.assertIn("empty", res["evidence"])

    def test_execute_missing_script_returns_script_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = root / "does_not_exist.py"

            res = execute_single_test_script(
                script_path=script,
                runner_cmd=[sys.executable, "-m", "unittest"],
                cwd=root,
                language="python"
            )
            self.assertEqual(res["status"], "error")
            self.assertEqual(res["failureType"], "SCRIPT_NOT_FOUND")
            self.assertEqual(res["stage"], "validation")


if __name__ == "__main__":
    unittest.main()

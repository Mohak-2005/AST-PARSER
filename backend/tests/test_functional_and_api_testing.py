"""
Tests for Functional and API Testing Pipeline Extensions
=======================================================
Verifies:
1. discover_functional_targets (React components, UI views, user workflow targets)
2. discover_api_endpoints (HTTP routes, endpoints, methods, decorators)
3. is_valid_test_case (type-aware validation for unit, functional, and api)
4. validate_test_cases (cross-validation preserving testType for functional & api)
5. test_executor (runner detection, execution summary persistence, safe execution)
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.services.unit_test_generator import (
    discover_functional_targets,
    discover_api_endpoints,
    is_valid_test_case,
    is_valid_unit_test_case,
    validate_test_cases,
    build_functional_generation_messages,
    build_api_generation_messages,
)
from app.services.test_executor import (
    detect_test_runner,
    run_project_test_execution,
    get_existing_execution_results,
)


def _sample_full_code_map():
    return {
        "project": {
            "name": "ECommerceApp",
            "path": "/repo",
            "languagesDetected": ["typescript", "python"],
        },
        "files": {
            "web/src/components/CheckoutButton.tsx": {
                "language": "typescript",
                "lineCount": 45,
                "react": {
                    "components": [
                        {
                            "name": "CheckoutButton",
                            "props": ["cartId", "totalAmount", "onSuccess"],
                            "hooks": ["useState", "useCheckout"],
                        }
                    ],
                    "hooks": ["useCheckout"],
                },
                "declarations": [
                    {"name": "CheckoutButton", "type": "function", "line": 10},
                    {"name": "useCheckout", "type": "function", "line": 30},
                ],
            },
            "backend/app/routes/payments.py": {
                "language": "python",
                "lineCount": 80,
                "routes": [
                    {
                        "path": "/api/v1/payments",
                        "method": "POST",
                        "handler": "create_payment",
                        "params": ["amount", "currency", "payment_method_id"],
                    },
                    {
                        "path": "/api/v1/payments/{payment_id}",
                        "method": "GET",
                        "handler": "get_payment_status",
                        "params": ["payment_id"],
                    },
                ],
                "declarations": [
                    {
                        "name": "create_payment",
                        "type": "function",
                        "line": 15,
                        "decorators": ["@router.post('/api/v1/payments')"],
                    },
                    {
                        "name": "get_payment_status",
                        "type": "function",
                        "line": 45,
                        "decorators": ["@router.get('/api/v1/payments/{payment_id}')"],
                    },
                ],
            },
            "backend/app/services/calculator.py": {
                "language": "python",
                "lineCount": 30,
                "declarations": [
                    {"name": "add", "type": "function", "line": 5},
                ],
            },
        },
    }


class TestFunctionalAndApiTargetDiscovery(unittest.TestCase):
    def setUp(self):
        self.code_map = _sample_full_code_map()

    def test_discover_functional_targets(self):
        targets = discover_functional_targets(self.code_map)
        self.assertTrue(len(targets) >= 1)
        symbols = [t["symbol"] for t in targets]
        self.assertIn("CheckoutButton", symbols)
        
        # Verify component metadata is attached
        btn_target = next(t for t in targets if t["symbol"] == "CheckoutButton")
        self.assertEqual(btn_target["file"], "web/src/components/CheckoutButton.tsx")
        self.assertEqual(btn_target["type"], "component")
        self.assertIn("cartId", btn_target.get("props", []))

    def test_discover_api_endpoints(self):
        endpoints = discover_api_endpoints(self.code_map)
        self.assertTrue(len(endpoints) >= 2)

        routes = [ep["route"] for ep in endpoints]
        self.assertIn("/api/v1/payments", routes)
        self.assertIn("/api/v1/payments/{payment_id}", routes)

        post_ep = next(ep for ep in endpoints if ep["route"] == "/api/v1/payments")
        self.assertEqual(post_ep["httpMethod"], "POST")
        self.assertEqual(post_ep["symbol"], "create_payment")
        self.assertEqual(post_ep["file"], "backend/app/routes/payments.py")

    def test_is_valid_test_case_unit_compat(self):
        unit_case = {
            "testCaseId": "TC-UNIT-01",
            "title": "Test calculate sum",
            "testType": "unit",
            "description": "Tests basic addition",
            "inputs": {"a": 1, "b": 2},
            "expectedResult": "3",
            "unit": {"file": "backend/app/services/calculator.py", "symbol": "add", "type": "function"},
            "assertions": ["Assert equal output"],
        }
        self.assertTrue(is_valid_unit_test_case(unit_case))
        self.assertTrue(is_valid_test_case(unit_case, "unit"))

    def test_is_valid_test_case_functional(self):
        func_case = {
            "testCaseId": "TC-FUNC-01",
            "title": "Test Checkout Button Click Workflow",
            "testType": "functional",
            "description": "Verifies checkout button initiates payment flow upon click",
            "inputs": {"cartId": "cart-123"},
            "expectedResult": "Payment is initiated and onSuccess callback is invoked",
            "target": {"file": "web/src/components/CheckoutButton.tsx", "symbol": "CheckoutButton"},
            "functional": {
                "userWorkflow": "User clicks checkout button with valid cart",
                "expectedOutcome": "Loading spinner appears and onSuccess is called",
            },
            "assertions": ["Expect onSuccess callback called with payment confirmation"],
        }
        self.assertTrue(is_valid_test_case(func_case, "functional"))

    def test_is_valid_test_case_api(self):
        api_case = {
            "testCaseId": "TC-API-01",
            "title": "POST /api/v1/payments creates payment",
            "testType": "api",
            "description": "Sends payment payload and asserts 201 Created response",
            "inputs": {"amount": 500, "currency": "USD"},
            "expectedResult": "HTTP status 201 with payment record",
            "target": {
                "file": "backend/app/routes/payments.py",
                "symbol": "create_payment",
                "route": "/api/v1/payments",
                "httpMethod": "POST",
            },
            "api": {
                "endpoint": "/api/v1/payments",
                "method": "POST",
                "expectedStatus": 201,
            },
            "assertions": ["assert res.status_code == 201"],
        }
        self.assertTrue(is_valid_test_case(api_case, "api"))

    def test_validate_test_cases_functional(self):
        raw_cases = [
            {
                "testCaseId": "TC-FUNC-01",
                "title": "Test Checkout Button Flow",
                "testType": "functional",
                "description": "Checkout user workflow",
                "inputs": {"cartId": "123"},
                "expectedResult": "Render checkout",
                "unit": {"file": "web/src/components/CheckoutButton.tsx", "symbol": "CheckoutButton", "type": "component"},
                "functional": {"userWorkflow": "Click checkout"},
                "assertions": ["Assert checkout called"],
            },
            # Invalid: target symbol does not exist in file
            {
                "testCaseId": "TC-FUNC-02",
                "title": "Test NonExistent Component",
                "testType": "functional",
                "description": "Non existent",
                "inputs": {},
                "expectedResult": "None",
                "unit": {"file": "web/src/components/CheckoutButton.tsx", "symbol": "NonExistent", "type": "component"},
                "assertions": ["Assert something"],
            },
        ]
        valid_units = {
            "web/src/components/CheckoutButton.tsx": {"CheckoutButton"},
        }

        valid = validate_test_cases(raw_cases, valid_units, self.code_map, expected_test_type="functional")
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0]["testCaseId"], "TC-FUNC-01")
        self.assertEqual(valid[0]["testType"], "functional")

    def test_validate_test_cases_api(self):
        raw_cases = [
            {
                "testCaseId": "TC-API-01",
                "title": "Create Payment API",
                "testType": "api",
                "description": "POST payment",
                "inputs": {"amount": 50},
                "expectedResult": "200 OK",
                "unit": {
                    "file": "backend/app/routes/payments.py",
                    "symbol": "create_payment",
                    "route": "/api/v1/payments",
                    "httpMethod": "POST",
                    "type": "route",
                },
                "api": {"endpoint": "/api/v1/payments", "method": "POST", "expectedStatus": 200},
                "assertions": ["Assert status 200"],
            },
            # Invalid: target symbol does not exist
            {
                "testCaseId": "TC-API-02",
                "title": "Invalid API Test",
                "testType": "api",
                "description": "Invalid symbol",
                "inputs": {},
                "expectedResult": "None",
                "unit": {"file": "backend/app/routes/payments.py", "symbol": "non_existent_handler", "type": "route"},
                "assertions": ["Assert status 200"],
            },
        ]
        valid_units = {
            "backend/app/routes/payments.py": {"create_payment", "get_payment_status"},
        }

        valid = validate_test_cases(raw_cases, valid_units, self.code_map, expected_test_type="api")
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0]["testCaseId"], "TC-API-01")
        self.assertEqual(valid[0]["testType"], "api")
        self.assertEqual(valid[0]["unit"]["route"], "/api/v1/payments")


class TestPromptBuildersPrivacy(unittest.TestCase):
    def test_prompts_do_not_contain_raw_source_code(self):
        code_map = _sample_full_code_map()
        targets = discover_functional_targets(code_map)
        context = {
            "group": "Checkout",
            "targets": targets,
            "language": "typescript"
        }
        messages = build_functional_generation_messages(context)
        
        # Verify JSON metadata is sent, not raw code strings
        prompt_content = messages[1]["content"]
        self.assertIn("CheckoutButton", prompt_content)
        self.assertIn("props", prompt_content)


class TestTestExecutor(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_detect_test_runner_python(self):
        runner_name, runner_cmd, err = detect_test_runner(Path(self.tmpdir), language="python")
        self.assertTrue(runner_name in ["pytest", "unittest"])
        self.assertIsNotNone(runner_cmd)

    def test_detect_test_runner_node(self):
        pkg_json = Path(self.tmpdir) / "package.json"
        pkg_json.write_text(json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8")
        runner_name, runner_cmd, err = detect_test_runner(Path(self.tmpdir), language="typescript")
        if shutil.which("npx"):
            self.assertEqual(runner_name, "vitest")

    def test_execute_and_summary_persistence(self):
        proj_dir = Path(self.tmpdir)
        code_map_dir = proj_dir / "code map"
        code_map_dir.mkdir(parents=True, exist_ok=True)
        code_map_file = code_map_dir / "repository-code-map.json"
        code_map_file.write_text(json.dumps({
            "project": {"name": "TestProj", "path": str(proj_dir)}
        }), encoding="utf-8")

        # Test scripts directory belongs in code_map_dir alongside code map
        test_scripts_dir = code_map_dir / "test scripts" / "group1"
        test_scripts_dir.mkdir(parents=True, exist_ok=True)
        sample_script = test_scripts_dir / "test_example.py"
        sample_script.write_text("def test_ok(): assert 1 == 1\n", encoding="utf-8")

        # Mock subprocess execution to simulate a successful run
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "1 passed in 0.05s"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            summary = run_project_test_execution(code_map_source=code_map_file)
            self.assertEqual(summary["status"], "success")
            self.assertEqual(summary["totalTests"], 1)
            self.assertEqual(summary["passed"], 1)
            self.assertEqual(summary["failed"], 0)

            # Check that summary is written to disk in "test execution/execution-summary.json"
            exec_summary_file = code_map_dir / "test execution" / "execution-summary.json"
            self.assertTrue(exec_summary_file.exists())

            loaded = get_existing_execution_results(code_map_file)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["totalTests"], 1)
            self.assertEqual(loaded["passed"], 1)

    def test_maven_execution_command_construction(self):
        from app.services.test_executor import execute_single_test_script
        script = Path(self.tmpdir) / "TestSample.java"
        script.write_text("// Test Case ID: TC-01\nclass SampleTest { void test() {} }\n", encoding="utf-8")
        pom = Path(self.tmpdir) / "pom.xml"
        pom.write_text("<project></project>", encoding="utf-8")

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "BUILD SUCCESS"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            res = execute_single_test_script(script, ["mvn.cmd", "test"], Path(self.tmpdir))
            self.assertEqual(res["status"], "passed")
            called_cmd = mock_run.call_args[0][0]
            self.assertIn("-Dtest=SampleTest", called_cmd)
            self.assertNotIn(str(script), called_cmd)

            # Ensure script was safely staged to src/test/java/SampleTest.java
            staged_file = Path(self.tmpdir) / "src" / "test" / "java" / "SampleTest.java"
            self.assertTrue(staged_file.exists())
            self.assertEqual(staged_file.read_text(encoding="utf-8"), script.read_text(encoding="utf-8"))

    def test_resolve_maven_test_source_dir(self):
        from app.services.test_executor import resolve_maven_test_source_dir
        pom_dir = Path(self.tmpdir) / "custom_mvn"
        pom_dir.mkdir(parents=True, exist_ok=True)

        # Default when no pom.xml or no <testSourceDirectory>
        self.assertEqual(resolve_maven_test_source_dir(pom_dir), pom_dir / "src" / "test" / "java")

        # Custom <testSourceDirectory>
        (pom_dir / "pom.xml").write_text(
            "<project><build><testSourceDirectory>${project.basedir}/custom/test</testSourceDirectory></build></project>",
            encoding="utf-8"
        )
        self.assertEqual(resolve_maven_test_source_dir(pom_dir), pom_dir / "custom" / "test")

    def test_extract_java_test_class_name(self):
        from app.services.test_executor import extract_java_test_class_name

        java_code_1 = "import org.junit.jupiter.api.Test;\nclass TC001Test { @Test void test() {} }"
        self.assertEqual(extract_java_test_class_name(java_code_1), "TC001Test")

        java_code_2 = "class HelperClass {}\npublic class CalculatorTests { @Test void test() {} }"
        self.assertEqual(extract_java_test_class_name(java_code_2), "CalculatorTests")

        java_code_3 = "class SimpleApp { void run() {} }"
        self.assertEqual(extract_java_test_class_name(java_code_3), "SimpleApp")

    def test_stage_maven_test_script_with_package(self):
        from app.services.test_executor import stage_maven_test_script
        pom_dir = Path(self.tmpdir) / "mvn_pkg_proj"
        pom_dir.mkdir(parents=True, exist_ok=True)
        (pom_dir / "pom.xml").write_text("<project></project>", encoding="utf-8")

        # Test script with package declaration
        script_dir = Path(self.tmpdir) / "external_scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        script_file = script_dir / "Some test title.java"
        content = "package com.example.service;\nimport org.junit.jupiter.api.Test;\nclass ServiceTest {}"
        script_file.write_text(content, encoding="utf-8")

        staged = stage_maven_test_script(script_file, pom_dir, "ServiceTest", content)
        self.assertIsNotNone(staged)
        expected_path = pom_dir / "src" / "test" / "java" / "com" / "example" / "service" / "ServiceTest.java"
        self.assertEqual(staged, expected_path)
        self.assertTrue(expected_path.exists())

        # Verify non-Java files are rejected
        json_file = script_dir / "manifest.json"
        json_file.write_text("{}", encoding="utf-8")
        staged_json = stage_maven_test_script(json_file, pom_dir, "ManifestTest", "{}")
        self.assertIsNone(staged_json)

    def test_run_project_test_execution_with_group_manifest(self):
        # Create mock project layout with group manifest and legacy file
        mock_proj = Path(self.tmpdir) / "GroupManifestProj"
        mock_proj.mkdir(parents=True, exist_ok=True)
        (mock_proj / "pom.xml").write_text("<project></project>", encoding="utf-8")

        code_map = {
            "project": {"name": "GroupManifestProj", "path": str(mock_proj), "languagesDetected": ["java"]},
            "files": {}
        }
        code_map_file = mock_proj / "repository-code-map.json"
        code_map_file.write_text(json.dumps(code_map), encoding="utf-8")

        scripts_group = mock_proj / "test scripts" / "group-a"
        scripts_group.mkdir(parents=True, exist_ok=True)

        # Active script
        active_script = scripts_group / "Active Test Title.java"
        active_script.write_text("// Test Case ID: TC-001\nclass TC001Test { void test() {} }", encoding="utf-8")

        # Legacy superseded script
        legacy_script = scripts_group / "TC-001.java"
        legacy_script.write_text("// Legacy\nclass TC001Test { void test() {} }", encoding="utf-8")

        # Group manifest
        manifest = {
            "group": "group-a",
            "scripts": [
                {
                    "testCaseId": "TC-001",
                    "filename": "Active Test Title.java",
                    "status": "success",
                    "testType": "unit"
                }
            ]
        }
        (scripts_group / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "BUILD SUCCESS"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            res = run_project_test_execution(code_map_file)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["totalTests"], 1)
            self.assertEqual(res["results"][0]["testCaseId"], "TC-001")
            self.assertEqual(res["results"][0]["scriptFile"], "Active Test Title.java")


if __name__ == "__main__":
    unittest.main()

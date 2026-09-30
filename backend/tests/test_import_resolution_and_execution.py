import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.services.import_resolver import (
    classify_import,
    extract_imports_from_script,
    detect_subproject_root,
    detect_js_module_system,
    resolve_project_local_import,
    validate_script_imports,
)
from app.services.test_executor import (
    categorize_execution_failure,
    detect_test_runner,
    execute_single_test_script,
)
from app.services.test_script_generator import resolve_target_import


class TestImportResolver(unittest.TestCase):
    """Unit tests for import classification, resolution, and validation."""

    def test_classify_import_standard_library(self):
        self.assertEqual(classify_import("os", "python"), "standard_library")
        self.assertEqual(classify_import("sys", "python"), "standard_library")
        self.assertEqual(classify_import("pathlib", "python"), "standard_library")
        self.assertEqual(classify_import("fs", "javascript"), "standard_library")
        self.assertEqual(classify_import("path", "javascript"), "standard_library")
        self.assertEqual(classify_import("node:crypto", "typescript"), "standard_library")
        self.assertEqual(classify_import("java.util.List", "java"), "standard_library")
        self.assertEqual(classify_import("javax.servlet.http.HttpServlet", "java"), "standard_library")
        self.assertEqual(classify_import("fmt", "go"), "standard_library")

    def test_classify_import_test_framework(self):
        self.assertEqual(classify_import("pytest", "python"), "test_framework")
        self.assertEqual(classify_import("unittest", "python"), "test_framework")
        self.assertEqual(classify_import("vitest", "javascript"), "test_framework")
        self.assertEqual(classify_import("jest", "javascript"), "test_framework")
        self.assertEqual(classify_import("@jest/globals", "typescript"), "test_framework")
        self.assertEqual(classify_import("supertest", "javascript"), "test_framework")
        self.assertEqual(classify_import("org.junit.jupiter.api.Test", "java"), "test_framework")
        self.assertEqual(classify_import("testing", "go"), "test_framework")

    def test_classify_import_project_local(self):
        self.assertEqual(classify_import("./utils", "javascript"), "project_local")
        self.assertEqual(classify_import("../services/auth", "typescript"), "project_local")
        self.assertEqual(classify_import("@/components/Button", "typescript"), "project_local")
        self.assertEqual(classify_import("~/utils/math", "javascript"), "project_local")
        self.assertEqual(classify_import(".submodule", "python"), "project_local")

    def test_classify_import_external_dependency(self):
        self.assertEqual(classify_import("express", "javascript"), "external_dependency")
        self.assertEqual(classify_import("react", "javascript"), "external_dependency")
        self.assertEqual(classify_import("fastapi", "python"), "external_dependency")
        self.assertEqual(classify_import("requests", "python"), "external_dependency")
        self.assertEqual(classify_import("org.apache.commons.lang3", "java"), "external_dependency")

    def test_extract_imports_from_script_python(self):
        code = """
import os
import sys as system
from app.services.calculator import add, subtract
from .local_mod import helper
"""
        imports = extract_imports_from_script(code, "python")
        specifiers = [i["specifier"] for i in imports]
        self.assertIn("os", specifiers)
        self.assertIn("sys", specifiers)
        self.assertIn("app.services.calculator", specifiers)
        self.assertIn(".local_mod", specifiers)

    def test_extract_imports_from_script_javascript(self):
        code = """
import { describe, it, expect } from 'vitest';
import express from 'express';
import { add } from './math';
const config = require('../config');
const { auth } = require('@/services/auth');
"""
        imports = extract_imports_from_script(code, "javascript")
        specifiers = [i["specifier"] for i in imports]
        self.assertIn("vitest", specifiers)
        self.assertIn("express", specifiers)
        self.assertIn("./math", specifiers)
        self.assertIn("../config", specifiers)
        self.assertIn("@/services/auth", specifiers)

    def test_detect_subproject_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backend = root / "backend"
            backend.mkdir()
            (backend / "package.json").write_text('{"name": "backend"}', encoding="utf-8")
            routes = backend / "routes"
            routes.mkdir()
            target = routes / "api.js"
            target.write_text("// api", encoding="utf-8")

            detected = detect_subproject_root(target, root)
            self.assertEqual(detected, backend)

    def test_detect_js_module_system(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "package.json").write_text('{"type": "commonjs"}', encoding="utf-8")
            cjs_file = root / "server.js"
            cjs_file.write_text("module.exports = { app };", encoding="utf-8")
            self.assertEqual(detect_js_module_system(cjs_file, root), "commonjs")

            esm_dir = root / "esm_proj"
            esm_dir.mkdir()
            (esm_dir / "package.json").write_text('{"type": "module"}', encoding="utf-8")
            esm_file = esm_dir / "index.js"
            esm_file.write_text("export default app;", encoding="utf-8")
            self.assertEqual(detect_js_module_system(esm_file, esm_dir), "esm")

    def test_resolve_project_local_import(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            src = root / "src"
            src.mkdir()
            utils_ts = src / "utils.ts"
            utils_ts.write_text("export function sum(a, b) { return a + b; }", encoding="utf-8")

            test_dir = root / "tests"
            test_dir.mkdir()
            script_path = test_dir / "test_utils.ts"

            res = resolve_project_local_import("../src/utils", script_path, root, "typescript")
            self.assertTrue(res["resolved"])
            self.assertEqual(res["resolved_path"], utils_ts)

    def test_validate_script_imports_detects_unresolved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            test_dir = root / "tests"
            test_dir.mkdir()
            script_path = test_dir / "test_broken.js"
            script_path.write_text("const { ghost } = require('./non_existent_file');", encoding="utf-8")

            res = validate_script_imports(script_path, root, "javascript")
            self.assertFalse(res["valid"])
            self.assertEqual(res["failureType"], "IMPORT_RESOLUTION_ERROR")
            self.assertEqual(res["original_import"], "./non_existent_file")


class TestTestExecutor(unittest.TestCase):
    """Unit tests for runner detection, process execution, and failure categorization."""

    def test_categorize_execution_failure(self):
        self.assertEqual(categorize_execution_failure(124, "", "timed out"), "TIMEOUT")
        self.assertEqual(categorize_execution_failure(1, "", "Execution timed out after 30s"), "TIMEOUT")
        self.assertEqual(
            categorize_execution_failure(1, "", "'vitest' is not recognized as an internal or external command"),
            "RUNNER_NOT_FOUND"
        )
        self.assertEqual(
            categorize_execution_failure(1, "", "The system cannot find the path specified."),
            "RUNNER_NOT_FOUND"
        )
        self.assertEqual(
            categorize_execution_failure(1, "", "ModuleNotFoundError: No module named 'jwt'"),
            "DEPENDENCY_ERROR"
        )
        self.assertEqual(
            categorize_execution_failure(1, "", "Cannot find module 'express'"),
            "DEPENDENCY_ERROR"
        )
        self.assertEqual(
            categorize_execution_failure(1, "", "ImportError: cannot import name 'calculate'"),
            "IMPORT_RESOLUTION_ERROR"
        )
        self.assertEqual(
            categorize_execution_failure(1, "", "ERROR collecting tests/test_math.py: compilation failure"),
            "TEST_COLLECTION_ERROR"
        )
        self.assertEqual(
            categorize_execution_failure(1, "AssertionError: 5 != 4", ""),
            "ASSERTION_FAILURE"
        )
        self.assertEqual(
            categorize_execution_failure(1, "FAIL  tests/unit.test.ts", ""),
            "ASSERTION_FAILURE"
        )
        self.assertEqual(
            categorize_execution_failure(1, "", "requests.exceptions.ConnectionError: [WinError 10061] No connection could be made because the target machine actively refused it"),
            "API_CONNECTION_ERROR"
        )
        self.assertEqual(
            categorize_execution_failure(1, "", "connect ECONNREFUSED 127.0.0.1:3000"),
            "API_CONNECTION_ERROR"
        )
        self.assertEqual(
            categorize_execution_failure(1, "", "pytest: error: unrecognized arguments: --invalid-flag"),
            "INVALID_RUNNER_INVOCATION"
        )
        self.assertEqual(
            categorize_execution_failure(1, "", "No test runner configured in package.json"),
            "RUNNER_NOT_CONFIGURED"
        )
        self.assertEqual(
            categorize_execution_failure(1, "", "The JAVA_HOME environment variable is not defined correctly"),
            "ENVIRONMENT_ERROR"
        )
        self.assertEqual(
            categorize_execution_failure(1, "", "fixture 'db_session' failed"),
            "TEST_SETUP_ERROR"
        )

    def test_detect_test_runner_python_unittest_or_pytest(self):
        runner, cmd, err = detect_test_runner(Path("."), language="python")
        self.assertIn(runner, ["pytest", "unittest"])
        self.assertIsNone(err)
        if runner == "unittest":
            self.assertIn("discover", cmd)

    def test_detect_test_runner_no_runner_configured(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pkg = root / "package.json"
            pkg.write_text('{"name": "no-runner", "dependencies": {}}', encoding="utf-8")

            with patch("shutil.which", return_value=None):
                runner, cmd, err = detect_test_runner(root, language="javascript")
                self.assertIsNone(runner)
                self.assertIsNone(cmd)
                self.assertIn("No test runner configured in package.json", err)

    def test_detect_test_runner_monorepo_subproject(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backend = root / "backend"
            backend.mkdir()
            pkg = backend / "package.json"
            pkg.write_text('{"name": "backend", "scripts": {"test": "jest"}}', encoding="utf-8")

            with patch("shutil.which", side_effect=lambda x, **kw: f"/bin/{x}"):
                runner, cmd, err = detect_test_runner(
                    root, language="javascript", target_file="backend/server.js"
                )
                self.assertIn(runner, ["jest", "npm test"])

    def test_execute_single_test_script_missing_script(self):
        res = execute_single_test_script(
            script_path=Path("non_existent_script.py"),
            runner_cmd=["python"],
            cwd=Path(".")
        )
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["failureType"], "SCRIPT_NOT_FOUND")

    def test_execute_single_test_script_invalid_cwd(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "test.py"
            script.write_text("assert True", encoding="utf-8")
            res = execute_single_test_script(
                script_path=script,
                runner_cmd=["python"],
                cwd=Path("non_existent_folder_xyz_123")
            )
            self.assertEqual(res["status"], "error")
            self.assertEqual(res["failureType"], "INVALID_WORKING_DIRECTORY")

    def test_execute_single_test_script_runner_not_found(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "test.py"
            script.write_text("assert True", encoding="utf-8")
            res = execute_single_test_script(
                script_path=script,
                runner_cmd=["non_existent_binary_xyz_123"],
                cwd=Path(temp_dir)
            )
            self.assertEqual(res["status"], "error")
            self.assertEqual(res["failureType"], "RUNNER_NOT_FOUND")


class TestScriptGeneratorIntegration(unittest.TestCase):
    """Unit tests for import resolution in script generator."""

    def test_resolve_target_import_commonjs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            src_file = root / "service.js"
            src_file.write_text("module.exports = { compute };", encoding="utf-8")
            script_dir = root / "tests"
            script_dir.mkdir()

            res = resolve_target_import(
                target_file="service.js",
                target_symbol="compute",
                script_output_dir=script_dir,
                project_root=root,
                language="javascript",
                source_code="module.exports = { compute };"
            )
            self.assertTrue(res["resolved"])
            self.assertIn("require(", res["import_statement"])
            self.assertIn("compute", res["import_statement"])

    def test_resolve_target_import_esm(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "package.json").write_text('{"type": "module"}', encoding="utf-8")
            src_file = root / "service.js"
            src_file.write_text("export function compute() {}", encoding="utf-8")
            script_dir = root / "tests"
            script_dir.mkdir()

            res = resolve_target_import(
                target_file="service.js",
                target_symbol="compute",
                script_output_dir=script_dir,
                project_root=root,
                language="javascript",
                source_code="export function compute() {}"
            )
            self.assertTrue(res["resolved"])
            self.assertIn("import { compute } from", res["import_statement"])


if __name__ == "__main__":
    unittest.main()

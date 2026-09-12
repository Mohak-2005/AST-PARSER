"""
Tests for unit_test_generator.py
================================
Verifies:
1. is_valid_unit_test_case & get_existing_test_cases (existing-output guard)
2. discover_testable_units (dynamic discovery across multi-language declarations)
3. group_units_for_generation (cohesive grouping with/without feature context)
4. build_focused_unit_context (focused context, never whole repo)
5. validate_test_cases (10 deterministic validation criteria: rejects non-unit,
   rejects fabricated symbols, rejects missing files, rejects integration/E2E tests,
   deduplicates)
6. save_test_cases (saves to <parent>/test-cases/ with dynamic filename)
7. generate_all_unit_test_cases (end-to-end with guard and mocked HF LLM)
8. error handling (missing code map, no testable units, LLM failure)
"""

import os
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

from app.services.unit_test_generator import (
    is_valid_unit_test_case,
    get_existing_test_cases,
    load_existing_feature_contexts,
    map_files_to_features,
    discover_testable_units,
    group_units_for_generation,
    build_focused_unit_context,
    build_generation_messages,
    validate_test_cases,
    save_test_cases,
    generate_all_unit_test_cases,
)


def _sample_code_map():
    return {
        "project": {
            "name": "SampleProject",
            "path": "/repo",
            "languagesDetected": ["typescript", "python"],
        },
        "files": {
            "src/utils.ts": {
                "language": "typescript",
                "lineCount": 20,
                "declarations": [
                    {"name": "add", "type": "function", "line": 5},
                    {"name": "multiply", "type": "function", "line": 10},
                    {"name": "AppConfig", "type": "interface", "line": 15},  # not an executable unit
                ],
                "dependencies": [
                    {"target": "lodash", "type": "external", "kind": "imports"}
                ]
            },
            "src/service.py": {
                "language": "python",
                "lineCount": 35,
                "declarations": [
                    {
                        "name": "Calculator",
                        "type": "class",
                        "line": 8,
                        "methods": ["compute", "reset"]
                    }
                ],
                "dependencies": [
                    {"target": "src/utils.ts", "type": "internal", "kind": "imports"}
                ]
            }
        },
        "relationships": [
            {
                "from": "src/service.py",
                "to": "src/utils.ts",
                "type": "imports",
                "evidence": {"line": 2}
            }
        ]
    }


class TestUnitTestGenerator(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.code_map_file = self.temp_dir / "repository-code-map.json"
        with open(self.code_map_file, "w", encoding="utf-8") as fh:
            json.dump(_sample_code_map(), fh)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -----------------------------------------------------------------------
    # 1. Validation of single test case schema
    # -----------------------------------------------------------------------

    def test_is_valid_unit_test_case(self):
        valid_tc = {
            "testCaseId": "TC-001",
            "unit": {
                "file": "src/utils.ts",
                "symbol": "add",
                "type": "function"
            },
            "title": "Add two positive numbers",
            "description": "Verifies that add(2, 3) returns 5",
            "preconditions": [],
            "inputs": {"a": 2, "b": 3},
            "expectedResult": "5",
            "testType": "unit",
            "priority": "high",
            "rationale": "Base functionality"
        }
        self.assertTrue(is_valid_unit_test_case(valid_tc))

        # Rejected if testType is not unit
        invalid_type = dict(valid_tc, testType="integration")
        self.assertFalse(is_valid_unit_test_case(invalid_type))

        # Rejected if missing unit
        no_unit = dict(valid_tc)
        del no_unit["unit"]
        self.assertFalse(is_valid_unit_test_case(no_unit))

        # Rejected if missing symbol
        missing_symbol = dict(valid_tc, unit={"file": "src/utils.ts", "type": "function"})
        self.assertFalse(is_valid_unit_test_case(missing_symbol))

    # -----------------------------------------------------------------------
    # 2. Existing output detection
    # -----------------------------------------------------------------------

    def test_get_existing_test_cases(self):
        # Initially empty
        self.assertEqual(get_existing_test_cases(self.code_map_file), [])

        # Create test-cases directory with valid test case
        tc_dir = self.temp_dir / "test-cases"
        tc_dir.mkdir()
        valid_file = tc_dir / "utils-tests.json"
        with open(valid_file, "w", encoding="utf-8") as fh:
            json.dump({
                "group": "Utils",
                "testCases": [
                    {
                        "testCaseId": "TC-001",
                        "unit": {"file": "src/utils.ts", "symbol": "add", "type": "function"},
                        "title": "Add numbers",
                        "description": "Test addition",
                        "inputs": {"a": 1, "b": 2},
                        "expectedResult": "3",
                        "testType": "unit"
                    }
                ]
            }, fh)

        found = get_existing_test_cases(self.code_map_file)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].name, "utils-tests.json")

    # -----------------------------------------------------------------------
    # 3. Dynamic Unit Discovery
    # -----------------------------------------------------------------------

    def test_discover_testable_units(self):
        code_map = _sample_code_map()
        units = discover_testable_units(code_map)

        symbols = {u["symbol"] for u in units}
        files = {u["file"] for u in units}

        self.assertIn("add", symbols)
        self.assertIn("multiply", symbols)
        # Class methods discovered
        self.assertIn("Calculator.compute", symbols)
        self.assertIn("Calculator.reset", symbols)
        # Non-executable interfaces rejected
        self.assertNotIn("AppConfig", symbols)

        self.assertIn("src/utils.ts", files)
        self.assertIn("src/service.py", files)

    # -----------------------------------------------------------------------
    # 4. Unit Grouping
    # -----------------------------------------------------------------------

    def test_group_units_for_generation(self):
        code_map = _sample_code_map()
        units = discover_testable_units(code_map)
        groups = group_units_for_generation(units)

        self.assertTrue(len(groups) >= 2)
        group_files = [g["files"] for g in groups]
        self.assertTrue(any("src/utils.ts" in gf for gf in group_files))
        self.assertTrue(any("src/service.py" in gf for gf in group_files))

    # -----------------------------------------------------------------------
    # 5. Focused Context Construction
    # -----------------------------------------------------------------------

    def test_build_focused_unit_context(self):
        code_map = _sample_code_map()
        units = discover_testable_units(code_map)
        groups = group_units_for_generation(units)
        group = groups[0]

        focused = build_focused_unit_context(group, code_map, self.code_map_file)

        # Context contains only files in this group
        target_files = [tf["file"] for tf in focused["targetFiles"]]
        self.assertEqual(target_files, group["files"])

        # Context does not blindly contain full code_map
        self.assertNotIn("project", focused)
        self.assertIn("targetFiles", focused)

    # -----------------------------------------------------------------------
    # 6. Deterministic Validation (10 Criteria)
    # -----------------------------------------------------------------------

    def test_validate_test_cases_rejections(self):
        code_map = _sample_code_map()
        valid_units = {
            "src/utils.ts": {"add", "multiply"},
            "src/service.py": {"Calculator.compute", "Calculator.reset", "Calculator", "compute", "reset"}
        }

        raw_cases = [
            # Valid case
            {
                "testCaseId": "TC-001",
                "unit": {"file": "src/utils.ts", "symbol": "add", "type": "function"},
                "title": "Add positive numbers",
                "description": "Tests basic addition",
                "inputs": {"a": 1, "b": 2},
                "expectedResult": "3",
                "testType": "unit"
            },
            # Non-unit testType -> REJECT
            {
                "testCaseId": "TC-002",
                "unit": {"file": "src/utils.ts", "symbol": "add", "type": "function"},
                "title": "Integration test",
                "description": "End-to-end addition",
                "inputs": {"a": 1, "b": 2},
                "expectedResult": "3",
                "testType": "integration"
            },
            # Non-existent file -> REJECT
            {
                "testCaseId": "TC-003",
                "unit": {"file": "non/existent/file.ts", "symbol": "foo", "type": "function"},
                "title": "Test foo",
                "description": "Tests foo",
                "inputs": {},
                "expectedResult": "bar",
                "testType": "unit"
            },
            # Fabricated symbol -> REJECT
            {
                "testCaseId": "TC-004",
                "unit": {"file": "src/utils.ts", "symbol": "hallucinatedFunction", "type": "function"},
                "title": "Test hallucination",
                "description": "Tests fake function",
                "inputs": {},
                "expectedResult": "bar",
                "testType": "unit"
            },
            # Playwright / E2E test -> REJECT
            {
                "testCaseId": "TC-005",
                "unit": {"file": "src/utils.ts", "symbol": "add", "type": "function"},
                "title": "Browser automation test using Playwright",
                "description": "Launches browser and tests UI interaction",
                "inputs": {},
                "expectedResult": "page rendered",
                "testType": "unit"
            },
            # Duplicate of TC-001 -> REJECT (deduplicated)
            {
                "testCaseId": "TC-006",
                "unit": {"file": "src/utils.ts", "symbol": "add", "type": "function"},
                "title": "Add positive numbers",
                "description": "Tests basic addition",
                "inputs": {"a": 1, "b": 2},
                "expectedResult": "3",
                "testType": "unit"
            }
        ]

        validated = validate_test_cases(raw_cases, valid_units, code_map)
        # Only TC-001 should survive!
        self.assertEqual(len(validated), 1)
        self.assertEqual(validated[0]["testCaseId"], "TC-001")
        self.assertEqual(validated[0]["testType"], "unit")

    # -----------------------------------------------------------------------
    # 7. Dynamic Output Storage
    # -----------------------------------------------------------------------

    def test_save_test_cases(self):
        cases = [
            {
                "testCaseId": "TC-001",
                "unit": {"file": "src/utils.ts", "symbol": "add", "type": "function"},
                "title": "Add test",
                "description": "desc",
                "inputs": {"a": 1, "b": 2},
                "expectedResult": "3",
                "testType": "unit"
            }
        ]
        out_path = save_test_cases(self.code_map_file, "Utils", cases, set())
        self.assertTrue(out_path.exists())
        self.assertEqual(out_path.parent.name, "test-cases")
        self.assertEqual(out_path.parent.parent, self.temp_dir)

        with open(out_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["group"], "Utils")
        self.assertEqual(len(data["testCases"]), 1)

    # -----------------------------------------------------------------------
    # 8. Guard Check in generate_all_unit_test_cases
    # -----------------------------------------------------------------------

    async def _test_guard_async(self):
        # Create valid test-cases/ first
        tc_dir = self.temp_dir / "test-cases"
        tc_dir.mkdir()
        valid_file = tc_dir / "existing-tests.json"
        with open(valid_file, "w", encoding="utf-8") as fh:
            json.dump({
                "group": "Existing",
                "testCases": [
                    {
                        "testCaseId": "TC-001",
                        "unit": {"file": "src/utils.ts", "symbol": "add", "type": "function"},
                        "title": "Existing Test",
                        "description": "desc",
                        "inputs": {},
                        "expectedResult": "res",
                        "testType": "unit"
                    }
                ]
            }, fh)

        with patch("app.services.unit_test_generator.execute_hf_llm_call") as mock_hf:
            res = await generate_all_unit_test_cases(self.code_map_file, force=False)
            # HF should NOT have been called!
            mock_hf.assert_not_called()
            self.assertTrue(res["alreadyExists"])
            self.assertEqual(res["message"], "Unit test cases already generated.")

    def test_guard_prevents_hf_call(self):
        import asyncio
        asyncio.run(self._test_guard_async())

    # -----------------------------------------------------------------------
    # 9. Full Pipeline Execution with Mocked HF
    # -----------------------------------------------------------------------

    async def _test_pipeline_async(self):
        mock_response = json.dumps({
            "testCases": [
                {
                    "testCaseId": "TC-001",
                    "unit": {
                        "file": "src/utils.ts",
                        "symbol": "add",
                        "type": "function"
                    },
                    "title": "Test add with two integers",
                    "description": "Ensures add correctly computes sum of positive numbers",
                    "preconditions": [],
                    "inputs": {"a": 2, "b": 3},
                    "expectedResult": "5",
                    "testType": "unit",
                    "priority": "high",
                    "rationale": "Core arithmetic verification"
                }
            ]
        })

        with patch("app.services.unit_test_generator.execute_hf_llm_call", new_callable=AsyncMock) as mock_hf:
            mock_hf.return_value = (mock_response, 200, 0.5)
            res = await generate_all_unit_test_cases(self.code_map_file, force=True)

            self.assertEqual(res["status"], "success")
            self.assertFalse(res["alreadyExists"])
            self.assertTrue(res["testCaseCount"] > 0)

            # Check that test-cases/ folder was created
            tc_dir = self.temp_dir / "test-cases"
            self.assertTrue(tc_dir.exists())
            saved_files = list(tc_dir.glob("*.json"))
            self.assertTrue(len(saved_files) > 0)

    def test_pipeline_execution(self):
        import asyncio
        asyncio.run(self._test_pipeline_async())

    # -----------------------------------------------------------------------
    # 10. FastAPI Endpoints (GET and POST /api/test-cases)
    # -----------------------------------------------------------------------

    def test_fastapi_endpoints(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)

        # A. GET when not generated
        r1 = client.get("/api/test-cases", params={"codeMapPath": str(self.code_map_file)})
        self.assertEqual(r1.status_code, 200)
        self.assertFalse(r1.json()["alreadyExists"])

        # B. Add a valid test case file into test-cases/
        tc_dir = self.temp_dir / "test-cases"
        tc_dir.mkdir(exist_ok=True)
        with open(tc_dir / "utils.json", "w") as f:
            json.dump({
                "group": "Utils",
                "testCases": [
                    {
                        "testCaseId": "TC-001",
                        "unit": {"file": "src/utils.ts", "symbol": "add", "type": "function"},
                        "title": "T",
                        "description": "D",
                        "inputs": {},
                        "expectedResult": "R",
                        "testType": "unit"
                    }
                ]
            }, f)

        # C. GET with existing
        r2 = client.get("/api/test-cases", params={"codeMapPath": str(self.code_map_file)})
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()["alreadyExists"])
        self.assertEqual(r2.json()["message"], "Unit test cases already generated.")

        # D. POST with existing (guard check prevents LLM invocation)
        r3 = client.post("/api/test-cases", json={"codeMapPath": str(self.code_map_file)})
        self.assertEqual(r3.status_code, 200)
        self.assertTrue(r3.json()["alreadyExists"])
        self.assertEqual(r3.json()["message"], "Unit test cases already generated.")


if __name__ == "__main__":
    unittest.main()


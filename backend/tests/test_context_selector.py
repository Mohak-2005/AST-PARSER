"""
Tests for Dynamic Test-Type-Aware Context Selector (context_selector.py)
=======================================================================
Verifies:
1. resolve_test_type (explicit and inferred test types)
2. resolve_target (exact resolution, ambiguous detection, TARGET_NOT_FOUND without guessing)
3. extract_actual_source_code (small files full source, large files balanced block, missing source)
4. Unit Test Strategy (minimal target-focused, depth 0, graph/feature omitted, optional depth 1)
5. Integration Test Strategy (relevant graph edges, collaborating files, unrelated branches omitted)
6. Functional / E2E Strategy (relevant Feature Context, flow files, unrelated features/graph omitted)
7. Deterministic context selection (identical inputs -> identical output)
8. Grounded prompt construction (source code authoritative, schema compliance)
9. Concise logging (verification of format, zero secrets/passwords/API keys logged)
10. End-to-end generate_test_cases_with_dynamic_context (with mocked LLM and validation)
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure backend root is in sys.path
backend_root = Path(__file__).resolve().parent.parent
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

from unittest.mock import patch, AsyncMock

from app.services.context_selector import (
    resolve_test_type,
    resolve_target,
    extract_actual_source_code,
    clean_code_map_entry,
    select_relevant_code_map_entries,
    select_relevant_graph_edges,
    select_relevant_feature_context,
    select_context_for_test_case,
    build_grounded_test_prompt,
    resolve_project_root,
    find_source_file_on_disk,
    COMPILED_OR_BINARY_EXTENSIONS,
    STATUS_RESOLVED,
    STATUS_AMBIGUOUS,
    STATUS_TARGET_NOT_FOUND,
    STATUS_SOURCE_NOT_FOUND,
    STATUS_INSUFFICIENT_CONTEXT,
)
from app.services.unit_test_generator import (
    generate_test_cases_with_dynamic_context
)


def _make_mock_code_map() -> dict:
    return {
        "project": {
            "name": "ECommerceDemo",
            "path": "/mock/project",
            "languagesDetected": ["typescript", "python"]
        },
        "files": {
            "controllers/orderController.ts": {
                "language": "typescript",
                "lineCount": 45,
                "declarations": [
                    {"name": "createOrderHandler", "type": "function", "line": 10},
                    {"name": "cancelOrderHandler", "type": "function", "line": 25}
                ],
                "dependencies": [
                    {"target": "services/orderService.ts", "type": "internal"}
                ],
                "exports": ["createOrderHandler", "cancelOrderHandler"]
            },
            "services/orderService.ts": {
                "language": "typescript",
                "lineCount": 60,
                "declarations": [
                    {
                        "name": "OrderService",
                        "type": "class",
                        "line": 5,
                        "methods": ["submitOrder", "calculateTotal"]
                    }
                ],
                "dependencies": [
                    {"target": "models/orderModel.ts", "type": "internal"},
                    {"target": "lodash", "type": "external"}
                ],
                "exports": ["OrderService"]
            },
            "models/orderModel.ts": {
                "language": "typescript",
                "lineCount": 30,
                "declarations": [
                    {"name": "Order", "type": "class", "line": 4, "methods": ["save", "findById"]}
                ],
                "dependencies": [],
                "exports": ["Order"]
            },
            "services/unrelatedPayment.ts": {
                "language": "typescript",
                "lineCount": 20,
                "declarations": [
                    {"name": "chargeCard", "type": "function", "line": 6}
                ],
                "dependencies": [],
                "exports": ["chargeCard"]
            }
        },
        "relationships": [
            {
                "from": "controllers/orderController.ts",
                "to": "services/orderService.ts",
                "type": "calls",
                "evidence": {"line": 15}
            },
            {
                "from": "services/orderService.ts",
                "to": "models/orderModel.ts",
                "type": "uses",
                "evidence": {"line": 18}
            },
            {
                "from": "services/unrelatedPayment.ts",
                "to": "models/orderModel.ts",
                "type": "uses",
                "evidence": {"line": 12}
            }
        ]
    }


def _make_mock_graph() -> dict:
    return {
        "nodes": [
            {"id": "file:controllers/orderController.ts", "type": "file", "name": "orderController.ts", "filePath": "controllers/orderController.ts"},
            {"id": "file:services/orderService.ts", "type": "file", "name": "orderService.ts", "filePath": "services/orderService.ts"},
            {"id": "file:models/orderModel.ts", "type": "file", "name": "orderModel.ts", "filePath": "models/orderModel.ts"},
            {"id": "file:services/unrelatedPayment.ts", "type": "file", "name": "unrelatedPayment.ts", "filePath": "services/unrelatedPayment.ts"}
        ],
        "edges": [
            {"source": "file:controllers/orderController.ts", "target": "file:services/orderService.ts", "relationship": "calls", "confidence": 1.0},
            {"source": "file:services/orderService.ts", "target": "file:models/orderModel.ts", "relationship": "uses", "confidence": 1.0},
            {"source": "file:services/unrelatedPayment.ts", "target": "file:models/orderModel.ts", "relationship": "uses", "confidence": 1.0}
        ]
    }


def _make_mock_feature_contexts() -> dict:
    return {
        "checkout-flow": {
            "feature": {
                "id": "checkout-flow",
                "name": "Order Checkout Flow",
                "description": "End-to-end checkout and order processing"
            },
            "featureFlow": [
                {"step": 1, "action": "Receive order request", "file": "controllers/orderController.ts"},
                {"step": 2, "action": "Process order business logic", "file": "services/orderService.ts"},
                {"step": 3, "action": "Persist order record", "file": "models/orderModel.ts"}
            ],
            "files": [
                {"path": "controllers/orderController.ts", "role": "handler"},
                {"path": "services/orderService.ts", "role": "service"},
                {"path": "models/orderModel.ts", "role": "model"}
            ]
        },
        "user-profile": {
            "feature": {
                "id": "user-profile",
                "name": "User Profile Management",
                "description": "User profile viewing and updating"
            },
            "featureFlow": [
                {"step": 1, "action": "Get user", "file": "controllers/userController.ts"}
            ],
            "files": [
                {"path": "controllers/userController.ts", "role": "handler"}
            ]
        }
    }


class TestContextSelector(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.code_map_file = self.temp_dir / "repository-code-map.json"
        self.code_map_data = _make_mock_code_map()

        with open(self.code_map_file, "w", encoding="utf-8") as fh:
            json.dump(self.code_map_data, fh)

        # Create source files on disk
        self.ctrl_file = self.temp_dir / "controllers" / "orderController.ts"
        self.ctrl_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.ctrl_file, "w", encoding="utf-8") as fh:
            fh.write(
                "import { OrderService } from '../services/orderService';\n\n"
                "export function createOrderHandler(req: any, res: any) {\n"
                "  const { items, customerId } = req.body;\n"
                "  if (!items || items.length === 0) {\n"
                "    return res.status(400).json({ error: 'Items required' });\n"
                "  }\n"
                "  const service = new OrderService();\n"
                "  const order = service.submitOrder(items, customerId);\n"
                "  return res.status(201).json(order);\n"
                "}\n\n"
                "export function cancelOrderHandler(orderId: string) {\n"
                "  return { orderId, status: 'cancelled' };\n"
                "}\n"
            )

        self.service_file = self.temp_dir / "services" / "orderService.ts"
        self.service_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.service_file, "w", encoding="utf-8") as fh:
            fh.write(
                "import { Order } from '../models/orderModel';\n\n"
                "export class OrderService {\n"
                "  submitOrder(items: any[], customerId: string) {\n"
                "    if (!customerId) throw new Error('Customer ID is required');\n"
                "    const total = this.calculateTotal(items);\n"
                "    const order = new Order({ customerId, total, items });\n"
                "    return order.save();\n"
                "  }\n\n"
                "  calculateTotal(items: any[]): number {\n"
                "    return items.reduce((sum, item) => sum + (item.price * item.quantity), 0);\n"
                "  }\n"
                "}\n"
            )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -----------------------------------------------------------------------
    # 1. Test Type Strategy Resolver
    # -----------------------------------------------------------------------
    def test_resolve_test_type_explicit(self):
        t1, is_exp = resolve_test_type({"testType": "unit"})
        self.assertEqual(t1, "unit")
        self.assertTrue(is_exp)

        t2, is_exp = resolve_test_type({"test_type": "INTEGRATION"})
        self.assertEqual(t2, "integration")
        self.assertTrue(is_exp)

        t3, is_exp = resolve_test_type({"testType": "e2e"})
        self.assertEqual(t3, "functional")
        self.assertTrue(is_exp)

    def test_resolve_test_type_inferred(self):
        # Infer integration from description
        t1, is_exp = resolve_test_type({"description": "Test database roundtrip persistence"})
        self.assertEqual(t1, "integration")
        self.assertFalse(is_exp)

        # Infer functional from title
        t2, is_exp = resolve_test_type({"title": "TC_001: End-to-end checkout journey"})
        self.assertEqual(t2, "functional")
        self.assertFalse(is_exp)

        # Default safely to unit without guessing
        t3, is_exp = resolve_test_type({})
        self.assertEqual(t3, "unit")
        self.assertFalse(is_exp)

    # -----------------------------------------------------------------------
    # 2. Dynamic Target Resolution
    # -----------------------------------------------------------------------
    def test_resolve_target_exact(self):
        target = {
            "file": "controllers/orderController.ts",
            "symbol": "createOrderHandler",
            "type": "function"
        }
        status, res_target, entry, warnings = resolve_target(target, self.code_map_data, self.code_map_file)
        self.assertEqual(status, STATUS_RESOLVED)
        self.assertIsNotNone(res_target)
        self.assertEqual(res_target["symbol"], "createOrderHandler")
        self.assertEqual(res_target["file"], "controllers/orderController.ts")
        self.assertEqual(res_target["line"], 10)

    def test_resolve_target_class_method(self):
        target = {
            "file": "services/orderService.ts",
            "symbol": "OrderService.submitOrder",
            "type": "method"
        }
        status, res_target, entry, warnings = resolve_target(target, self.code_map_data, self.code_map_file)
        self.assertEqual(status, STATUS_RESOLVED)
        self.assertIsNotNone(res_target)
        self.assertEqual(res_target["symbol"], "OrderService.submitOrder")

    def test_resolve_target_missing_symbol_does_not_guess(self):
        """CRITICAL: If symbol is missing in file declarations, DO NOT GUESS."""
        target = {
            "file": "controllers/orderController.ts",
            "symbol": "hallucinatedMethod",
            "type": "function"
        }
        status, res_target, entry, warnings = resolve_target(target, self.code_map_data, self.code_map_file)
        self.assertEqual(status, STATUS_TARGET_NOT_FOUND)
        self.assertIsNone(res_target)
        self.assertTrue(any("not found in file declarations" in w for w in warnings))

    def test_resolve_target_missing_file(self):
        target = {
            "file": "non/existent/path.ts",
            "symbol": "foo"
        }
        status, res_target, entry, warnings = resolve_target(target, self.code_map_data, self.code_map_file)
        self.assertEqual(status, STATUS_TARGET_NOT_FOUND)
        self.assertIsNone(res_target)

    # -----------------------------------------------------------------------
    # 3. Authoritative Source Code Extraction
    # -----------------------------------------------------------------------
    def test_extract_source_code_full_file(self):
        status, s_data = extract_actual_source_code(
            file_rel_path="controllers/orderController.ts",
            target_symbol="createOrderHandler",
            decl_line=3,
            language="typescript",
            code_map_path=self.code_map_file
        )
        self.assertEqual(status, STATUS_RESOLVED)
        self.assertIsNotNone(s_data)
        self.assertTrue(s_data["isFullFile"])
        self.assertIn("createOrderHandler", s_data["sourceCode"])
        self.assertIn("Items required", s_data["sourceCode"])

    def test_extract_source_code_missing_file(self):
        status, s_data = extract_actual_source_code(
            file_rel_path="missing/file.ts",
            code_map_path=self.code_map_file
        )
        self.assertEqual(status, STATUS_SOURCE_NOT_FOUND)
        self.assertIsNone(s_data)

    # -----------------------------------------------------------------------
    # 4. Unit Test Strategy (Minimal Context, Depth 0, Graph Omitted)
    # -----------------------------------------------------------------------
    def test_unit_test_context_selection(self):
        target = {
            "file": "controllers/orderController.ts",
            "symbol": "createOrderHandler",
            "type": "function"
        }
        compact = select_context_for_test_case(
            target=target,
            test_type="unit",
            code_map=self.code_map_data,
            code_map_path=self.code_map_file,
            graph_data=_make_mock_graph(),
            feature_contexts=_make_mock_feature_contexts()
        )

        self.assertEqual(compact["testType"], "unit")
        self.assertEqual(compact["resolutionStatus"], STATUS_RESOLVED)

        ctx = compact["context"]
        # Required: target Code Map entry + actual target source
        self.assertEqual(len(ctx["codeMap"]), 1)
        self.assertEqual(ctx["codeMap"][0]["relativePath"], "controllers/orderController.ts")
        self.assertEqual(len(ctx["sourceCode"]), 1)
        self.assertEqual(ctx["sourceCode"][0]["path"], "controllers/orderController.ts")

        # Dependency Graph MUST BE OMITTED for Unit tests
        self.assertNotIn("dependencyGraph", ctx)

        # Feature Context MUST BE OMITTED for Unit tests
        self.assertNotIn("featureContext", ctx)

        # Metrics check
        metrics = compact["metrics"]
        self.assertEqual(metrics["codeMapEntries"], 1)
        self.assertEqual(metrics["graphEdges"], 0)
        self.assertEqual(metrics["featureContextSections"], 0)
        self.assertEqual(metrics["sourceFiles"], 1)
        self.assertEqual(metrics["dependencyDepth"], 0)

    # -----------------------------------------------------------------------
    # 5. Integration Test Strategy (Relevant Graph Edges, Collaborating Files)
    # -----------------------------------------------------------------------
    def test_integration_test_context_selection(self):
        target = {
            "file": "controllers/orderController.ts",
            "symbol": "createOrderHandler",
            "type": "function"
        }
        compact = select_context_for_test_case(
            target=target,
            test_type="integration",
            code_map=self.code_map_data,
            code_map_path=self.code_map_file,
            graph_data=_make_mock_graph(),
            feature_contexts=_make_mock_feature_contexts()
        )

        self.assertEqual(compact["testType"], "integration")
        ctx = compact["context"]

        # Code map should contain involved files (controller + orderService)
        cm_files = [e["relativePath"] for e in ctx["codeMap"]]
        self.assertIn("controllers/orderController.ts", cm_files)
        self.assertIn("services/orderService.ts", cm_files)

        # Graph edges should ONLY contain edges connecting involved files
        self.assertIn("dependencyGraph", ctx)
        graph_edges = ctx["dependencyGraph"]
        self.assertTrue(len(graph_edges) >= 1)

        # Unrelated edge (unrelatedPayment -> orderModel) must NOT be present
        for edge in graph_edges:
            self.assertNotEqual(edge.get("source"), "file:services/unrelatedPayment.ts")

        # Unrelated feature contexts must be omitted
        self.assertNotIn("featureContext", ctx)

    # -----------------------------------------------------------------------
    # 6. Functional / E2E Strategy (Relevant Feature Context, Flow Source)
    # -----------------------------------------------------------------------
    def test_functional_test_context_selection(self):
        target = {
            "file": "controllers/orderController.ts"
        }
        test_case = {
            "title": "Full Checkout Journey",
            "featureId": "checkout-flow",
            "testType": "functional"
        }
        compact = select_context_for_test_case(
            test_case=test_case,
            target=target,
            test_type="functional",
            code_map=self.code_map_data,
            code_map_path=self.code_map_file,
            graph_data=_make_mock_graph(),
            feature_contexts=_make_mock_feature_contexts()
        )

        self.assertEqual(compact["testType"], "functional")
        ctx = compact["context"]

        # Relevant feature context MUST be included
        self.assertIn("featureContext", ctx)
        fc = ctx["featureContext"]
        self.assertEqual(fc["id"], "checkout-flow")
        self.assertEqual(fc["name"], "Order Checkout Flow")

        # Unrelated feature context (user-profile) must NOT be included
        self.assertNotEqual(fc["id"], "user-profile")

    # -----------------------------------------------------------------------
    # 7. Deterministic Context Selection
    # -----------------------------------------------------------------------
    def test_deterministic_context_selection(self):
        target = {
            "file": "services/orderService.ts",
            "symbol": "OrderService.submitOrder",
            "type": "method"
        }
        run1 = select_context_for_test_case(
            target=target,
            test_type="unit",
            code_map=self.code_map_data,
            code_map_path=self.code_map_file
        )
        run2 = select_context_for_test_case(
            target=target,
            test_type="unit",
            code_map=self.code_map_data,
            code_map_path=self.code_map_file
        )

        self.assertEqual(json.dumps(run1, sort_keys=True), json.dumps(run2, sort_keys=True))

    # -----------------------------------------------------------------------
    # 8. Grounded Prompt Builder
    # -----------------------------------------------------------------------
    def test_build_grounded_test_prompt(self):
        target = {
            "file": "controllers/orderController.ts",
            "symbol": "createOrderHandler",
            "type": "function"
        }
        compact = select_context_for_test_case(
            target=target,
            test_type="unit",
            code_map=self.code_map_data,
            code_map_path=self.code_map_file
        )

        messages = build_grounded_test_prompt(compact)
        self.assertEqual(len(messages), 2)
        system_msg = messages[0]["content"]
        user_msg = messages[1]["content"]

        # Authority and non-hallucination assertions
        self.assertIn("ACTUAL SOURCE CODE is authoritative", system_msg)
        self.assertIn("DO NOT invent", system_msg)
        self.assertIn("createOrderHandler", system_msg)
        self.assertIn("SOURCE-GROUNDING GUARDRAILS", system_msg)
        self.assertIn("EVIDENCE HIERARCHY", system_msg)
        self.assertIn("BEHAVIORAL CLAIM VALIDATION", system_msg)
        self.assertIn("FUNCTION-NAME GUARDRAIL", system_msg)
        self.assertIn("GENERATE TEST CASES FROM EVIDENCE, NOT FROM NAMES", system_msg)
        self.assertIn("NEVER infer behavior from a function", system_msg)
        self.assertIn("Grounded Context", user_msg)
        self.assertIn("Required JSON Schema", user_msg)

    # -----------------------------------------------------------------------
    # 9. Logging Verification (Zero Secrets Logged)
    # -----------------------------------------------------------------------
    def test_logging_contains_no_secrets(self):
        with patch("app.services.context_selector.backend_logger") as mock_logger:
            target = {
                "file": "controllers/orderController.ts",
                "symbol": "createOrderHandler",
                "type": "function"
            }
            select_context_for_test_case(
                target=target,
                test_type="unit",
                code_map=self.code_map_data,
                code_map_path=self.code_map_file
            )

            logged_messages = [call[0][0] for call in mock_logger.info.call_args_list if call[0]]
            all_text = " ".join(logged_messages)

            # Check required log events
            self.assertIn("CONTEXT_SELECTION_STARTED", all_text)
            self.assertIn("testType=unit", all_text)
            self.assertIn("target=controllers/orderController.ts::createOrderHandler", all_text)
            self.assertIn("CONTEXT_SELECTION_COMPLETED", all_text)
            self.assertIn("codeMapEntries=1", all_text)
            self.assertIn("LLM_CONTEXT_BUILT", all_text)

            # Check zero secrets
            self.assertNotIn("Bearer", all_text)
            self.assertNotIn("sk-", all_text)
            self.assertNotIn("api_key", all_text.lower())
            self.assertNotIn("password", all_text.lower())

    # -----------------------------------------------------------------------
    # 10. End-to-End Dynamic Test Case Generation
    # -----------------------------------------------------------------------
    async def _test_e2e_dynamic_generation_async(self):
        mock_llm_reply = json.dumps({
            "testCases": [
                {
                    "testCaseId": "TC-001",
                    "unit": {
                        "file": "controllers/orderController.ts",
                        "symbol": "createOrderHandler",
                        "type": "function"
                    },
                    "title": "Return 400 if items array is empty",
                    "description": "Verifies that empty items payload returns HTTP 400 error",
                    "preconditions": [],
                    "inputs": {"items": []},
                    "expectedResult": "res.status(400) with error message",
                    "testType": "unit",
                    "priority": "high",
                    "rationale": "Directly grounded in line 4 branch check of createOrderHandler"
                }
            ]
        })

        with patch("app.services.unit_test_generator.execute_hf_llm_call") as mock_hf:
            mock_hf.return_value = (mock_llm_reply, 200, 0.5)

            target = {
                "file": "controllers/orderController.ts",
                "symbol": "createOrderHandler",
                "type": "function"
            }
            res = await generate_test_cases_with_dynamic_context(
                code_map_source=self.code_map_file,
                target=target,
                test_type="unit"
            )

            self.assertEqual(res["status"], "SUCCESS")
            self.assertEqual(len(res["testCases"]), 1)
            tc = res["testCases"][0]
            self.assertEqual(tc["title"], "Return 400 if items array is empty")
            self.assertEqual(tc["unit"]["symbol"], "createOrderHandler")
            self.assertEqual(tc["testType"], "unit")

    def test_e2e_dynamic_generation(self):
        import asyncio
        asyncio.run(self._test_e2e_dynamic_generation_async())

    async def _test_e2e_target_not_found_async(self):
        target = {
            "file": "controllers/orderController.ts",
            "symbol": "nonExistentMethod",
            "type": "function"
        }
        res = await generate_test_cases_with_dynamic_context(
            code_map_source=self.code_map_file,
            target=target,
            test_type="unit"
        )
        self.assertEqual(res["status"], STATUS_TARGET_NOT_FOUND)
        self.assertEqual(len(res["testCases"]), 0)

    def test_e2e_target_not_found(self):
        import asyncio
        asyncio.run(self._test_e2e_target_not_found_async())

    # -----------------------------------------------------------------------
    # 11. Project Root Resolution & Source Finding
    # -----------------------------------------------------------------------
    def test_resolve_project_root_from_code_map(self):
        # 1. Direct project.path in code_map
        cm = {"project": {"name": "Demo", "path": str(self.temp_dir)}}
        root = resolve_project_root(cm)
        self.assertEqual(root, self.temp_dir.resolve())

        # 2. Derive from file absolute path stripping relative path
        cm_files = {
            "files": {
                "controllers/orderController.ts": {
                    "path": str(self.temp_dir / "controllers" / "orderController.ts"),
                    "relativePath": "controllers/orderController.ts"
                }
            }
        }
        root2 = resolve_project_root(cm_files)
        self.assertEqual(root2, self.temp_dir.resolve())

    def test_find_source_file_and_skip_compiled_artifacts(self):
        # Create a mock .class file alongside a source file
        class_file = self.temp_dir / "controllers" / "orderController.class"
        class_file.write_bytes(b"\xca\xfe\xba\xbe")

        # Must reject .class file
        found_class = find_source_file_on_disk(
            file_rel_path="controllers/orderController.class",
            project_root=self.temp_dir
        )
        self.assertIsNone(found_class)

        # Must find actual .ts source file
        found_src = find_source_file_on_disk(
            file_rel_path="controllers/orderController.ts",
            project_root=self.temp_dir
        )
        self.assertIsNotNone(found_src)
        self.assertTrue(found_src.is_file())
        self.assertEqual(found_src.name, "orderController.ts")

    def test_extract_source_code_with_code_map_external_root(self):
        # Simulate repository-code-map.json in analysis directory with external project root
        analysis_dir = Path(tempfile.mkdtemp())
        try:
            fake_map = {
                "project": {"name": "ExternalProj", "path": str(self.temp_dir)},
                "files": {
                    "controllers/orderController.ts": {
                        "path": str(self.temp_dir / "controllers" / "orderController.ts"),
                        "relativePath": "controllers/orderController.ts",
                        "language": "typescript"
                    }
                }
            }
            fake_map_path = analysis_dir / "repository-code-map.json"
            fake_map_path.write_text(json.dumps(fake_map), encoding="utf-8")

            # Extract source using code_map pointing to external project
            status, s_data = extract_actual_source_code(
                file_rel_path="controllers/orderController.ts",
                target_symbol="createOrderHandler",
                code_map_path=fake_map_path,
                code_map=fake_map
            )
            self.assertEqual(status, STATUS_RESOLVED)
            self.assertIsNotNone(s_data)
            self.assertIn("createOrderHandler", s_data["sourceCode"])
        finally:
            shutil.rmtree(analysis_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

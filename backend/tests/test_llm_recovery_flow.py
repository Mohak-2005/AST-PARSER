import unittest
from unittest.mock import patch, AsyncMock
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.llm_service import generate_dependency_graph

class TestLlmRecoveryFlow(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.llm_service._execute_openrouter_call")
    async def test_llm_repair_request_flow(self, mock_execute):
        # 1st call (initial generation): broken JSON that deterministic repair cannot fix
        broken_json = '{"nodes": [ {"id": "node1", "name": "app.js", broken_syntax here'
        # 2nd call (repair request): valid JSON returned by LLM repair
        repaired_json = '{"nodes": [{"id": "node1", "name": "app.js"}], "edges": []}'

        mock_execute.side_effect = [
            (broken_json, 200, 1.0),
            (repaired_json, 200, 0.5)
        ]

        code_map = {
            "project": {"name": "test_proj", "path": "/test"},
            "files": [],
            "relationships": []
        }

        result = await generate_dependency_graph(code_map, analysis_id="test_repair_flow")
        self.assertIsNotNone(result)
        self.assertEqual(len(result["nodes"]), 1)
        self.assertEqual(result["nodes"][0]["id"], "node1")
        # Should have called LLM twice: initial + repair
        self.assertEqual(mock_execute.call_count, 2)

    @patch("app.services.llm_service._execute_openrouter_call")
    async def test_graph_retry_flow(self, mock_execute):
        # 1st call: broken JSON
        # 2nd call (repair request): still broken JSON
        # 3rd call (graph retry): valid JSON!
        broken_json_1 = '{"nodes": [ broken1'
        broken_json_2 = '{"nodes": [ broken2'
        valid_retry = '{"nodes": [{"id": "retry_node", "name": "retry.js"}], "edges": []}'

        mock_execute.side_effect = [
            (broken_json_1, 200, 1.0),
            (broken_json_2, 200, 0.5),
            (valid_retry, 200, 1.2)
        ]

        code_map = {
            "project": {"name": "test_proj", "path": "/test"},
            "files": [],
            "relationships": []
        }

        result = await generate_dependency_graph(code_map, analysis_id="test_retry_flow")
        self.assertIsNotNone(result)
        self.assertEqual(result["nodes"][0]["id"], "retry_node")
        self.assertEqual(mock_execute.call_count, 3)

    @patch("app.services.llm_service._execute_openrouter_call")
    async def test_all_stages_fail_raises_controlled_error(self, mock_execute):
        # All calls return unrecoverable text
        mock_execute.return_value = ("Completely unrecoverable garbage text", 200, 1.0)

        code_map = {
            "project": {"name": "test_proj", "path": "/test"},
            "files": [],
            "relationships": []
        }

        with self.assertRaises(ValueError) as ctx:
            await generate_dependency_graph(code_map, analysis_id="test_all_fail")
        
        self.assertIn("LLM JSON recovery failed", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()

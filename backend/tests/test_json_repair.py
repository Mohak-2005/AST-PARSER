import unittest
import json
import sys
from pathlib import Path

# Add backend directory to sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.json_repair_service import (
    strip_markdown_fences,
    extract_json_object,
    parse_json,
    repair_json_deterministically
)
from app.services.llm_service import (
    parse_and_recover_json,
    GRAPH_SCHEMA,
    build_user_prompt
)

class TestJsonRepairService(unittest.TestCase):
    def test_valid_json(self):
        sample = '{"nodes": [{"id": "1", "name": "app.js"}], "edges": []}'
        ok, data, err = parse_json(sample)
        self.assertTrue(ok)
        self.assertEqual(len(data["nodes"]), 1)

    def test_strip_markdown_fences(self):
        sample = "```json\n{\"nodes\": [], \"edges\": []}\n```"
        stripped = strip_markdown_fences(sample)
        ok, data, _ = parse_json(stripped)
        self.assertTrue(ok)
        self.assertEqual(data, {"nodes": [], "edges": []})

    def test_strip_markdown_fences_with_surrounding_text(self):
        sample = "Here is the result:\n```json\n{\"nodes\": [{\"id\": \"node1\"}], \"edges\": []}\n```\nHope it helps!"
        stripped = strip_markdown_fences(sample)
        ok, data, _ = parse_json(stripped)
        self.assertTrue(ok)
        self.assertEqual(len(data["nodes"]), 1)

    def test_extract_json_object(self):
        sample = 'Prefix text before JSON {"nodes": [{"id": "a", "desc": "bracket {inside} string"}], "edges": []} suffix text after'
        extracted = extract_json_object(sample)
        ok, data, _ = parse_json(extracted)
        self.assertTrue(ok)
        self.assertEqual(data["nodes"][0]["desc"], "bracket {inside} string")

    def test_trailing_commas_repair(self):
        sample = '{"nodes": [{"id": "1", "name": "test",},], "edges": [],}'
        ok, data, strategy, _ = repair_json_deterministically(sample)
        self.assertTrue(ok)
        self.assertIn("TRAILING_COMMAS", strategy)
        self.assertEqual(data["nodes"][0]["name"], "test")

    def test_unescaped_windows_path_repair(self):
        sample = '{"nodes": [{"id": "file:app.js", "filePath": "C:\\Users\\Mohak\\Desktop\\app.js"}], "edges": []}'
        ok, data, strategy, _ = repair_json_deterministically(sample)
        self.assertTrue(ok)
        self.assertEqual(data["nodes"][0]["id"], "file:app.js")

    def test_unclosed_structure_repair(self):
        sample = '{"nodes": [{"id": "1", "name": "app.js"}], "edges": ['
        ok, data, strategy, _ = repair_json_deterministically(sample)
        self.assertTrue(ok)
        self.assertIn("BALANCE_UNCLOSED", strategy)
        self.assertEqual(data["edges"], [])
        self.assertEqual(data["nodes"][0]["id"], "1")

    def test_unclosed_object_repair(self):
        sample = '{"nodes": [{"id": "1", "name": "app.js"'
        ok, data, strategy, _ = repair_json_deterministically(sample)
        self.assertTrue(ok)
        self.assertIn("BALANCE_UNCLOSED", strategy)
        self.assertEqual(data["nodes"][0]["name"], "app.js")

    def test_completely_invalid_fails_safely(self):
        sample = 'This is complete gibberish with no JSON structures at all.'
        ok, data, strategy, err_info = repair_json_deterministically(sample)
        self.assertFalse(ok)
        self.assertIsNone(data)

    def test_parse_and_recover_valid(self):
        sample = '{"nodes": [{"id": "1", "type": "file", "name": "index.js"}], "edges": []}'
        ok, data, method, diag = parse_and_recover_json(sample, analysis_id="test-1")
        self.assertTrue(ok)
        self.assertEqual(method, "DIRECT_PARSE")
        self.assertEqual(len(data["nodes"]), 1)

    def test_parse_and_recover_trailing_commas(self):
        sample = '{"nodes": [{"id": "1", "type": "file", "name": "index.js",},], "edges": [],}'
        ok, data, strategy, diag = parse_and_recover_json(sample, analysis_id="test-2")
        self.assertTrue(ok)
        self.assertIn("TRAILING_COMMAS", strategy)
        self.assertEqual(len(data["nodes"]), 1)

    def test_parse_and_recover_markdown_wrapped(self):
        sample = "```json\n{\"nodes\": [{\"id\": \"1\", \"name\": \"app.js\"}], \"edges\": []}\n```"
        ok, data, method, diag = parse_and_recover_json(sample, analysis_id="test-3")
        self.assertTrue(ok)
        self.assertEqual(method, "DIRECT_PARSE")

    def test_build_user_prompt(self):
        code_map = {
            "project": {"name": "test"},
            "files": [],
            "relationships": []
        }
        prompt = build_user_prompt(code_map)
        self.assertIn("Analyze this repository Code Map", prompt)
        self.assertIn('"nodes":', prompt)
        self.assertIn('"edges":', prompt)

if __name__ == "__main__":
    unittest.main()

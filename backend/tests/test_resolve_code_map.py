import unittest
from unittest.mock import patch, AsyncMock
import tempfile
import shutil
import json
from pathlib import Path
from fastapi.testclient import TestClient

from app.main import app
from app.routes.analyze import _resolve_code_map_source, _find_code_map_on_disk

class TestResolveCodeMap(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.temp_dir = Path(tempfile.mkdtemp())
        self.code_map_file = self.temp_dir / "repository-code-map.json"
        self.sample_code_map = {
            "project": {
                "name": "test_project",
                "path": str(self.temp_dir),
                "codeMapPath": str(self.code_map_file),
            },
            "files": {
                "src/main.py": {"classes": [], "functions": []}
            },
            "relationships": []
        }
        with open(self.code_map_file, "w", encoding="utf-8") as f:
            json.dump(self.sample_code_map, f)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_direct_valid_file_path(self):
        result = await _resolve_code_map_source(str(self.code_map_file), None)
        self.assertEqual(Path(result).resolve(), self.code_map_file.resolve())

    async def test_direct_valid_directory_path(self):
        result = await _resolve_code_map_source(str(self.temp_dir), None)
        self.assertEqual(Path(result).resolve(), self.code_map_file.resolve())

    async def test_sanitization_of_null_and_undefined(self):
        # Both "null" and "undefined" as string should be treated as None
        result = await _resolve_code_map_source("null", "undefined")
        self.assertIsNone(result)

        result = await _resolve_code_map_source("undefined", "none")
        self.assertIsNone(result)

        result = await _resolve_code_map_source("   ", "")
        self.assertIsNone(result)

    @patch("app.routes.analyze.get_graph", new_callable=AsyncMock)
    async def test_resolve_by_project_id_with_empty_db_codemap(self, mock_get_graph):
        # Even if DB has codeMap = {}, it should resolve to file on disk via cand_path or output dir
        mock_get_graph.return_value = {
            "projectId": "proj-123",
            "projectName": "test_project",
            "projectPath": str(self.temp_dir),
            "codeMap": {},
            "nodes": [],
            "edges": []
        }
        result = await _resolve_code_map_source(None, "proj-123")
        self.assertIsNotNone(result)
        self.assertEqual(Path(result).resolve(), self.code_map_file.resolve())

    @patch("app.routes.analyze.get_graph", new_callable=AsyncMock)
    async def test_resolve_by_project_id_with_db_dict_fallback(self, mock_get_graph):
        # If no file exists on disk, but DB has codeMap with files, return the dict
        non_existent_path = str(self.temp_dir / "does_not_exist")
        db_cm = {
            "project": {"name": "in_memory_proj", "path": non_existent_path},
            "files": {"file.py": {}},
            "relationships": []
        }
        mock_get_graph.return_value = {
            "projectId": "proj-mem",
            "projectName": "in_memory_proj",
            "projectPath": non_existent_path,
            "codeMap": db_cm,
            "nodes": [],
            "edges": []
        }
        result = await _resolve_code_map_source(None, "proj-mem")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["project"]["name"], "in_memory_proj")

    @patch("app.routes.analyze.get_graph", new_callable=AsyncMock)
    async def test_resolve_invalid_project_id_returns_none(self, mock_get_graph):
        mock_get_graph.return_value = None
        result = await _resolve_code_map_source(None, "invalid-uuid")
        self.assertIsNone(result)

    def test_endpoint_missing_params_returns_400(self):
        resp = self.client.get("/api/feature-context")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("codeMapPath or valid projectId is required", resp.json()["detail"])

        resp_post = self.client.post("/api/feature-context", json={})
        self.assertEqual(resp_post.status_code, 400)
        self.assertIn("codeMapPath or valid projectId is required", resp_post.json()["detail"])

    @patch("app.routes.analyze.get_graph", new_callable=AsyncMock)
    def test_endpoint_invalid_project_id_returns_400(self, mock_get_graph):
        mock_get_graph.return_value = None
        resp = self.client.get("/api/feature-context?projectId=nonexistent-id")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("codeMapPath or valid projectId is required", resp.json()["detail"])

    @patch("app.routes.analyze.get_graph", new_callable=AsyncMock)
    def test_endpoint_valid_project_id_empty_db_codemap_finds_disk_file(self, mock_get_graph):
        mock_get_graph.return_value = {
            "projectId": "proj-123",
            "projectName": "test_project",
            "projectPath": str(self.temp_dir),
            "codeMap": {},
            "nodes": [],
            "edges": []
        }
        # GET /api/feature-context with valid projectId resolving to disk file
        resp = self.client.get("/api/feature-context?projectId=proj-123")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("status", data)
        self.assertIn("alreadyExists", data)

if __name__ == "__main__":
    unittest.main()

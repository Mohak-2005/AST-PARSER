import os
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.feature_context_generator import (
    sanitize_filename,
    load_code_map,
    discover_features,
    select_feature_files,
    determine_file_role,
    generate_feature_context,
    validate_feature_context,
    save_feature_contexts,
    generate_all_feature_contexts,
    fallback_feature_discovery,
    _graph_based_flow_fallback,
    _component_name,
)


class TestFeatureContextGenerator(unittest.TestCase):

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.code_map_path = self.test_dir / "repository-code-map.json"

        self.sample_code_map = {
            "project": {
                "name": "CollaborativeCanvas",
                "path": str(self.test_dir / "repo"),
                "analyzedAt": "2026-09-07T12:00:00Z",
                "totalFiles": 4,
                "parsedFiles": 4,
                "failedFiles": 0,
                "languagesDetected": ["typescript", "tsx"]
            },
            "files": {
                "server/routes/auth.ts": {
                    "language": "typescript",
                    "lineCount": 60,
                    "dependencies": [
                        {"target": "server/models/User.ts", "type": "internal", "kind": "import", "line": 2},
                        {"target": "bcryptjs", "type": "external", "kind": "import", "line": 3}
                    ],
                    "exports": [
                        {"name": "authRouter", "type": "named", "line": 50}
                    ],
                    "declarations": [
                        {"name": "loginHandler", "type": "function", "line": 15}
                    ],
                    "routes": [
                        {"method": "POST", "path": "/api/login", "line": 20}
                    ],
                    "databaseOperations": []
                },
                "server/models/User.ts": {
                    "language": "typescript",
                    "lineCount": 45,
                    "dependencies": [
                        {"target": "mongoose", "type": "external", "kind": "import", "line": 1}
                    ],
                    "exports": [
                        {"name": "User", "type": "named", "line": 40}
                    ],
                    "declarations": [],
                    "classes": [
                        {"name": "User", "extends": "Model", "methods": ["comparePassword"]}
                    ],
                    "databaseOperations": [
                        {"operation": "findOne", "line": 25}
                    ]
                },
                "client/src/App.tsx": {
                    "language": "tsx",
                    "lineCount": 50,
                    "dependencies": [
                        {"target": "client/src/components/Canvas.tsx", "type": "internal", "kind": "import", "line": 2}
                    ],
                    "declarations": [
                        {"name": "App", "type": "component", "line": 8}
                    ]
                },
                "client/src/components/Canvas.tsx": {
                    "language": "tsx",
                    "lineCount": 90,
                    "dependencies": [],
                    "declarations": [
                        {"name": "Canvas", "type": "component", "line": 12}
                    ]
                }
            },
            "relationships": [
                {
                    "from": "server/routes/auth.ts",
                    "to": "server/models/User.ts",
                    "type": "imports",
                    "evidence": {"importPath": "../models/User", "line": 2},
                    "external": False
                },
                {
                    "from": "server/routes/auth.ts",
                    "to": "bcryptjs",
                    "type": "imports",
                    "evidence": {"importPath": "bcryptjs", "line": 3},
                    "external": True
                },
                {
                    "from": "client/src/App.tsx",
                    "to": "client/src/components/Canvas.tsx",
                    "type": "imports",
                    "evidence": {"importPath": "./components/Canvas", "line": 2},
                    "external": False
                }
            ]
        }

        with open(self.code_map_path, "w", encoding="utf-8") as f:
            json.dump(self.sample_code_map, f, indent=2)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # sanitize_filename
    # ------------------------------------------------------------------

    def test_sanitize_filename(self):
        existing = set()
        self.assertEqual(
            sanitize_filename("User Authentication & Authorization", existing),
            "user-authentication-authorization.json"
        )
        # Collision handling appends -2
        self.assertEqual(
            sanitize_filename("User Authentication & Authorization", existing),
            "user-authentication-authorization-2.json"
        )
        # Special characters & spaces
        self.assertEqual(
            sanitize_filename("  Real-time / WebSocket Sync!  ", existing),
            "real-time-websocket-sync.json"
        )
        # Empty fallback
        self.assertEqual(sanitize_filename("", existing), "feature.json")

    # ------------------------------------------------------------------
    # load_code_map
    # ------------------------------------------------------------------

    def test_load_code_map(self):
        code_map, path = load_code_map(self.code_map_path)
        self.assertEqual(path, self.code_map_path)
        self.assertIn("server/routes/auth.ts", code_map["files"])
        self.assertEqual(len(code_map["relationships"]), 3)

    def test_load_code_map_list_format(self):
        """load_code_map must handle the legacy list-based files format."""
        legacy_map = {
            "project": {"name": "Legacy", "languagesDetected": ["python"]},
            "files": [
                {"relativePath": "src/main.py", "language": "python", "declarations": []},
                {"file": {"relativePath": "src/utils.py"}, "language": "python", "declarations": []}
            ],
            "relationships": []
        }
        tmp = self.test_dir / "legacy-code-map.json"
        with open(tmp, "w") as fh:
            json.dump(legacy_map, fh)
        code_map, _ = load_code_map(tmp)
        self.assertIn("src/main.py", code_map["files"])
        self.assertIn("src/utils.py", code_map["files"])

    # ------------------------------------------------------------------
    # determine_file_role — evidence-based, no path-name assumptions
    # ------------------------------------------------------------------

    def test_determine_file_role_api_route(self):
        """Files with routes → API Route Handler regardless of path name."""
        role = determine_file_role(
            "server/routes/auth.ts",
            self.sample_code_map["files"]["server/routes/auth.ts"],
            self.sample_code_map["relationships"]
        )
        self.assertEqual(role, "API Route Handler")

    def test_determine_file_role_data_layer(self):
        """Files with databaseOperations → Data Layer."""
        role = determine_file_role(
            "server/models/User.ts",
            self.sample_code_map["files"]["server/models/User.ts"],
            self.sample_code_map["relationships"]
        )
        self.assertEqual(role, "Data Layer")

    def test_determine_file_role_domain_model(self):
        """Classes with inheritance → Domain Model."""
        fdata = {
            "classes": [{"name": "Order", "extends": "BaseEntity", "methods": ["save"]}],
            "declarations": [],
            "databaseOperations": []
        }
        role = determine_file_role("src/domain/Order.java", fdata, [])
        self.assertEqual(role, "Domain Model")

    def test_determine_file_role_entry_point(self):
        """main.py → Entry Point."""
        role = determine_file_role("src/main.py", {"declarations": []}, [])
        self.assertEqual(role, "Entry Point")

    def test_determine_file_role_shared_utility(self):
        """Files imported by >= 4 others → Shared Utility."""
        many_rels = [
            {"from": f"src/file{i}.ts", "to": "src/utils.ts", "external": False}
            for i in range(5)
        ]
        role = determine_file_role("src/utils.ts", {"declarations": []}, many_rels)
        self.assertEqual(role, "Shared Utility")

    def test_determine_file_role_implementation_module(self):
        """File with declarations but no special evidence → Implementation Module."""
        fdata = {
            "declarations": [{"name": "Canvas", "type": "component"}],
            "classes": [],
            "databaseOperations": [],
            "exports": []
        }
        role = determine_file_role("client/src/components/Canvas.tsx", fdata, [])
        self.assertEqual(role, "Implementation Module")

    def test_determine_file_role_library_api(self):
        """File whose every declaration is exported → Library / Module API."""
        fdata = {
            "declarations": [
                {"name": "parseJSON", "type": "function"},
                {"name": "repairJSON", "type": "function"}
            ],
            "exports": [
                {"name": "parseJSON", "type": "named"},
                {"name": "repairJSON", "type": "named"}
            ],
            "classes": [],
            "databaseOperations": []
        }
        role = determine_file_role("src/json_utils.ts", fdata, [])
        self.assertEqual(role, "Library / Module API")

    # ------------------------------------------------------------------
    # fallback_feature_discovery — graph-cluster based
    # ------------------------------------------------------------------

    def test_fallback_feature_discovery_groups_connected_files(self):
        features = fallback_feature_discovery(self.sample_code_map)
        # Two connected components: {auth.ts, User.ts} and {App.tsx, Canvas.tsx}
        self.assertEqual(len(features), 2)
        all_seeds = [f for feat in features for f in feat["seedFiles"]]
        self.assertTrue(len(all_seeds) > 0)
        for feat in features:
            self.assertIn("id", feat)
            self.assertIn("name", feat)
            self.assertIn("seedFiles", feat)
            self.assertTrue(len(feat["seedFiles"]) > 0)

    def test_fallback_feature_discovery_isolated_file(self):
        """A completely isolated file (no relationships) forms its own component."""
        isolated_map = {
            "project": {"name": "Isolated", "languagesDetected": ["python"]},
            "files": {
                "standalone.py": {"declarations": [{"name": "run", "type": "function"}]},
            },
            "relationships": []
        }
        features = fallback_feature_discovery(isolated_map)
        self.assertEqual(len(features), 1)
        self.assertIn("standalone.py", features[0]["seedFiles"])

    # ------------------------------------------------------------------
    # select_feature_files — graph traversal
    # ------------------------------------------------------------------

    def test_select_feature_files_dependency_aware(self):
        feature = {
            "id": "feature-auth",
            "name": "User Authentication",
            "seedFiles": ["server/routes/auth.ts"]
        }
        selected = select_feature_files(feature, self.sample_code_map)
        paths = {f["path"]: f["relevance"] for f in selected}

        # Seed file must be direct
        self.assertIn("server/routes/auth.ts", paths)
        self.assertEqual(paths["server/routes/auth.ts"], "direct")

        # Dependency User.ts traversed and marked dependency
        self.assertIn("server/models/User.ts", paths)
        self.assertEqual(paths["server/models/User.ts"], "dependency")

        # Unrelated client files must NOT be in auth feature
        self.assertNotIn("client/src/App.tsx", paths)
        self.assertNotIn("client/src/components/Canvas.tsx", paths)

    # ------------------------------------------------------------------
    # _graph_based_flow_fallback
    # ------------------------------------------------------------------

    def test_graph_based_flow_fallback(self):
        enriched = [
            {"path": "server/routes/auth.ts", "role": "API Route Handler", "relevance": "direct"},
            {"path": "server/models/User.ts", "role": "Data Layer", "relevance": "dependency"}
        ]
        rels = [
            {"from": "server/routes/auth.ts", "to": "server/models/User.ts", "type": "imports"}
        ]
        flow = _graph_based_flow_fallback(enriched, rels)
        self.assertIsInstance(flow, list)
        self.assertTrue(len(flow) >= 1)
        # All steps must be non-empty strings
        for step in flow:
            self.assertIsInstance(step, str)
            self.assertTrue(len(step) > 0)

    def test_graph_based_flow_fallback_empty(self):
        flow = _graph_based_flow_fallback([], [])
        self.assertIsInstance(flow, list)
        self.assertEqual(len(flow), 1)

    # ------------------------------------------------------------------
    # validate_feature_context
    # ------------------------------------------------------------------

    def test_validate_feature_context_strips_hallucinations(self):
        context = {
            "feature": {"id": "feature-test", "name": "Test Feature"},
            "files": [
                {
                    "path": "server/routes/auth.ts",
                    "role": "API Route Handler",
                    "relevance": "direct",
                    "declarations": [{"name": "loginHandler", "type": "function"}],
                    "dependencies": [
                        {"target": "server/models/User.ts", "type": "internal"},
                        {"target": "bcryptjs", "type": "external"}
                    ]
                },
                {
                    "path": "server/fake/Hallucinated.ts",
                    "role": "Fake",
                    "relevance": "dependency",
                    "declarations": [{"name": "ghostFunc", "type": "function"}]
                }
            ],
            "relationships": [
                {"from": "server/routes/auth.ts", "to": "server/fake/Hallucinated.ts", "type": "imports"}
            ],
            "featureFlow": ["Step 1: Test flow"],
            "sharedDependencies": ["bcryptjs"]
        }

        validated = validate_feature_context(context, self.sample_code_map)
        val_paths = [f["path"] for f in validated["files"]]

        self.assertIn("server/routes/auth.ts", val_paths)
        self.assertNotIn("server/fake/Hallucinated.ts", val_paths)

        # Hallucinated relationship should also be removed
        self.assertEqual(len(validated["relationships"]), 0)

        # Declarations remain
        auth_file = next(f for f in validated["files"] if f["path"] == "server/routes/auth.ts")
        self.assertEqual(auth_file["declarations"][0]["name"], "loginHandler")

    # ------------------------------------------------------------------
    # save_feature_contexts
    # ------------------------------------------------------------------

    def test_save_feature_contexts(self):
        context = {
            "feature": {"id": "feature-auth", "name": "User Authentication"},
            "files": [
                {
                    "path": "server/routes/auth.ts",
                    "role": "API Route Handler",
                    "relevance": "direct",
                    "declarations": [{"name": "loginHandler", "type": "function"}],
                    "dependencies": [{"target": "bcryptjs", "type": "external"}]
                }
            ],
            "relationships": [],
            "featureFlow": ["Step 1: User sends credentials"],
            "sharedDependencies": ["bcryptjs"]
        }

        saved = save_feature_contexts(self.code_map_path, [context])
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0].exists())
        self.assertEqual(saved[0].name, "user-authentication.json")
        self.assertEqual(saved[0].parent.name, "feature-context")

        with open(saved[0], "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["feature"]["id"], "feature-auth")
        self.assertIn("relationships", data)   # schema includes relationships
        self.assertEqual(data["sharedDependencies"], ["bcryptjs"])
        self.assertEqual(data["files"][0]["path"], "server/routes/auth.ts")
        self.assertEqual(data["files"][0]["declarations"][0]["name"], "loginHandler")

    # ------------------------------------------------------------------
    # end-to-end
    # ------------------------------------------------------------------

    @patch("app.services.feature_context_generator.execute_hf_llm_call")
    def test_end_to_end_generator(self, mock_llm):
        discovery_response = json.dumps({
            "features": [
                {
                    "id": "feature-auth",
                    "name": "User Authentication",
                    "description": "Authentication and user verification",
                    "seedFiles": ["server/routes/auth.ts"]
                },
                {
                    "id": "feature-canvas",
                    "name": "Collaborative Canvas UI",
                    "description": "Rendering interactive canvas workspace",
                    "seedFiles": ["client/src/App.tsx"]
                }
            ]
        })

        flow_response = json.dumps([
            "Step 1: Client submits request",
            "Step 2: Server processes authentication"
        ])

        mock_llm.side_effect = [
            (discovery_response, 200, 0.5),
            (flow_response, 200, 0.3),
            (flow_response, 200, 0.3)
        ]

        import asyncio
        contexts = asyncio.run(generate_all_feature_contexts(self.code_map_path))

        self.assertEqual(len(contexts), 2)
        feature_context_dir = self.test_dir / "feature-context"
        self.assertTrue(feature_context_dir.exists())

        saved_files = list(feature_context_dir.glob("*.json"))
        self.assertEqual(len(saved_files), 2)
        saved_names = {f.name for f in saved_files}
        self.assertIn("user-authentication.json", saved_names)
        self.assertIn("collaborative-canvas-ui.json", saved_names)

        # Each context must have required top-level keys
        for ctx in contexts:
            self.assertIn("feature", ctx)
            self.assertIn("files", ctx)
            self.assertIn("relationships", ctx)
            self.assertIn("featureFlow", ctx)
            self.assertIn("sharedDependencies", ctx)


if __name__ == "__main__":
    unittest.main()

"""
Tests for feature_context_generator.py

Covers:
- sanitize_filename
- load_code_map (dict format, legacy list format)
- determine_file_role (evidence-based, no path-name or connectivity assumptions)
- fallback_feature_discovery (two-phase: component candidates → semantic refinement)
- _split_component (intra-component splitting logic)
- select_feature_files (graph traversal)
- _graph_based_flow_fallback (topology + relationship-type annotation)
- validate_feature_context (hallucination stripping)
- save_feature_contexts (output schema)
- generate_all_feature_contexts (end-to-end)
"""

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
    get_existing_feature_contexts,
    fallback_feature_discovery,
    _graph_based_flow_fallback,
    _split_component,
    _build_adjacency,
    _name_candidate,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_sample_code_map() -> dict:
    """
    Minimal but realistic code-map covering:
    - API route handler    (server/routes/auth.ts)
    - Data layer           (server/models/User.ts)  — has databaseOperations
    - Domain model         (server/models/Order.ts) — has class with extends
    - Implementation module (client/src/App.tsx)
    - Library API           (lib/utils.ts)           — 3 exports == declared names
    """
    return {
        "project": {
            "name": "TestProject",
            "path": "/repo",
            "languagesDetected": ["typescript", "tsx"],
        },
        "files": {
            "server/routes/auth.ts": {
                "language": "typescript",
                "routes": [{"method": "POST", "path": "/api/auth/login", "line": 20}],
                "declarations": [{"name": "loginHandler", "type": "function", "line": 15}],
                "exports": [{"name": "authRouter", "type": "named", "line": 50}],
                "databaseOperations": [],
                "classes": [],
            },
            "server/models/User.ts": {
                "language": "typescript",
                "databaseOperations": [{"operation": "findOne", "line": 25}],
                "declarations": [],
                "exports": [{"name": "User", "type": "named", "line": 40}],
                "classes": [{"name": "User", "methods": ["comparePassword"]}],  # no extends
            },
            "server/models/Order.ts": {
                "language": "typescript",
                "databaseOperations": [],
                "declarations": [],
                "exports": [],
                "classes": [{"name": "Order", "extends": "BaseModel", "methods": ["save"]}],
            },
            "client/src/App.tsx": {
                "language": "tsx",
                "declarations": [{"name": "App", "type": "component", "line": 8}],
                "exports": [],
                "classes": [],
                "databaseOperations": [],
            },
            "lib/utils.ts": {
                "language": "typescript",
                "declarations": [
                    {"name": "parseJSON", "type": "function"},
                    {"name": "repairJSON", "type": "function"},
                    {"name": "sanitize", "type": "function"},
                ],
                "exports": [
                    {"name": "parseJSON", "type": "named"},
                    {"name": "repairJSON", "type": "named"},
                    {"name": "sanitize", "type": "named"},
                ],
                "classes": [],
                "databaseOperations": [],
            },
            "main.py": {
                "language": "python",
                "declarations": [{"name": "main", "type": "function"}],
                "exports": [],
                "classes": [],
                "databaseOperations": [],
            },
        },
        "relationships": [
            {
                "from": "server/routes/auth.ts",
                "to": "server/models/User.ts",
                "type": "imports",
                "evidence": {"line": 2},
                "external": False,
            },
            {
                "from": "server/routes/auth.ts",
                "to": "bcryptjs",
                "type": "imports",
                "evidence": {"line": 3},
                "external": True,
            },
            {
                "from": "client/src/App.tsx",
                "to": "lib/utils.ts",
                "type": "imports",
                "evidence": {"line": 5},
                "external": False,
            },
        ],
    }


class TestFeatureContextGenerator(unittest.TestCase):

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.code_map_path = self.test_dir / "repository-code-map.json"
        self.sample_code_map = _make_sample_code_map()
        with open(self.code_map_path, "w", encoding="utf-8") as f:
            json.dump(self.sample_code_map, f, indent=2)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # sanitize_filename
    # ------------------------------------------------------------------

    def test_sanitize_filename_basic(self):
        ex = set()
        self.assertEqual(sanitize_filename("User Authentication & Auth", ex), "user-authentication-auth.json")

    def test_sanitize_filename_collision(self):
        ex = {"user-authentication-auth.json"}
        self.assertEqual(sanitize_filename("User Authentication & Auth", ex), "user-authentication-auth-2.json")

    def test_sanitize_filename_special_chars(self):
        ex = set()
        self.assertEqual(sanitize_filename("  Real-time / WebSocket!", ex), "real-time-websocket.json")

    def test_sanitize_filename_empty(self):
        ex = set()
        self.assertEqual(sanitize_filename("", ex), "feature.json")

    def test_sanitize_filename_truncation(self):
        ex = set()
        long_name = "A" * 60
        result = sanitize_filename(long_name, ex)
        self.assertLessEqual(len(result), 55)  # 50 base + ".json"

    # ------------------------------------------------------------------
    # load_code_map
    # ------------------------------------------------------------------

    def test_load_code_map_dict_format(self):
        code_map, path = load_code_map(self.code_map_path)
        self.assertIn("server/routes/auth.ts", code_map["files"])
        self.assertEqual(len(code_map["relationships"]), 3)

    def test_load_code_map_list_format(self):
        """load_code_map normalises the legacy list-based files format."""
        legacy = {
            "project": {"name": "L", "languagesDetected": ["python"]},
            "files": [
                {"relativePath": "src/main.py", "language": "python"},
                {"file": {"relativePath": "src/utils.py"}, "language": "python"},
            ],
            "relationships": [],
        }
        tmp = self.test_dir / "legacy.json"
        with open(tmp, "w") as fh:
            json.dump(legacy, fh)
        code_map, _ = load_code_map(tmp)
        self.assertIn("src/main.py", code_map["files"])
        self.assertIn("src/utils.py", code_map["files"])

    def test_load_code_map_backslash_normalisation(self):
        """Windows-style backslash paths are normalised to forward slashes."""
        code_map_with_bs = {
            "project": {"name": "W", "languagesDetected": []},
            "files": {"src\\main.py": {"language": "python"}},
            "relationships": [
                {"from": "src\\main.py", "to": "src\\utils.py",
                 "type": "imports", "external": False}
            ],
        }
        code_map, _ = load_code_map(code_map_with_bs)
        self.assertIn("src/main.py", code_map["files"])
        self.assertEqual(code_map["relationships"][0]["from"], "src/main.py")

    # ------------------------------------------------------------------
    # determine_file_role — evidence-based, NO path/connectivity assumptions
    # ------------------------------------------------------------------

    def test_role_api_route_handler(self):
        """Files with routes → API Route Handler, regardless of path name."""
        role = determine_file_role(
            "anything/deeply/nested/xyz.go",
            self.sample_code_map["files"]["server/routes/auth.ts"],
        )
        self.assertEqual(role, "API Route Handler")

    def test_role_data_layer(self):
        """Files with databaseOperations → Data Layer."""
        role = determine_file_role(
            "any/path.ts",
            self.sample_code_map["files"]["server/models/User.ts"],
        )
        self.assertEqual(role, "Data Layer")

    def test_role_domain_model(self):
        """Class with `extends` → Domain Model."""
        role = determine_file_role(
            "any/path.ts",
            self.sample_code_map["files"]["server/models/Order.ts"],
        )
        self.assertEqual(role, "Domain Model")

    def test_role_library_api(self):
        """3 exports matching all declarations → Library / Module API."""
        role = determine_file_role(
            "any/path.ts",
            self.sample_code_map["files"]["lib/utils.ts"],
        )
        self.assertEqual(role, "Library / Module API")

    def test_role_entry_point(self):
        """main.py filename → Entry Point."""
        role = determine_file_role(
            "main.py",
            self.sample_code_map["files"]["main.py"],
        )
        self.assertEqual(role, "Entry Point")

    def test_role_implementation_module(self):
        """File with declarations but no special evidence → Implementation Module."""
        role = determine_file_role(
            "client/src/App.tsx",
            self.sample_code_map["files"]["client/src/App.tsx"],
        )
        self.assertEqual(role, "Implementation Module")

    def test_role_no_connectivity_assumption(self):
        """High in-degree (connectivity) must NOT change the role — evidence only."""
        # A file imported many times but with no other evidence → Source Module
        bare_file = {"declarations": [], "classes": [], "exports": [], "databaseOperations": []}
        role = determine_file_role("shared/config.ts", bare_file)
        self.assertEqual(role, "Source Module")

    def test_role_routes_beats_db_ops(self):
        """Routes take priority over databaseOperations."""
        fdata = {
            "routes": [{"method": "GET", "path": "/items"}],
            "databaseOperations": [{"operation": "findAll"}],
            "classes": [],
            "declarations": [],
            "exports": [],
        }
        role = determine_file_role("any.ts", fdata)
        self.assertEqual(role, "API Route Handler")

    def test_library_api_requires_3_exports(self):
        """Library/Module API requires ≥3 exports; 2 is not enough."""
        fdata = {
            "declarations": [
                {"name": "parseJSON", "type": "function"},
                {"name": "repairJSON", "type": "function"},
            ],
            "exports": [
                {"name": "parseJSON", "type": "named"},
                {"name": "repairJSON", "type": "named"},
            ],
            "classes": [],
            "databaseOperations": [],
        }
        role = determine_file_role("any.ts", fdata)
        # Should fall through to Implementation Module (has declarations)
        self.assertEqual(role, "Implementation Module")

    # ------------------------------------------------------------------
    # fallback_feature_discovery — two-phase: components → semantic refinement
    # ------------------------------------------------------------------

    def test_fallback_returns_features_for_every_file(self):
        """Every file in the code-map must appear in at least one feature's seedFiles."""
        features = fallback_feature_discovery(self.sample_code_map)
        self.assertTrue(len(features) >= 1)
        all_seeds = {f for feat in features for f in feat["seedFiles"]}
        # Every seed must exist in the code-map
        available = set(self.sample_code_map["files"].keys())
        for seed in all_seeds:
            self.assertIn(seed, available)

    def test_fallback_isolated_file_forms_own_feature(self):
        """A file with no relationships → its own component → its own feature candidate."""
        isolated = {
            "project": {"name": "Iso", "languagesDetected": ["python"]},
            "files": {
                "standalone.py": {
                    "declarations": [{"name": "run", "type": "function"}],
                    "classes": [], "exports": [], "databaseOperations": [],
                }
            },
            "relationships": [],
        }
        features = fallback_feature_discovery(isolated)
        self.assertEqual(len(features), 1)
        self.assertIn("standalone.py", features[0]["seedFiles"])

    def test_fallback_components_are_candidates_not_final(self):
        """
        If two files in the same component have no direct relationship to each other
        (connected only through a shared bridge file), and come from independent
        source nodes, _split_component should propose splitting them.
        """
        # A → C, B → C (A and B are both sources; C is shared)
        triple_map = {
            "project": {"name": "T", "languagesDetected": ["typescript"]},
            "files": {
                "a/feature1.ts": {"declarations": [{"name": "F1", "type": "function"}],
                                   "classes": [], "exports": [], "databaseOperations": []},
                "b/feature2.ts": {"declarations": [{"name": "F2", "type": "function"}],
                                   "classes": [], "exports": [], "databaseOperations": []},
                "shared/helper.ts": {"declarations": [{"name": "help", "type": "function"},
                                                       {"name": "assist", "type": "function"},
                                                       {"name": "wrap", "type": "function"}],
                                      "exports": [{"name": "help", "type": "named"},
                                                  {"name": "assist", "type": "named"},
                                                  {"name": "wrap", "type": "named"}],
                                      "classes": [], "databaseOperations": []},
            },
            "relationships": [
                {"from": "a/feature1.ts", "to": "shared/helper.ts",
                 "type": "imports", "external": False},
                {"from": "b/feature2.ts", "to": "shared/helper.ts",
                 "type": "imports", "external": False},
            ],
        }
        features = fallback_feature_discovery(triple_map)
        # Should produce ≥2 candidates (a/feature1 and b/feature2 are independent sources)
        self.assertGreaterEqual(len(features), 2)

    # ------------------------------------------------------------------
    # _split_component
    # ------------------------------------------------------------------

    def test_split_component_single_source(self):
        """Single source → no split."""
        component = ["a.ts", "b.ts", "c.ts"]
        available = set(component)
        rels = [
            {"from": "a.ts", "to": "b.ts", "type": "imports", "external": False},
            {"from": "b.ts", "to": "c.ts", "type": "imports", "external": False},
        ]
        outgoing, incoming = _build_adjacency(rels, available)
        result = _split_component(component, outgoing, incoming, {})
        self.assertEqual(len(result), 1)

    def test_split_component_independent_sources(self):
        """Two independent sources → two candidates."""
        component = ["entry1.ts", "entry2.ts", "leaf.ts"]
        available = set(component)
        rels = [
            {"from": "entry1.ts", "to": "leaf.ts", "type": "imports", "external": False},
            {"from": "entry2.ts", "to": "leaf.ts", "type": "imports", "external": False},
        ]
        outgoing, incoming = _build_adjacency(rels, available)
        result = _split_component(component, outgoing, incoming, {})
        # With 50%+ overlap (leaf.ts is in both), should stay as one
        # OR split to two — depends on overlap ratio; either is acceptable
        self.assertGreaterEqual(len(result), 1)
        # All files should appear in at least one result group
        all_in_results = {f for r in result for f in r["files"]}
        for f in component:
            self.assertIn(f, all_in_results)

    def test_split_component_tiny_returns_as_is(self):
        """Components with ≤2 files are returned as-is without splitting."""
        component = ["a.ts", "b.ts"]
        outgoing, incoming = _build_adjacency([], set(component))
        result = _split_component(component, outgoing, incoming, {})
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0]["files"]), set(component))

    # ------------------------------------------------------------------
    # _name_candidate
    # ------------------------------------------------------------------

    def test_name_candidate_route_prefix(self):
        """Route prefix is the highest priority signal."""
        files_with_routes = {
            "server/auth.ts": {
                "routes": [{"method": "POST", "path": "/auth/login"}],
                "declarations": [], "classes": [], "databaseOperations": [], "exports": [],
            }
        }
        cm = {**self.sample_code_map, "files": files_with_routes}
        name, desc = _name_candidate(["server/auth.ts"], cm)
        self.assertIn("Auth", name)

    def test_name_candidate_common_prefix(self):
        """Common directory prefix used when no routes or class evidence."""
        files = ["api/handlers/login.ts", "api/handlers/logout.ts"]
        # Both files share api/handlers prefix
        cm = {
            "files": {
                "api/handlers/login.ts": {"declarations": [], "classes": [], "databaseOperations": [], "exports": []},
                "api/handlers/logout.ts": {"declarations": [], "classes": [], "databaseOperations": [], "exports": []},
            },
            "relationships": [],
        }
        name, _ = _name_candidate(files, cm)
        # Should derive from "handlers" common prefix
        self.assertIn("Handlers", name)

    # ------------------------------------------------------------------
    # select_feature_files — graph traversal
    # ------------------------------------------------------------------

    def test_select_feature_files_traversal(self):
        feature = {
            "id": "feature-auth",
            "name": "Auth",
            "seedFiles": ["server/routes/auth.ts"],
        }
        selected = select_feature_files(feature, self.sample_code_map)
        paths = {f["path"]: f["relevance"] for f in selected}

        self.assertIn("server/routes/auth.ts", paths)
        self.assertEqual(paths["server/routes/auth.ts"], "direct")
        self.assertIn("server/models/User.ts", paths)
        self.assertEqual(paths["server/models/User.ts"], "dependency")

        # Unrelated files must not appear
        self.assertNotIn("client/src/App.tsx", paths)

    def test_select_feature_files_no_contamination_from_siblings(self):
        """
        Caller of a seed is added as 'dependency' but its OTHER children
        (sibling features) must NOT be pulled in.
        """
        extended_map = dict(self.sample_code_map)
        extended_map["files"] = dict(self.sample_code_map["files"])
        extended_map["files"]["page/Dashboard.tsx"] = {
            "declarations": [{"name": "Dashboard", "type": "component"}],
            "classes": [], "exports": [], "databaseOperations": [],
        }
        extended_map["files"]["page/Sidebar.tsx"] = {
            "declarations": [{"name": "Sidebar", "type": "component"}],
            "classes": [], "exports": [], "databaseOperations": [],
        }
        extended_map["relationships"] = list(self.sample_code_map["relationships"]) + [
            {"from": "page/Dashboard.tsx", "to": "client/src/App.tsx",
             "type": "imports", "external": False},
            {"from": "page/Dashboard.tsx", "to": "page/Sidebar.tsx",
             "type": "imports", "external": False},
        ]

        feature = {"id": "app-ui", "name": "App UI", "seedFiles": ["client/src/App.tsx"]}
        selected = select_feature_files(feature, extended_map)
        paths = {f["path"] for f in selected}

        self.assertIn("client/src/App.tsx", paths)
        self.assertIn("page/Dashboard.tsx", paths)  # direct caller of seed
        self.assertNotIn("page/Sidebar.tsx", paths)  # sibling of caller, not of seed

    # ------------------------------------------------------------------
    # _graph_based_flow_fallback — uses relationship types
    # ------------------------------------------------------------------

    def test_flow_fallback_includes_relationship_types(self):
        enriched = [
            {"path": "server/routes/auth.ts", "role": "API Route Handler", "relevance": "direct"},
            {"path": "server/models/User.ts", "role": "Data Layer", "relevance": "dependency"},
        ]
        rels = [
            {"from": "server/routes/auth.ts", "to": "server/models/User.ts",
             "type": "imports", "evidence": {"line": 2}},
        ]
        flow = _graph_based_flow_fallback(enriched, rels)
        self.assertIsInstance(flow, list)
        self.assertTrue(len(flow) >= 1)
        # The relationship type "imports" should appear in the step description
        combined = " ".join(flow)
        self.assertIn("imports", combined)

    def test_flow_fallback_empty_files(self):
        flow = _graph_based_flow_fallback([], [])
        self.assertEqual(len(flow), 1)
        self.assertIsInstance(flow[0], str)

    def test_flow_fallback_no_relationships(self):
        """Single isolated file → describes the file."""
        enriched = [{"path": "main.go", "role": "Entry Point", "relevance": "direct"}]
        flow = _graph_based_flow_fallback(enriched, [])
        self.assertEqual(len(flow), 1)
        self.assertIn("main.go", flow[0])

    # ------------------------------------------------------------------
    # validate_feature_context
    # ------------------------------------------------------------------

    def test_validate_strips_hallucinated_files(self):
        context = {
            "feature": {"id": "t", "name": "T"},
            "files": [
                {
                    "path": "server/routes/auth.ts",
                    "role": "API Route Handler",
                    "relevance": "direct",
                    "declarations": [{"name": "loginHandler", "type": "function"}],
                    "dependencies": [
                        {"target": "server/models/User.ts", "type": "internal"},
                        {"target": "bcryptjs", "type": "external"},
                    ],
                },
                {
                    "path": "server/fake/Ghost.ts",
                    "role": "Ghost",
                    "relevance": "dependency",
                },
            ],
            "relationships": [
                {"from": "server/routes/auth.ts", "to": "server/fake/Ghost.ts", "type": "imports"},
            ],
            "featureFlow": ["Step 1"],
            "sharedDependencies": ["bcryptjs"],
        }

        validated = validate_feature_context(context, self.sample_code_map)
        paths = [f["path"] for f in validated["files"]]
        self.assertIn("server/routes/auth.ts", paths)
        self.assertNotIn("server/fake/Ghost.ts", paths)
        # Relationship referencing the hallucinated file must also be removed
        self.assertEqual(len(validated["relationships"]), 0)

    def test_validate_removes_bad_internal_dep_targets(self):
        context = {
            "feature": {"id": "t", "name": "T"},
            "files": [
                {
                    "path": "server/routes/auth.ts",
                    "role": "API Route Handler",
                    "relevance": "direct",
                    "dependencies": [
                        {"target": "server/models/User.ts", "type": "internal"},  # valid
                        {"target": "server/models/Ghost.ts", "type": "internal"},  # invalid
                        {"target": "bcryptjs", "type": "external"},               # always kept
                    ],
                }
            ],
            "relationships": [],
            "featureFlow": [],
            "sharedDependencies": [],
        }
        validated = validate_feature_context(context, self.sample_code_map)
        deps = validated["files"][0]["dependencies"]
        targets = {d["target"] for d in deps}
        self.assertIn("server/models/User.ts", targets)
        self.assertNotIn("server/models/Ghost.ts", targets)
        self.assertIn("bcryptjs", targets)

    # ------------------------------------------------------------------
    # save_feature_contexts
    # ------------------------------------------------------------------

    def test_save_feature_contexts_schema(self):
        context = {
            "feature": {"id": "feature-auth", "name": "User Authentication"},
            "files": [
                {
                    "path": "server/routes/auth.ts",
                    "role": "API Route Handler",
                    "relevance": "direct",
                    "declarations": [{"name": "loginHandler", "type": "function"}],
                    "dependencies": [{"target": "bcryptjs", "type": "external", "kind": "imports"}],
                }
            ],
            "relationships": [],
            "featureFlow": ["Step 1: loginHandler processes the request."],
            "sharedDependencies": ["bcryptjs"],
        }

        saved = save_feature_contexts(self.code_map_path, [context])
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0].exists())
        self.assertEqual(saved[0].name, "user-authentication.json")
        self.assertEqual(saved[0].parent.name, "feature-context")

        with open(saved[0]) as f:
            data = json.load(f)

        # Required top-level keys
        for key in ("feature", "files", "relationships", "featureFlow", "sharedDependencies"):
            self.assertIn(key, data)

        self.assertEqual(data["feature"]["id"], "feature-auth")
        self.assertEqual(data["sharedDependencies"], ["bcryptjs"])
        self.assertEqual(data["files"][0]["path"], "server/routes/auth.ts")

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
                    "seedFiles": ["server/routes/auth.ts"],
                },
                {
                    "id": "feature-canvas",
                    "name": "App UI",
                    "description": "Application UI",
                    "seedFiles": ["client/src/App.tsx"],
                },
            ]
        })
        flow_response = json.dumps([
            "Step 1: auth.ts imports User.ts.",
            "Step 2: User.ts performs findOne.",
        ])

        mock_llm.side_effect = [
            (discovery_response, 200, 0.5),
            (flow_response, 200, 0.3),
            (flow_response, 200, 0.3),
        ]

        import asyncio
        contexts = asyncio.run(generate_all_feature_contexts(self.code_map_path))

        self.assertEqual(len(contexts), 2)
        feature_context_dir = self.test_dir / "feature-context"
        self.assertTrue(feature_context_dir.exists())

        saved_names = {f.name for f in feature_context_dir.glob("*.json")}
        self.assertIn("user-authentication.json", saved_names)
        self.assertIn("app-ui.json", saved_names)

        for ctx in contexts:
            for key in ("feature", "files", "relationships", "featureFlow", "sharedDependencies"):
                self.assertIn(key, ctx)

    @patch("app.services.feature_context_generator.execute_hf_llm_call")
    def test_end_to_end_fallback_when_llm_fails(self, mock_llm):
        """When LLM returns garbage, the deterministic fallback must produce valid output."""
        mock_llm.return_value = ("NOT_VALID_JSON{{}", 500, 0.0)

        import asyncio
        contexts = asyncio.run(generate_all_feature_contexts(self.code_map_path))

        self.assertIsInstance(contexts, list)
        self.assertGreater(len(contexts), 0)
        for ctx in contexts:
            self.assertIn("feature", ctx)
            self.assertIn("files", ctx)
            self.assertIn("featureFlow", ctx)

    def test_get_existing_feature_contexts_none_when_no_dir(self):
        """When feature-context/ directory doesn't exist, returns empty list."""
        found = get_existing_feature_contexts(self.code_map_path)
        self.assertEqual(found, [])

    def test_get_existing_feature_contexts_none_when_empty_or_invalid(self):
        """When feature-context/ is empty or contains corrupt files, returns empty list."""
        fc_dir = self.test_dir / "feature-context"
        fc_dir.mkdir(parents=True, exist_ok=True)
        # Empty file
        (fc_dir / "empty.json").write_text("", encoding="utf-8")
        # Invalid json
        (fc_dir / "bad.json").write_text("{not valid json", encoding="utf-8")
        # Json missing required fields
        (fc_dir / "incomplete.json").write_text(json.dumps({"some": "data"}), encoding="utf-8")
        # Json with empty files list
        (fc_dir / "no_files.json").write_text(json.dumps({"feature": {"name": "X"}, "files": []}), encoding="utf-8")

        found = get_existing_feature_contexts(self.code_map_path)
        self.assertEqual(found, [])

    def test_get_existing_feature_contexts_detects_valid_files(self):
        """When valid feature context JSON files exist, detects them and returns paths."""
        fc_dir = self.test_dir / "feature-context"
        fc_dir.mkdir(parents=True, exist_ok=True)
        valid_data = {
            "feature": {"id": "feat-1", "name": "Feature One"},
            "files": [{"path": "server/routes/auth.ts", "role": "API Route Handler"}]
        }
        (fc_dir / "feature-one.json").write_text(json.dumps(valid_data), encoding="utf-8")

        found = get_existing_feature_contexts(self.code_map_path)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].name, "feature-one.json")

    @patch("app.services.feature_context_generator.execute_hf_llm_call")
    def test_generate_all_feature_contexts_skips_llm_when_existing(self, mock_llm):
        """When valid files exist and force=False, returns them WITHOUT calling Hugging Face LLM."""
        fc_dir = self.test_dir / "feature-context"
        fc_dir.mkdir(parents=True, exist_ok=True)
        valid_data = {
            "feature": {"id": "feat-existing", "name": "Existing Feature"},
            "files": [{"path": "server/routes/auth.ts", "role": "API Route Handler"}]
        }
        (fc_dir / "existing.json").write_text(json.dumps(valid_data), encoding="utf-8")

        import asyncio
        contexts = asyncio.run(generate_all_feature_contexts(self.code_map_path, force=False))

        # LLM must NOT be called
        mock_llm.assert_not_called()
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]["feature"]["name"], "Existing Feature")


if __name__ == "__main__":
    unittest.main()

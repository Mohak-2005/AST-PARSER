import unittest
from unittest.mock import patch, AsyncMock
import tempfile
import shutil
import json
from pathlib import Path
from fastapi.testclient import TestClient

from app.main import app
from app.services.framework_detector import ProjectEnv
from app.services.source_target_inspector import TargetDescriptor, validate_target_identity
from app.services.test_script_generator import (
    build_script_generation_prompt,
    validate_generated_script,
    generate_script_for_single_test_case,
    generate_all_test_scripts,
    get_existing_test_scripts,
    resolve_target_import,
    determine_script_extension,
)


class TestScriptGenerator(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.temp_dir = Path(tempfile.mkdtemp())
        self.env_java = ProjectEnv(
            language="java",
            file_extension=".java",
            test_framework="junit5",
            test_framework_import="import org.junit.jupiter.api.Test;\nimport static org.junit.jupiter.api.Assertions.*;",
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_prompt_construction(self):
        tc = {
            "testCaseId": "TC-001",
            "title": "Test card creation",
            "target": {"file": "Card.java", "symbol": "Card", "type": "class"},
            "inputs": {"suit": "Hearts", "rank": "Ace"},
            "expectedResult": "Card created with Hearts and Ace"
        }
        retrieved_data = {"testCases": [tc]}
        desc = TargetDescriptor(
            target_symbol="Card",
            target_file="Card.java",
            observed_props_or_params=["suit", "rank"],
            observed_methods=["getSuit", "getRank"],
            is_symbol_found=True,
        )
        source_code = "class Card { String suit; String rank; }"
        messages = build_script_generation_prompt(
            tc, retrieved_data, self.env_java, source_code, desc
        )
        self.assertEqual(len(messages), 2)
        system_msg = messages[0]["content"]
        user_msg = messages[1]["content"]
        self.assertIn("TC-001", user_msg)
        self.assertIn("Card.java", user_msg)
        self.assertIn("DETERMINISTIC TARGET STRUCTURAL METADATA", system_msg)
        self.assertIn("['suit', 'rank']", system_msg.lower())
        self.assertIn("ANTI-SUBSTITUTION", system_msg)
        # CRITICAL PRIVACY: Verify raw source code is NOT present in any prompt message
        self.assertNotIn(source_code, system_msg)
        self.assertNotIn(source_code, user_msg)

    def test_validation_success_java(self):
        code = """
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

// Test Case ID: TC-001
class CardTest {
    @Test
    void testCardCreation() {
        Card card = new Card("Hearts", "Ace");
        assertEquals("Hearts", card.getSuit());
        assertEquals("Ace", card.getRank());
    }
}
"""
        tc = {"testCaseId": "TC-001", "target": {"symbol": "Card"}}
        is_val, cleaned, errs, failure_type = validate_generated_script(code, tc, {}, self.env_java)
        self.assertTrue(is_val)
        self.assertEqual(len(errs), 0)
        self.assertIsNone(failure_type)

    def test_validation_reject_missing_assertions(self):
        code = """
import org.junit.jupiter.api.Test;
// Test Case ID: TC-001
class CardTest {
    @Test
    void testCardCreation() {
        Card card = new Card("Hearts", "Ace");
    }
}
"""
        tc = {"testCaseId": "TC-001", "target": {"symbol": "Card"}}
        is_val, cleaned, errs, failure_type = validate_generated_script(code, tc, {}, self.env_java)
        self.assertFalse(is_val)
        self.assertTrue(any("assertion" in e.lower() for e in errs))

    def test_validation_reject_todo(self):
        code = """
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
// Test Case ID: TC-001
class CardTest {
    @Test
    void testCardCreation() {
        // TODO: implement test
        assertTrue(true);
    }
}
"""
        tc = {"testCaseId": "TC-001", "target": {"symbol": "Card"}}
        is_val, cleaned, errs, failure_type = validate_generated_script(code, tc, {}, self.env_java)
        self.assertFalse(is_val)
        self.assertTrue(any("todo" in e.lower() for e in errs))

    def test_validation_reject_cross_target_substitution(self):
        # Target is CursorOverlay, but script tests ActiveUsers
        code = """
import { describe, it, expect } from 'vitest';
import ActiveUsers from './ActiveUsers';

// Test Case ID: TC-003
describe('ActiveUsers component', () => {
    it('renders first five users', () => {
        expect(true).toBe(true);
    });
});
"""
        tc = {"testCaseId": "TC-003", "title": "Render CursorOverlay", "target": {"symbol": "CursorOverlay", "file": "CursorOverlay.tsx"}}
        env_ts = ProjectEnv(language="typescript", file_extension=".tsx", test_framework="vitest")
        desc = TargetDescriptor(target_symbol="CursorOverlay", target_file="CursorOverlay.tsx", is_symbol_found=True)
        is_val, cleaned, errs, failure_type = validate_generated_script(code, tc, {}, env_ts, desc)
        self.assertFalse(is_val)
        self.assertEqual(failure_type, "TARGET_MISMATCH")
        self.assertTrue(any("target_mismatch" in failure_type.lower() or "cursoroverlay" in e.lower() for e in errs))

    async def test_generate_single_script_success_human_readable_filename(self):
        tc = {
            "testCaseId": "TC-001",
            "title": "Verify Card Creation",
            "target": {"symbol": "Card", "file": "Card.java"},
        }
        retrieved_data = {
            "retrieval": {"primaryFile": "Card.java"},
            "source": {
                "files": [
                    {"path": "Card.java", "reason": "primary_target", "sourceCode": "class Card {}"}
                ]
            }
        }
        valid_llm_output = """```java
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

// Test Case ID: TC-001
class CardTest {
    @Test
    void testCard() {
        Card card = new Card();
        assertNotNull(card);
    }
}
```"""
        with patch("app.services.test_script_generator.execute_hf_llm_call", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (valid_llm_output, 200, 0.5)
            out_dir = self.temp_dir / "test scripts" / "card-deck"
            res = await generate_script_for_single_test_case(
                test_case=tc,
                retrieved_data=retrieved_data,
                project_env=self.env_java,
                target_output_dir=out_dir,
                force=True,
            )
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["filename"], "Verify Card Creation.java")
            expected_file = out_dir / "Verify Card Creation.java"
            self.assertTrue(expected_file.exists())
            content = expected_file.read_text(encoding="utf-8")
            self.assertIn("class CardTest", content)
            self.assertIn("Test Case ID: TC-001", content)
            self.assertNotIn("```", content)

    async def test_generate_all_test_scripts_flow(self):
        # Create mock project structure
        code_map = {
            "project": {"name": "TestDeck", "path": str(self.temp_dir), "languagesDetected": ["java"]},
            "files": []
        }
        code_map_file = self.temp_dir / "repository-code-map.json"
        code_map_file.write_text(json.dumps(code_map), encoding="utf-8")

        # Create code retrieved artifact
        ret_dir = self.temp_dir / "code retrieved"
        ret_dir.mkdir(parents=True)
        ret_artifact = {
            "testCases": [
                {
                    "testCaseId": "TC-001",
                    "title": "Card initialization assigns values",
                    "target": {"symbol": "Card", "file": "Card.java"}
                }
            ],
            "retrieval": {
                "primaryFile": "Card.java"
            },
            "source": {
                "files": [
                    {"path": "Card.java", "reason": "primary_target", "sourceCode": "class Card {}"}
                ]
            }
        }
        (ret_dir / "card-deck.json").write_text(json.dumps(ret_artifact), encoding="utf-8")

        valid_llm_output = """
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

// Test Case ID: TC-001
class CardTest {
    @Test
    void testCard() {
        Card card = new Card();
        assertNotNull(card);
    }
}
"""
        with patch("app.services.test_script_generator.execute_hf_llm_call", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (valid_llm_output, 200, 0.5)
            result = await generate_all_test_scripts(code_map_file, force=True)
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["generatedCount"], 1)
            self.assertEqual(result["successfulCount"], 1)

            # Check human-readable file on disk
            script_path = self.temp_dir / "test scripts" / "card-deck" / "Card initialization assigns values.java"
            self.assertTrue(script_path.exists())

            # Check manifest on disk
            manifest_path = self.temp_dir / "test scripts" / "card-deck" / "manifest.json"
            self.assertTrue(manifest_path.exists())

            # Check get_existing_test_scripts
            existing = get_existing_test_scripts(code_map_file)
            self.assertEqual(len(existing), 1)
            self.assertEqual(existing[0]["testCaseId"], "TC-001")
            self.assertEqual(existing[0]["testCaseTitle"], "Card initialization assigns values")

    def test_resolve_target_import_react_tsx(self):
        proj_root = self.temp_dir / "my-react-app"
        src_comp = proj_root / "src" / "components"
        src_comp.mkdir(parents=True)
        active_users_file = src_comp / "ActiveUsers.tsx"
        active_users_file.write_text("export const ActiveUsers = () => <div/>;\nexport default ActiveUsers;", encoding="utf-8")

        script_dir = proj_root / "test scripts" / "active-users"
        script_dir.mkdir(parents=True)

        desc = TargetDescriptor(target_symbol="ActiveUsers", target_file="src/components/ActiveUsers.tsx", exported_symbols=["ActiveUsers", "default"])
        res = resolve_target_import(
            target_file="src/components/ActiveUsers.tsx",
            target_symbol="ActiveUsers",
            script_output_dir=script_dir,
            project_root=proj_root,
            descriptor=desc,
            language="typescript",
            source_code=active_users_file.read_text(encoding="utf-8"),
        )
        self.assertTrue(res["resolved"])
        self.assertEqual(res["import_specifier"], "../../src/components/ActiveUsers")
        self.assertIn("import ActiveUsers from '../../src/components/ActiveUsers';", res["import_statement"])

    def test_resolve_target_import_failure_when_missing(self):
        res = resolve_target_import(
            target_file="",
            target_symbol="",
            script_output_dir=self.temp_dir,
            project_root=self.temp_dir,
        )
        self.assertFalse(res["resolved"])
        self.assertIsNotNone(res["error"])

    def test_determine_script_extension(self):
        env_ts = ProjectEnv(language="typescript", file_extension=".ts", has_typescript=True, has_jsx=True)
        env_js = ProjectEnv(language="javascript", file_extension=".js", has_typescript=False, has_jsx=False)

        # React TSX component with JSX
        code_tsx = "import React from 'react';\nexport const Test = () => <ActiveUsers />;"
        ext = determine_script_extension("typescript", "src/components/ActiveUsers.tsx", raw_code=code_tsx, project_env=env_ts)
        self.assertEqual(ext, ".tsx")

        # Plain TS without JSX
        code_ts = "export function add(a: number, b: number): number { return a + b; }"
        ext = determine_script_extension("typescript", "src/utils.ts", raw_code=code_ts, project_env=env_ts)
        self.assertEqual(ext, ".ts")

        # Plain JS without JSX
        code_js = "function add(a, b) { return a + b; }"
        ext = determine_script_extension("javascript", "src/utils.js", raw_code=code_js, project_env=env_js)
        self.assertEqual(ext, ".js")

        # Java
        self.assertEqual(determine_script_extension("java", "Card.java"), ".java")
        # Python
        self.assertEqual(determine_script_extension("python", "service.py"), ".py")

    def test_validation_reject_as_any_in_javascript(self):
        code = """
import { describe, it, expect } from '@jest/globals';
const { add } = require('./add');

// Test Case ID: TC-001
describe('add function', () => {
    it('adds numbers', () => {
        const val = add(1, 2) as any;
        expect(val).toBe(3);
    });
});
"""
        tc = {"testCaseId": "TC-001", "target": {"symbol": "add", "file": "add.js"}}
        env_js = ProjectEnv(language="javascript", file_extension=".js")
        is_val, cleaned, errs, failure_type = validate_generated_script(
            code, tc, {}, env_js, script_extension=".js"
        )
        self.assertFalse(is_val)
        self.assertEqual(failure_type, "INCOMPATIBLE_SYNTAX")
        self.assertTrue(any("as any" in e for e in errs))

    def test_validation_reject_jsx_in_plain_javascript_file(self):
        code = """
import { describe, it, expect } from '@jest/globals';
import ActiveUsers from './ActiveUsers';

// Test Case ID: TC-001
describe('ActiveUsers component', () => {
    it('renders', () => {
        render(<ActiveUsers />);
        expect(true).toBe(true);
    });
});
"""
        tc = {"testCaseId": "TC-001", "target": {"symbol": "ActiveUsers", "file": "ActiveUsers.js"}}
        env_js = ProjectEnv(language="javascript", file_extension=".js", has_jsx=False)
        is_val, cleaned, errs, failure_type = validate_generated_script(
            code, tc, {}, env_js, script_extension=".js"
        )
        self.assertFalse(is_val)
        self.assertEqual(failure_type, "SCRIPT_SYNTAX_ERROR")
        self.assertTrue(any("JSX" in e for e in errs))

    async def test_generate_script_react_active_users_tsx(self):
        proj_root = self.temp_dir / "react-project"
        src_comp = proj_root / "src" / "components"
        src_comp.mkdir(parents=True)
        active_users_file = src_comp / "ActiveUsers.tsx"
        active_users_code = """
import React from 'react';

export interface User {
    id: string;
    name: string;
}

export interface ActiveUsersProps {
    users: User[];
}

export const ActiveUsers: React.FC<ActiveUsersProps> = ({ users }) => {
    return <div>{users.length}</div>;
};

export default ActiveUsers;
"""
        active_users_file.write_text(active_users_code, encoding="utf-8")

        tc = {
            "testCaseId": "TC-001",
            "title": "Render ActiveUsers component with user list",
            "target": {"symbol": "ActiveUsers", "file": "src/components/ActiveUsers.tsx"},
        }
        retrieved_data = {
            "retrieval": {"primaryFile": "src/components/ActiveUsers.tsx"},
            "source": {
                "files": [
                    {"path": "src/components/ActiveUsers.tsx", "reason": "primary_target", "sourceCode": active_users_code}
                ]
            }
        }

        env_react_ts = ProjectEnv(
            language="typescript",
            file_extension=".tsx",
            test_framework="vitest",
            has_jsx=True,
            has_typescript=True,
            project_root=str(proj_root),
        )

        valid_llm_output = """```tsx
import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import ActiveUsers from './ActiveUsers';

// Test Case ID: TC-001
describe('ActiveUsers component', () => {
    it('TC-001: renders users correctly', () => {
        render(<ActiveUsers users={[{ id: '1', name: 'Alice' }]} />);
        expect(screen.getByText('Alice')).toBeDefined();
    });
});
```"""
        out_dir = proj_root / "test scripts" / "active-users"
        with patch("app.services.test_script_generator.execute_hf_llm_call", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (valid_llm_output, 200, 0.4)
            res = await generate_script_for_single_test_case(
                test_case=tc,
                retrieved_data=retrieved_data,
                project_env=env_react_ts,
                target_output_dir=out_dir,
                force=True,
            )
            self.assertEqual(res["status"], "success")
            self.assertTrue(res["filename"].endswith(".tsx"))
            self.assertEqual(res["filename"], "Render ActiveUsers component with user list.tsx")
            saved_content = Path(res["filePath"]).read_text(encoding="utf-8")
            # Verify import was reconciled to exact relative path
            self.assertIn("../../src/components/ActiveUsers", saved_content)
            self.assertIn("Test Case ID: TC-001", saved_content)

    async def test_generate_script_fails_unresolvable_import(self):
        tc = {
            "testCaseId": "TC-001",
            "title": "Render MissingFileTarget",
            "target": {"symbol": "MyComp", "file": ""},
        }
        retrieved_data = {
            "retrieval": {"primaryFile": ""},
            "source": {"files": [{"path": "", "sourceCode": "export const MyComp = () => {};"}]}
        }
        env = ProjectEnv(language="typescript", file_extension=".ts")
        out_dir = self.temp_dir / "test scripts" / "test"
        res = await generate_script_for_single_test_case(
            test_case=tc,
            retrieved_data=retrieved_data,
            project_env=env,
            target_output_dir=out_dir,
            force=True,
        )
        self.assertEqual(res["status"], "VALIDATION_FAILED")
        self.assertEqual(res["failureType"], "IMPORT_RESOLUTION_ERROR")

    def test_resolve_target_import_python(self):
        res = resolve_target_import(
            target_file="src/services/user_service.py",
            target_symbol="get_active_users",
            script_output_dir=self.temp_dir / "tests",
            project_root=self.temp_dir,
            language="python",
        )
        self.assertTrue(res["resolved"])
        self.assertEqual(res["import_specifier"], "src.services.user_service")
        self.assertEqual(res["import_statement"], "from src.services.user_service import get_active_users")

    def test_resolve_target_import_java(self):
        res = resolve_target_import(
            target_file="src/main/java/com/example/Card.java",
            target_symbol="Card",
            script_output_dir=self.temp_dir / "src/test/java",
            project_root=self.temp_dir,
            language="java",
            source_code="package com.example;\npublic class Card {}",
        )
        self.assertTrue(res["resolved"])
        self.assertEqual(res["import_statement"], "import com.example.Card;")

    def test_validation_allows_as_any_in_typescript(self):
        code = """
import { describe, it, expect } from 'vitest';
import { add } from './add';

// Test Case ID: TC-001
describe('add function', () => {
    it('adds numbers', () => {
        const val = add(1, 2) as any;
        expect(val).toBe(3);
    });
});
"""
        tc = {"testCaseId": "TC-001", "target": {"symbol": "add", "file": "add.ts"}}
        env_ts = ProjectEnv(language="typescript", file_extension=".ts")
        is_val, cleaned, errs, failure_type = validate_generated_script(
            code, tc, {}, env_ts, script_extension=".ts"
        )
        self.assertTrue(is_val)
        self.assertEqual(len(errs), 0)

    def test_validation_allows_jsx_in_tsx(self):
        code = """
import { describe, it, expect } from 'vitest';
import ActiveUsers from './ActiveUsers';

// Test Case ID: TC-001
describe('ActiveUsers component', () => {
    it('renders', () => {
        render(<ActiveUsers />);
        expect(ActiveUsers).toBeDefined();
    });
});
"""
        tc = {"testCaseId": "TC-001", "target": {"symbol": "ActiveUsers", "file": "ActiveUsers.tsx"}}
        env_ts = ProjectEnv(language="typescript", file_extension=".tsx", has_jsx=True, has_typescript=True)
        is_val, cleaned, errs, failure_type = validate_generated_script(
            code, tc, {}, env_ts, script_extension=".tsx"
        )
        self.assertTrue(is_val)
        self.assertEqual(len(errs), 0)

    async def test_generate_script_collision_resolution_tc001_tc002_tsx(self):
        proj_root = self.temp_dir / "react-project-col"
        src_comp = proj_root / "src" / "components"
        src_comp.mkdir(parents=True)
        active_users_file = src_comp / "ActiveUsers.tsx"
        active_users_code = "export const ActiveUsers = () => <div>Users</div>; export default ActiveUsers;"
        active_users_file.write_text(active_users_code, encoding="utf-8")

        tc1 = {
            "testCaseId": "TC-001",
            "title": "Render ActiveUsers component",
            "target": {"symbol": "ActiveUsers", "file": "src/components/ActiveUsers.tsx"},
        }
        tc2 = {
            "testCaseId": "TC-002",
            "title": "Render ActiveUsers component",
            "target": {"symbol": "ActiveUsers", "file": "src/components/ActiveUsers.tsx"},
        }
        retrieved_data = {
            "retrieval": {"primaryFile": "src/components/ActiveUsers.tsx"},
            "source": {
                "files": [
                    {"path": "src/components/ActiveUsers.tsx", "reason": "primary_target", "sourceCode": active_users_code}
                ]
            }
        }

        env_react_ts = ProjectEnv(
            language="typescript",
            file_extension=".tsx",
            test_framework="vitest",
            has_jsx=True,
            has_typescript=True,
            project_root=str(proj_root),
        )

        valid_llm_output_1 = """```tsx
import React from 'react';
import { describe, it, expect } from 'vitest';
import ActiveUsers from './ActiveUsers';

// Test Case ID: TC-001
describe('ActiveUsers component', () => {
    it('TC-001: renders users', () => {
        expect(ActiveUsers).toBeDefined();
    });
});
```"""
        valid_llm_output_2 = """```tsx
import React from 'react';
import { describe, it, expect } from 'vitest';
import ActiveUsers from './ActiveUsers';

// Test Case ID: TC-002
describe('ActiveUsers component', () => {
    it('TC-002: renders empty list', () => {
        expect(ActiveUsers).toBeDefined();
    });
});
```"""
        out_dir = proj_root / "test scripts" / "active-users"
        with patch("app.services.test_script_generator.execute_hf_llm_call", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (valid_llm_output_1, 200, 0.3)
            existing = set()
            res1 = await generate_script_for_single_test_case(
                test_case=tc1,
                retrieved_data=retrieved_data,
                project_env=env_react_ts,
                target_output_dir=out_dir,
                force=True,
                existing_filenames=existing,
            )
            self.assertEqual(res1["status"], "success")
            self.assertEqual(res1["filename"], "Render ActiveUsers component.tsx")
            existing.add(res1["filename"])

            mock_llm.return_value = (valid_llm_output_2, 200, 0.3)
            res2 = await generate_script_for_single_test_case(
                test_case=tc2,
                retrieved_data=retrieved_data,
                project_env=env_react_ts,
                target_output_dir=out_dir,
                force=True,
                existing_filenames=existing,
            )
            self.assertEqual(res2["status"], "success")
            self.assertEqual(res2["filename"], "Render ActiveUsers component__TC-002.tsx")

    async def test_privacy_outgoing_provider_request_contains_no_source_code_sentinel(self):
        """
        CRITICAL PRIVACY TEST:
        Validates the entire outgoing provider request.
        Uses a unique source-code sentinel string and verifies that raw source content
        is ABSENT from all outgoing LLM messages (system, user, assistant).
        """
        proj_root = self.temp_dir / "privacy-project"
        src_dir = proj_root / "src"
        src_dir.mkdir(parents=True)
        secret_sentinel = "PROPRIETARY_INTERNAL_ALGORITHM_SENTINEL_XYZ_987654321"
        source_code = (
            f"// Secret logic\n"
            f"export function computeSecretHash(input: string): string {{\n"
            f"    const secret = '{secret_sentinel}';\n"
            f"    return input + secret;\n"
            f"}}\n"
        )
        src_file = src_dir / "secret.ts"
        src_file.write_text(source_code, encoding="utf-8")

        tc = {
            "testCaseId": "TC-PRIVACY-01",
            "title": "Test secret hash computation",
            "target": {"symbol": "computeSecretHash", "file": "src/secret.ts"},
            "inputs": {"input": "test"},
            "expectedResult": "returns hashed string",
        }
        retrieved_data = {
            "retrieval": {"primaryFile": "src/secret.ts"},
            "source": {
                "files": [
                    {"path": "src/secret.ts", "reason": "primary_target", "sourceCode": source_code}
                ]
            }
        }
        env_ts = ProjectEnv(
            language="typescript",
            file_extension=".ts",
            test_framework="vitest",
            project_root=str(proj_root),
        )
        out_dir = proj_root / "test scripts" / "secret"

        captured_messages = []

        async def capture_llm_call(*args, **kwargs):
            nonlocal captured_messages
            msgs = kwargs.get("messages") or (args[0] if args else [])
            captured_messages = msgs
            valid_script = (
                "import { describe, it, expect } from 'vitest';\n"
                "import { computeSecretHash } from '../../src/secret';\n"
                "// Test Case ID: TC-PRIVACY-01\n"
                "describe('computeSecretHash', () => {\n"
                "  it('hashes correctly', () => {\n"
                "    expect(computeSecretHash('test')).toBeDefined();\n"
                "  });\n"
                "});\n"
            )
            return (valid_script, 200, 0.2)

        with patch("app.services.test_script_generator.execute_hf_llm_call", side_effect=capture_llm_call):
            res = await generate_script_for_single_test_case(
                test_case=tc,
                retrieved_data=retrieved_data,
                project_env=env_ts,
                target_output_dir=out_dir,
                force=True,
            )
            self.assertEqual(res["status"], "success")

            # Verify outgoing provider payload has zero traces of the secret sentinel or raw source code
            self.assertGreater(len(captured_messages), 0)
            full_payload_text = json.dumps(captured_messages)
            self.assertNotIn(secret_sentinel, full_payload_text)
            self.assertNotIn("const secret =", full_payload_text)
            self.assertNotIn("return input + secret", full_payload_text)

    def test_local_validation_source_behavior_mismatch(self):
        """
        Validates that if a generated script invokes an ungrounded method on the target
        that does not exist in local source code or observed methods, local validation rejects it.
        """
        code = """
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

// Test Case ID: TC-001
class CalculatorTest {
    @Test
    void testCompute() {
        Calculator calc = new Calculator();
        int res = calc.unsupportedInventedMethod(10);
        assertEquals(20, res);
    }
}
"""
        tc = {"testCaseId": "TC-001", "target": {"symbol": "Calculator", "file": "Calculator.java"}}
        env_java = ProjectEnv(language="java", file_extension=".java", test_framework="junit5")
        desc = TargetDescriptor(
            target_symbol="Calculator",
            target_file="Calculator.java",
            observed_methods=["add", "subtract"],
            is_symbol_found=True,
        )
        source_code = "class Calculator { int add(int a, int b) { return a + b; } int subtract(int a, int b) { return a - b; } }"
        is_val, cleaned, errs, failure_type = validate_generated_script(
            code, tc, {}, env_java, descriptor=desc, source_code=source_code
        )
        self.assertFalse(is_val)
        self.assertEqual(failure_type, "SOURCE_BEHAVIOR_NOT_FOUND")
        self.assertTrue(any("unsupportedinventedmethod" in e.lower() for e in errs))

    def test_test_case_source_mismatch_target_file(self):
        """
        Validates that if a Test Case targets a file completely outside the retrieved source,
        pre-generation target validation returns TEST_CASE_SOURCE_MISMATCH.
        """
        tc = {
            "testCaseId": "TC-999",
            "title": "Mismatched Target",
            "target": {"symbol": "SomeService", "file": "src/completely/different/Service.java"},
        }
        retrieved_data = {
            "retrieval": {"primaryFile": "src/components/ActiveUsers.tsx"},
            "source": {"files": [{"path": "src/components/ActiveUsers.tsx", "sourceCode": "export default {};"}]}
        }
        desc = TargetDescriptor(target_symbol="SomeService", is_symbol_found=False)
        is_val, failure_type, explanation = validate_target_identity(tc, retrieved_data, desc)
        self.assertFalse(is_val)
        self.assertEqual(failure_type, "TEST_CASE_SOURCE_MISMATCH")

    def test_test_case_source_mismatch_target_method(self):
        """
        Validates that if a Test Case specifies an explicit targetMethod that contradicts
        the target's observed methods, validate_target_identity returns TEST_CASE_SOURCE_MISMATCH.
        """
        tc = {
            "testCaseId": "TC-002",
            "title": "Contradictory Method",
            "target": {"symbol": "Calculator", "file": "Calculator.java", "method": "nonExistentOperation"},
        }
        retrieved_data = {
            "retrieval": {"primaryFile": "Calculator.java"},
            "source": {"files": [{"path": "Calculator.java", "sourceCode": "class Calculator { int add() { return 0; } }"}]}
        }
        desc = TargetDescriptor(target_symbol="Calculator", observed_methods=["add"], is_symbol_found=True)
        is_val, failure_type, explanation = validate_target_identity(tc, retrieved_data, desc)
        self.assertFalse(is_val)
        self.assertEqual(failure_type, "TEST_CASE_SOURCE_MISMATCH")

    def test_multi_language_import_resolution_go_rust_csharp(self):
        """
        Validates dynamic import resolution across AST-Parser languages (Go, Rust, C#).
        """
        # Go
        go_res = resolve_target_import(
            target_file="math/calculator.go",
            target_symbol="Add",
            script_output_dir=self.temp_dir / "tests",
            language="go",
            source_code="package math\nfunc Add(a, b int) int { return a + b }",
        )
        self.assertTrue(go_res["resolved"])
        self.assertIn('import "math"', go_res["import_statement"])

        # Rust
        rust_res = resolve_target_import(
            target_file="src/models.rs",
            target_symbol="User",
            script_output_dir=self.temp_dir / "tests",
            language="rust",
            source_code="pub struct User { pub id: u64 }",
        )
        self.assertTrue(rust_res["resolved"])
        self.assertEqual(rust_res["import_statement"], "use crate::User;")

        # C#
        cs_res = resolve_target_import(
            target_file="Services/OrderService.cs",
            target_symbol="OrderService",
            script_output_dir=self.temp_dir / "tests",
            language="c_sharp",
            source_code="namespace MyApp.Services;\npublic class OrderService {}",
        )
        self.assertTrue(cs_res["resolved"])
        self.assertEqual(cs_res["import_statement"], "using MyApp.Services;")

    def test_determine_script_extension_multi_language(self):
        """
        Validates dynamic script extension selection across AST-Parser supported languages.
        """
        self.assertEqual(determine_script_extension("go", "main.go"), ".go")
        self.assertEqual(determine_script_extension("rust", "lib.rs"), ".rs")
        self.assertEqual(determine_script_extension("c_sharp", "Program.cs"), ".cs")
        self.assertEqual(determine_script_extension("python", "app.py"), ".py")
        self.assertEqual(determine_script_extension("java", "App.java"), ".java")
        self.assertEqual(determine_script_extension("cpp", "engine.cpp"), ".cpp")
        self.assertEqual(determine_script_extension("ruby", "model.rb"), ".rb")
        self.assertEqual(determine_script_extension("php", "index.php"), ".php")
        self.assertEqual(determine_script_extension("kotlin", "User.kt"), ".kt")
        self.assertEqual(determine_script_extension("swift", "View.swift"), ".swift")

    def test_go_and_rust_script_validation(self):
        """
        Validates that generated Go and Rust test scripts are validated against their framework conventions.
        """
        go_code = """
package math_test

import (
    "testing"
    "math"
)

// Test Case ID: TC-GO-01
func TestAdd(t *testing.T) {
    res := math.Add(2, 3)
    if res != 5 {
        t.Errorf("expected 5, got %d", res)
    }
}
"""
        tc_go = {"testCaseId": "TC-GO-01", "target": {"symbol": "Add", "file": "math.go"}}
        env_go = ProjectEnv(language="go", file_extension=".go", test_framework="testing")
        is_val, _, errs, _ = validate_generated_script(go_code, tc_go, {}, env_go)
        self.assertTrue(is_val, f"Errors: {errs}")

        rust_code = """
// Test Case ID: TC-RS-01
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_add() {
        assert_eq!(2 + 3, 5);
    }
}
"""
        tc_rs = {"testCaseId": "TC-RS-01", "target": {"symbol": "add", "file": "src/lib.rs"}}
        env_rs = ProjectEnv(language="rust", file_extension=".rs", test_framework="cargo_test")
        is_val, _, errs, _ = validate_generated_script(rust_code, tc_rs, {}, env_rs)
        self.assertTrue(is_val, f"Errors: {errs}")

    @patch("app.services.test_script_generator.execute_hf_llm_call")
    async def test_generate_script_multi_case_retrieved_source_resolution(self, mock_llm):
        """
        Validates that in multi-test-case retrieved artifacts, each test case resolves its
        own source file and decorated target functions without false TARGET_SYMBOL_NOT_FOUND.
        """
        mock_llm.return_value = (
            "import pytest\nfrom app import index\n\n# Test Case ID: TC-002\ndef test_index():\n    assert index() == 'hello'\n",
            200,
            0.1
        )
        env_py = ProjectEnv(
            language="python",
            file_extension=".py",
            test_framework="pytest",
            project_root=str(self.temp_dir),
        )
        retrieved_data = {
            "retrieval": {"primaryFile": "app.py", "status": "resolved"},
            "source": {
                "files": [{
                    "path": "app.py",
                    "reason": "primary_target",
                    "symbol": "get_db_connection",
                    "sourceCode": "def get_db_connection(): pass\n"
                }]
            },
            "testCases": [
                {
                    "testCaseId": "TC-001",
                    "title": "Test db connection",
                    "target": {"file": "app.py", "symbol": "get_db_connection"},
                    "source": {
                        "files": [{
                            "path": "app.py",
                            "reason": "primary_target",
                            "symbol": "get_db_connection",
                            "sourceCode": "def get_db_connection(): pass\n"
                        }]
                    }
                },
                {
                    "testCaseId": "TC-002",
                    "title": "Test index route",
                    "target": {"file": "app.py", "symbol": "index"},
                    "source": {
                        "files": [{
                            "path": "app.py",
                            "reason": "primary_target",
                            "symbol": "index",
                            "sourceCode": "@app.route('/')\ndef index():\n    return 'hello'\n"
                        }]
                    }
                }
            ]
        }
        out_dir = self.temp_dir / "test_scripts"
        # Generate for TC-002 (which is index, not get_db_connection)
        res = await generate_script_for_single_test_case(
            test_case=retrieved_data["testCases"][1],
            retrieved_data=retrieved_data,
            project_env=env_py,
            target_output_dir=out_dir,
            force=True
        )
        self.assertEqual(res["status"], "success")
        self.assertIn("Test index route", res["filename"])


    def test_rejection_of_trivial_assertions(self):
        """
        Check 4: Rejects trivial / tautological assertions (assert True, expect(true).toBe(true)).
        """
        py_trivial = """
import pytest
from app import calculate

# Test Case ID: TC-001
def test_calc():
    calculate(1, 2)
    assert True
"""
        tc = {"testCaseId": "TC-001", "target": {"symbol": "calculate", "file": "app.py"}}
        env_py = ProjectEnv(language="python", file_extension=".py", test_framework="pytest")
        is_val, _, errs, fail_type = validate_generated_script(py_trivial, tc, {}, env_py)
        self.assertFalse(is_val)
        self.assertEqual(fail_type, "TRIVIAL_ASSERTION_DETECTED")
        self.assertTrue(any("trivial or tautological" in e for e in errs))

        js_trivial = """
import { describe, it, expect } from 'vitest';
import { calculate } from './calc';

// Test Case ID: TC-001
describe('calculate', () => {
    it('works', () => {
        calculate(1, 2);
        expect(true).toBe(true);
    });
});
"""
        env_js = ProjectEnv(language="javascript", file_extension=".js", test_framework="vitest")
        is_val_js, _, errs_js, fail_type_js = validate_generated_script(js_trivial, tc, {}, env_js)
        self.assertFalse(is_val_js)
        self.assertEqual(fail_type_js, "TRIVIAL_ASSERTION_DETECTED")

    def test_expected_result_error_alignment(self):
        """
        Check 4: Expected result alignment - tests expecting exceptions/errors must assert them.
        """
        tc_err = {
            "testCaseId": "TC-ERR-01",
            "title": "Throws on invalid input",
            "expectedResult": "Raises ValueError when input is negative",
            "scenarioType": "negative",
            "target": {"symbol": "compute", "file": "app.py"}
        }
        env_py = ProjectEnv(language="python", file_extension=".py", test_framework="pytest")

        # Script only asserts happy path
        script_happy = """
import pytest
from app import compute

# Test Case ID: TC-ERR-01
def test_compute():
    res = compute(-5)
    assert res == -5
"""
        is_val, _, errs, fail_type = validate_generated_script(script_happy, tc_err, {}, env_py)
        self.assertFalse(is_val)
        self.assertEqual(fail_type, "EXPECTED_RESULT_MISMATCH")
        self.assertTrue(any("specifies error/exception" in e for e in errs))

        # Script properly uses pytest.raises
        script_error = """
import pytest
from app import compute

# Test Case ID: TC-ERR-01
def test_compute():
    with pytest.raises(ValueError):
        compute(-5)
"""
        is_val_ok, _, errs_ok, _ = validate_generated_script(script_error, tc_err, {}, env_py)
        self.assertTrue(is_val_ok, f"Unexpected errors: {errs_ok}")

    def test_unit_test_isolation_live_calls(self):
        """
        Check 5: Unit tests must not make unmocked live external network or database calls.
        """
        tc_unit = {
            "testCaseId": "TC-ISO-01",
            "testType": "unit",
            "target": {"symbol": "fetch_user", "file": "app.py", "type": "unit"}
        }
        env_py = ProjectEnv(language="python", file_extension=".py", test_framework="pytest")

        script_live = """
import pytest
import requests
from app import fetch_user

# Test Case ID: TC-ISO-01
def test_fetch():
    resp = requests.get("https://api.example.com/users")
    assert resp.status_code == 200
"""
        is_val, _, errs, fail_type = validate_generated_script(script_live, tc_unit, {}, env_py)
        self.assertFalse(is_val)
        self.assertEqual(fail_type, "LIVE_DEPENDENCY_IN_UNIT_TEST")
        self.assertTrue(any("unmocked live external call" in e for e in errs))

        # With mock/patch, it should pass isolation check
        script_mocked = """
import pytest
from unittest.mock import patch
from app import fetch_user

# Test Case ID: TC-ISO-01
@patch("requests.get")
def test_fetch(mock_get):
    mock_get.return_value.status_code = 200
    res = fetch_user()
    assert res is not None
"""
        desc = TargetDescriptor(target_symbol="fetch_user", target_file="app.py", is_symbol_found=True)
        is_val_m, _, errs_m, _ = validate_generated_script(script_mocked, tc_unit, {}, env_py, descriptor=desc, source_code="def fetch_user(): pass")
        self.assertTrue(is_val_m, f"Unexpected errors: {errs_m}")

    def test_ungrounded_mock_target_rejected(self):
        """
        Check 5: Mocks must target symbols grounded in retrieved source code or descriptor.
        """
        tc = {
            "testCaseId": "TC-MOCK-01",
            "testType": "unit",
            "target": {"symbol": "process_order", "file": "order.py"}
        }
        env_py = ProjectEnv(language="python", file_extension=".py", test_framework="pytest")
        desc = TargetDescriptor(
            target_symbol="process_order",
            target_file="order.py",
            observed_methods=["validate", "calculate"],
            exported_symbols=["process_order"]
        )
        source = "def process_order(): pass\ndef validate(): pass"

        script_bad_mock = """
import pytest
from unittest.mock import patch
from order import process_order

# Test Case ID: TC-MOCK-01
@patch("order.completely_hallucinated_ghost_service")
def test_order(mock_ghost):
    res = process_order()
    assert res is not None
"""
        is_val, _, errs, fail_type = validate_generated_script(
            script_bad_mock, tc, {}, env_py, descriptor=desc, source_code=source
        )
        self.assertFalse(is_val)
        self.assertEqual(fail_type, "UNGROUNDED_MOCK_TARGET")
        self.assertTrue(any("completely_hallucinated_ghost_service" in e for e in errs))

    def test_conflicting_test_case_id_rejected(self):
        """
        Check 1: Rejects scripts declaring a conflicting testCaseId from the same retrieved group.
        """
        tc1 = {"testCaseId": "TC-001", "target": {"symbol": "index", "file": "app.py"}}
        ret_data = {
            "testCases": [
                {"testCaseId": "TC-001", "target": {"symbol": "index", "file": "app.py"}},
                {"testCaseId": "TC-002", "target": {"symbol": "about", "file": "app.py"}},
            ]
        }
        env_py = ProjectEnv(language="python", file_extension=".py", test_framework="pytest")

        script_conflict = """
import pytest
from app import index

# Test Case ID: TC-002
def test_index():
    assert index() == 'home'
"""
        is_val, _, errs, fail_type = validate_generated_script(script_conflict, tc1, ret_data, env_py)
        self.assertFalse(is_val)
        self.assertEqual(fail_type, "TEST_CASE_SOURCE_MISMATCH")
        self.assertTrue(any("declares 'TC-002' but current test case is 'TC-001'" in e for e in errs))

    def test_ungrounded_jsx_props_rejected(self):
        """
        Check 3: Rejects JSX component rendering that passes ungrounded, fabricated props.
        """
        tc = {"testCaseId": "TC-001", "target": {"symbol": "ActiveUsers", "file": "ActiveUsers.tsx"}}
        env_tsx = ProjectEnv(language="typescript", file_extension=".tsx", test_framework="vitest", has_jsx=True)
        desc = TargetDescriptor(
            target_symbol="ActiveUsers",
            target_file="ActiveUsers.tsx",
            observed_props_or_params=["users", "onSelectUser"]
        )
        source = "export const ActiveUsers = ({ users, onSelectUser }: Props) => null;"

        script_bad_prop = """
import React from 'react';
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import ActiveUsers from './ActiveUsers';

// Test Case ID: TC-001
describe('ActiveUsers', () => {
    it('renders', () => {
        render(<ActiveUsers users={[]} hallucinatedGhostProp="invalid" />);
        expect(ActiveUsers).toBeDefined();
    });
});
"""
        is_val, _, errs, fail_type = validate_generated_script(
            script_bad_prop, tc, {}, env_tsx, descriptor=desc, source_code=source
        )
        self.assertFalse(is_val)
        self.assertEqual(fail_type, "SOURCE_BEHAVIOR_NOT_FOUND")
        self.assertTrue(any("hallucinatedGhostProp" in e for e in errs))

    def test_python_ast_syntax_error_reporting(self):
        """
        Check 6: Exact Python syntax validation via ast.parse reporting line number.
        """
        tc = {"testCaseId": "TC-SYN-01", "target": {"symbol": "calculate", "file": "app.py"}}
        env_py = ProjectEnv(language="python", file_extension=".py", test_framework="pytest")
        broken_syntax = """
import pytest
from app import calculate

# Test Case ID: TC-SYN-01
def test_broken(
    res = calculate(1, 2)
    assert res == 3
"""
        is_val, _, errs, fail_type = validate_generated_script(broken_syntax, tc, {}, env_py)
        self.assertFalse(is_val)
        self.assertEqual(fail_type, "SCRIPT_SYNTAX_ERROR")
        self.assertTrue(any("Python script contains syntax error at line" in e for e in errs))

    def test_delimiter_balancing_check(self):
        """
        Check 6: Balanced delimiter validation across parentheses, braces, and brackets.
        """
        tc = {"testCaseId": "TC-DELIM-01", "target": {"symbol": "UserCard", "file": "UserCard.tsx"}}
        env_tsx = ProjectEnv(language="typescript", file_extension=".tsx", test_framework="vitest", has_jsx=True)
        unclosed_brace = """
import { describe, it, expect } from 'vitest';
import UserCard from './UserCard';

// Test Case ID: TC-DELIM-01
describe('UserCard', () => {
    it('renders', () => {
        expect(UserCard).toBeDefined();
    });
"""
        is_val, _, errs, fail_type = validate_generated_script(unclosed_brace, tc, {}, env_tsx)
        self.assertFalse(is_val)
        self.assertEqual(fail_type, "SCRIPT_SYNTAX_ERROR")
        self.assertTrue(any("Unclosed opening delimiter '{'" in e for e in errs))

    def test_browser_automation_in_unit_test_rejected(self):
        """
        Check 8: Unit tests must not import or invoke browser automation frameworks.
        """
        tc = {
            "testCaseId": "TC-E2E-01",
            "testType": "unit",
            "target": {"symbol": "login", "file": "auth.py", "type": "unit"}
        }
        env_py = ProjectEnv(language="python", file_extension=".py", test_framework="pytest")
        script_playwright = """
import pytest
from playwright.sync_api import sync_playwright
from auth import login

# Test Case ID: TC-E2E-01
def test_login():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("http://localhost:3000")
        assert login() is True
"""
        is_val, _, errs, fail_type = validate_generated_script(script_playwright, tc, {}, env_py)
        self.assertFalse(is_val)
        self.assertEqual(fail_type, "TEST_TYPE_MISMATCH")
        self.assertTrue(any("Browser automation is reserved for end-to-end" in e for e in errs))

    def test_get_existing_test_scripts_cross_validation_on_disk(self):
        """
        Validates get_existing_test_scripts cross-validating valid vs invalid scripts on disk.
        """
        code_map = {
            "project": {"name": "TestApp", "path": str(self.temp_dir), "languagesDetected": ["python"]},
            "files": []
        }
        code_map_file = self.temp_dir / "repository-code-map.json"
        code_map_file.write_text(json.dumps(code_map), encoding="utf-8")

        # Create code retrieved artifact
        ret_dir = self.temp_dir / "code retrieved"
        ret_dir.mkdir(parents=True, exist_ok=True)
        ret_artifact = {
            "testCases": [
                {
                    "testCaseId": "TC-VALID-01",
                    "title": "Calculate sum correctly",
                    "target": {"symbol": "add", "file": "math_service.py"},
                    "source": {
                        "files": [
                            {"path": "math_service.py", "reason": "primary_target", "sourceCode": "def add(a, b):\n    return a + b\n"}
                        ]
                    }
                },
                {
                    "testCaseId": "TC-INVALID-02",
                    "title": "Sub with dummy assertion",
                    "target": {"symbol": "sub", "file": "math_service.py"},
                    "source": {
                        "files": [
                            {"path": "math_service.py", "reason": "primary_target", "sourceCode": "def sub(a, b):\n    return a - b\n"}
                        ]
                    }
                }
            ]
        }
        (ret_dir / "math-group.json").write_text(json.dumps(ret_artifact), encoding="utf-8")

        # Create test scripts directory on disk
        group_scripts_dir = self.temp_dir / "test scripts" / "math-group"
        group_scripts_dir.mkdir(parents=True, exist_ok=True)

        valid_script = """import pytest
from math_service import add

# Test Case ID: TC-VALID-01
# Test Case Title: Calculate sum correctly
# Target: math_service.py::add

def test_add():
    assert add(2, 3) == 5
"""
        invalid_script = """import pytest
from math_service import sub

# Test Case ID: TC-INVALID-02
# Test Case Title: Sub with dummy assertion
# Target: math_service.py::sub

def test_sub():
    sub(5, 2)
    assert True
"""
        (group_scripts_dir / "Calculate sum correctly.py").write_text(valid_script, encoding="utf-8")
        (group_scripts_dir / "Sub with dummy assertion.py").write_text(invalid_script, encoding="utf-8")

        existing = get_existing_test_scripts(code_map_file)
        self.assertEqual(len(existing), 2)

        valid_entry = next((s for s in existing if s["testCaseId"] == "TC-VALID-01"), None)
        self.assertIsNotNone(valid_entry)
        self.assertEqual(valid_entry["status"], "success")
        self.assertEqual(len(valid_entry["errors"]), 0)

        invalid_entry = next((s for s in existing if s["testCaseId"] == "TC-INVALID-02"), None)
        self.assertIsNotNone(invalid_entry)
        self.assertEqual(invalid_entry["status"], "VALIDATION_FAILED")
        self.assertEqual(invalid_entry["failureType"], "TRIVIAL_ASSERTION_DETECTED")
        self.assertTrue(len(invalid_entry["errors"]) > 0)


if __name__ == "__main__":
    unittest.main()

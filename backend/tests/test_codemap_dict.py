import json
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.llm_service import build_user_prompt, clean_file_for_prompt
from app.services.graph_validator import (
    extract_code_map_entities,
    extract_code_map_imports,
    validate_graph
)
from app.models.schemas import CodeMapPayload

def test_codemap_dict_support():
    print("=== TESTING BACKEND DICTIONARY-KEYED CODE MAP SUPPORT ===")

    # 1. Test CodeMapPayload Pydantic validation with dictionary files
    dict_code_map = {
        "project": {
            "name": "MernApp",
            "path": "C:/Projects/MernApp",
            "analyzedAt": "2026-09-07T12:00:00.000Z",
            "totalFiles": 2,
            "parsedFiles": 2,
            "failedFiles": 0
        },
        "files": {
            "backend/models/Payment.js": {
                "language": "javascript",
                "lineCount": 49,
                "dependencies": [
                    {"target": "mongoose", "type": "external", "kind": "require", "line": 1}
                ],
                "exports": [
                    {"name": "Payment", "type": "model", "line": 47}
                ],
                "declarations": [
                    {"name": "PaymentSchema", "type": "schema", "line": 5},
                    {"name": "Payment", "type": "model", "line": 47}
                ]
            },
            "backend/routes/paymentRoutes.js": {
                "language": "javascript",
                "lineCount": 30,
                "dependencies": [
                    {"target": "../models/Payment.js", "type": "internal", "kind": "require", "line": 2}
                ],
                "routes": [
                    {"method": "POST", "path": "/api/payment/create"}
                ]
            }
        },
        "relationships": [
            {
                "from": "backend/routes/paymentRoutes.js",
                "to": "backend/models/Payment.js",
                "type": "imports",
                "evidence": {"line": 2}
            },
            {
                "from": "backend/models/Payment.js",
                "to": "mongoose",
                "type": "imports",
                "evidence": {"line": 1},
                "external": True
            }
        ]
    }

    # Pydantic validation
    payload = CodeMapPayload.model_validate(dict_code_map)
    assert isinstance(payload.files, dict), "payload.files must be accepted as dict"
    print("  [PASS] CodeMapPayload validated dictionary files successfully.")

    # 2. Test clean_file_for_prompt
    payment_f = dict_code_map["files"]["backend/models/Payment.js"]
    cleaned = clean_file_for_prompt(payment_f, path_key="backend/models/Payment.js")
    assert cleaned["relativePath"] == "backend/models/Payment.js"
    assert "imports" in cleaned
    assert cleaned["imports"][0]["source"] == "mongoose"
    assert "declarations" in cleaned
    assert len(cleaned["declarations"]) == 2
    # Verify empty fields are omitted
    assert "routes" not in cleaned
    assert "databaseOperations" not in cleaned
    print("  [PASS] clean_file_for_prompt produced sparse facts and omitted empty fields.")

    # 3. Test build_user_prompt
    prompt = build_user_prompt(dict_code_map)
    assert "backend/models/Payment.js" in prompt
    assert "mongoose" in prompt
    print("  [PASS] build_user_prompt rendered prompt with dictionary files.")

    # 4. Test validator entities and imports
    entities = extract_code_map_entities(dict_code_map)
    assert "backend/models/Payment.js" in entities
    assert "PaymentSchema" in entities
    assert "Payment" in entities
    assert "mongoose" in entities
    print("  [PASS] extract_code_map_entities extracted declarations and dependencies from dict.")

    imports = extract_code_map_imports(dict_code_map)
    assert "mongoose" in imports
    assert "package:mongoose" in imports
    print("  [PASS] extract_code_map_imports extracted external dependency 'mongoose'.")

    # 5. Test validate_graph
    raw_graph = {
        "nodes": [
            {"id": "file:backend/models/Payment.js", "type": "file", "name": "Payment.js", "filePath": "backend/models/Payment.js"},
            {"id": "file:backend/routes/paymentRoutes.js", "type": "file", "name": "paymentRoutes.js", "filePath": "backend/routes/paymentRoutes.js"},
            {"id": "package:mongoose", "type": "package", "name": "mongoose"}
        ],
        "edges": [
            {
                "id": "e1",
                "source": "file:backend/routes/paymentRoutes.js",
                "target": "file:backend/models/Payment.js",
                "relationship": "imports",
                "evidence": {"line": 2}
            },
            {
                "id": "e2",
                "source": "file:backend/models/Payment.js",
                "target": "package:mongoose",
                "relationship": "imports",
                "evidence": {"line": 1}
            }
        ]
    }

    validated = validate_graph(raw_graph, dict_code_map)
    assert len(validated["nodes"]) == 3, f"Expected 3 valid nodes, got {len(validated['nodes'])}"
    assert len(validated["edges"]) == 2, f"Expected 2 valid edges, got {len(validated['edges'])}"
    print("  [PASS] validate_graph verified nodes and edges against dictionary-keyed code map.")

    print("\n=== ALL BACKEND DICTIONARY CODE MAP TESTS PASSED! ===")

if __name__ == "__main__":
    test_codemap_dict_support()

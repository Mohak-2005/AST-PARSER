import unittest
from app.services.source_target_inspector import (
    inspect_source_target,
    validate_target_identity,
    TargetDescriptor,
)


class TestSourceTargetInspector(unittest.TestCase):
    def test_inspect_ts_react_component(self):
        source = """
import React from 'react';

interface CursorOverlayProps {
    cursors: Record<string, { x: number; y: number }>;
    currentUserId?: string;
}

export const CursorOverlay: React.FC<CursorOverlayProps> = ({ cursors, currentUserId }) => {
    return <div className="overlay">{Object.keys(cursors).length}</div>;
};

export default CursorOverlay;
"""
        desc = inspect_source_target(source, "CursorOverlay", "CursorOverlay.tsx", "typescript")
        self.assertTrue(desc.is_symbol_found)
        self.assertIn("CursorOverlay", desc.exported_symbols)
        self.assertIn("cursors", desc.observed_props_or_params)
        self.assertIn("currentUserId", desc.observed_props_or_params)
        self.assertNotIn("users", desc.observed_props_or_params)

    def test_inspect_java_class(self):
        source = """
package com.example;

public class Card {
    private String suit;
    private String rank;

    public Card(String suit, String rank) {
        this.suit = suit;
        this.rank = rank;
    }

    public String getSuit() { return suit; }
    public String getRank() { return rank; }
}
"""
        desc = inspect_source_target(source, "Card", "Card.java", "java")
        self.assertTrue(desc.is_symbol_found)
        self.assertIn("Card", desc.exported_symbols)
        self.assertIn("suit", desc.observed_props_or_params)
        self.assertIn("rank", desc.observed_props_or_params)
        self.assertIn("getSuit", desc.observed_methods)
        self.assertIn("getRank", desc.observed_methods)

    def test_validate_target_identity_success(self):
        tc = {
            "testCaseId": "TC-001",
            "target": {"file": "src/components/CursorOverlay.tsx", "symbol": "CursorOverlay"}
        }
        retrieved_data = {
            "retrieval": {"primaryFile": "src/components/CursorOverlay.tsx"},
            "source": {"files": [{"path": "src/components/CursorOverlay.tsx", "reason": "primary_target"}]}
        }
        desc = TargetDescriptor(target_symbol="CursorOverlay", is_symbol_found=True)
        is_val, failure_type, expl = validate_target_identity(tc, retrieved_data, desc)
        self.assertTrue(is_val)
        self.assertIsNone(failure_type)

    def test_validate_target_identity_file_mismatch(self):
        tc = {
            "testCaseId": "TC-001",
            "target": {"file": "src/components/ActiveUsers.tsx", "symbol": "ActiveUsers"}
        }
        retrieved_data = {
            "retrieval": {"primaryFile": "src/components/CursorOverlay.tsx"},
            "source": {"files": [{"path": "src/components/CursorOverlay.tsx", "reason": "primary_target"}]}
        }
        desc = TargetDescriptor(target_symbol="ActiveUsers", is_symbol_found=False)
        is_val, failure_type, expl = validate_target_identity(tc, retrieved_data, desc)
        self.assertFalse(is_val)
        self.assertEqual(failure_type, "TEST_CASE_SOURCE_MISMATCH")
        self.assertIn("ActiveUsers.tsx", expl)

    def test_validate_target_identity_symbol_not_found(self):
        tc = {
            "testCaseId": "TC-001",
            "target": {"file": "Card.java", "symbol": "NonExistentSymbol"}
        }
        retrieved_data = {
            "retrieval": {"primaryFile": "Card.java"},
            "source": {"files": [{"path": "Card.java", "reason": "primary_target"}]}
        }
        desc = TargetDescriptor(target_symbol="NonExistentSymbol", is_symbol_found=False)
        is_val, failure_type, expl = validate_target_identity(tc, retrieved_data, desc)
        self.assertFalse(is_val)
        self.assertEqual(failure_type, "TARGET_SYMBOL_NOT_FOUND")

    def test_inspect_go_struct_and_methods(self):
        source = """
package math

type Calculator struct {
    Precision int
}

func (c *Calculator) Add(a, b int) int {
    return a + b
}

func (c *Calculator) Multiply(a, b int) int {
    return a * b
}
"""
        desc = inspect_source_target(source, "Calculator", "math/calculator.go", "go")
        self.assertTrue(desc.is_symbol_found)
        self.assertIn("Calculator", desc.exported_symbols)
        self.assertIn("Add", desc.observed_methods)
        self.assertIn("Multiply", desc.observed_methods)
        self.assertIn("Precision", desc.observed_props_or_params)

    def test_inspect_rust_struct_and_impl(self):
        source = """
pub struct BankAccount {
    pub balance: f64,
}

impl BankAccount {
    pub fn deposit(&mut self, amount: f64) {
        self.balance += amount;
    }

    pub fn withdraw(&mut self, amount: f64) -> bool {
        if self.balance >= amount {
            self.balance -= amount;
            true
        } else {
            false
        }
    }
}
"""
        desc = inspect_source_target(source, "BankAccount", "src/account.rs", "rust")
        self.assertTrue(desc.is_symbol_found)
        self.assertIn("BankAccount", desc.exported_symbols)
        self.assertIn("deposit", desc.observed_methods)
        self.assertIn("withdraw", desc.observed_methods)
        self.assertIn("balance", desc.observed_props_or_params)

    def test_inspect_csharp_class(self):
        source = """
namespace Shop.Services;

public class OrderService {
    public int OrderId { get; set; }

    public void ProcessOrder(int id) {
    }

    public bool CancelOrder(int id) {
        return true;
    }
}
"""
        desc = inspect_source_target(source, "OrderService", "Services/OrderService.cs", "c_sharp")
        self.assertTrue(desc.is_symbol_found)
        self.assertIn("OrderService", desc.exported_symbols)
        self.assertIn("ProcessOrder", desc.observed_methods)
        self.assertIn("CancelOrder", desc.observed_methods)
        self.assertIn("OrderId", desc.observed_props_or_params)

    def test_inspect_python_flask_decorated_routes(self):
        source = """
from flask import Flask, render_template, request, jsonify
import mysql.connector

app = Flask(__name__)

def get_db_connection():
    try:
        conn = mysql.connector.connect()
        return conn
    except Exception:
        return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/summary')
def get_summary():
    return jsonify({"total": 100})

@app.route('/api/transaction/<int:trans_id>', methods=['PUT'])
async def update_transaction(
    trans_id: int,
    amount: float,
    description: str = ""
) -> dict:
    return {"status": "ok"}

class TransactionManager:
    @classmethod
    def get_summary(cls, user_id: int) -> dict:
        return {}
"""
        # Test regular function
        desc1 = inspect_source_target(source, "get_db_connection", "app.py", "python")
        self.assertTrue(desc1.is_symbol_found)
        self.assertIn("get_db_connection", desc1.exported_symbols)

        # Test simple decorated route
        desc2 = inspect_source_target(source, "index", "app.py", "python")
        self.assertTrue(desc2.is_symbol_found)
        self.assertIn("index", desc2.exported_symbols)

        # Test second decorated route
        desc3 = inspect_source_target(source, "get_summary", "app.py", "python")
        self.assertTrue(desc3.is_symbol_found)
        self.assertIn("get_summary", desc3.exported_symbols)

        # Test async decorated route with multi-line parameters and return annotation
        desc4 = inspect_source_target(source, "update_transaction", "app.py", "python")
        self.assertTrue(desc4.is_symbol_found)
        self.assertIn("update_transaction", desc4.exported_symbols)
        self.assertIn("trans_id", desc4.observed_props_or_params)
        self.assertIn("amount", desc4.observed_props_or_params)
        self.assertIn("description", desc4.observed_props_or_params)

        # Test qualified symbol matching
        desc5 = inspect_source_target(source, "app.index", "app.py", "python")
        self.assertTrue(desc5.is_symbol_found)

        desc6 = inspect_source_target(source, "TransactionManager.get_summary", "app.py", "python")
        self.assertTrue(desc6.is_symbol_found)

        # Test genuinely missing symbol
        desc7 = inspect_source_target(source, "delete_nonexistent_table", "app.py", "python")
        self.assertFalse(desc7.is_symbol_found)

    def test_validate_target_identity_with_per_test_case_source_files(self):
        # Multi-case retrieved data structure
        retrieved_data = {
            "retrieval": {"primaryFile": "app.py", "status": "resolved"},
            "source": {
                "files": [{
                    "path": "app.py",
                    "reason": "primary_target",
                    "symbol": "get_db_connection",
                    "sourceCode": "def get_db_connection(): return None"
                }]
            }
        }
        # TC-002 has its own source file containing 'index'
        tc_002 = {
            "testCaseId": "TC-002",
            "target": {"file": "app.py", "symbol": "index"},
            "retrieval": {"primaryFile": "app.py", "status": "resolved"},
            "source": {
                "files": [{
                    "path": "app.py",
                    "reason": "primary_target",
                    "symbol": "index",
                    "sourceCode": "@app.route('/')\ndef index(): return 'ok'"
                }]
            }
        }
        desc_002 = inspect_source_target(tc_002["source"]["files"][0]["sourceCode"], "index", "app.py", "python")
        is_val, failure_type, expl = validate_target_identity(tc_002, retrieved_data, desc_002)
        self.assertTrue(is_val)
        self.assertIsNone(failure_type)

    def test_inspect_java_annotated_class_and_methods(self):
        source = """
package com.example.controller;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class UserController {
    @GetMapping("/users")
    public List<User> listUsers(@RequestParam int limit) {
        return List.of();
    }
}
"""
        desc = inspect_source_target(source, "UserController", "UserController.java", "java")
        self.assertTrue(desc.is_symbol_found)
        self.assertIn("UserController", desc.exported_symbols)
        self.assertIn("listUsers", desc.observed_methods)

        # Target is specific method
        desc_method = inspect_source_target(source, "listUsers", "UserController.java", "java")
        self.assertTrue(desc_method.is_symbol_found)

    def test_inspect_go_package_private_function(self):
        source = """
package util

func helperFunction(id int, name string) bool {
    return id > 0
}
"""
        desc = inspect_source_target(source, "helperFunction", "util.go", "go")
        self.assertTrue(desc.is_symbol_found)
        self.assertIn("helperFunction", desc.exported_symbols)
        self.assertIn("id", desc.observed_props_or_params)
        self.assertIn("name", desc.observed_props_or_params)

    def test_inspect_rust_private_function(self):
        source = """
fn private_calculate(x: i32, y: i32) -> i32 {
    x + y
}
"""
        desc = inspect_source_target(source, "private_calculate", "calc.rs", "rust")
        self.assertTrue(desc.is_symbol_found)
        self.assertIn("private_calculate", desc.exported_symbols)
        self.assertIn("x", desc.observed_props_or_params)
        self.assertIn("y", desc.observed_props_or_params)


if __name__ == "__main__":
    unittest.main()

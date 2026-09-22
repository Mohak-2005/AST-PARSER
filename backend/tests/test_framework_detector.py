import unittest
import tempfile
import shutil
import json
from pathlib import Path

from app.services.framework_detector import (
    detect_project_environment,
    ProjectEnv,
)


class TestFrameworkDetector(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_detect_java_default(self):
        code_map = {
            "project": {"name": "DemoJava", "languagesDetected": ["java"]},
            "files": []
        }
        env = detect_project_environment(self.temp_dir, code_map)
        self.assertEqual(env.language, "java")
        self.assertEqual(env.file_extension, ".java")
        self.assertEqual(env.test_framework, "junit5")
        self.assertIn("org.junit.jupiter", env.test_framework_import)

    def test_detect_java_maven_junit4(self):
        pom = self.temp_dir / "pom.xml"
        pom.write_text("<project><dependencies><dependency><groupId>junit</groupId><artifactId>junit</artifactId><version>4.13.2</version></dependency></dependencies></project>", encoding="utf-8")
        code_map = {
            "project": {"name": "MavenJUnit4", "languagesDetected": ["java"]}
        }
        env = detect_project_environment(self.temp_dir, code_map)
        self.assertEqual(env.language, "java")
        self.assertEqual(env.build_tool, "maven")
        self.assertEqual(env.test_framework, "junit4")
        self.assertIn("org.junit.Test", env.test_framework_import)

    def test_detect_python_pytest(self):
        reqs = self.temp_dir / "requirements.txt"
        reqs.write_text("fastapi\npytest>=7.0.0\nuvicorn\n", encoding="utf-8")
        code_map = {
            "project": {"name": "PyTestProject", "languagesDetected": ["python"]}
        }
        env = detect_project_environment(self.temp_dir, code_map)
        self.assertEqual(env.language, "python")
        self.assertEqual(env.file_extension, ".py")
        self.assertEqual(env.test_framework, "pytest")
        self.assertEqual(env.build_tool, "pip")

    def test_detect_typescript_vitest(self):
        pkg = self.temp_dir / "package.json"
        pkg.write_text(json.dumps({
            "name": "ts-project",
            "devDependencies": {
                "typescript": "^5.0.0",
                "vitest": "^1.0.0"
            }
        }), encoding="utf-8")
        code_map = {
            "project": {"name": "TSVitest", "languagesDetected": ["typescript"]}
        }
        env = detect_project_environment(self.temp_dir, code_map)
        self.assertEqual(env.language, "typescript")
        self.assertEqual(env.file_extension, ".ts")
        self.assertEqual(env.test_framework, "vitest")
        self.assertIn("vitest", env.test_framework_import)

    def test_detect_javascript_jest(self):
        pkg = self.temp_dir / "package.json"
        pkg.write_text(json.dumps({
            "name": "js-project",
            "devDependencies": {
                "jest": "^29.0.0"
            }
        }), encoding="utf-8")
        code_map = {
            "project": {"name": "JSJest", "languagesDetected": ["javascript"]}
        }
        env = detect_project_environment(self.temp_dir, code_map)
        self.assertEqual(env.language, "javascript")
        self.assertEqual(env.file_extension, ".js")
        self.assertEqual(env.test_framework, "jest")

    def test_detect_go(self):
        go_mod = self.temp_dir / "go.mod"
        go_mod.write_text("module example.com/test\ngo 1.20\n", encoding="utf-8")
        code_map = {
            "project": {"name": "GoModule", "languagesDetected": ["go"]}
        }
        env = detect_project_environment(self.temp_dir, code_map)
        self.assertEqual(env.language, "go")
        self.assertEqual(env.file_extension, ".go")
        self.assertEqual(env.test_framework, "testing")


    def test_detect_react_typescript_vitest(self):
        pkg = self.temp_dir / "package.json"
        pkg.write_text(json.dumps({
            "name": "react-ts-app",
            "dependencies": {
                "react": "^18.2.0",
                "react-dom": "^18.2.0"
            },
            "devDependencies": {
                "typescript": "^5.0.0",
                "vitest": "^1.0.0",
                "@testing-library/react": "^14.0.0"
            }
        }), encoding="utf-8")
        code_map = {
            "project": {"name": "ReactTSVitest", "languagesDetected": ["typescript"]}
        }
        env = detect_project_environment(self.temp_dir, code_map)
        self.assertEqual(env.language, "typescript")
        self.assertEqual(env.file_extension, ".tsx")
        self.assertTrue(env.has_jsx)
        self.assertTrue(env.has_typescript)
        self.assertEqual(env.test_framework, "vitest")
        self.assertIn("vitest", env.test_framework_import)

    def test_detect_react_javascript_jest(self):
        pkg = self.temp_dir / "package.json"
        pkg.write_text(json.dumps({
            "name": "react-js-app",
            "dependencies": {
                "react": "^18.2.0"
            },
            "devDependencies": {
                "jest": "^29.0.0"
            }
        }), encoding="utf-8")
        code_map = {
            "project": {"name": "ReactJSJest", "languagesDetected": ["javascript"]}
        }
        env = detect_project_environment(self.temp_dir, code_map)
        self.assertEqual(env.language, "javascript")
        self.assertEqual(env.file_extension, ".jsx")
        self.assertTrue(env.has_jsx)
        self.assertFalse(env.has_typescript)
        self.assertEqual(env.test_framework, "jest")


if __name__ == "__main__":
    unittest.main()

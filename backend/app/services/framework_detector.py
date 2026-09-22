from __future__ import annotations

import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("test_script_generator.detector")


@dataclass
class ProjectEnv:
    language: str = "unknown"
    language_version: Optional[str] = None
    file_extension: str = ".txt"
    build_tool: Optional[str] = None
    test_framework: str = "unknown"
    test_framework_import: str = ""
    test_directory: str = ""
    test_file_naming_pattern: str = "{name}Test{ext}"
    assertion_style: str = "standard"
    source_roots: List[str] = field(default_factory=list)
    detected_dependencies: List[str] = field(default_factory=list)
    confidence: float = 0.5
    project_root: Optional[str] = None
    has_jsx: bool = False
    has_typescript: bool = False
    is_esm: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "language": self.language,
            "language_version": self.language_version,
            "file_extension": self.file_extension,
            "build_tool": self.build_tool,
            "test_framework": self.test_framework,
            "test_framework_import": self.test_framework_import,
            "test_directory": self.test_directory,
            "test_file_naming_pattern": self.test_file_naming_pattern,
            "assertion_style": self.assertion_style,
            "source_roots": self.source_roots,
            "detected_dependencies": self.detected_dependencies,
            "confidence": self.confidence,
            "project_root": self.project_root,
            "has_jsx": self.has_jsx,
            "has_typescript": self.has_typescript,
            "is_esm": self.is_esm,
        }


# Mapping of file extensions to canonical language identifiers
EXT_TO_LANG: Dict[str, str] = {
    ".java": "java",
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".cs": "c_sharp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".scala": "scala",
    ".swift": "swift",
}

LANG_TO_EXT: Dict[str, str] = {
    "java": ".java",
    "python": ".py",
    "javascript": ".js",
    "typescript": ".ts",
    "go": ".go",
    "rust": ".rs",
    "c_sharp": ".cs",
    "cpp": ".cpp",
    "c": ".c",
    "ruby": ".rb",
    "php": ".php",
    "kotlin": ".kt",
    "scala": ".scala",
    "swift": ".swift",
}


def _detect_java_environment(root: Optional[Path], code_map: Optional[dict]) -> ProjectEnv:
    env = ProjectEnv(
        language="java",
        file_extension=".java",
        test_framework="junit5",
        test_framework_import="import org.junit.jupiter.api.Test;\nimport static org.junit.jupiter.api.Assertions.*;",
        test_directory="src/test/java",
        test_file_naming_pattern="{name}Test.java",
        assertion_style="junit_jupiter",
        confidence=0.8,
    )

    if not root or not root.exists():
        return env

    # Check Maven (pom.xml)
    pom_file = root / "pom.xml"
    if pom_file.exists():
        env.build_tool = "maven"
        env.confidence = 0.95
        try:
            tree = ET.parse(pom_file)
            xml_root = tree.getroot()
            # Namespace handling for Maven
            ns = {"m": xml_root.tag.split("}")[0].strip("{")} if "}" in xml_root.tag else {}
            prefix = "m:" if ns else ""

            pom_text = pom_file.read_text(encoding="utf-8", errors="ignore")
            if "org.junit.jupiter" in pom_text or "junit-jupiter" in pom_text:
                env.test_framework = "junit5"
                env.test_framework_import = "import org.junit.jupiter.api.Test;\nimport static org.junit.jupiter.api.Assertions.*;"
                env.assertion_style = "junit_jupiter"
            elif "junit:junit" in pom_text or "<artifactId>junit</artifactId>" in pom_text or ("<groupId>junit</groupId>" in pom_text and "4." in pom_text):
                env.test_framework = "junit4"
                env.test_framework_import = "import org.junit.Test;\nimport static org.junit.Assert.*;"
                env.assertion_style = "junit4"
            elif "org.testng" in pom_text or "<artifactId>testng</artifactId>" in pom_text:
                env.test_framework = "testng"
                env.test_framework_import = "import org.testng.annotations.Test;\nimport static org.testng.Assert.*;"
                env.assertion_style = "testng"
        except Exception as e:
            logger.debug("Failed to parse pom.xml: %s", e)

    # Check Gradle (build.gradle, build.gradle.kts)
    gradle_file = root / "build.gradle"
    gradle_kts = root / "build.gradle.kts"
    if gradle_file.exists() or gradle_kts.exists():
        env.build_tool = "gradle"
        target_gradle = gradle_file if gradle_file.exists() else gradle_kts
        gradle_text = target_gradle.read_text(encoding="utf-8", errors="ignore")
        if "junit-jupiter" in gradle_text or "useJUnitPlatform()" in gradle_text:
            env.test_framework = "junit5"
            env.test_framework_import = "import org.junit.jupiter.api.Test;\nimport static org.junit.jupiter.api.Assertions.*;"
            env.assertion_style = "junit_jupiter"
        elif "junit:junit" in gradle_text:
            env.test_framework = "junit4"
            env.test_framework_import = "import org.junit.Test;\nimport static org.junit.Assert.*;"
            env.assertion_style = "junit4"
        elif "testng" in gradle_text:
            env.test_framework = "testng"
            env.test_framework_import = "import org.testng.annotations.Test;\nimport static org.testng.Assert.*;"
            env.assertion_style = "testng"

    # Check existing test directory
    for candidate in ["src/test/java", "test", "tests"]:
        if (root / candidate).exists():
            env.test_directory = candidate
            break

    return env


def _detect_python_environment(root: Optional[Path], code_map: Optional[dict]) -> ProjectEnv:
    env = ProjectEnv(
        language="python",
        file_extension=".py",
        test_framework="pytest",
        test_framework_import="import pytest",
        test_directory="tests",
        test_file_naming_pattern="test_{name}.py",
        assertion_style="pytest",
        confidence=0.8,
    )

    if not root or not root.exists():
        return env

    # Check pyproject.toml, requirements.txt, setup.py
    pyproject = root / "pyproject.toml"
    reqs = root / "requirements.txt"
    setup_py = root / "setup.py"

    all_configs_text = ""
    if pyproject.exists():
        env.build_tool = "poetry" if "tool.poetry" in pyproject.read_text(encoding="utf-8", errors="ignore") else "pyproject"
        all_configs_text += "\n" + pyproject.read_text(encoding="utf-8", errors="ignore")
    if reqs.exists():
        if not env.build_tool:
            env.build_tool = "pip"
        all_configs_text += "\n" + reqs.read_text(encoding="utf-8", errors="ignore")
    if setup_py.exists():
        if not env.build_tool:
            env.build_tool = "setuptools"
        all_configs_text += "\n" + setup_py.read_text(encoding="utf-8", errors="ignore")

    if "pytest" in all_configs_text.lower():
        env.test_framework = "pytest"
        env.test_framework_import = "import pytest"
        env.assertion_style = "pytest"
    elif "unittest" in all_configs_text.lower():
        env.test_framework = "unittest"
        env.test_framework_import = "import unittest"
        env.assertion_style = "unittest"

    for candidate in ["tests", "test"]:
        if (root / candidate).exists():
            env.test_directory = candidate
            break

    return env


def _detect_js_ts_environment(root: Optional[Path], code_map: Optional[dict], is_ts: bool) -> ProjectEnv:
    has_ts = is_ts
    has_jsx = False
    is_esm = False

    # Check code_map for JSX or TS files
    if code_map:
        files = code_map.get("files")
        file_list = []
        if isinstance(files, list):
            file_list = files
        elif isinstance(files, dict):
            file_list = list(files.values())

        for fe in file_list:
            fp = ""
            if isinstance(fe, dict):
                fp = (fe.get("file", {}).get("path") or fe.get("path") or "").lower()
            elif isinstance(fe, str):
                fp = fe.lower()
            if fp.endswith(".tsx"):
                has_jsx = True
                has_ts = True
            elif fp.endswith(".jsx"):
                has_jsx = True
            elif fp.endswith(".ts"):
                has_ts = True

    if root and root.exists():
        if (root / "tsconfig.json").exists():
            has_ts = True

    lang = "typescript" if has_ts else "javascript"
    ext = (".tsx" if has_ts else ".jsx") if has_jsx else (".ts" if has_ts else ".js")

    env = ProjectEnv(
        language=lang,
        file_extension=ext,
        test_framework="jest",
        test_framework_import="import { describe, it, expect, beforeEach } from '@jest/globals';",
        test_directory="__tests__",
        test_file_naming_pattern="{name}.test" + ext,
        assertion_style="jest",
        confidence=0.8,
        has_jsx=has_jsx,
        has_typescript=has_ts,
        is_esm=is_esm,
    )

    if not root or not root.exists():
        return env

    pkg_file = root / "package.json"
    if pkg_file.exists():
        env.build_tool = "npm"
        if (root / "yarn.lock").exists():
            env.build_tool = "yarn"
        elif (root / "pnpm-lock.yaml").exists():
            env.build_tool = "pnpm"

        try:
            pkg = json.loads(pkg_file.read_text(encoding="utf-8", errors="ignore"))
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            env.detected_dependencies = list(deps.keys())

            if pkg.get("type") == "module":
                is_esm = True

            if "typescript" in deps or "@types/jest" in deps or "ts-jest" in deps:
                has_ts = True
            if "react" in deps or "react-dom" in deps or "@testing-library/react" in deps:
                has_jsx = True

            test_script = ""
            if isinstance(pkg.get("scripts"), dict):
                test_script = str(pkg["scripts"].get("test", "")).lower()

            has_vitest_config = (root / "vitest.config.ts").exists() or (root / "vitest.config.js").exists()
            has_vite_config = (root / "vite.config.ts").exists() or (root / "vite.config.js").exists()
            has_jest_config = (root / "jest.config.js").exists() or (root / "jest.config.ts").exists() or (root / "jest.config.json").exists()

            if "vitest" in deps or "vitest" in test_script or has_vitest_config or (has_vite_config and "vitest" in test_script):
                env.test_framework = "vitest"
                env.test_framework_import = "import { describe, it, expect, beforeEach } from 'vitest';"
                env.assertion_style = "vitest"
            elif "jest" in deps or "@types/jest" in deps or "jest" in test_script or has_jest_config or "ts-jest" in deps or "react-scripts" in deps:
                env.test_framework = "jest"
                env.test_framework_import = (
                    "import { describe, it, expect, beforeEach } from '@jest/globals';"
                    if (has_ts or is_esm)
                    else "const { describe, it, expect, beforeEach } = require('@jest/globals');"
                )
                env.assertion_style = "jest"
            elif "mocha" in deps or "mocha" in test_script:
                env.test_framework = "mocha"
                env.test_framework_import = "import { expect } from 'chai';" if (has_ts or is_esm) else "const { expect } = require('chai');"
                env.assertion_style = "chai"
        except Exception as e:
            logger.debug("Failed to read package.json: %s", e)

    # Recompute language & ext after checking package.json
    lang = "typescript" if has_ts else "javascript"
    ext = (".tsx" if has_ts else ".jsx") if has_jsx else (".ts" if has_ts else ".js")
    env.language = lang
    env.file_extension = ext
    env.has_jsx = has_jsx
    env.has_typescript = has_ts
    env.is_esm = is_esm
    env.test_file_naming_pattern = "{name}.test" + ext

    for candidate in ["tests", "test", "__tests__", "src/__tests__"]:
        if (root / candidate).exists():
            env.test_directory = candidate
            break

    return env


def _detect_go_environment(root: Optional[Path], code_map: Optional[dict]) -> ProjectEnv:
    env = ProjectEnv(
        language="go",
        file_extension=".go",
        test_framework="testing",
        test_framework_import='import (\n\t"testing"\n)',
        test_directory=".",
        test_file_naming_pattern="{name}_test.go",
        assertion_style="go_testing",
        build_tool="go_modules" if (root and (root / "go.mod").exists()) else "go",
        confidence=0.9,
    )
    return env


def _detect_rust_environment(root: Optional[Path], code_map: Optional[dict]) -> ProjectEnv:
    env = ProjectEnv(
        language="rust",
        file_extension=".rs",
        test_framework="cargo_test",
        test_framework_import="#[cfg(test)]\nmod tests {\n    use super::*;\n",
        test_directory="tests" if (root and (root / "tests").exists()) else "src",
        test_file_naming_pattern="{name}_test.rs",
        assertion_style="assert",
        build_tool="cargo",
        confidence=0.9,
    )
    return env


def detect_project_environment(
    project_root: Optional[Union[str, Path]],
    code_map: Optional[dict] = None,
) -> ProjectEnv:
    """
    Dynamically analyzes the project root and repository-code-map.json to detect:
    - Primary programming language
    - Language file extension
    - Build tool (Maven, Gradle, npm, pip, Cargo, etc.)
    - Test framework (JUnit 5, JUnit 4, Jest, Vitest, Pytest, Go testing, etc.)
    - Import headers and standard assertion style
    - Test directory and naming conventions
    """
    root_path = Path(project_root) if project_root else None
    if not root_path and code_map:
        cand = code_map.get("project", {}).get("path")
        if cand:
            root_path = Path(cand)

    # Step 1: Detect language from code_map if available
    detected_lang: Optional[str] = None
    if code_map:
        langs = code_map.get("project", {}).get("languagesDetected") or code_map.get("stats", {}).get("languagesDetected")
        if langs and isinstance(langs, list) and len(langs) > 0:
            detected_lang = langs[0].lower()

        if not detected_lang:
            # Check files array or object
            files = code_map.get("files")
            if isinstance(files, list) and files:
                first = files[0]
                detected_lang = (
                    first.get("file", {}).get("language")
                    or first.get("language")
                )
            elif isinstance(files, dict) and files:
                first = next(iter(files.values()))
                detected_lang = (
                    first.get("file", {}).get("language")
                    or first.get("language")
                )

    # Step 2: If language not found from code_map, scan files on disk
    if not detected_lang and root_path and root_path.exists():
        # Count extensions
        ext_counts: Dict[str, int] = {}
        for root_dir, _, filenames in os.walk(root_path):
            # Skip hidden / node_modules
            if any(part.startswith(".") or part in ["node_modules", "target", "build", "dist", "venv", "__pycache__"] for part in Path(root_dir).parts):
                continue
            for f in filenames:
                ext = Path(f).suffix.lower()
                if ext in EXT_TO_LANG:
                    ext_counts[ext] = ext_counts.get(ext, 0) + 1

        if ext_counts:
            best_ext = max(ext_counts, key=ext_counts.get)
            detected_lang = EXT_TO_LANG[best_ext]

    detected_lang = (detected_lang or "java").lower()

    # Step 3: Route to language-specific detector
    if detected_lang == "java":
        env = _detect_java_environment(root_path, code_map)
    elif detected_lang == "python":
        env = _detect_python_environment(root_path, code_map)
    elif detected_lang in ["typescript", "ts"]:
        env = _detect_js_ts_environment(root_path, code_map, is_ts=True)
    elif detected_lang in ["javascript", "js"]:
        env = _detect_js_ts_environment(root_path, code_map, is_ts=False)
    elif detected_lang == "go":
        env = _detect_go_environment(root_path, code_map)
    elif detected_lang == "rust":
        env = _detect_rust_environment(root_path, code_map)
    else:
        # Generic fallback
        ext = LANG_TO_EXT.get(detected_lang, f".{detected_lang}")
        env = ProjectEnv(
            language=detected_lang,
            file_extension=ext,
            test_framework="standard",
            test_framework_import="",
            test_directory="tests",
            test_file_naming_pattern="{name}Test" + ext,
            assertion_style="assert",
            confidence=0.5,
        )

    if root_path:
        env.project_root = str(root_path)

    return env

"""
Pydantic schemas for the AST Dependency Graph Analyzer backend.

The CodeMapPayload schema is intentionally flexible (extra='allow', list[Any])
because the agent produces a variety of structures for imports/exports/symbols etc.
We validate what we can and pass the rest to the LLM as-is.
"""

from pydantic import BaseModel
from typing import Any, Optional, Union


# ---------------------------------------------------------------------------
# Code Map models — received from the Local Agent
# ---------------------------------------------------------------------------

class FileInfo(BaseModel):
    path: str
    relativePath: str
    name: str
    extension: str
    language: Optional[str] = None   # None when parse failed
    lineCount: int = 0

    model_config = {'extra': 'allow'}


class ParseInfo(BaseModel):
    status: str   # 'success' | 'error'
    errors: list[str] = []


class FileArtifact(BaseModel):
    """
    Represents one parsed source file.
    Sub-fields (imports, symbols, calls, etc.) are treated as raw Any
    because the agent may use different field shapes over time.
    The LLM receives the full dict, so strict sub-typing here is not required.
    """
    file: FileInfo
    parse: ParseInfo
    symbols: list[Any] = []
    imports: list[Any] = []
    exports: list[Any] = []
    calls: list[Any] = []
    references: list[Any] = []
    classes: list[Any] = []
    react: Any = {}
    routes: list[Any] = []
    databaseOperations: list[Any] = []

    model_config = {'extra': 'allow'}


class ProjectInfo(BaseModel):
    name: str
    path: str
    analyzedAt: str
    totalFiles: int
    parsedFiles: int
    failedFiles: int
    codeMapPath: Optional[str] = None


class CodeMapPayload(BaseModel):
    project: ProjectInfo
    files: Union[dict[str, Any], list[Any]]
    relationships: list[dict] = []
    analysis_id: Optional[str] = None
    codeMapPath: Optional[str] = None

    model_config = {'extra': 'allow'}


# ---------------------------------------------------------------------------
# Graph models — produced by LLM + validator, stored in PostgreSQL
# ---------------------------------------------------------------------------

class GraphNodeLocation(BaseModel):
    startLine: Optional[int] = None
    startColumn: Optional[int] = None
    endLine: Optional[int] = None
    endColumn: Optional[int] = None


class GraphNode(BaseModel):
    id: str
    type: str
    name: str
    filePath: Optional[str] = None
    location: Optional[GraphNodeLocation] = None
    metadata: dict = {}

    model_config = {'extra': 'allow'}


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    relationship: str
    confidence: float = 1.0
    evidence: dict = {}
    reason: str = ""

    model_config = {'extra': 'allow'}


class GraphData(BaseModel):
    projectId: str
    projectName: str
    projectPath: Optional[str] = None
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    codeMap: Optional[dict] = None


class AnalyzeResponse(BaseModel):
    projectId: str
    graph: GraphData
    message: str = "Analysis complete"


class FeatureContextRequest(BaseModel):
    codeMapPath: Optional[str] = None
    projectId: Optional[str] = None
    force: Optional[bool] = False


class FeatureContextResponse(BaseModel):
    status: str
    featureCount: int
    savedFiles: list[str] = []
    message: str = "Feature contexts generated successfully"
    alreadyExists: bool = False


class UnitTestCaseRequest(BaseModel):
    codeMapPath: Optional[str] = None
    projectId: Optional[str] = None
    force: Optional[bool] = False
    testType: Optional[str] = "unit"


class UnitTestCaseResponse(BaseModel):
    status: str
    testCaseCount: int = 0
    unitCount: int = 0
    savedFiles: list[str] = []
    message: str = "Unit test cases generated successfully"
    alreadyExists: bool = False
    testType: Optional[str] = "unit"
    breakdown: Optional[dict] = {}


class RetrievedCodeRequest(BaseModel):
    codeMapPath: Optional[str] = None
    projectId: Optional[str] = None
    projectPath: Optional[str] = None
    testType: Optional[str] = None


class RetrievedCodeResponse(BaseModel):
    status: str
    retrievedCount: int = 0
    savedFiles: list[str] = []
    files: list[dict] = []
    message: str = ""
    alreadyExists: bool = False
    testType: Optional[str] = None


class TestScriptRequest(BaseModel):
    codeMapPath: Optional[str] = None
    projectId: Optional[str] = None
    force: Optional[bool] = False
    testType: Optional[str] = None


class TestScriptResponse(BaseModel):
    status: str
    generatedCount: int = 0
    successfulCount: int = 0
    failedCount: int = 0
    scriptsDir: Optional[str] = None
    projectEnvironment: Optional[dict] = None
    scripts: list[dict] = []
    message: str = ""
    alreadyExists: bool = False
    testType: Optional[str] = None


# ---------------------------------------------------------------------------
# Test Execution models
# ---------------------------------------------------------------------------

class TestExecutionRequest(BaseModel):
    codeMapPath: Optional[str] = None
    projectId: Optional[str] = None
    testType: Optional[str] = None
    scriptFiles: Optional[list[str]] = None
    timeoutSeconds: Optional[float] = None


class TestExecutionResult(BaseModel):
    testCaseId: str
    scriptFile: str
    testType: str = "unit"
    status: str   # 'passed' | 'failed' | 'error' | 'skipped'
    exitCode: int = 0
    stdout: str = ""
    stderr: str = ""
    durationMs: float = 0.0
    runner: str = ""
    failureType: Optional[str] = None
    command: Optional[str] = None
    workingDirectory: Optional[str] = None
    projectRoot: Optional[str] = None
    startupCommand: Optional[str] = None
    stage: Optional[str] = None
    evidence: Optional[str] = None


class TestExecutionResponse(BaseModel):
    status: str
    totalTests: int = 0
    passed: int = 0
    failed: int = 0
    durationMs: float = 0.0
    runner: str = ""
    results: list[TestExecutionResult] = []
    message: str = ""
    summaryPath: Optional[str] = None



import os

base_dir = r"c:\Users\Mohak\Desktop\AST-Parser\backend"

files = {
    ".env": """OPENROUTER_API_KEY=your_openrouter_api_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct
DATABASE_URL=postgresql://username:password@localhost:5432/dependency_graph
PORT=5000""",
    ".env.example": """OPENROUTER_API_KEY=your_openrouter_api_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct
DATABASE_URL=postgresql://username:password@localhost:5432/dependency_graph
PORT=5000""",
    "requirements.txt": """fastapi==0.111.0
uvicorn[standard]==0.29.0
httpx==0.27.0
asyncpg==0.29.0
python-dotenv==1.0.1
pydantic==2.7.1
pydantic-settings==2.3.1""",
    "app/__init__.py": "",
    "app/routes/__init__.py": "",
    "app/services/__init__.py": "",
    "app/models/__init__.py": "",
    "app/db/__init__.py": "",
    "app/config.py": """from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    openrouter_api_key: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct"
    database_url: str
    port: int = 5000

    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()""",
    "app/models/schemas.py": """from pydantic import BaseModel
from typing import Any, Optional
import uuid

# Code Map models (received from agent)
class FileInfo(BaseModel):
    path: str
    relativePath: str
    name: str
    extension: str
    language: str
    lineCount: int

class ParseInfo(BaseModel):
    status: str  # 'success' | 'error'
    errors: list[str] = []

class Location(BaseModel):
    startLine: int
    startColumn: int
    endLine: int
    endColumn: int

class ImportInfo(BaseModel):
    modulePath: str
    importType: str  # 'default' | 'named' | 'namespace' | 'sideEffect'
    importedNames: list[str] = []
    location: Optional[Location] = None
    external: bool = False

class ExportInfo(BaseModel):
    exportType: str  # 'default' | 'named' | 're-export'
    name: Optional[str] = None
    location: Optional[Location] = None

class SymbolInfo(BaseModel):
    name: str
    type: str  # 'function' | 'class' | 'variable' | 'component' | 'method' | 'interface'
    location: Optional[Location] = None

class CallInfo(BaseModel):
    callee: str
    containingSymbol: Optional[str] = None
    location: Optional[Location] = None

class ClassInfo(BaseModel):
    name: str
    extends: Optional[str] = None
    implements: list[str] = []
    methods: list[str] = []
    hasConstructor: bool = False
    location: Optional[Location] = None

class ReactInfo(BaseModel):
    components: list[dict] = []
    jsxElements: list[dict] = []

class RouteInfo(BaseModel):
    method: Optional[str] = None
    path: Optional[str] = None
    handler: Optional[str] = None
    location: Optional[Location] = None

class DbOperation(BaseModel):
    operation: str
    callee: str
    containingSymbol: Optional[str] = None
    location: Optional[Location] = None

class FileArtifact(BaseModel):
    file: FileInfo
    parse: ParseInfo
    symbols: list[SymbolInfo] = []
    imports: list[ImportInfo] = []
    exports: list[ExportInfo] = []
    calls: list[CallInfo] = []
    references: list[Any] = []
    classes: list[ClassInfo] = []
    react: ReactInfo = ReactInfo()
    routes: list[RouteInfo] = []
    databaseOperations: list[DbOperation] = []

class Relationship(BaseModel):
    from_: str  # use alias
    to: str
    type: str
    evidence: dict = {}
    external: bool = False

    model_config = {'populate_by_name': True}

class ProjectInfo(BaseModel):
    name: str
    path: str
    analyzedAt: str
    totalFiles: int
    parsedFiles: int
    failedFiles: int

class CodeMapPayload(BaseModel):
    project: ProjectInfo
    files: list[FileArtifact]
    relationships: list[dict] = []

# Graph models
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

class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    relationship: str
    confidence: float = 1.0
    evidence: dict = {}
    reason: str = ""

class GraphData(BaseModel):
    projectId: str
    projectName: str
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

class AnalyzeResponse(BaseModel):
    projectId: str
    graph: GraphData
    message: str = "Analysis complete"
""",
    "app/db/database.py": """import asyncpg
import logging
from app.config import settings

logger = logging.getLogger(__name__)
pool = None

async def create_pool():
    global pool
    pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
    logger.info("Database pool created")
    return pool

async def close_pool():
    global pool
    if pool:
        await pool.close()

def get_pool():
    return pool

CREATE_TABLES_SQL = \"\"\"
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_name TEXT NOT NULL,
    project_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'analyzing',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS graph_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    name TEXT NOT NULL,
    file_path TEXT,
    metadata JSONB DEFAULT '{}',
    UNIQUE(project_id, node_id)
);

CREATE TABLE IF NOT EXISTS graph_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    relationship TEXT NOT NULL,
    confidence FLOAT DEFAULT 1.0,
    evidence JSONB DEFAULT '{}',
    metadata JSONB DEFAULT '{}',
    UNIQUE(project_id, source_node_id, target_node_id, relationship)
);
\"\"\"

async def create_tables():
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLES_SQL)
    logger.info("Tables created/verified")
""",
    "app/services/llm_service.py": """import httpx
import json
import logging
from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = \"\"\"You are a code analysis assistant. You receive a structured Code Map of a software repository and generate a dependency graph.

You MUST:
- Only create nodes for entities that actually exist in the Code Map
- Only create edges for relationships that have evidence in the Code Map
- Never invent files, classes, functions, imports, or dependencies
- Return ONLY valid JSON matching the exact schema provided
- Set confidence 1.0 for direct imports, 0.8 for inferred calls, 0.6 for indirect references

Valid node types: project, directory, file, module, class, function, method, component, route, database_operation
Valid edge relationship types: imports, exports, calls, references, extends, implements, renders, routes_to, handles, uses, contains

For every edge, provide evidence with at least: file path and line number where available.\"\"\"

def build_user_prompt(code_map: dict) -> str:
    # Summarize the code map to reduce token usage while keeping all relationships
    summary = {
        "project": code_map.get("project", {}),
        "files": []
    }
    for f in code_map.get("files", []):
        file_summary = {
            "relativePath": f.get("file", {}).get("relativePath", ""),
            "language": f.get("file", {}).get("language", ""),
            "parseStatus": f.get("parse", {}).get("status", ""),
            "imports": f.get("imports", []),
            "exports": f.get("exports", []),
            "symbols": f.get("symbols", []),
            "classes": f.get("classes", []),
            "calls": f.get("calls", [])[:20],  # limit calls
            "routes": f.get("routes", []),
            "react": f.get("react", {}),
            "databaseOperations": f.get("databaseOperations", [])
        }
        summary["files"].append(file_summary)
    summary["relationships"] = code_map.get("relationships", [])
    
    schema = {
        "nodes": [
            {"id": "unique_string", "type": "file|class|function|component|route", "name": "string", "filePath": "optional string", "location": {"startLine": 0, "endLine": 0}, "metadata": {}}
        ],
        "edges": [
            {"id": "unique_string", "source": "node_id", "target": "node_id", "relationship": "imports|calls|extends|etc", "confidence": 1.0, "evidence": {"file": "path", "line": 0}, "reason": "explanation"}
        ]
    }
    
    return f\"\"\"Analyze this repository Code Map and generate a dependency graph.

Code Map:
{json.dumps(summary, indent=2)}

Generate a JSON object with this exact structure:
{json.dumps(schema, indent=2)}

Rules:
- Every node 'id' must be unique and descriptive (e.g., 'file:src/App.js', 'class:UserService', 'function:getUserById')
- Every edge 'source' and 'target' must reference existing node 'id' values
- Only generate nodes for entities found in the Code Map above
- Only generate edges with actual evidence from the Code Map
- Return ONLY the JSON object, no markdown, no explanation\"\"\"

async def generate_dependency_graph(code_map: dict) -> dict:
    logger.info(f"Calling LLM: {settings.openrouter_model}")
    
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:5000",
        "X-Title": "AST Dependency Graph Analyzer"
    }
    
    payload = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(code_map)}
        ],
        "temperature": 0.1,
        "max_tokens": 8000
    }
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{settings.openrouter_base_url}/chat/completions",
            headers=headers,
            json=payload
        )
        response.raise_for_status()
        
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    logger.info("LLM response received")
    
    # Strip markdown code blocks if present
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\\n")
        content = "\\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
    
    return json.loads(content)
""",
    "app/services/graph_validator.py": """import logging
from typing import Any

logger = logging.getLogger(__name__)

VALID_NODE_TYPES = {"project", "directory", "file", "module", "class", "function", "method", "component", "route", "database_operation"}
VALID_RELATIONSHIP_TYPES = {"imports", "exports", "calls", "references", "extends", "implements", "renders", "routes_to", "handles", "uses", "contains"}

def extract_code_map_entities(code_map: dict) -> set:
    \"\"\"Extract all known entity names/paths from the code map for validation.\"\"\"
    entities = set()
    for f in code_map.get("files", []):
        file_info = f.get("file", {})
        entities.add(file_info.get("relativePath", ""))
        entities.add(file_info.get("name", ""))
        for sym in f.get("symbols", []):
            entities.add(sym.get("name", ""))
        for cls in f.get("classes", []):
            entities.add(cls.get("name", ""))
        for imp in f.get("imports", []):
            entities.add(imp.get("modulePath", ""))
    return {e for e in entities if e}

def validate_graph(raw_graph: dict, code_map: dict) -> dict:
    \"\"\"Validate and clean the LLM-generated graph against the Code Map.\"\"\"
    nodes = raw_graph.get("nodes", [])
    edges = raw_graph.get("edges", [])
    
    # 1. Validate + deduplicate nodes
    seen_node_ids = set()
    valid_nodes = []
    for node in nodes:
        node_id = node.get("id", "").strip()
        node_type = node.get("type", "").strip()
        node_name = node.get("name", "").strip()
        
        if not node_id:
            logger.warning(f"Skipping node with no ID: {node}")
            continue
        if node_id in seen_node_ids:
            logger.warning(f"Duplicate node ID skipped: {node_id}")
            continue
        if node_type not in VALID_NODE_TYPES:
            logger.warning(f"Invalid node type '{node_type}' for node {node_id}, defaulting to 'file'")
            node_type = "file"
        if not node_name:
            logger.warning(f"Node {node_id} has no name, skipping")
            continue
        
        seen_node_ids.add(node_id)
        valid_nodes.append({
            "id": node_id,
            "type": node_type,
            "name": node_name,
            "filePath": node.get("filePath"),
            "location": node.get("location"),
            "metadata": node.get("metadata", {})
        })
    
    logger.info(f"Valid nodes: {len(valid_nodes)} / {len(nodes)}")
    
    # 2. Validate + deduplicate edges
    seen_edges = set()
    valid_edges = []
    for edge in edges:
        source = edge.get("source", "").strip()
        target = edge.get("target", "").strip()
        relationship = edge.get("relationship", "").strip()
        evidence = edge.get("evidence", {})
        edge_id = edge.get("id", f"{source}->{target}:{relationship}")
        
        if not source or not target or not relationship:
            logger.warning(f"Edge missing required fields: {edge}")
            continue
        if source not in seen_node_ids:
            logger.warning(f"Edge source '{source}' not in valid nodes, skipping")
            continue
        if target not in seen_node_ids:
            logger.warning(f"Edge target '{target}' not in valid nodes, skipping")
            continue
        if relationship not in VALID_RELATIONSHIP_TYPES:
            logger.warning(f"Invalid relationship type '{relationship}', skipping edge {edge_id}")
            continue
        if not evidence:
            logger.warning(f"Edge {edge_id} has no evidence, skipping")
            continue
        
        dedup_key = f"{source}->{target}:{relationship}"
        if dedup_key in seen_edges:
            logger.warning(f"Duplicate edge skipped: {dedup_key}")
            continue
        
        seen_edges.add(dedup_key)
        valid_edges.append({
            "id": edge_id,
            "source": source,
            "target": target,
            "relationship": relationship,
            "confidence": float(edge.get("confidence", 1.0)),
            "evidence": evidence,
            "reason": edge.get("reason", "")
        })
    
    logger.info(f"Valid edges: {len(valid_edges)} / {len(edges)}")
    return {"nodes": valid_nodes, "edges": valid_edges}
""",
    "app/services/db_repository.py": """import json
import logging
from app.db.database import get_pool

logger = logging.getLogger(__name__)

async def save_project(project_name: str, project_path: str) -> str:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO projects (project_name, project_path, status) VALUES ($1, $2, $3) RETURNING id",
            project_name, project_path, "complete"
        )
    return str(row["id"])

async def save_graph(project_id: str, nodes: list, edges: list):
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Delete existing nodes/edges for this project (re-analysis)
            await conn.execute("DELETE FROM graph_nodes WHERE project_id=$1", project_id)
            await conn.execute("DELETE FROM graph_edges WHERE project_id=$1", project_id)
            
            for node in nodes:
                await conn.execute(
                    \"\"\"INSERT INTO graph_nodes (project_id, node_id, node_type, name, file_path, metadata)
                       VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (project_id, node_id) DO UPDATE
                       SET node_type=$3, name=$4, file_path=$5, metadata=$6\"\"\",
                    project_id, node["id"], node["type"], node["name"],
                    node.get("filePath"), json.dumps(node.get("metadata", {}))
                )
            
            for edge in edges:
                await conn.execute(
                    \"\"\"INSERT INTO graph_edges (project_id, source_node_id, target_node_id, relationship, confidence, evidence)
                       VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (project_id, source_node_id, target_node_id, relationship) DO UPDATE
                       SET confidence=$5, evidence=$6\"\"\",
                    project_id, edge["source"], edge["target"], edge["relationship"],
                    edge.get("confidence", 1.0), json.dumps(edge.get("evidence", {}))
                )
    logger.info(f"Saved {len(nodes)} nodes and {len(edges)} edges for project {project_id}")

async def get_graph(project_id: str) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        project = await conn.fetchrow("SELECT * FROM projects WHERE id=$1", project_id)
        if not project:
            return None
        nodes = await conn.fetch("SELECT * FROM graph_nodes WHERE project_id=$1", project_id)
        edges = await conn.fetch("SELECT * FROM graph_edges WHERE project_id=$1", project_id)
    
    return {
        "projectId": str(project["id"]),
        "projectName": project["project_name"],
        "nodes": [{
            "id": n["node_id"],
            "type": n["node_type"],
            "name": n["name"],
            "filePath": n["file_path"],
            "metadata": json.loads(n["metadata"]) if n["metadata"] else {}
        } for n in nodes],
        "edges": [{
            "id": str(e["id"]),
            "source": e["source_node_id"],
            "target": e["target_node_id"],
            "relationship": e["relationship"],
            "confidence": e["confidence"],
            "evidence": json.loads(e["evidence"]) if e["evidence"] else {},
            "reason": ""
        } for e in edges]
    }

async def list_projects() -> list:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, project_name, project_path, status, created_at FROM projects ORDER BY created_at DESC")
    return [{"id": str(r["id"]), "name": r["project_name"], "path": r["project_path"], "status": r["status"], "createdAt": str(r["created_at"])} for r in rows]
""",
    "app/routes/analyze.py": """import logging
from fastapi import APIRouter, HTTPException
from app.models.schemas import CodeMapPayload, AnalyzeResponse, GraphData
from app.services.llm_service import generate_dependency_graph
from app.services.graph_validator import validate_graph
from app.services.db_repository import save_project, save_graph

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(payload: CodeMapPayload):
    logger.info(f"Received Code Map for project: {payload.project.name} with {len(payload.files)} files")
    
    # Validate payload basics
    if not payload.files:
        raise HTTPException(status_code=422, detail="Code Map contains no files")
    
    # Convert payload to dict for LLM
    code_map_dict = payload.model_dump(by_alias=True)
    
    # Call LLM
    try:
        raw_graph = await generate_dependency_graph(code_map_dict)
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        raise HTTPException(status_code=502, detail=f"LLM generation failed: {str(e)}")
    
    # Validate graph
    try:
        validated = validate_graph(raw_graph, code_map_dict)
    except Exception as e:
        logger.error(f"Graph validation failed: {e}")
        raise HTTPException(status_code=422, detail=f"Graph validation failed: {str(e)}")
    
    if not validated["nodes"]:
        raise HTTPException(status_code=422, detail="Validated graph has no nodes")
    
    # Save to PostgreSQL
    try:
        project_id = await save_project(payload.project.name, payload.project.path)
        await save_graph(project_id, validated["nodes"], validated["edges"])
    except Exception as e:
        logger.error(f"Database save failed: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    
    graph = GraphData(
        projectId=project_id,
        projectName=payload.project.name,
        nodes=validated["nodes"],
        edges=validated["edges"]
    )
    
    return AnalyzeResponse(projectId=project_id, graph=graph)
""",
    "app/routes/graph.py": """import logging
from fastapi import APIRouter, HTTPException
from app.services.db_repository import get_graph, list_projects

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/api/graph/{project_id}")
async def get_project_graph(project_id: str):
    graph = await get_graph(project_id)
    if not graph:
        raise HTTPException(status_code=404, detail="Project not found")
    return graph

@router.get("/api/projects")
async def get_projects():
    return await list_projects()

@router.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}
""",
    "app/main.py": """import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from app.routes.analyze import router as analyze_router
from app.routes.graph import router as graph_router
from app.db.database import create_pool, close_pool, create_tables

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up backend...")
    await create_pool()
    await create_tables()
    logger.info("Backend ready")
    yield
    await close_pool()
    logger.info("Backend shut down")

app = FastAPI(title="AST Dependency Graph Analyzer", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analyze_router)
app.include_router(graph_router)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 5000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
"""
}

for rel_path, content in files.items():
    full_path = os.path.join(base_dir, rel_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
print("Files created successfully.")

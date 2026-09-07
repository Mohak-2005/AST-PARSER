import json
import logging
from app.db.database import get_pool

logger = logging.getLogger(__name__)

async def save_project(project_name: str, project_path: str, code_map: dict = None) -> str:
    pool = get_pool()
    code_map_json = json.dumps(code_map or {})
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO projects (project_name, project_path, status, code_map)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            project_name, project_path, "complete", code_map_json
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
                    """INSERT INTO graph_nodes (project_id, node_id, node_type, name, file_path, metadata)
                       VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (project_id, node_id) DO UPDATE
                       SET node_type=$3, name=$4, file_path=$5, metadata=$6""",
                    project_id, node["id"], node["type"], node["name"],
                    node.get("filePath"), json.dumps(node.get("metadata", {}))
                )
            
            for edge in edges:
                await conn.execute(
                    """INSERT INTO graph_edges (project_id, source_node_id, target_node_id, relationship, confidence, evidence)
                       VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (project_id, source_node_id, target_node_id, relationship) DO UPDATE
                       SET confidence=$5, evidence=$6""",
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
    
    code_map_val = project.get("code_map") if "code_map" in project else None
    if isinstance(code_map_val, str):
        try:
            code_map_data = json.loads(code_map_val)
        except Exception:
            code_map_data = {}
    elif isinstance(code_map_val, dict):
        code_map_data = code_map_val
    else:
        code_map_data = {}

    return {
        "projectId": str(project["id"]),
        "projectName": project["project_name"],
        "projectPath": project["project_path"],
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
        } for e in edges],
        "codeMap": code_map_data
    }

async def list_projects() -> list:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, project_name, project_path, status, created_at FROM projects ORDER BY created_at DESC")
    return [{"id": str(r["id"]), "name": r["project_name"], "path": r["project_path"], "status": r["status"], "createdAt": str(r["created_at"])} for r in rows]

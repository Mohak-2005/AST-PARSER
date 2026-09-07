import logging
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

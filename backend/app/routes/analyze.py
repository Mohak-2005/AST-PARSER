import time
import uuid
import traceback
from fastapi import APIRouter, HTTPException
from app.models.schemas import CodeMapPayload, AnalyzeResponse, GraphData
from app.services.llm_service import generate_dependency_graph
from app.services.graph_validator import validate_graph
from app.services.db_repository import save_project, save_graph
from app.logger import backend_logger, log_stage, log_error

router = APIRouter()

@router.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(payload: CodeMapPayload):
    total_start = time.time()
    analysis_id = payload.analysis_id or str(uuid.uuid4())
    project_name = payload.project.name
    project_path = payload.project.path

    backend_logger.info(f"ANALYSIS STARTED [analysis_id={analysis_id}] for project: {project_name}")
    log_stage("BACKEND_REQUEST", "START", analysis_id=analysis_id, extra={
        "project": project_name,
        "files": len(payload.files)
    })

    # Validate payload basics
    if not payload.files:
        err_msg = "Code Map contains no files"
        log_stage("BACKEND_REQUEST", "FAILURE", message=err_msg, analysis_id=analysis_id)
        log_error("BACKEND_REQUEST", "ValidationError", err_msg, analysis_id=analysis_id, project_path=project_path)
        raise HTTPException(status_code=422, detail={"detail": "Validation error", "error": err_msg})

    log_stage("CODE_MAP_PROCESSING", "START", analysis_id=analysis_id)
    t0_prep = time.time()
    try:
        code_map_dict = payload.model_dump(by_alias=True)
        dur_prep = (time.time() - t0_prep) * 1000
        log_stage("CODE_MAP_PROCESSING", "SUCCESS", analysis_id=analysis_id, duration_ms=dur_prep)
    except Exception as exc:
        dur_prep = (time.time() - t0_prep) * 1000
        err_msg = f"Failed to serialize Code Map payload: {str(exc)}"
        backend_logger.error(err_msg)
        log_stage("CODE_MAP_PROCESSING", "FAILURE", message=err_msg, analysis_id=analysis_id, duration_ms=dur_prep)
        log_error("CODE_MAP_PROCESSING", type(exc).__name__, err_msg, stack_trace=traceback.format_exc(), analysis_id=analysis_id, project_path=project_path)
        raise HTTPException(status_code=422, detail={"detail": "Code Map processing failed", "error": str(exc)})

    # Call LLM
    try:
        raw_graph = await generate_dependency_graph(code_map_dict, analysis_id=analysis_id)
    except TimeoutError as exc:
        err_msg = str(exc)
        backend_logger.error(f"LLM call timed out: {err_msg}")
        raise HTTPException(status_code=504, detail={"detail": "LLM generation timed out", "error": err_msg})
    except ValueError as exc:
        err_msg = str(exc)
        backend_logger.error(f"LLM input/output validation error: {err_msg}")
        raise HTTPException(status_code=422, detail={"detail": "LLM validation failed", "error": err_msg})
    except Exception as exc:
        err_msg = str(exc)
        backend_logger.error(f"LLM call failed: {err_msg}")
        log_error("LLM_REQUEST", type(exc).__name__, err_msg, stack_trace=traceback.format_exc(), analysis_id=analysis_id, project_path=project_path)
        raise HTTPException(status_code=502, detail={"detail": "LLM generation failed", "error": err_msg})

    # Validate graph
    backend_logger.info("GRAPH VALIDATION STARTED")
    log_stage("GRAPH_VALIDATION", "START", analysis_id=analysis_id)
    t0_val = time.time()
    try:
        validated = validate_graph(raw_graph, code_map_dict)
        dur_val = (time.time() - t0_val) * 1000
        backend_logger.info(f"GRAPH VALIDATION COMPLETED in {dur_val:.2f}ms")
        log_stage("GRAPH_VALIDATION", "SUCCESS", analysis_id=analysis_id, duration_ms=dur_val, extra={
            "valid_nodes": len(validated["nodes"]),
            "valid_edges": len(validated["edges"])
        })
    except Exception as exc:
        dur_val = (time.time() - t0_val) * 1000
        err_msg = f"Graph validation failed: {str(exc)}"
        backend_logger.error(err_msg)
        log_stage("GRAPH_VALIDATION", "FAILURE", message=err_msg, analysis_id=analysis_id, duration_ms=dur_val)
        log_error("GRAPH_VALIDATION", type(exc).__name__, err_msg, stack_trace=traceback.format_exc(), analysis_id=analysis_id, project_path=project_path)
        raise HTTPException(status_code=422, detail={"detail": "Graph validation failed", "error": str(exc)})

    if not validated["nodes"]:
        err_msg = "Validated graph has no nodes"
        backend_logger.error(err_msg)
        log_stage("GRAPH_VALIDATION", "FAILURE", message=err_msg, analysis_id=analysis_id)
        log_error("GRAPH_VALIDATION", "EmptyGraphError", err_msg, analysis_id=analysis_id, project_path=project_path)
        raise HTTPException(status_code=422, detail={"detail": "Graph validation failed", "error": err_msg})

    # Save to PostgreSQL
    log_stage("DATABASE_STORAGE", "START", analysis_id=analysis_id)
    t0_db = time.time()
    try:
        project_id = await save_project(payload.project.name, payload.project.path, code_map=code_map_dict)
        await save_graph(project_id, validated["nodes"], validated["edges"])
        dur_db = (time.time() - t0_db) * 1000
        backend_logger.info(f"DATABASE SAVE COMPLETED in {dur_db:.2f}ms")
        log_stage("DATABASE_STORAGE", "SUCCESS", analysis_id=analysis_id, duration_ms=dur_db)
    except Exception as exc:
        dur_db = (time.time() - t0_db) * 1000
        err_msg = f"Database error: {str(exc)}"
        backend_logger.error(err_msg)
        log_stage("DATABASE_STORAGE", "FAILURE", message=err_msg, analysis_id=analysis_id, duration_ms=dur_db)
        log_error("DATABASE_STORAGE", type(exc).__name__, err_msg, stack_trace=traceback.format_exc(), analysis_id=analysis_id, project_path=project_path)
        raise HTTPException(status_code=500, detail={"detail": "Database failure", "error": str(exc)})

    total_dur_s = time.time() - total_start
    backend_logger.info(f"ANALYSIS COMPLETED in {total_dur_s:.2f}s [analysis_id={analysis_id}]")
    log_stage("BACKEND_REQUEST", "SUCCESS", analysis_id=analysis_id, duration_ms=total_dur_s * 1000)

    graph = GraphData(
        projectId=project_id,
        projectName=payload.project.name,
        projectPath=payload.project.path,
        nodes=validated["nodes"],
        edges=validated["edges"],
        codeMap=code_map_dict
    )

    return AnalyzeResponse(projectId=project_id, graph=graph, message="Analysis complete")

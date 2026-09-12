import time
import uuid
import traceback
import asyncio
from pathlib import Path
from typing import Optional, Union
from fastapi import APIRouter, HTTPException
from app.models.schemas import (
    CodeMapPayload,
    AnalyzeResponse,
    GraphData,
    FeatureContextRequest,
    FeatureContextResponse,
    UnitTestCaseRequest,
    UnitTestCaseResponse,
    RetrievedCodeRequest,
    RetrievedCodeResponse,
)
from app.services.llm_service import generate_dependency_graph
from app.services.graph_validator import validate_graph
from app.services.db_repository import save_project, save_graph, get_graph
from app.services.feature_context_generator import (
    generate_all_feature_contexts,
    get_existing_feature_contexts,
)
from app.services.unit_test_generator import (
    generate_all_unit_test_cases,
    get_existing_test_cases,
)
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

    # Trigger Feature Context Generation if codeMapPath is provided
    code_map_target = payload.project.codeMapPath or payload.codeMapPath
    if code_map_target:
        async def _run_fc():
            try:
                await generate_all_feature_contexts(code_map_target)
            except Exception as fc_err:
                backend_logger.warn(f"[FeatureContext] Generation task notice: {fc_err}")
        asyncio.create_task(_run_fc())

    return AnalyzeResponse(projectId=project_id, graph=graph, message="Analysis complete")


async def _resolve_code_map_source(code_map_path: Optional[str], project_id: Optional[str]) -> Optional[Union[str, dict]]:
    """Helper to resolve the code-map file path or dict from codeMapPath or projectId."""
    if code_map_path:
        return code_map_path
    if project_id:
        graph = await get_graph(project_id)
        if graph and graph.get("codeMap"):
            cm = graph["codeMap"]
            cand_path = cm.get("project", {}).get("codeMapPath") or cm.get("codeMapPath")
            if cand_path and Path(cand_path).exists():
                return cand_path
            p_name = graph.get("projectName")
            if p_name:
                fallback_file = Path("C:/Users/Mohak/Desktop/test") / p_name / "repository-code-map.json"
                if fallback_file.exists():
                    return str(fallback_file)
            return cm
    return None


@router.get("/api/feature-context", response_model=FeatureContextResponse)
async def check_feature_contexts_endpoint(
    codeMapPath: Optional[str] = None,
    projectId: Optional[str] = None,
):
    code_map_source = await _resolve_code_map_source(codeMapPath, projectId)
    if not code_map_source:
        raise HTTPException(status_code=400, detail="codeMapPath or valid projectId is required")

    existing_files = get_existing_feature_contexts(code_map_source)
    if existing_files:
        return FeatureContextResponse(
            status="already_exists",
            alreadyExists=True,
            featureCount=len(existing_files),
            savedFiles=[f.name for f in existing_files],
            message=f"Feature context already generated for {len(existing_files)} features.",
        )
    return FeatureContextResponse(
        status="not_found",
        alreadyExists=False,
        featureCount=0,
        savedFiles=[],
        message="No feature context generated yet.",
    )


@router.post("/api/feature-context", response_model=FeatureContextResponse)
async def create_feature_contexts_endpoint(req: FeatureContextRequest):
    code_map_source = await _resolve_code_map_source(req.codeMapPath, req.projectId)
    if not code_map_source:
        raise HTTPException(status_code=400, detail="codeMapPath or valid projectId is required")

    # Double-check existing files before invoking LLM if not forced
    if not req.force:
        existing_files = get_existing_feature_contexts(code_map_source)
        if existing_files:
            return FeatureContextResponse(
                status="already_exists",
                alreadyExists=True,
                featureCount=len(existing_files),
                savedFiles=[f.name for f in existing_files],
                message=f"Feature context already generated for {len(existing_files)} features.",
            )

    try:
        contexts = await generate_all_feature_contexts(code_map_source, force=bool(req.force))
        saved_files = [
            f"{c.get('feature', {}).get('id') or 'feature'}.json" for c in contexts
        ]
        return FeatureContextResponse(
            status="success",
            alreadyExists=False,
            featureCount=len(contexts),
            savedFiles=saved_files,
            message=f"Feature context generated successfully for {len(contexts)} features.",
        )
    except Exception as exc:
        backend_logger.error(f"[FeatureContext] Endpoint error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/test-cases", response_model=UnitTestCaseResponse)
async def check_unit_test_cases_endpoint(
    codeMapPath: Optional[str] = None,
    projectId: Optional[str] = None,
):
    code_map_source = await _resolve_code_map_source(codeMapPath, projectId)
    if not code_map_source:
        raise HTTPException(status_code=400, detail="codeMapPath or valid projectId is required")

    existing_files = get_existing_test_cases(code_map_source)
    if existing_files:
        return UnitTestCaseResponse(
            status="already_exists",
            alreadyExists=True,
            testCaseCount=len(existing_files),
            savedFiles=[f.name for f in existing_files],
            message="Unit test cases already generated.",
        )
    return UnitTestCaseResponse(
        status="not_found",
        alreadyExists=False,
        testCaseCount=0,
        savedFiles=[],
        message="No unit test cases generated yet.",
    )


@router.post("/api/test-cases", response_model=UnitTestCaseResponse)
async def create_unit_test_cases_endpoint(req: UnitTestCaseRequest):
    code_map_source = await _resolve_code_map_source(req.codeMapPath, req.projectId)
    if not code_map_source:
        raise HTTPException(status_code=400, detail="codeMapPath or valid projectId is required")

    # Guard check: Do not invoke LLM if valid test cases exist and force is False
    if not req.force:
        existing_files = get_existing_test_cases(code_map_source)
        if existing_files:
            return UnitTestCaseResponse(
                status="already_exists",
                alreadyExists=True,
                testCaseCount=len(existing_files),
                savedFiles=[f.name for f in existing_files],
                message="Unit test cases already generated.",
            )

    try:
        res = await generate_all_unit_test_cases(code_map_source, force=bool(req.force))
        return UnitTestCaseResponse(
            status=res.get("status", "success"),
            alreadyExists=res.get("alreadyExists", False),
            testCaseCount=res.get("testCaseCount", 0),
            unitCount=res.get("unitCount", 0),
            savedFiles=res.get("savedFiles", []),
            message=res.get("message", "Unit test cases generated successfully."),
        )
    except ValueError as val_err:
        backend_logger.warning(f"[UnitTestGenerator] Input/validation notice: {val_err}")
        raise HTTPException(status_code=422, detail=str(val_err))
    except Exception as exc:
        backend_logger.error(f"[UnitTestGenerator] Endpoint error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


def get_existing_retrieved_code(code_map_source) -> list[Path]:
    target_dir = None
    if isinstance(code_map_source, (str, Path)):
        p = Path(code_map_source)
        if p.is_file():
            target_dir = p.parent
        elif p.is_dir():
            target_dir = p
    elif isinstance(code_map_source, dict):
        cand = code_map_source.get("project", {}).get("codeMapPath") or code_map_source.get("codeMapPath")
        if cand and Path(cand).exists():
            target_dir = Path(cand).parent

    if not target_dir:
        return []

    retrieved_dir = target_dir / "code retrieved"
    if not retrieved_dir.exists() or not retrieved_dir.is_dir():
        return []

    return [f for f in retrieved_dir.glob("*.json") if f.is_file()]


@router.get("/api/retrieved-code", response_model=RetrievedCodeResponse)
async def check_retrieved_code_endpoint(
    codeMapPath: Optional[str] = None,
    projectId: Optional[str] = None,
):
    code_map_source = await _resolve_code_map_source(codeMapPath, projectId)
    if not code_map_source:
        raise HTTPException(status_code=400, detail="codeMapPath or valid projectId is required")

    existing_files = get_existing_retrieved_code(code_map_source)
    if existing_files:
        files_data = []
        for f in existing_files:
            try:
                import json
                with open(f, "r", encoding="utf-8") as fp:
                    files_data.append({"fileName": f.name, "filePath": str(f), "content": json.load(fp)})
            except Exception:
                pass
        return RetrievedCodeResponse(
            status="success",
            alreadyExists=True,
            retrievedCount=len(existing_files),
            savedFiles=[f.name for f in existing_files],
            files=files_data,
            message=f"Found {len(existing_files)} retrieved code artifact(s).",
        )
    return RetrievedCodeResponse(
        status="not_found",
        alreadyExists=False,
        retrievedCount=0,
        savedFiles=[],
        files=[],
        message="No retrieved code found yet.",
    )


@router.post("/api/retrieve-code", response_model=RetrievedCodeResponse)
async def trigger_retrieve_code_endpoint(req: RetrievedCodeRequest):
    code_map_source = await _resolve_code_map_source(req.codeMapPath, req.projectId)
    if not code_map_source:
        raise HTTPException(status_code=400, detail="codeMapPath or valid projectId is required")

    # Delegate to Local Agent at http://localhost:3001/agent/retrieve-code
    import httpx
    agent_url = "http://localhost:3001/agent/retrieve-code"
    cm_path_str = str(code_map_source) if isinstance(code_map_source, (str, Path)) else None
    payload = {"codeMapPath": cm_path_str, "projectId": req.projectId, "projectPath": req.projectPath}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(agent_url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                existing_files = get_existing_retrieved_code(code_map_source)
                files_data = []
                for f in existing_files:
                    try:
                        import json
                        with open(f, "r", encoding="utf-8") as fp:
                            files_data.append({"fileName": f.name, "filePath": str(f), "content": json.load(fp)})
                    except Exception:
                        pass
                return RetrievedCodeResponse(
                    status="success",
                    alreadyExists=True,
                    retrievedCount=data.get("retrievedCount", len(existing_files)),
                    savedFiles=data.get("savedFiles", [f.name for f in existing_files]),
                    files=files_data,
                    message=data.get("message", "Source code retrieved successfully."),
                )
    except Exception as exc:
        backend_logger.warning(f"[RetrieveCode] Agent call failed: {exc}")

    # Fallback if agent not reachable: check if files exist on disk
    existing_files = get_existing_retrieved_code(code_map_source)
    if existing_files:
        files_data = []
        for f in existing_files:
            try:
                import json
                with open(f, "r", encoding="utf-8") as fp:
                    files_data.append({"fileName": f.name, "filePath": str(f), "content": json.load(fp)})
            except Exception:
                pass
        return RetrievedCodeResponse(
            status="success",
            alreadyExists=True,
            retrievedCount=len(existing_files),
            savedFiles=[f.name for f in existing_files],
            files=files_data,
            message="Retrieved code found on disk.",
        )

    raise HTTPException(status_code=502, detail="Local Agent code retrieval was unreachable or failed.")



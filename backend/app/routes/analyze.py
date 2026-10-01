import os
import json
import time
import uuid
import traceback
import asyncio
from pathlib import Path
from typing import Optional, Union
from fastapi import APIRouter, HTTPException
from app.config import settings, BACKEND_DIR
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
    TestScriptRequest,
    TestScriptResponse,
    TestExecutionRequest,
    TestExecutionResponse,
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
    generate_all_test_cases,
    get_existing_test_cases,
)
from app.services.test_script_generator import (
    generate_all_test_scripts,
    get_existing_test_scripts,
)
from app.services.test_executor import (
    run_project_test_execution,
    get_existing_execution_results,
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


def _find_code_map_on_disk(
    project_name: Optional[str],
    project_path: Optional[str],
    project_id: Optional[str],
    candidate_path: Optional[str] = None,
) -> Optional[str]:
    # 1. Candidate path (e.g. from codeMap.project.codeMapPath or directly passed)
    if candidate_path:
        cand = Path(candidate_path)
        if cand.is_file():
            return str(cand.resolve())
        elif (cand / "repository-code-map.json").is_file():
            return str((cand / "repository-code-map.json").resolve())

    # 2. In project directory itself
    if project_path:
        p_dir = Path(project_path)
        if (p_dir / "repository-code-map.json").is_file():
            return str((p_dir / "repository-code-map.json").resolve())
        elif p_dir.is_file() and p_dir.name == "repository-code-map.json":
            return str(p_dir.resolve())

    # 3. Base output directories discovery
    base_dirs: list[Path] = []
    desktop_test = Path(r"C:\Users\Mohak\Desktop\test")
    if desktop_test.is_dir():
        base_dirs.append(desktop_test)
    if getattr(settings, "analysis_output_dir", None):
        p_set = Path(settings.analysis_output_dir).resolve()
        if p_set not in base_dirs:
            base_dirs.append(p_set)
    env_out = os.environ.get("ANALYSIS_OUTPUT_DIR")
    if env_out and Path(env_out).resolve() not in base_dirs:
        base_dirs.append(Path(env_out).resolve())

    # Fallback: check agent/.env if not already discovered
    try:
        agent_env = BACKEND_DIR.parent / "agent" / ".env"
        if agent_env.is_file():
            with open(agent_env, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("ANALYSIS_OUTPUT_DIR="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            p_val = Path(val)
                            if not p_val.is_absolute():
                                p_val = (BACKEND_DIR.parent / p_val).resolve()
                            if p_val not in base_dirs:
                                base_dirs.append(p_val)
    except Exception:
        pass

    # Default fallback: analysis_output directory in project root
    default_out = (BACKEND_DIR.parent / "analysis_output").resolve()
    if default_out not in base_dirs:
        base_dirs.append(default_out)

    # Search in discovered base directories
    for base_dir in base_dirs:
        if not base_dir.is_dir():
            continue

        # Fast direct check: project_name folder
        if project_name:
            cand1 = base_dir / project_name / "repository-code-map.json"
            if cand1.is_file():
                return str(cand1.resolve())

        # Fast direct check: project_path folder name
        if project_path:
            folder_name = Path(project_path).name
            if folder_name:
                cand2 = base_dir / folder_name / "repository-code-map.json"
                if cand2.is_file():
                    return str(cand2.resolve())

        # Scan subdirectories with matching heuristics (bounded to immediate subdirs)
        try:
            for sub_dir in base_dir.iterdir():
                if not sub_dir.is_dir():
                    continue
                map_file = sub_dir / "repository-code-map.json"
                if not map_file.is_file():
                    continue

                # Case-insensitive name match
                if project_name and sub_dir.name.lower() == project_name.lower():
                    return str(map_file.resolve())
                if project_path and sub_dir.name.lower() == Path(project_path).name.lower():
                    return str(map_file.resolve())

                # Deep inspection of code-map metadata
                try:
                    with open(map_file, "r", encoding="utf-8") as fh:
                        cm_data = json.load(fh)
                    proj_meta = cm_data.get("project", {}) if isinstance(cm_data, dict) else {}
                    if not isinstance(proj_meta, dict):
                        proj_meta = {}

                    if project_id and (
                        cm_data.get("analysis_id") == project_id
                        or proj_meta.get("analysis_id") == project_id
                        or proj_meta.get("projectId") == project_id
                        or proj_meta.get("id") == project_id
                    ):
                        return str(map_file.resolve())

                    if project_path:
                        m_path = proj_meta.get("path")
                        if m_path and (m_path == project_path or Path(m_path).resolve() == Path(project_path).resolve()):
                            return str(map_file.resolve())

                    if project_name and proj_meta.get("name") == project_name:
                        return str(map_file.resolve())
                except Exception:
                    continue
        except Exception:
            continue

    return None


async def _resolve_code_map_source(code_map_path: Optional[str], project_id: Optional[str]) -> Optional[Union[str, dict]]:
    """Helper to resolve the code-map file path or dict from codeMapPath or projectId."""
    # Sanitize string inputs from frontend (e.g. "null", "undefined", empty strings)
    if code_map_path:
        code_map_path = code_map_path.strip()
        if not code_map_path or code_map_path.lower() in ("null", "undefined", "none"):
            code_map_path = None

    if project_id:
        project_id = project_id.strip()
        if not project_id or project_id.lower() in ("null", "undefined", "none"):
            project_id = None

    # Case 1: code_map_path was provided directly and exists on disk
    if code_map_path:
        p = Path(code_map_path)
        if p.is_file():
            return str(p.resolve())
        elif (p / "repository-code-map.json").is_file():
            return str((p / "repository-code-map.json").resolve())
        elif not project_id:
            # If no project_id is available, return raw string
            return code_map_path

    # Case 2: project_id provided (or fallback if code_map_path did not exist on disk)
    if project_id:
        try:
            graph = await get_graph(project_id)
        except Exception as exc:
            backend_logger.warning(f"[CodeMapResolver] get_graph({project_id}) error: {exc}")
            graph = None

        if not graph:
            return None

        p_name = graph.get("projectName")
        p_path = graph.get("projectPath")
        cm = graph.get("codeMap") or {}

        cand_path = None
        if isinstance(cm, dict):
            cand_path = cm.get("project", {}).get("codeMapPath") or cm.get("codeMapPath")
        if not cand_path and code_map_path:
            cand_path = code_map_path

        # 1. Search for repository-code-map.json on disk
        found_file = _find_code_map_on_disk(
            project_name=p_name,
            project_path=p_path,
            project_id=project_id,
            candidate_path=cand_path,
        )
        if found_file:
            return found_file

        # 2. If no file on disk, check if codeMap stored in DB contains valid files
        if isinstance(cm, dict) and bool(cm.get("files")):
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
    testType: Optional[str] = None,
):
    code_map_source = await _resolve_code_map_source(codeMapPath, projectId)
    if not code_map_source:
        raise HTTPException(status_code=400, detail="codeMapPath or valid projectId is required")

    t_filter = (testType or "").strip().lower() if testType else None
    existing_files = get_existing_test_cases(code_map_source, test_type=t_filter)
    if existing_files:
        label = t_filter.capitalize() if t_filter else "Unit"
        return UnitTestCaseResponse(
            status="already_exists",
            alreadyExists=True,
            testCaseCount=len(existing_files),
            savedFiles=[f.name for f in existing_files],
            message=f"{label} test cases already generated.",
            testType=t_filter or "unit",
        )
    return UnitTestCaseResponse(
        status="not_found",
        alreadyExists=False,
        testCaseCount=0,
        savedFiles=[],
        message="No test cases generated yet.",
        testType=t_filter or "unit",
    )


@router.post("/api/test-cases", response_model=UnitTestCaseResponse)
async def create_unit_test_cases_endpoint(req: UnitTestCaseRequest):
    code_map_source = await _resolve_code_map_source(req.codeMapPath, req.projectId)
    if not code_map_source:
        raise HTTPException(status_code=400, detail="codeMapPath or valid projectId is required")

    req_type = (req.testType or "unit").strip().lower()

    # Guard check: Do not invoke LLM if valid test cases exist and force is False
    if not req.force:
        existing_files = get_existing_test_cases(code_map_source, test_type=req_type if req_type != "all" else None)
        if existing_files:
            return UnitTestCaseResponse(
                status="already_exists",
                alreadyExists=True,
                testCaseCount=len(existing_files),
                savedFiles=[f.name for f in existing_files],
                message=f"{req_type.capitalize()} test cases already generated.",
                testType=req_type,
            )

    try:
        res = await generate_all_test_cases(code_map_source, force=bool(req.force), test_type=req_type)
        return UnitTestCaseResponse(
            status=res.get("status", "success"),
            alreadyExists=res.get("alreadyExists", False),
            testCaseCount=res.get("testCaseCount", 0),
            unitCount=res.get("unitCount", 0),
            savedFiles=res.get("savedFiles", []),
            message=res.get("message", "Test cases generated successfully."),
            testType=res.get("testType", req_type),
            breakdown=res.get("breakdown", {}),
        )
    except ValueError as val_err:
        backend_logger.warning(f"[TestCaseGenerator] Input/validation notice: {val_err}")
        raise HTTPException(status_code=422, detail=str(val_err))
    except Exception as exc:
        backend_logger.error(f"[TestCaseGenerator] Endpoint error: {exc}")
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


@router.get("/api/test-scripts", response_model=TestScriptResponse)
async def check_test_scripts_endpoint(
    codeMapPath: Optional[str] = None,
    projectId: Optional[str] = None,
):
    code_map_source = await _resolve_code_map_source(codeMapPath, projectId)
    if not code_map_source:
        raise HTTPException(status_code=400, detail="codeMapPath or valid projectId is required")

    existing_scripts = get_existing_test_scripts(code_map_source)
    if existing_scripts:
        successful_count = sum(1 for s in existing_scripts if s.get("status") == "success")
        failed_count = len(existing_scripts) - successful_count
        return TestScriptResponse(
            status="success",
            alreadyExists=True,
            generatedCount=len(existing_scripts),
            successfulCount=successful_count,
            failedCount=failed_count,
            scripts=existing_scripts,
            message=f"Found {len(existing_scripts)} generated test script(s) ({successful_count} valid, {failed_count} failed cross-validation).",
        )
    return TestScriptResponse(
        status="not_found",
        alreadyExists=False,
        generatedCount=0,
        successfulCount=0,
        failedCount=0,
        scripts=[],
        message="No test scripts found yet.",
    )


@router.post("/api/test-scripts", response_model=TestScriptResponse)
async def generate_test_scripts_endpoint(req: TestScriptRequest):
    code_map_source = await _resolve_code_map_source(req.codeMapPath, req.projectId)
    if not code_map_source:
        raise HTTPException(status_code=400, detail="codeMapPath or valid projectId is required")

    try:
        result = await generate_all_test_scripts(
            code_map_source=code_map_source,
            force=req.force or False,
            analysis_id=req.projectId,
        )
        if result.get("status") == "not_found":
            raise HTTPException(status_code=404, detail=result.get("message"))
        return TestScriptResponse(
            status="success",
            alreadyExists=False,
            generatedCount=result.get("generatedCount", 0),
            successfulCount=result.get("successfulCount", 0),
            scriptsDir=result.get("scriptsDir"),
            projectEnvironment=result.get("projectEnvironment"),
            scripts=result.get("scripts", []),
            message=result.get("message", "Test scripts generated successfully."),
        )
    except HTTPException:
        raise
    except Exception as exc:
        backend_logger.error(f"Error generating test scripts: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to generate test scripts: {str(exc)}")


@router.post("/api/test-execution", response_model=TestExecutionResponse)
async def execute_tests_endpoint(req: TestExecutionRequest):
    code_map_source = await _resolve_code_map_source(req.codeMapPath, req.projectId)
    if not code_map_source:
        raise HTTPException(status_code=400, detail="codeMapPath or valid projectId is required")

    try:
        exec_kwargs = {}
        if req.timeoutSeconds is not None:
            exec_kwargs["timeout_s"] = req.timeoutSeconds

        result = run_project_test_execution(
            code_map_source=code_map_source,
            test_type=req.testType,
            script_files=req.scriptFiles,
            **exec_kwargs
        )
        return TestExecutionResponse(**result)
    except Exception as exc:
        backend_logger.error(f"[TestExecution] Execution error: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to execute tests: {str(exc)}")


@router.get("/api/test-execution", response_model=TestExecutionResponse)
async def get_test_execution_endpoint(
    codeMapPath: Optional[str] = None,
    projectId: Optional[str] = None,
):
    code_map_source = await _resolve_code_map_source(codeMapPath, projectId)
    if not code_map_source:
        raise HTTPException(status_code=400, detail="codeMapPath or valid projectId is required")

    summary = get_existing_execution_results(code_map_source)
    if summary:
        return TestExecutionResponse(
            status=summary.get("status", "success"),
            totalTests=summary.get("totalTests", 0),
            passed=summary.get("passed", 0),
            failed=summary.get("failed", 0),
            durationMs=summary.get("durationMs", 0.0),
            runner=summary.get("runner", ""),
            results=summary.get("results", []),
            message=f"Found existing execution results ({summary.get('passed', 0)} passed, {summary.get('failed', 0)} failed)."
        )
    return TestExecutionResponse(
        status="not_found",
        totalTests=0,
        passed=0,
        failed=0,
        durationMs=0.0,
        runner="none",
        results=[],
        message="No test execution results found."
    )



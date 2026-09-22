import httpx
import json
import time
from typing import Optional, Tuple, Any, Dict
from app.config import settings
from app.logger import backend_logger, log_stage, log_error, log_llm_metrics
from app.services.json_repair_service import (
    strip_markdown_fences,
    parse_json,
    repair_json_deterministically
)
from app.services.llm_provider import execute_provider_call, get_provider_config

SYSTEM_PROMPT = """You are a code analysis assistant. You receive a structured Code Map of a software repository and generate a dependency graph representing ACTUAL APPLICATION SOURCE CODE ONLY.

You MUST:
- Only create nodes for entities that actually exist in the Code Map
- Only create edges for relationships that have direct evidence in the Code Map
- Never invent files, classes, functions, imports, or dependencies
- Do NOT create test nodes, test cases, test suites, or test scripts (e.g. no test_case, test_suite, tc_* nodes)
- Only include external package nodes (e.g. flask, express, mysql.connector) when they are directly imported by application source files present in the Code Map. Never include unimported external packages (e.g. do NOT include @playwright/test if no application file imports it).
- Return ONLY valid JSON matching the exact schema provided
- Set confidence 1.0 for direct imports, 0.8 for inferred calls, 0.6 for indirect references

Valid node types: project, directory, file, module, class, function, method, component, route, database_operation
Valid edge relationship types: imports, exports, calls, references, extends, implements, renders, routes_to, handles, uses, contains

For every edge, provide evidence with at least: file path and line number where available."""

GRAPH_SCHEMA = {
    "nodes": [
        {
            "id": "unique_string",
            "type": "file|class|function|component|route",
            "name": "string",
            "filePath": "optional string",
            "location": {"startLine": 0, "endLine": 0},
            "metadata": {}
        }
    ],
    "edges": [
        {
            "id": "unique_string",
            "source": "node_id",
            "target": "node_id",
            "relationship": "imports|calls|extends|etc",
            "confidence": 1.0,
            "evidence": {"file": "path", "line": 0},
            "reason": "explanation"
        }
    ]
}


def clean_file_for_prompt(f: dict, path_key: str = "") -> dict:
    """
    Condense AST data to essential facts for dependency resolution
    without bloated AST coordinate trees, empty arrays, or boilerplate.
    """
    file_info = f.get("file", {}) if isinstance(f.get("file"), dict) else {}
    rel_path = path_key or f.get("relativePath") or file_info.get("relativePath", "")
    language = f.get("language") or file_info.get("language", "")
    parse_status = f.get("parseStatus") or f.get("parse", {}).get("status", "")

    cleaned = {
        "relativePath": rel_path,
        "language": language
    }
    if parse_status and parse_status != "success":
        cleaned["parseStatus"] = parse_status

    # Imports / Dependencies
    imports = []
    if f.get("dependencies"):
        for dep in f["dependencies"]:
            src = dep.get("target") or dep.get("source") or ""
            if src:
                item = {"source": src}
                if dep.get("kind"):
                    item["kind"] = dep["kind"]
                if dep.get("line"):
                    item["line"] = dep["line"]
                imports.append(item)
    elif f.get("imports"):
        for imp in f.get("imports", []):
            source = imp.get("source") or imp.get("modulePath") or ""
            specs = []
            if imp.get("specifiers"):
                specs = [s.get("name") if isinstance(s, dict) else str(s) for s in imp.get("specifiers", [])]
            elif imp.get("importedNames"):
                specs = imp.get("importedNames", [])
            line = imp.get("line")
            if not line and isinstance(imp.get("location"), dict):
                line = imp["location"].get("startLine")
            item = {"source": source}
            if specs:
                item["specifiers"] = specs
            if line:
                item["line"] = line
            imports.append(item)

    if imports:
        cleaned["imports"] = imports

    # Exports
    exports = []
    for e in f.get("exports", []):
        if isinstance(e, dict):
            val = e.get("name") or e.get("text")
        else:
            val = str(e)
        if val:
            exports.append(val)
    if exports:
        cleaned["exports"] = exports

    # Declarations / Symbols
    declarations = []
    if f.get("declarations"):
        for d in f["declarations"]:
            decl_item = {"name": d.get("name"), "type": d.get("type")}
            if d.get("line"):
                decl_item["line"] = d["line"]
            if d.get("extends"):
                decl_item["extends"] = d["extends"]
            if d.get("methods"):
                decl_item["methods"] = d["methods"]
            declarations.append(decl_item)
    else:
        for s in f.get("symbols", []):
            line = s.get("line")
            if not line and isinstance(s.get("location"), dict):
                line = s["location"].get("startLine")
            declarations.append({
                "name": s.get("name"),
                "type": s.get("type"),
                "line": line
            })
        for c in f.get("classes", []):
            declarations.append({
                "name": c.get("name"),
                "type": "class",
                "extends": c.get("extends"),
                "methods": c.get("methods", [])
            })
    if declarations:
        cleaned["declarations"] = declarations

    # Routes
    routes = []
    for r in f.get("routes", []):
        routes.append({"method": r.get("method"), "path": r.get("path")})
    if routes:
        cleaned["routes"] = routes

    # DB Operations
    db_ops = []
    for d in f.get("databaseOperations", []):
        db_ops.append({"operation": d.get("operation")})
    if db_ops:
        cleaned["databaseOperations"] = db_ops

    return cleaned


def build_user_prompt(code_map: dict) -> str:
    raw_files = code_map.get("files", {})
    if isinstance(raw_files, dict):
        file_items = [clean_file_for_prompt(v, path_key=k) for k, v in raw_files.items()]
    elif isinstance(raw_files, list):
        file_items = [clean_file_for_prompt(f) for f in raw_files]
    else:
        file_items = []

    summary = {
        "project": code_map.get("project", {}),
        "files": file_items,
        "relationships": code_map.get("relationships", [])
    }
    
    return f"""Analyze this repository Code Map and generate a dependency graph.

Code Map:
{json.dumps(summary, separators=(',', ':'))}

Generate a JSON object with this exact structure:
{json.dumps(GRAPH_SCHEMA, indent=2)}

Rules:
- Every node 'id' must be unique and descriptive (e.g., 'file:src/App.js', 'class:UserService', 'function:getUserById')
- Every edge 'source' and 'target' must reference existing node 'id' values
- Only generate nodes for entities found in the Code Map above
- Only generate edges with actual evidence from the Code Map
- Return ONLY the JSON object, no markdown, no explanation"""


async def _execute_openrouter_call(
    messages: list,
    max_tokens: int = 4000,
    temperature: float = 0.1,
    analysis_id: str = None,
    stage_name: str = "LLM_REQUEST",
    attempt: int = 1,
    metrics_info: dict = None
) -> Tuple[str, int, float]:
    """
    Unified LLM caller delegating dynamically to the configured provider (Groq, HF, Mistral, Gemini).
    Maintained for full backward compatibility with existing tests and callers.
    """
    return await execute_provider_call(
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        analysis_id=analysis_id,
        stage_name=stage_name,
        attempt=attempt,
        metrics_info=metrics_info
    )


def parse_and_recover_json(
    raw_content: str,
    analysis_id: str = None,
    attempt_desc: str = "initial"
) -> Tuple[bool, Optional[dict], str, dict]:
    """
    Execute Step 1 (Direct parsing with stripped markdown fences)
    and Step 2 (Safe deterministic repair).
    Returns (success, parsed_dict, strategy_name, diagnostics)
    """
    t0_parse = time.time()
    response_size = len(raw_content) if raw_content else 0
    backend_logger.info(f"LLM response parsing started ({attempt_desc}) size={response_size}")
    log_stage("LLM_RESPONSE_PARSING", "START", analysis_id=analysis_id, extra={
        "attempt": attempt_desc,
        "response_size": response_size
    })

    # Step 1: Strip markdown code fences
    cleaned = strip_markdown_fences(raw_content)
    ok, data, err = parse_json(cleaned)

    if ok:
        dur_parse_ms = (time.time() - t0_parse) * 1000
        backend_logger.info("LLM response parsing completed successfully (direct parse)")
        log_stage("LLM_RESPONSE_PARSING", "SUCCESS", analysis_id=analysis_id, duration_ms=dur_parse_ms, extra={
            "method": "DIRECT_PARSE",
            "attempt": attempt_desc
        })
        return True, data, "DIRECT_PARSE", {}

    # Step 2: Deterministic JSON Repair
    line_no = err.lineno if err else None
    col_no = err.colno if err else None
    err_type = type(err).__name__ if err else "UnknownError"
    err_msg = str(err) if err else ""

    backend_logger.warning(
        f"LLM response contained invalid JSON ({err_type} at line {line_no}, col {col_no}): {err_msg}. "
        f"Attempting safe deterministic repair."
    )
    log_stage("LLM_RESPONSE_PARSING", "INVALID_JSON", analysis_id=analysis_id, extra={
        "line": line_no,
        "column": col_no,
        "error_type": err_type,
        "attempt": attempt_desc
    })

    log_stage("LLM_JSON_REPAIR", "START", analysis_id=analysis_id, extra={"attempt": attempt_desc})
    t0_det = time.time()
    det_ok, det_data, strategy, det_diag = repair_json_deterministically(raw_content)
    dur_det_ms = (time.time() - t0_det) * 1000

    if det_ok:
        backend_logger.info(f"Deterministic JSON repair SUCCEEDED using strategy '{strategy}' in {dur_det_ms:.2f}ms")
        log_stage("LLM_JSON_REPAIR", "SUCCESS", analysis_id=analysis_id, duration_ms=dur_det_ms, extra={
            "strategy": strategy,
            "attempt": attempt_desc
        })
        dur_total_ms = (time.time() - t0_parse) * 1000
        log_stage("LLM_RESPONSE_PARSING", "SUCCESS", analysis_id=analysis_id, duration_ms=dur_total_ms, extra={
            "method": "DETERMINISTIC_REPAIR",
            "strategy": strategy,
            "attempt": attempt_desc
        })
        return True, det_data, strategy, {}

    backend_logger.warning(f"Deterministic JSON repair FAILED in {dur_det_ms:.2f}ms")
    log_stage("LLM_JSON_REPAIR", "DETERMINISTIC_REPAIR_FAILED", analysis_id=analysis_id, duration_ms=dur_det_ms, extra={
        "attempt": attempt_desc
    })
    return False, None, "DETERMINISTIC_FAILED", {
        "initial_error": err_msg,
        "line": line_no,
        "column": col_no,
        "error_type": err_type,
        "det_diag": det_diag
    }


async def request_llm_json_repair(
    malformed_content: str,
    analysis_id: str = None,
    attempt: int = 1
) -> Tuple[bool, Optional[dict], Optional[str]]:
    """
    Send a dedicated JSON repair request to the LLM preserving all graph data.
    """
    repair_prompt = f"""The following response was intended to be a dependency graph JSON object but contains invalid JSON syntax.

Repair ONLY the JSON syntax.

Do not add, remove, rename, or modify any nodes, edges, relationships, evidence, confidence values, file paths, IDs, or other semantic information.

Return ONLY one valid JSON object matching the required schema.

Do not use Markdown.
Do not add explanations.
Do not add comments.

Malformed response:
{malformed_content}

Required schema:
{json.dumps(GRAPH_SCHEMA, indent=2)}"""

    t0 = time.time()
    backend_logger.info(f"Sending LLM JSON repair request (attempt {attempt}) [analysis_id={analysis_id}]")
    log_stage("LLM_JSON_REPAIR_REQUEST", "START", analysis_id=analysis_id, extra={
        "attempt": attempt,
        "malformed_chars": len(malformed_content)
    })

    try:
        repaired_raw, status_code, dur_s = await _execute_openrouter_call(
            messages=[{"role": "user", "content": repair_prompt}],
            max_tokens=4000,
            temperature=0.1,
            analysis_id=analysis_id,
            stage_name="LLM_JSON_REPAIR_REQUEST",
            attempt=attempt
        )
    except Exception as exc:
        err_msg = f"LLM JSON repair request failed: {str(exc)}"
        backend_logger.error(err_msg)
        log_stage("LLM_JSON_REPAIR_REQUEST", "FAILURE", message=err_msg, analysis_id=analysis_id, extra={"attempt": attempt})
        return False, None, err_msg

    # Parse repaired response
    repaired_cleaned = strip_markdown_fences(repaired_raw)
    ok, data, err = parse_json(repaired_cleaned)
    if ok:
        dur_ms = (time.time() - t0) * 1000
        backend_logger.info(f"LLM JSON repair SUCCEEDED on attempt {attempt}")
        log_stage("LLM_JSON_REPAIR_REQUEST", "SUCCESS", analysis_id=analysis_id, duration_ms=dur_ms, extra={"attempt": attempt})
        return True, data, None

    # Try deterministic repair on the repaired response as well
    det_ok, det_data, strategy, _ = repair_json_deterministically(repaired_raw)
    if det_ok:
        dur_ms = (time.time() - t0) * 1000
        backend_logger.info(f"LLM JSON repair SUCCEEDED with deterministic polish ({strategy}) on attempt {attempt}")
        log_stage("LLM_JSON_REPAIR_REQUEST", "SUCCESS", analysis_id=analysis_id, duration_ms=dur_ms, extra={
            "attempt": attempt,
            "strategy": strategy
        })
        return True, det_data, None

    dur_ms = (time.time() - t0) * 1000
    err_msg = f"Repaired response remained invalid JSON: {str(err)}"
    backend_logger.warning(f"LLM JSON repair attempt {attempt} returned invalid JSON: {err_msg}")
    log_stage("LLM_JSON_REPAIR_REQUEST", "FAILURE", message=err_msg, analysis_id=analysis_id, duration_ms=dur_ms, extra={"attempt": attempt})
    return False, None, err_msg


async def generate_dependency_graph(code_map: dict, analysis_id: str = None) -> dict:
    raw_files = code_map.get("files", {})
    file_count = len(raw_files) if isinstance(raw_files, (dict, list)) else 0
    code_map_json = json.dumps(code_map)
    code_map_bytes = len(code_map_json.encode("utf-8"))
    
    prompt = build_user_prompt(code_map)
    prompt_chars = len(prompt)

    # 1. Deterministic size check to prevent runaway / frozen requests
    MAX_PROMPT_CHARS = 120_000
    MAX_FILE_COUNT = 150
    if prompt_chars > MAX_PROMPT_CHARS or file_count > MAX_FILE_COUNT:
        err_msg = (
            f"Repository too large for single-request analysis (file count: {file_count}, "
            f"prompt size: {prompt_chars} chars). Please select a project or sub-directory with fewer files."
        )
        backend_logger.error(err_msg)
        log_error("LLM_REQUEST", "RepositorySizeLimitExceeded", err_msg, analysis_id=analysis_id)
        raise ValueError(err_msg)

    # 2. Required logging before LLM request (NO API KEY LENGTH LOGGED)
    cfg = get_provider_config(require_api_key=False)
    key_loaded = bool(cfg.api_key)

    backend_logger.info(f"CODE MAP FILE COUNT: {file_count}")
    backend_logger.info(f"PROMPT CHARACTER COUNT: {prompt_chars}")
    backend_logger.info(f"LLM PROVIDER: {cfg.name}")
    backend_logger.info(f"MODEL USED: {cfg.model}")
    backend_logger.info(f"BASE URL USED: {cfg.base_url}")
    backend_logger.info(f"API KEY LOADED: {key_loaded}")

    backend_logger.info("LLM REQUEST STARTED")
    log_stage("LLM_REQUEST", "START", analysis_id=analysis_id, extra={
        "file_count": file_count,
        "prompt_chars": prompt_chars,
        "provider": cfg.name,
        "model": cfg.model
    })

    metrics_info = {
        "file_count": file_count,
        "code_map_bytes": code_map_bytes,
        "prompt_chars": prompt_chars
    }

    initial_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt}
    ]

    # Step 1: Initial LLM Generation Call
    raw_content, status_code, dur_s = await _execute_openrouter_call(
        messages=initial_messages,
        max_tokens=4000,
        temperature=0.1,
        analysis_id=analysis_id,
        stage_name="LLM_REQUEST",
        attempt=1,
        metrics_info=metrics_info
    )

    log_stage("LLM_REQUEST", "SUCCESS", analysis_id=analysis_id, duration_ms=dur_s * 1000)

    # Step 2: Attempt standard parse & safe deterministic repair
    ok, parsed_graph, strategy, parse_diag = parse_and_recover_json(
        raw_content=raw_content,
        analysis_id=analysis_id,
        attempt_desc="initial_generation"
    )

    if ok:
        return parsed_graph

    # Step 3: LLM JSON Repair Request (if configured)
    repair_attempts = settings.llm_json_repair_attempts
    last_repair_error = parse_diag.get("initial_error", "Unknown JSON error")

    for attempt in range(1, repair_attempts + 1):
        repair_ok, repair_graph, repair_err = await request_llm_json_repair(
            malformed_content=raw_content,
            analysis_id=analysis_id,
            attempt=attempt
        )
        if repair_ok:
            log_stage("LLM_RESPONSE_PARSING", "SUCCESS", analysis_id=analysis_id, extra={
                "method": "LLM_JSON_REPAIR",
                "attempt": attempt
            })
            return repair_graph
        last_repair_error = repair_err or last_repair_error

    # Step 4: Complete Graph Generation Retry (if configured)
    retry_attempts = settings.llm_graph_retry_attempts
    last_retry_error = None

    for attempt in range(1, retry_attempts + 1):
        backend_logger.info(f"Starting complete graph-generation retry (attempt {attempt}) [analysis_id={analysis_id}]")
        log_stage("LLM_GRAPH_RETRY", "START", analysis_id=analysis_id, extra={"attempt": attempt})

        try:
            retry_raw, retry_status, retry_dur = await _execute_openrouter_call(
                messages=initial_messages,
                max_tokens=4000,
                temperature=0.1,
                analysis_id=analysis_id,
                stage_name="LLM_GRAPH_RETRY",
                attempt=attempt,
                metrics_info=metrics_info
            )

            # Attempt parse and deterministic repair on retry output
            retry_ok, retry_graph, retry_strat, retry_diag = parse_and_recover_json(
                raw_content=retry_raw,
                analysis_id=analysis_id,
                attempt_desc=f"graph_retry_{attempt}"
            )

            if retry_ok:
                backend_logger.info(f"Complete graph-generation retry SUCCEEDED on attempt {attempt}")
                log_stage("LLM_GRAPH_RETRY", "SUCCESS", analysis_id=analysis_id, duration_ms=retry_dur * 1000, extra={"attempt": attempt})
                return retry_graph

            # If retry was also malformed, optionally try 1 LLM repair request on retry output
            if repair_attempts > 0:
                rep_ret_ok, rep_ret_graph, _ = await request_llm_json_repair(
                    malformed_content=retry_raw,
                    analysis_id=analysis_id,
                    attempt=1
                )
                if rep_ret_ok:
                    backend_logger.info(f"Complete graph-generation retry SUCCEEDED with LLM repair on attempt {attempt}")
                    log_stage("LLM_GRAPH_RETRY", "SUCCESS", analysis_id=analysis_id, duration_ms=retry_dur * 1000, extra={
                        "attempt": attempt,
                        "method": "RETRY_WITH_REPAIR"
                    })
                    return rep_ret_graph

            last_retry_error = retry_diag.get("initial_error", "Invalid JSON in retry")
            log_stage("LLM_GRAPH_RETRY", "FAILURE", message=last_retry_error, analysis_id=analysis_id, extra={"attempt": attempt})

        except Exception as retry_exc:
            last_retry_error = str(retry_exc)
            backend_logger.error(f"Graph retry attempt {attempt} failed: {last_retry_error}")
            log_stage("LLM_GRAPH_RETRY", "FAILURE", message=last_retry_error, analysis_id=analysis_id, extra={"attempt": attempt})

    # Step 5: Controlled Final Failure with Detailed Recovery Diagnostics
    final_err_msg = (
        f"LLM JSON recovery failed: initial generation returned invalid JSON ({parse_diag.get('initial_error')}), "
        f"deterministic repair failed, {repair_attempts} LLM repair attempt(s) failed ({last_repair_error}), "
        f"and {retry_attempts} complete graph retry attempt(s) failed ({last_retry_error or 'no valid JSON returned'})."
    )
    backend_logger.error(final_err_msg)
    log_stage("LLM_RESPONSE_PARSING", "FAILURE", message=final_err_msg, analysis_id=analysis_id)
    log_error(
        stage="LLM_RESPONSE_PARSING",
        error_type="JSONRecoveryFailure",
        error_message=final_err_msg,
        analysis_id=analysis_id
    )
    raise ValueError(final_err_msg)


# Unified provider caller exports for project-wide LLM tasks
execute_hf_llm_call = _execute_openrouter_call
execute_llm_call = execute_provider_call

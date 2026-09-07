import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime

LOGS_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
BACKEND_LOG_FILE = LOGS_DIR / "backend.log"
ERRORS_LOG_FILE = LOGS_DIR / "errors.log"

try:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
except Exception as e:
    print(f"Failed to create logs directory: {e}", file=sys.stderr)

log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)
max_bytes = int(os.getenv("LOG_MAX_SIZE_MB", "10")) * 1024 * 1024
backup_count = int(os.getenv("LOG_BACKUP_COUNT", "5"))

class FlushingRotatingFileHandler(RotatingFileHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

# Base formatter
formatter = logging.Formatter(
    fmt="%(asctime)s %(levelname)-5s [BACKEND] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Backend logger
backend_logger = logging.getLogger("backend")
backend_logger.setLevel(log_level)
backend_logger.propagate = False

if not backend_logger.handlers:
    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(log_level)
    ch.setFormatter(formatter)
    backend_logger.addHandler(ch)

    # File handler for backend.log
    try:
        fh = FlushingRotatingFileHandler(
            str(BACKEND_LOG_FILE),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8"
        )
        fh.setLevel(log_level)
        fh.setFormatter(formatter)
        backend_logger.addHandler(fh)

        # Also attach to root logger so all app logs write to backend.log
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        root_logger.addHandler(fh)
    except Exception as e:
        print(f"Failed to setup backend log file handler: {e}", file=sys.stderr)

# Errors logger
errors_logger = logging.getLogger("errors")
errors_logger.setLevel(logging.ERROR)
errors_logger.propagate = False

if not errors_logger.handlers:
    try:
        efh = FlushingRotatingFileHandler(
            str(ERRORS_LOG_FILE),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8"
        )
        efh.setLevel(logging.ERROR)
        error_formatter = logging.Formatter(
            fmt="%(asctime)s [BACKEND] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        efh.setFormatter(error_formatter)
        errors_logger.addHandler(efh)
    except Exception as e:
        print(f"Failed to setup error log file handler: {e}", file=sys.stderr)


def log_stage(stage: str, status: str, message: str = "", analysis_id: str = None, duration_ms: float = None, extra: dict = None):
    """
    Log stage transitions in the required format:
    e.g. 2026-09-04 12:10:21 INFO [LLM_REQUEST] START analysis_id=...
    """
    aid_str = f"analysis_id={analysis_id}" if analysis_id else ""
    dur_str = f"duration_ms={duration_ms:.2f}" if duration_ms is not None else ""
    extra_parts = []
    if extra:
        for k, v in extra.items():
            extra_parts.append(f"{k}={v}")
    extra_str = " ".join(extra_parts)
    
    parts = [f"[{stage}]", status]
    if aid_str:
        parts.append(aid_str)
    if dur_str:
        parts.append(dur_str)
    if message:
        parts.append(message)
    if extra_str:
        parts.append(extra_str)
        
    line = " ".join(parts)
    if status == "FAILURE":
        backend_logger.error(line)
        errors_logger.error(line)
    else:
        backend_logger.info(line)


def log_error(stage: str, error_type: str, error_message: str, stack_trace: str = "", analysis_id: str = None, project_path: str = "", file_path: str = ""):
    """
    Log explicit error details to both backend.log and errors.log.
    """
    parts = [
        f"[{stage}]",
        f"error_type={error_type}",
        f'error_message="{error_message}"'
    ]
    if analysis_id:
        parts.append(f"analysis_id={analysis_id}")
    if project_path:
        parts.append(f'project_path="{project_path}"')
    if file_path:
        parts.append(f'file_path="{file_path}"')
    if stack_trace:
        parts.append(f"\nstack={stack_trace}")
        
    line = " ".join(parts)
    backend_logger.error(line)
    errors_logger.error(line)


def log_llm_metrics(
    analysis_id: str,
    model: str,
    file_count: int,
    code_map_bytes: int,
    prompt_chars: int,
    start_time: float,
    end_time: float,
    duration_s: float,
    status_code: int,
    success: bool,
    error_type: str = None,
    error_message: str = None
):
    """
    Required debug information for LLM requests.
    """
    dt_start = datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    dt_end = datetime.fromtimestamp(end_time).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    
    status_str = "SUCCESS" if success else "FAILURE"
    parts = [
        "[LLM_METRICS]",
        f"analysis_id={analysis_id}",
        f"model={model}",
        f"code_map_file_count={file_count}",
        f"code_map_bytes={code_map_bytes}",
        f"prompt_chars={prompt_chars}",
        f"start={dt_start}",
        f"end={dt_end}",
        f"duration_s={duration_s:.2f}",
        f"http_status={status_code}",
        f"result={status_str}"
    ]
    if error_type:
        parts.append(f"error_type={error_type}")
    if error_message:
        parts.append(f'error_message="{error_message}"')
        
    line = " ".join(parts)
    backend_logger.info(line)
    if not success:
        errors_logger.error(line)

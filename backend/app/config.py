from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings

BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BACKEND_DIR / ".env"

class Settings(BaseSettings):
    openrouter_api_key: Optional[str] = None
    openrouter_base_url: Optional[str] = None
    openrouter_model: Optional[str] = None

    hf_token: Optional[str] = None
    hf_model: Optional[str] = None
    hf_base_url: Optional[str] = None

    database_url: str
    port: int = 5000
    llm_connect_timeout: float = 30.0
    llm_read_timeout: float = 1800.0
    llm_write_timeout: float = 60.0
    llm_pool_timeout: float = 60.0
    analysis_timeout_seconds: int = 3600
    llm_json_repair_attempts: int = 1
    llm_graph_retry_attempts: int = 1

    class Config:
        env_file = ENV_PATH
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"

    def model_post_init(self, __context):
        # Resolve active API key
        if not self.openrouter_api_key and self.hf_token:
            self.openrouter_api_key = self.hf_token
        elif not self.openrouter_api_key:
            self.openrouter_api_key = ""

        # Resolve active Model
        if not self.openrouter_model and self.hf_model:
            self.openrouter_model = self.hf_model
        elif not self.openrouter_model:
            self.openrouter_model = "meta-llama/llama-3.3-70b-instruct"

        # Resolve active Base URL
        if not self.openrouter_base_url:
            if self.hf_token:
                self.openrouter_base_url = self.hf_base_url or "https://router.huggingface.co/v1"
            else:
                self.openrouter_base_url = "https://openrouter.ai/api/v1"

settings = Settings()
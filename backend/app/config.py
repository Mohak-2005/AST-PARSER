from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings

BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BACKEND_DIR / ".env"

class Settings(BaseSettings):
    llm_provider: str = "groq"
    llm_fallback_provider: Optional[str] = None

    # Hugging Face
    hf_token: Optional[str] = None
    hf_model: Optional[str] = None
    hf_base_url: Optional[str] = None

    # Groq
    groq_api_key: Optional[str] = None
    groq_model: Optional[str] = None
    groq_base_url: Optional[str] = None

    # Mistral
    mistral_api_key: Optional[str] = None
    mistral_model: Optional[str] = None
    mistral_base_url: Optional[str] = None

    # Gemini
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    gemini_base_url: Optional[str] = None

    # OpenRouter (legacy)
    openrouter_api_key: Optional[str] = None
    openrouter_base_url: Optional[str] = None
    openrouter_model: Optional[str] = None

    database_url: str
    port: int = 5000
    llm_connect_timeout: float = 30.0
    llm_read_timeout: float = 1800.0
    llm_write_timeout: float = 60.0
    llm_pool_timeout: float = 60.0
    analysis_timeout_seconds: int = 3600
    llm_json_repair_attempts: int = 1
    llm_graph_retry_attempts: int = 1
    analysis_output_dir: Optional[str] = "C:\\Users\\Mohak\\Desktop\\test"

    class Config:
        env_file = ENV_PATH
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"

    def model_post_init(self, __context):
        # Default provider base URLs and models if not explicitly set
        if not self.hf_base_url:
            self.hf_base_url = "https://router.huggingface.co/v1"
        if not self.hf_model:
            self.hf_model = "Qwen/Qwen3-Coder-30B-A3B-Instruct"

        if not self.groq_base_url:
            self.groq_base_url = "https://api.groq.com/openai/v1"
        if not self.groq_model:
            self.groq_model = "openai/gpt-oss-120b"

        if not self.mistral_base_url:
            self.mistral_base_url = "https://api.mistral.ai/v1"
        if not self.mistral_model:
            self.mistral_model = "mistral-small-latest"

        if not self.gemini_base_url:
            self.gemini_base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
        if not self.gemini_model:
            self.gemini_model = "gemini-2.5-flash"

        # Legacy backward-compatibility for any tests referencing openrouter_*
        if not self.openrouter_api_key and self.hf_token:
            self.openrouter_api_key = self.hf_token
        elif not self.openrouter_api_key:
            self.openrouter_api_key = ""

        if not self.openrouter_model and self.hf_model:
            self.openrouter_model = self.hf_model
        elif not self.openrouter_model:
            self.openrouter_model = "meta-llama/llama-3.3-70b-instruct"

        if not self.openrouter_base_url:
            self.openrouter_base_url = self.hf_base_url or "https://router.huggingface.co/v1"

settings = Settings()
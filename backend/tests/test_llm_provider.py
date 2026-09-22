import unittest
from unittest.mock import patch, AsyncMock, MagicMock
import httpx
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.llm_provider import (
    get_active_provider_name,
    get_provider_config,
    execute_provider_call,
    ProviderConfig
)
from app.config import settings


class TestLlmProvider(unittest.IsolatedAsyncioTestCase):
    def test_get_active_provider_name(self):
        self.assertEqual(get_active_provider_name("groq"), "groq")
        self.assertEqual(get_active_provider_name("Groq"), "groq")
        self.assertEqual(get_active_provider_name("HF"), "hf")
        self.assertEqual(get_active_provider_name("huggingface"), "hf")
        self.assertEqual(get_active_provider_name("Hugging_Face"), "hf")
        self.assertEqual(get_active_provider_name("Mistral"), "mistral")
        self.assertEqual(get_active_provider_name("Gemini"), "gemini")

    def test_get_provider_config_supported(self):
        with patch.object(settings, "groq_api_key", "test_groq_key"), \
             patch.object(settings, "groq_model", "test_groq_model"), \
             patch.object(settings, "groq_base_url", "https://api.groq.com/openai/v1"):
            cfg = get_provider_config("groq")
            self.assertEqual(cfg.name, "groq")
            self.assertEqual(cfg.model, "test_groq_model")
            self.assertEqual(cfg.base_url, "https://api.groq.com/openai/v1")
            self.assertEqual(cfg.api_key, "test_groq_key")

        with patch.object(settings, "hf_token", "test_hf_token"), \
             patch.object(settings, "hf_model", "test_hf_model"), \
             patch.object(settings, "hf_base_url", "https://router.huggingface.co/v1"):
            cfg = get_provider_config("hf")
            self.assertEqual(cfg.name, "hf")
            self.assertEqual(cfg.model, "test_hf_model")
            self.assertEqual(cfg.base_url, "https://router.huggingface.co/v1")
            self.assertEqual(cfg.api_key, "test_hf_token")

        with patch.object(settings, "mistral_api_key", "test_mistral_key"), \
             patch.object(settings, "mistral_model", "test_mistral_model"), \
             patch.object(settings, "mistral_base_url", "https://api.mistral.ai/v1"):
            cfg = get_provider_config("mistral")
            self.assertEqual(cfg.name, "mistral")
            self.assertEqual(cfg.model, "test_mistral_model")
            self.assertEqual(cfg.base_url, "https://api.mistral.ai/v1")
            self.assertEqual(cfg.api_key, "test_mistral_key")

        with patch.object(settings, "gemini_api_key", "test_gemini_key"), \
             patch.object(settings, "gemini_model", "test_gemini_model"), \
             patch.object(settings, "gemini_base_url", "https://generativelanguage.googleapis.com/v1beta/openai"):
            cfg = get_provider_config("gemini")
            self.assertEqual(cfg.name, "gemini")
            self.assertEqual(cfg.model, "test_gemini_model")
            self.assertEqual(cfg.base_url, "https://generativelanguage.googleapis.com/v1beta/openai")
            self.assertEqual(cfg.api_key, "test_gemini_key")

    def test_missing_api_key_validation(self):
        with patch.object(settings, "groq_api_key", ""):
            with self.assertRaises(ValueError) as ctx:
                get_provider_config("groq", require_api_key=True)
            self.assertIn("Missing GROQ_API_KEY", str(ctx.exception))

            # When require_api_key=False, does not raise
            cfg = get_provider_config("groq", require_api_key=False)
            self.assertEqual(cfg.api_key, "")

    def test_unsupported_provider_raises(self):
        with self.assertRaises(ValueError) as ctx:
            get_provider_config("nonexistent_provider")
        self.assertIn("Unsupported LLM provider", str(ctx.exception))

    async def test_execute_provider_call_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "{\"testCases\": []}"}}]
        }
        mock_response.raise_for_status.return_value = None

        with patch.object(settings, "groq_api_key", "mock_key"), \
             patch.object(settings, "groq_model", "openai/gpt-oss-120b"), \
             patch.object(settings, "groq_base_url", "https://api.groq.com/openai/v1"), \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            raw, status, dur = await execute_provider_call(
                messages=[{"role": "user", "content": "Hello"}],
                provider="groq"
            )

            self.assertEqual(raw, "{\"testCases\": []}")
            self.assertEqual(status, 200)
            self.assertGreaterEqual(dur, 0.0)

            # Verify POST target URL and auth headers
            call_args, call_kwargs = mock_post.call_args
            self.assertEqual(call_args[0], "https://api.groq.com/openai/v1/chat/completions")
            self.assertEqual(call_kwargs["headers"]["Authorization"], "Bearer mock_key")
            self.assertEqual(call_kwargs["json"]["model"], "openai/gpt-oss-120b")

    async def test_execute_provider_call_fallback(self):
        mock_groq_resp = MagicMock()
        mock_groq_resp.status_code = 429
        mock_groq_resp.text = "Rate limit exceeded"

        mock_gemini_resp = MagicMock()
        mock_gemini_resp.status_code = 200
        mock_gemini_resp.json.return_value = {
            "choices": [{"message": {"content": "Fallback success"}}]
        }
        mock_gemini_resp.raise_for_status.return_value = None

        request_dummy = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        http_status_err = httpx.HTTPStatusError("429 Rate limit", request=request_dummy, response=mock_groq_resp)

        with patch.object(settings, "groq_api_key", "groq_key"), \
             patch.object(settings, "gemini_api_key", "gemini_key"), \
             patch.object(settings, "llm_fallback_provider", "gemini"), \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            # First call (Groq) fails with HTTPStatusError, second call (Gemini) succeeds
            mock_post.side_effect = [http_status_err, mock_gemini_resp]

            raw, status, dur = await execute_provider_call(
                messages=[{"role": "user", "content": "Hello"}],
                provider="groq"
            )

            self.assertEqual(raw, "Fallback success")
            self.assertEqual(status, 200)
            self.assertEqual(mock_post.call_count, 2)


if __name__ == "__main__":
    unittest.main()

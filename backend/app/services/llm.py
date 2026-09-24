"""
LLM client for OpenRouter (OpenAI-compatible /chat/completions).

Design goals (per project spec):
- No hardcoded API key. Reads OPENROUTER_API_KEY / OPENROUTER_MODEL from env.
- If no key is configured, `is_available` is False and callers MUST use the
  deterministic fallback path (see services/planner.py). We never pretend
  the fallback is an LLM call.
- Keeps the number of LLM calls low: callers only invoke this for task
  understanding / planning / re-planning / observation interpretation, never
  per low-level browser action.
- Handles rate-limit / error responses gracefully so the caller can fall
  back to deterministic behaviour instead of crashing.
"""
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("agent.llm")


class LLMError(Exception):
    def __init__(self, message: str, category: str = "api_error"):
        super().__init__(message)
        self.category = category


class LLMClient:
    def __init__(self):
        self.api_key = settings.OPENROUTER_API_KEY
        self.model = settings.OPENROUTER_MODEL
        self.base_url = settings.OPENROUTER_BASE_URL

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """
        Calls OpenRouter's chat completions endpoint asking for a strict JSON
        response, and parses it. Raises LLMError on any failure (network,
        rate limit, malformed JSON) so the caller can fall back deterministically.
        """
        if not self.is_available:
            raise LLMError("No OPENROUTER_API_KEY configured", category="no_key")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/BrowserAutomationAgent",
            "X-Title": "BrowserAutomationAgent",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
        except httpx.RequestError as e:
            raise LLMError(f"Network error calling OpenRouter: {e}", category="network_error")

        if resp.status_code == 429:
            raise LLMError("OpenRouter rate limit reached", category="rate_limit")
        if resp.status_code >= 400:
            raise LLMError(f"OpenRouter returned HTTP {resp.status_code}: {resp.text[:300]}",
                            category="api_error")

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise LLMError(f"Malformed OpenRouter response: {e}", category="malformed_response")

        content = content.strip()
        # Strip markdown code fences if the model wrapped its JSON.
        if content.startswith("```"):
            content = content.strip("`")
            if content.lower().startswith("json"):
                content = content[4:]
            content = content.strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try to salvage a JSON object embedded in extra text.
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(content[start:end + 1])
                except json.JSONDecodeError:
                    pass
            raise LLMError(f"Could not parse JSON from model output: {content[:300]}",
                            category="malformed_response")


llm_client = LLMClient()

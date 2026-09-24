"""
Central configuration for the Browser Automation Agent.

All values are read from environment variables (via a .env file loaded by
python-dotenv-style manual parsing, kept dependency-free). Nothing here is
hardcoded that shouldn't be - in particular, no API keys ever have a
fallback literal value.
"""
import os
from pathlib import Path
from typing import List

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so we don't need an extra dependency."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not value:
            continue  # Blank .env value means "use the built-in default", not "".
        # Do not override a value already set in the real environment.
        os.environ.setdefault(key, value)


_load_dotenv(PROJECT_ROOT / ".env")


def _split_csv(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


class Settings:
    # --- LLM / OpenRouter ---
    OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "").strip()
    OPENROUTER_MODEL: str = os.environ.get("OPENROUTER_MODEL", "openrouter/free").strip()
    OPENROUTER_BASE_URL: str = os.environ.get(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
    ).strip()

    @property
    def LLM_MODE(self) -> str:
        return "openrouter" if self.OPENROUTER_API_KEY else "deterministic_fallback"

    # --- Database ---
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL", f"sqlite:///{BACKEND_DIR / 'agent.db'}"
    )

    # --- Security / domain restriction ---
    ALLOWED_DOMAINS: List[str] = _split_csv(
        os.environ.get("ALLOWED_DOMAINS", "localhost,127.0.0.1")
    )

    # --- Demo site ---
    DEMO_SITE_BASE_URL: str = os.environ.get(
        "DEMO_SITE_BASE_URL", "http://127.0.0.1:8001"
    ).strip()
    DEMO_SITE_USERNAME: str = os.environ.get("DEMO_SITE_USERNAME", "demo")
    DEMO_SITE_PASSWORD: str = os.environ.get("DEMO_SITE_PASSWORD", "demo1234")

    # --- Agent behaviour ---
    MAX_RETRIES_PER_ACTION: int = int(os.environ.get("MAX_RETRIES_PER_ACTION", "2"))
    MAX_REPLANS_PER_TASK: int = int(os.environ.get("MAX_REPLANS_PER_TASK", "2"))
    ACTION_TIMEOUT_MS: int = int(os.environ.get("ACTION_TIMEOUT_MS", "5000"))
    PLAYWRIGHT_HEADLESS: bool = os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() != "false"

    # --- CORS ---
    FRONTEND_ORIGIN: str = os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173")


settings = Settings()

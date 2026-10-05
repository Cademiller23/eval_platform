from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def load_dotenv_file(path: Path | str = ".env") -> None:
    """Minimal .env loader so HF_TOKEN / MODAL_TOKEN_* work without extra dependencies.

    Real environment variables always win over the file.
    """
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("'\"")
        if key and val and key not in os.environ:
            os.environ[key] = val


load_dotenv_file()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVAL_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"   # no auth: only expose on a network you trust (EVAL_HOST=0.0.0.0)
    port: int = 8000
    data_dir: Path = Path("./data")
    modal_app: str = "coherence-eval-serving"
    modal_cls: str = "ModelServer"
    default_provider: str | None = None
    # Seconds a cold start may take before we give up (weights download + engine boot).
    provision_timeout_s: int = 45 * 60
    # Max concurrent requests while running the domain suites.
    suite_concurrency: int = 4
    # Needle-in-haystack context size (approx. tokens).
    haystack_tokens: int = 3000

    @property
    def runs_dir(self) -> Path:
        p = self.data_dir / "runs"
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


def hf_token() -> str | None:
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or None


def modal_credentials_present() -> bool:
    if os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"):
        return True
    return (Path.home() / ".modal.toml").exists()


def modal_sdk_installed() -> bool:
    try:
        import modal  # noqa: F401
        return True
    except Exception:
        return False

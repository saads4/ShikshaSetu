"""Settings loaded from the environment (and a repo-root .env file, if present)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER_MARK = "CHANGE_ME"


def _load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #")[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


class Settings(BaseModel):
    app_env: str = "local"
    demo_mode: bool = True
    database_url: str
    jwt_secret: str
    jwt_ttl_minutes: int = 60
    session_idle_minutes: int = 30
    master_key: str
    identity_hmac_key: str
    identity_hmac_key_version: int = 1
    seed_profile: str = "standard"
    seed_rng: int = 20261026
    seed_anchor_date: str = ""
    seed_demo_password: str = "1234"
    mock_services_url: str = "http://127.0.0.1:8002"
    registry_timeout_ms: int = 2000
    registry_breaker_threshold: int = 5
    registry_breaker_reset_s: int = 30
    sms_fixed_otp: str = ""
    worker_mode: str = "process"
    storage_dir: str = "./data/objects"
    worm_dir: str = "./data/worm"
    keys_dir: str = "./data/keys"
    cors_origins: str = "http://localhost:5173"
    log_level: str = "INFO"

    def production_problems(self) -> list[str]:
        """REQ-AUTH-07: reasons the API must refuse to start in production."""
        problems = []
        if self.demo_mode:
            problems.append("DEMO_MODE=true is not allowed in production")
        for name in ("jwt_secret", "master_key", "identity_hmac_key"):
            if PLACEHOLDER_MARK in getattr(self, name) or len(getattr(self, name)) < 32:
                problems.append(f"{name.upper()} is a placeholder or too short")
        return problems


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable {name} (see .env.example)")
    return value


@lru_cache
def get_settings() -> Settings:
    _load_dotenv()
    return Settings(
        app_env=_env("APP_ENV", "local"),
        demo_mode=_env("DEMO_MODE", "true").lower() == "true",
        database_url=_env("DATABASE_URL"),
        jwt_secret=_env("JWT_SECRET"),
        jwt_ttl_minutes=int(_env("JWT_TTL_MINUTES", "60")),
        session_idle_minutes=int(_env("SESSION_IDLE_MINUTES", "30")),
        master_key=_env("MASTER_KEY"),
        identity_hmac_key=_env("IDENTITY_HMAC_KEY"),
        identity_hmac_key_version=int(_env("IDENTITY_HMAC_KEY_VERSION", "1")),
        seed_profile=_env("SEED_PROFILE", "standard"),
        seed_rng=int(_env("SEED_RNG", "20261026")),
        seed_anchor_date=_env("SEED_ANCHOR_DATE", ""),
        seed_demo_password=_env("SEED_DEMO_PASSWORD", "1234"),
        mock_services_url=_env("MOCK_SERVICES_URL", "http://127.0.0.1:8002"),
        registry_timeout_ms=int(_env("REGISTRY_TIMEOUT_MS", "2000")),
        registry_breaker_threshold=int(_env("REGISTRY_BREAKER_THRESHOLD", "5")),
        registry_breaker_reset_s=int(_env("REGISTRY_BREAKER_RESET_S", "30")),
        sms_fixed_otp=_env("SMS_FIXED_OTP", ""),
        worker_mode=_env("WORKER_MODE", "process"),
        storage_dir=_env("STORAGE_DIR", "./data/objects"),
        worm_dir=_env("WORM_DIR", "./data/worm"),
        keys_dir=_env("KEYS_DIR", "./data/keys"),
        cors_origins=_env("CORS_ORIGINS", "http://localhost:5173"),
        log_level=_env("LOG_LEVEL", "INFO"),
    )

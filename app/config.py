"""Application configuration using environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _split_origins(value: str | None) -> List[str]:
    if not value:
        return ["*"]
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    port: int = int(os.getenv("PORT", "5000"))
    app_name: str = os.getenv("APP_NAME", "NirwanaStays-AI")
    brand_owner: str = os.getenv("BRAND_OWNER", "Tartaria Technologies")
    cors_origins: List[str] = field(default_factory=lambda: _split_origins(os.getenv("CORS_ORIGINS")))

    sarvam_base_url: str = os.getenv("SARVAM_BASE_URL", "https://api.sarvam.ai/v1")
    sarvam_api_key: str | None = os.getenv("SARVAM_API_KEY")
    sarvam_model: str = os.getenv("SARVAM_MODEL", "sarvam-m")

    openrouter_api_key: str | None = os.getenv("OPENROUTER_API_KEY")
    openrouter_base_url: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    openrouter_agent_model: str = os.getenv("OPENROUTER_AGENT_MODEL", "minimax/minimax-m2:free")

    mcp_token: str | None = os.getenv("MCP_TOKEN")

    controller_timeout: float = float(os.getenv("CONTROLLER_TIMEOUT_SECONDS", "8"))

    # Backend API for MCP tools
    backend_base_url: str = os.getenv("BACKEND_BASE_URL", "http://localhost:4000")
    backend_api_token: str | None = os.getenv("BACKEND_API_TOKEN")

    # Summarization controls
    summary_max_tokens: int = int(os.getenv("SUMMARY_MAX_TOKENS", "512"))
    summary_style: str = os.getenv("SUMMARY_STYLE", "concise, polite, professional")


settings = Settings()


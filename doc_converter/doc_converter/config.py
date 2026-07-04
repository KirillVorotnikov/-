"""Application settings loaded from environment and CLI overrides."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

VlmBackendChoice = Literal["openai", "anthropic", "local", "mock", "off"]
OutputFormat = Literal["md", "html", "both"]


class Settings(BaseSettings):
    """Runtime configuration for doc-convert."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    vlm_backend: VlmBackendChoice = Field(default="off", alias="VLM_BACKEND")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    ocr_lang: str = Field(default="ru", alias="OCR_LANG")
    vlm_timeout_sec: int = Field(default=30, alias="VLM_TIMEOUT_SEC")
    openai_vlm_model: str = Field(default="gpt-4o-mini", alias="OPENAI_VLM_MODEL")
    anthropic_vlm_model: str = Field(
        default="claude-3-5-sonnet-20241022",
        alias="ANTHROPIC_VLM_MODEL",
    )

    output_format: OutputFormat = "md"
    output_dir: str = "./out"
    academic: bool = False
    recursive: bool = False
    grobid_server: str | None = Field(default=None, alias="GROBID_SERVER")

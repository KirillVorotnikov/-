"""Abstract vision-language model backend."""

from abc import ABC, abstractmethod
from pathlib import Path


class VLMBackend(ABC):
    """Send image + prompt to a vision-language model."""

    @abstractmethod
    def ask(self, image_path: Path, prompt: str, expect_json: bool = False) -> str:
        """Return model text response (JSON string when *expect_json* is True)."""

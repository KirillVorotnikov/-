"""Abstract base class for format-specific converters."""

from abc import ABC, abstractmethod
from pathlib import Path

from doc_converter.ir import ParsedDocument


class BaseConverter(ABC):
    """Parse a source file into the shared intermediate representation."""

    @abstractmethod
    def parse(self, path: Path) -> ParsedDocument:
        """Extract structured content from *path*."""

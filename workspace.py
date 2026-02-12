"""Workspace manager — handles saving generated files."""

import logging
from pathlib import Path

from .memory import Memory

logger = logging.getLogger("pilobster.workspace")


class Workspace:
    """Manages the local workspace folder for generated code and files."""

    def __init__(self, path: str, memory: Memory):
        self.path = Path(path)
        self.memory = memory
        self.path.mkdir(parents=True, exist_ok=True)

    async def save_file(self, filename: str, content: str) -> Path:
        """Save content to a file in the workspace.

        Sanitises the filename to prevent directory traversal.
        """
        # Sanitise: strip path separators, keep only the filename
        safe_name = Path(filename).name
        if not safe_name:
            safe_name = "untitled.txt"

        filepath = self.path / safe_name

        # Don't overwrite — add a number suffix if the file exists
        if filepath.exists():
            stem = filepath.stem
            suffix = filepath.suffix
            counter = 1
            while filepath.exists():
                filepath = self.path / f"{stem}_{counter}{suffix}"
                counter += 1

        filepath.write_text(content)
        await self.memory.log_file(filepath.name, f"Generated file: {safe_name}")
        logger.info(f"Saved file: {filepath}")
        return filepath

    def list_files(self) -> list[dict]:
        """List all files in the workspace."""
        files = []
        for item in sorted(self.path.iterdir()):
            if item.is_file():
                stat = item.stat()
                files.append(
                    {
                        "name": item.name,
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                    }
                )
        return files

    def read_file(self, filename: str) -> str | None:
        """Read a file from the workspace."""
        safe_name = Path(filename).name
        filepath = self.path / safe_name
        if filepath.exists() and filepath.is_file():
            return filepath.read_text()
        return None

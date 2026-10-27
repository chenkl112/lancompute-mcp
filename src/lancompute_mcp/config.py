"""Per-account workspace and durable job storage."""

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Readers see either the old complete document or the new complete document."""
    fd, temporary = tempfile.mkstemp(prefix=".status-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Settings:
    workspace: Path
    state_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            workspace=Path(
                os.environ.get("LANCOMPUTE_WORKSPACE", str(Path.home() / "lancompute-workspace"))
            )
            .expanduser()
            .resolve(),
            state_dir=Path(
                os.environ.get("LANCOMPUTE_STATE_DIR", str(Path.home() / ".lancompute-mcp"))
            )
            .expanduser()
            .resolve(),
        )

    def prepare(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    @property
    def jobs_dir(self) -> Path:
        return self.state_dir / "jobs"

    def workspace_path(self, relative: str) -> Path:
        """Constrain file tools and job working directories, including symlinks."""
        candidate = (self.workspace / relative).resolve()
        if not candidate.is_relative_to(self.workspace.resolve()):
            raise ValueError("Path must be inside LANCOMPUTE_WORKSPACE.")
        return candidate

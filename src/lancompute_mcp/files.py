"""Bounded file transfers so the client can send scripts and retrieve results."""

import base64
import binascii
from typing import Any

from .config import Settings

MAX_WRITE_BYTES = 4 * 1024 * 1024
MAX_READ_BYTES = 128 * 1024


def write_file(
    settings: Settings,
    path: str,
    content: str,
    encoding: str = "utf-8",
    overwrite: bool = False,
) -> dict[str, Any]:
    if encoding == "utf-8":
        data = content.encode("utf-8")
    elif encoding == "base64":
        try:
            data = base64.b64decode(content, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("Invalid base64 content.") from error
    else:
        raise ValueError("encoding must be 'utf-8' or 'base64'.")
    if len(data) > MAX_WRITE_BYTES:
        raise ValueError("File exceeds the 4 MiB write limit; use SSH/SCP for larger files.")
    target = settings.workspace_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb" if overwrite else "xb") as stream:
        stream.write(data)
    return {"path": str(target.relative_to(settings.workspace)), "bytes_written": len(data)}


def read_file(
    settings: Settings,
    path: str,
    offset: int = 0,
    max_bytes: int = 65536,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    if offset < 0 or not 1 <= max_bytes <= MAX_READ_BYTES:
        raise ValueError("offset must be >= 0; max_bytes must be between 1 and 131072.")
    if encoding not in {"utf-8", "base64"}:
        raise ValueError("encoding must be 'utf-8' or 'base64'.")
    source = settings.workspace_path(path)
    with source.open("rb") as stream:
        stream.seek(offset)
        data = stream.read(max_bytes)
        total_bytes = source.stat().st_size
    return {
        "path": str(source.relative_to(settings.workspace)),
        "encoding": encoding,
        "content": (
            base64.b64encode(data).decode("ascii")
            if encoding == "base64"
            else data.decode("utf-8", errors="replace")
        ),
        "total_bytes": total_bytes,
        "next_offset": offset + len(data),
        "eof": offset + len(data) >= total_bytes,
    }

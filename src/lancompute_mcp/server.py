"""MCP stdio tools; SSH supplies transport encryption and authentication."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import files, hardware, jobs
from .config import Settings


def create_server(settings: Settings | None = None) -> FastMCP:
    settings = settings or Settings.from_env()
    settings.prepare()
    mcp = FastMCP(
        "lancompute-mcp",
        instructions=(
            "Execute compute tasks on this host. First inspect get_system_info; "
            "upload scripts with write_file, submit using start_job, and poll get_job. "
            "Use the returned python_executable to run Python with server dependencies. "
            "Jobs survive MCP disconnection. File paths and cwd are workspace-relative. "
            "Commands execute with the host account's full permissions. "
            "GPU workloads require an appropriate driver and framework on the host."
        ),
    )

    @mcp.tool()
    def get_system_info() -> dict[str, Any]:
        """Report host OS, CPU, available RAM/disk, Python path, and NVIDIA GPUs if available."""
        return hardware.system_info(settings)

    @mcp.tool()
    def start_job(
        command: str,
        cwd: str = ".",
        shell: str = "auto",
        timeout_seconds: int = 3600,
    ) -> dict[str, Any]:
        """Submit a background command; return immediately with a job_id.

        auto selects PowerShell on Windows and bash elsewhere. cwd is relative
        to the workspace and must already exist. timeout_seconds is 1..604800.
        Use get_job to check completion and read output; avoid starting duplicates.
        """
        return jobs.start_job(settings, command, cwd, shell, timeout_seconds)

    @mcp.tool()
    def get_job(job_id: str, tail_bytes: int = 16384) -> dict[str, Any]:
        """Get durable job status, exit code, and the last bytes of stdout/stderr.

        Terminal states: succeeded, failed, cancelled, timed_out, interrupted.
        tail_bytes is 1..131072 per stream. Logs may be incomplete while running.
        """
        return jobs.get_job(settings, job_id, tail_bytes)

    @mcp.tool()
    def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
        """List the newest jobs with their metadata and status (limit 1..100)."""
        return jobs.list_jobs(settings, limit)

    @mcp.tool()
    def cancel_job(job_id: str) -> dict[str, Any]:
        """Request cancellation of a job and its process tree; poll get_job for final status."""
        return jobs.cancel_job(settings, job_id)

    @mcp.tool()
    def write_file(
        path: str, content: str, encoding: str = "utf-8", overwrite: bool = False
    ) -> dict[str, Any]:
        """Upload a file within the workspace (up to 4 MiB).

        encoding: utf-8 text or base64 binary. Parent directories are created.
        Existing files require overwrite=true. Use SCP for larger files.
        """
        return files.write_file(settings, path, content, encoding, overwrite)

    @mcp.tool()
    def read_file(
        path: str, offset: int = 0, max_bytes: int = 65536, encoding: str = "utf-8"
    ) -> dict[str, Any]:
        """Read a workspace file, with byte offset and up to 128 KiB per call.

        Use base64 for binary files or exact byte reconstruction of chunked UTF-8.
        Continue at next_offset until eof is true.
        """
        return files.read_file(settings, path, offset, max_bytes, encoding)

    return mcp


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()

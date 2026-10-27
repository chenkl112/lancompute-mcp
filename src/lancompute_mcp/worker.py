"""A separate supervisor per job; survives MCP/SSH session disconnection."""

import base64
import contextlib
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil

from .config import read_json, utc_now, write_json


def shell_arguments(command: str, shell: str) -> list[str]:
    if shell == "auto":
        shell = "powershell" if os.name == "nt" else "bash"
    if shell == "powershell":
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if not executable:
            raise ValueError("PowerShell not found on the compute host.")
        script = (
            "$ErrorActionPreference = 'Stop'; "
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
            "$OutputEncoding = [Console]::OutputEncoding; "
            "$global:LASTEXITCODE = 0; & {\n"
            + command
            + "\n}; $ok = $?; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; "
            "if (-not $ok) { exit 1 }; exit 0"
        )
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
    if shell == "bash":
        executable = shutil.which("bash")
        if not executable:
            raise ValueError("bash not found on the compute host.")
        return [executable, "-c", command]
    if shell == "cmd" and os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command]
    raise ValueError(f"Unsupported shell on this host: {shell}")


def stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Stop descendants as well as the shell wrapper."""
    if os.name != "nt":
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        # The shell may exit before grandchildren that ignore SIGTERM.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    else:
        try:
            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
        except psutil.NoSuchProcess:
            return
        targets = list(reversed(children)) + [parent]
        for target in targets:
            with contextlib.suppress(psutil.NoSuchProcess):
                target.terminate()
        _, alive = psutil.wait_procs(targets, timeout=2)
        for target in alive:
            with contextlib.suppress(psutil.NoSuchProcess):
                target.kill()
    process.wait(timeout=5)


def run(directory: Path) -> int:
    metadata = read_json(directory / "job.json")
    worker = psutil.Process()
    status: dict[str, Any] = {
        "status": "running",
        "started_at": utc_now(),
        "worker_pid": worker.pid,
        "worker_create_time": worker.create_time(),
    }
    process: subprocess.Popen[bytes] | None = None
    write_json(directory / "status.json", status)
    try:
        if (directory / "cancel").exists():
            status["status"] = "cancelled"
        else:
            arguments = shell_arguments(metadata["command"], metadata["shell"])
            kwargs: dict[str, Any] = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            else:
                kwargs["start_new_session"] = True
            environment = os.environ.copy()
            environment["PYTHONIOENCODING"] = "utf-8"
            environment["PYTHONUNBUFFERED"] = "1"
            started = time.monotonic()
            with (directory / "stdout.log").open("wb") as stdout:
                with (directory / "stderr.log").open("wb") as stderr:
                    process = subprocess.Popen(
                        arguments,
                        cwd=metadata["cwd"],
                        stdin=subprocess.DEVNULL,
                        stdout=stdout,
                        stderr=stderr,
                        env=environment,
                        **kwargs,
                    )
                    status["pid"] = process.pid
                    write_json(directory / "status.json", status)
                    while process.poll() is None:
                        if (directory / "cancel").exists():
                            status["status"] = "cancelled"
                            stop_process_tree(process)
                            break
                        if time.monotonic() - started >= metadata["timeout_seconds"]:
                            status["status"] = "timed_out"
                            stop_process_tree(process)
                            break
                        time.sleep(0.1)
                    status["exit_code"] = process.wait()
                    if status["status"] == "running":
                        status["status"] = "succeeded" if status["exit_code"] == 0 else "failed"
    except BaseException as error:
        status.update(status="failed", error=f"{type(error).__name__}: {error}")
        if process is not None and process.poll() is None:
            with contextlib.suppress(Exception):
                stop_process_tree(process)
    finally:
        status["finished_at"] = utc_now()
        write_json(directory / "status.json", status)
    return 0


if __name__ == "__main__":
    raise SystemExit(run(Path(sys.argv[1]).resolve()))

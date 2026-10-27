"""Start detached workers and inspect their durable status/log files."""

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import psutil

from .config import Settings, read_json, utc_now, write_json

ACTIVE_STATES = {"queued", "running"}
JOB_ID = re.compile(r"[a-f0-9]{32}\Z")


def _launch_worker(directory: Path, working_dir: Path) -> dict[str, Any]:
    arguments = [sys.executable, "-m", "lancompute_mcp.worker", str(directory)]
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        # Detaching the console alone does not escape an SSH session's Job Object.
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_BREAKAWAY_FROM_JOB
        )
    else:
        kwargs["start_new_session"] = True
    try:
        with (directory / "worker.log").open("ab") as log:
            process = subprocess.Popen(
                arguments,
                cwd=working_dir,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                close_fds=True,
                **kwargs,
            )
            worker_pid = process.pid
    except OSError as error:
        if os.name != "nt" or getattr(error, "winerror", None) != 5:
            raise
        # Windows OpenSSH can forbid breakaway. The local CIM provider launches
        # the worker outside the SSH process tree, under the calling account.
        from .worker import shell_arguments

        command_line = subprocess.list2cmdline(arguments).replace("'", "''")
        current_dir = str(working_dir).replace("'", "''")
        script = (
            "$startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly "
            "-Property @{ShowWindow=[uint16]0; CreateFlags=[uint32]0x08000000}; "
            "$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
            "-Arguments @{CommandLine='"
            + command_line
            + "'; CurrentDirectory='"
            + current_dir
            + "'; ProcessStartupInformation=$startup}; "
            "if ($result.ReturnValue -ne 0) { "
            "throw ('CIM launch failed: ' + $result.ReturnValue) }; "
            "$result.ProcessId"
        )
        result = subprocess.run(
            shell_arguments(script, "powershell"),
            capture_output=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(f"Windows could not detach the worker: {result.stderr}") from error
        worker_pid = int(result.stdout.strip().splitlines()[-1])
    try:
        create_time = psutil.Process(worker_pid).create_time()
    except psutil.NoSuchProcess:
        create_time = 0
    return {"worker_pid": worker_pid, "worker_create_time": create_time}


def job_path(settings: Settings, job_id: str) -> Path:
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid job_id.")
    directory = settings.jobs_dir / job_id
    if not (directory / "job.json").is_file():
        raise ValueError(f"Job not found: {job_id}")
    return directory


def start_job(
    settings: Settings,
    command: str,
    cwd: str = ".",
    shell: str = "auto",
    timeout_seconds: int = 3600,
) -> dict[str, Any]:
    if not command.strip() or len(command.encode("utf-8")) > 10000:
        raise ValueError("command must be non-empty and at most 10000 UTF-8 bytes.")
    if shell not in {"auto", "powershell", "bash", "cmd"}:
        raise ValueError("shell must be auto, powershell, bash, or cmd.")
    if not 1 <= timeout_seconds <= 604800:
        raise ValueError("timeout_seconds must be between 1 and 604800 (7 days).")
    working_dir = settings.workspace_path(cwd)
    if not working_dir.is_dir():
        raise ValueError("cwd must be an existing directory inside LANCOMPUTE_WORKSPACE.")
    # Validate shell availability before reporting successful submission.
    from .worker import shell_arguments

    shell_arguments(command, shell)
    job_id = uuid.uuid4().hex
    directory = settings.jobs_dir / job_id
    directory.mkdir()
    metadata = {
        "job_id": job_id,
        "command": command,
        "cwd": str(working_dir),
        "shell": shell,
        "timeout_seconds": timeout_seconds,
        "submitted_at": utc_now(),
    }
    write_json(directory / "job.json", metadata)
    write_json(directory / "status.json", {"status": "queued"})
    try:
        launch = _launch_worker(directory, working_dir)
        write_json(directory / "launch.json", launch)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        write_json(
            directory / "status.json",
            {"status": "failed", "error": str(error), "finished_at": utc_now()},
        )
        raise
    return {"job_id": job_id, "status": "queued", "submitted_at": metadata["submitted_at"]}


def _status(directory: Path) -> dict[str, Any]:
    status = read_json(directory / "status.json")
    process_info = status
    if status["status"] == "queued" and (directory / "launch.json").is_file():
        process_info = read_json(directory / "launch.json")
    if status["status"] in ACTIVE_STATES and "worker_pid" in process_info:
        try:
            process = psutil.Process(process_info["worker_pid"])
            alive = (
                abs(process.create_time() - process_info["worker_create_time"]) < 0.01
                and process.status() != psutil.STATUS_ZOMBIE
            )
        except psutil.Error:
            alive = False
        if not alive:
            # Re-read to avoid replacing a just-completed worker's final state.
            status = read_json(directory / "status.json")
            if status["status"] in ACTIVE_STATES:
                status = {**status, "status": "interrupted", "error": "Worker is no longer alive."}
    return status


def _tail(path: Path, max_bytes: int) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            length = stream.tell()
            stream.seek(max(0, length - max_bytes))
            content = stream.read(max_bytes).decode("utf-8", errors="replace")
        return {"text": content, "total_bytes": length, "truncated": length > max_bytes}
    except FileNotFoundError:
        return {"text": "", "total_bytes": 0, "truncated": False}


def get_job(settings: Settings, job_id: str, tail_bytes: int = 16384) -> dict[str, Any]:
    if not 1 <= tail_bytes <= 131072:
        raise ValueError("tail_bytes must be between 1 and 131072.")
    directory = job_path(settings, job_id)
    return {
        **read_json(directory / "job.json"),
        **_status(directory),
        "stdout": _tail(directory / "stdout.log", tail_bytes),
        "stderr": _tail(directory / "stderr.log", tail_bytes),
        "worker_log": _tail(directory / "worker.log", tail_bytes),
    }


def list_jobs(settings: Settings, limit: int = 20) -> list[dict[str, Any]]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100.")
    jobs = []
    for directory in settings.jobs_dir.iterdir():
        if JOB_ID.fullmatch(directory.name) and (directory / "job.json").is_file():
            metadata = read_json(directory / "job.json")
            jobs.append({**metadata, **_status(directory)})
    return sorted(jobs, key=lambda item: item["submitted_at"], reverse=True)[:limit]


def cancel_job(settings: Settings, job_id: str) -> dict[str, Any]:
    directory = job_path(settings, job_id)
    status = _status(directory)
    if status["status"] not in ACTIVE_STATES:
        return {"job_id": job_id, "status": status["status"], "cancel_requested": False}
    (directory / "cancel").touch()
    return {"job_id": job_id, "status": status["status"], "cancel_requested": True}

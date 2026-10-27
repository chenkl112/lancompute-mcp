import json
import os
import subprocess
import sys
import time

import psutil
import pytest

from lancompute_mcp.config import Settings
from lancompute_mcp.jobs import cancel_job, get_job, list_jobs, start_job


def wait_for_job(settings, job_id, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = get_job(settings, job_id)
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(0.05)
    cancel_job(settings, job_id)
    pytest.fail(f"Job did not complete: {get_job(settings, job_id)}")


def test_success_logs_and_reload(settings, python_job):
    command = python_job("import sys\nprint('你好')\nprint('diagnostic', file=sys.stderr)\n")
    submitted = start_job(settings, command)
    # A new server instance can inspect a job launched by a previous instance.
    reloaded = Settings(settings.workspace, settings.state_dir)
    result = wait_for_job(reloaded, submitted["job_id"])
    assert result["status"] == "succeeded", result
    assert result["exit_code"] == 0
    assert "你好" in result["stdout"]["text"]
    assert "diagnostic" in result["stderr"]["text"]
    assert list_jobs(reloaded)[0]["job_id"] == submitted["job_id"]
    assert not cancel_job(reloaded, submitted["job_id"])["cancel_requested"]


def test_native_nonzero_exit_is_failure(settings, python_job):
    submitted = start_job(settings, python_job("raise SystemExit(7)"))
    result = wait_for_job(settings, submitted["job_id"])
    assert result["status"] == "failed", result
    assert result["exit_code"] != 0


def test_timeout_terminates_process(settings, python_job):
    submitted = start_job(settings, python_job("import time\ntime.sleep(60)"), timeout_seconds=1)
    result = wait_for_job(settings, submitted["job_id"])
    assert result["status"] == "timed_out", result
    assert result["exit_code"] != 0


def test_cancel_terminates_descendants(settings, python_job):
    command = python_job(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    submitted = start_job(settings, command)
    job_id = submitted["job_id"]
    try:
        deadline = time.monotonic() + 15
        child_pid = None
        while time.monotonic() < deadline:
            output = get_job(settings, job_id)["stdout"]["text"].strip()
            if output:
                child_pid = int(output)
                break
            time.sleep(0.05)
        assert child_pid is not None, get_job(settings, job_id)
        assert cancel_job(settings, job_id)["cancel_requested"]
        result = wait_for_job(settings, job_id)
        assert result["status"] == "cancelled", result
        try:
            child = psutil.Process(child_pid)
            assert child.status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            pass
    finally:
        cancel_job(settings, job_id)


def test_log_tails_are_bounded(settings, python_job):
    submitted = start_job(settings, python_job("print('x' * 100000)"))
    wait_for_job(settings, submitted["job_id"])
    result = get_job(settings, submitted["job_id"], tail_bytes=512)
    assert len(result["stdout"]["text"].encode()) == 512
    assert result["stdout"]["truncated"]
    assert result["stdout"]["total_bytes"] >= 100000


def test_submission_validation_does_not_create_jobs(settings):
    for kwargs in (
        {"command": ""},
        {"command": "echo hello", "cwd": ".."},
        {"command": "echo hello", "cwd": "nonexistent"},
        {"command": "echo hello", "timeout_seconds": 0},
        {"command": "echo hello", "shell": "invalid"},
    ):
        with pytest.raises(ValueError):
            start_job(settings, **kwargs)
    assert list(settings.jobs_dir.iterdir()) == []
    with pytest.raises(ValueError):
        get_job(settings, "../outside")


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific PowerShell behavior")
def test_powershell_error_is_failure(settings):
    job_id = start_job(settings, "Write-Error 'intentional failure'")["job_id"]
    assert wait_for_job(settings, job_id)["status"] == "failed"


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are Windows-specific")
def test_windows_worker_survives_session_job_object(settings, python_job):
    """Simulate SSH forbidding breakaway, then destroying its session Job Object."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    assert handle, ctypes.get_last_error()
    harness = (
        "import json, sys\nfrom pathlib import Path\n"
        "from lancompute_mcp.config import Settings\n"
        "from lancompute_mcp.jobs import start_job\n"
        "sys.stdin.readline()\n"
        "settings = Settings(Path(sys.argv[1]), Path(sys.argv[2]))\n"
        "print(json.dumps(start_job(settings, sys.argv[3])), flush=True)\n"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            harness,
            str(settings.workspace),
            str(settings.state_dir),
            python_job("import time\ntime.sleep(2)\nprint('outside session job')"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert kernel.AssignProcessToJobObject(handle, int(process._handle)), (
            ctypes.get_last_error()
        )
        stdout, stderr = process.communicate("start\n", timeout=45)
        assert process.returncode == 0, stderr
        submitted = json.loads(stdout)
        kernel.TerminateJobObject(handle, 1)
        result = wait_for_job(settings, submitted["job_id"])
        assert result["status"] == "succeeded", result
        assert "outside session job" in result["stdout"]["text"]
    finally:
        kernel.TerminateJobObject(handle, 1)
        kernel.CloseHandle(handle)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)

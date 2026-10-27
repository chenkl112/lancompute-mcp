"""Hardware availability, with optional NVIDIA driver reporting."""

import csv
import platform
import shutil
import subprocess
import sys
from typing import Any

import psutil

from .config import Settings


def system_info(settings: Settings) -> dict[str, Any]:
    memory = psutil.virtual_memory()
    disk = shutil.disk_usage(settings.workspace)
    result: dict[str, Any] = {
        "hostname": platform.node(),
        "os": platform.platform(),
        "architecture": platform.machine(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "cpu": {
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(),
            "usage_percent": psutil.cpu_percent(interval=0.1),
        },
        "memory": {"total_bytes": memory.total, "available_bytes": memory.available},
        "disk": {"total_bytes": disk.total, "free_bytes": disk.free},
        "workspace": str(settings.workspace),
        "state_dir": str(settings.state_dir),
        "nvidia_gpus": [],
    }
    executable = shutil.which("nvidia-smi")
    if not executable:
        result["gpu_note"] = "nvidia-smi not found; other GPUs may still be available."
        return result
    try:
        process = subprocess.run(
            [
                executable,
                "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=True,
        )
        result["nvidia_gpus"] = [
            dict(
                zip(
                    ("name", "memory_total_mib", "memory_used_mib", "usage_percent"),
                    row,
                    strict=True,
                )
            )
            for row in csv.reader(process.stdout.splitlines(), skipinitialspace=True)
        ]
    except (OSError, subprocess.SubprocessError) as error:
        result["gpu_note"] = f"nvidia-smi unavailable: {error}"
    return result

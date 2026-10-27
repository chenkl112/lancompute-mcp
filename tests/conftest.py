import os
import shlex
import sys
import uuid

import pytest

from lancompute_mcp.config import Settings
from lancompute_mcp.files import write_file


@pytest.fixture
def settings(tmp_path):
    value = Settings(workspace=tmp_path / "workspace", state_dir=tmp_path / "state")
    value.prepare()
    return value


@pytest.fixture
def python_job(settings):
    def build(code):
        filename = f"scripts/{uuid.uuid4().hex}.py"
        write_file(settings, filename, code)
        if os.name == "nt":
            executable = sys.executable.replace("'", "''")
            return f"& '{executable}' '{filename}'"
        return f"{shlex.quote(sys.executable)} {shlex.quote(filename)}"

    return build

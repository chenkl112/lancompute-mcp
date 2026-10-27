import json
import os
import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from lancompute_mcp.jobs import get_job


def tool_data(result):
    assert not result.isError, result
    return json.loads(next(block.text for block in result.content if block.type == "text"))


def test_stdio_session_tools_and_disconnection(settings, python_job):
    async def scenario():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "lancompute_mcp.server"],
            env={
                **os.environ,
                "LANCOMPUTE_WORKSPACE": str(settings.workspace),
                "LANCOMPUTE_STATE_DIR": str(settings.state_dir),
            },
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert names == {
                    "get_system_info",
                    "start_job",
                    "get_job",
                    "list_jobs",
                    "cancel_job",
                    "write_file",
                    "read_file",
                }
                info = tool_data(await session.call_tool("get_system_info"))
                assert info["memory"]["total_bytes"] > 0
                bad = await session.call_tool(
                    "write_file", {"path": "../escape.txt", "content": "bad"}
                )
                assert bad.isError
                tool_data(
                    await session.call_tool(
                        "write_file", {"path": "hello.txt", "content": "你好 MCP"}
                    )
                )
                saved = tool_data(await session.call_tool("read_file", {"path": "hello.txt"}))
                assert saved["content"] == "你好 MCP"
                submitted = tool_data(
                    await session.call_tool(
                        "start_job",
                        {
                            "command": python_job("import time\ntime.sleep(2)\nprint('survived')"),
                        },
                    )
                )
        # Both the MCP process and its stdio session have now exited.
        for _ in range(200):
            result = get_job(settings, submitted["job_id"])
            if result["status"] not in {"queued", "running"}:
                break
            await anyio.sleep(0.05)
        assert result["status"] == "succeeded", result
        assert "survived" in result["stdout"]["text"]
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = tool_data(
                    await session.call_tool("get_job", {"job_id": submitted["job_id"]})
                )
                assert result["status"] == "succeeded"

    anyio.run(scenario)

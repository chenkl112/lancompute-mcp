import base64
import os

import pytest

from lancompute_mcp.files import read_file, write_file


def test_text_binary_and_chunked_transfers(settings):
    write_file(settings, "scripts/hello.py", "print('你好')\n")
    assert read_file(settings, "scripts/hello.py")["content"] == "print('你好')\n"
    binary = bytes(range(256)) * 3
    write_file(settings, "output/result.bin", base64.b64encode(binary).decode(), "base64")
    result = bytearray()
    offset = 0
    while True:
        chunk = read_file(settings, "output/result.bin", offset, 47, "base64")
        result.extend(base64.b64decode(chunk["content"]))
        offset = chunk["next_offset"]
        if chunk["eof"]:
            break
    assert result == binary


def test_overwrite_is_explicit(settings):
    write_file(settings, "result.txt", "first")
    with pytest.raises(FileExistsError):
        write_file(settings, "result.txt", "second")
    assert read_file(settings, "result.txt")["content"] == "first"
    write_file(settings, "result.txt", "second", overwrite=True)
    assert read_file(settings, "result.txt")["content"] == "second"


def test_parent_traversal_and_absolute_escape_are_rejected(settings, tmp_path):
    for path in ("../escape.txt", str(tmp_path / "escape.txt")):
        with pytest.raises(ValueError, match="inside"):
            write_file(settings, path, "bad")
        with pytest.raises(ValueError, match="inside"):
            read_file(settings, path)
    assert not (tmp_path / "escape.txt").exists()


def test_symlink_escape_is_rejected(settings, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (settings.workspace / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name == "nt":
            pytest.skip("Windows runner has no symlink permission")
        raise
    with pytest.raises(ValueError, match="inside"):
        write_file(settings, "link/stolen.txt", "bad")
    assert not (outside / "stolen.txt").exists()


def test_transfer_limits_and_invalid_encoding(settings):
    with pytest.raises(ValueError, match="4 MiB"):
        write_file(settings, "large.bin", "x" * (4 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="base64"):
        write_file(settings, "invalid.bin", "!!!", "base64")
    with pytest.raises(ValueError):
        read_file(settings, "any", max_bytes=131073)
    with pytest.raises(ValueError):
        read_file(settings, "any", offset=-1)

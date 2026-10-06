"""Lock files: write `<path>.lock`, then rename it over `<path>`.

O_BINARY matters: on Windows os.open defaults to text mode, which turns
every 0x0a byte written into 0x0d 0x0a.
"""

from __future__ import annotations

import os
from pathlib import Path

from pygit.errors import GitError

_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)


def write_locked(path: Path, data: bytes, busy_message: str | None = None) -> None:
    path = Path(path)
    lock = path.with_name(path.name + ".lock")
    try:
        fd = os.open(lock, _FLAGS, 0o644)
    except FileExistsError:
        raise GitError(busy_message or f"Unable to create '{lock}': File exists.")
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
    except BaseException:
        os.close(fd)
        os.unlink(lock)
        raise
    os.close(fd)
    os.replace(lock, path)

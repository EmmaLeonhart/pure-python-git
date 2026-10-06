"""Author and committer identity, from the environment and config."""

from __future__ import annotations

import os
import re

from pygit.errors import GitError
from pygit.objects import Signature
from pygit.refs import now_signature_time

_DATE_RE = re.compile(r"^@?(\d+)(?:\s+([+-]\d{4}))?$")


def parse_date(value: str) -> tuple[int, bytes]:
    """Accept git's internal date format: `<unix seconds> <+HHMM>` (or `@<secs>`)."""
    m = _DATE_RE.match(value.strip())
    if not m:
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(value.strip())
        except ValueError:
            raise GitError(f"invalid date format: {value}")
        ts = int(dt.timestamp())
        off = dt.utcoffset()
        mins = int(off.total_seconds() // 60) if off is not None else 0
        sign = "-" if mins < 0 else "+"
        mins = abs(mins)
        return ts, f"{sign}{mins // 60:02d}{mins % 60:02d}".encode()
    tz = m.group(2) or "+0000"
    return int(m.group(1)), tz.encode()


def ident(repo, role: str) -> Signature:
    """`role` is "author" or "committer"; env vars override config."""
    up = role.upper()
    name = os.environ.get(f"GIT_{up}_NAME") or repo.config.get(f"{role}.name") or repo.config.get("user.name")
    email = os.environ.get(f"GIT_{up}_EMAIL") or repo.config.get(f"{role}.email") or repo.config.get("user.email") \
        or os.environ.get("EMAIL")
    if not name or email is None:
        raise GitError(
            "Author identity unknown\n\n*** Please tell me who you are.\n\nRun\n\n"
            '  git config --global user.email "you@example.com"\n'
            '  git config --global user.name "Your Name"\n\n'
            "to set your account's default identity.")
    date = os.environ.get(f"GIT_{up}_DATE")
    if date:
        ts, tz = parse_date(date)
    else:
        t, z = now_signature_time()
        ts, tz = t, z.encode()
    return Signature(name.encode(), email.encode(), ts, tz)

#!/usr/bin/env python3
"""Save this Claude Code session's transcript into sessions/ and commit it.

Written by cleanvibe. `.claude/settings.json` runs it on two hooks, so saving
the transcript never depends on the agent remembering to:

* Stop (after every response): copy the transcript and render it. Commit
  sessions/ only if the last commit touching it is at least an hour old
  (override with CLEANVIBE_LOG_COMMIT_SECONDS).
* SessionEnd (with --push): copy, render and always commit, then push if the
  branch has an upstream.

Claude Code passes the hook input as JSON on stdin; `transcript_path` and
`session_id` are the fields used here. For each session this writes

    sessions/<date>_<session8>.jsonl   the raw transcript (complete)
    sessions/<date>_<session8>.md      a readable rendering of the conversation

and commits ONLY sessions/ (anything else staged is left alone). Stdlib only.
It never fails the hook: errors go to stderr and the exit code is always 0.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SESSIONS = ROOT / "sessions"
COMMIT_EVERY = 3600  # seconds between session-log commits during a session

_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_TOOL_HINT_KEYS = ("description", "command", "file_path", "pattern", "query", "url", "prompt")


def _load(path):
    entries = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    return entries


def _clean(text):
    return _REMINDER.sub("", text or "").strip()


def _one_line(value, limit=120):
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def render(entries, title):
    """Render transcript entries as Markdown: user and Claude turns, tool calls
    as one-line notes, AskUserQuestion questions and answers in full."""
    out = [f"# {title}", ""]
    speaker = None
    ask_ids = set()

    def say(who, text):
        nonlocal speaker
        if who != speaker:
            out.extend([f"## {who}", ""])
            speaker = who
        out.extend([text, ""])

    for e in entries:
        if e.get("isSidechain"):
            continue
        if e.get("isMeta"):
            # Scheduled [cleanvibe cron] prompts are meta entries; show them so a
            # reader catching up can see what triggered the actions that follow.
            if e.get("type") == "user":
                text = _clean(_result_text((e.get("message") or {}).get("content")))
                if text.startswith("[cleanvibe cron]"):
                    say("Cron", text)
            continue
        kind = e.get("type")
        if kind == "attachment":
            att = e.get("attachment") or {}
            if att.get("type") == "queued_command" and att.get("prompt"):
                say("User", _clean(att["prompt"]))
            continue
        if kind not in ("user", "assistant"):
            continue
        content = (e.get("message") or {}).get("content")
        who = "User" if kind == "user" else "Claude"
        if isinstance(content, str):
            text = _clean(content)
            if text:
                say(who, text)
            continue
        for block in content or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = _clean(block.get("text"))
                if text:
                    say(who, text)
            elif btype == "tool_use":
                name = block.get("name", "tool")
                args = block.get("input") or {}
                if name == "AskUserQuestion":
                    ask_ids.add(block.get("id"))
                    qs = [q.get("question", "") for q in args.get("questions", [])]
                    say("Claude", "**Asked:**\n" + "\n".join(f"- {q}" for q in qs))
                else:
                    hint = next((args[k] for k in _TOOL_HINT_KEYS if args.get(k)), "")
                    note = f"> *{name}*" + (f": {_one_line(hint)}" if hint else "")
                    say("Claude", note)
            elif btype == "tool_result" and block.get("tool_use_id") in ask_ids:
                say("User", "**Answered:** " + _clean(_result_text(block.get("content"))))
    return "\n".join(out).rstrip() + "\n"


def _local_date(stamp):
    """The local calendar date of a transcript timestamp (they are UTC)."""
    if not re.match(r"\d{4}-\d{2}-\d{2}", stamp):
        return "undated"
    try:
        from datetime import datetime
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
    except ValueError:
        return stamp[:10]


def _stem(entries, session_id):
    stamp = next((e["timestamp"] for e in entries if e.get("timestamp")), "")
    return f"{_local_date(stamp)}_{(session_id or 'session')[:8]}"


def save(hook):
    transcript = Path(hook["transcript_path"]).expanduser()
    entries = _load(transcript)
    stem = _stem(entries, hook.get("session_id"))
    SESSIONS.mkdir(exist_ok=True)
    shutil.copyfile(transcript, SESSIONS / f"{stem}.jsonl")
    (SESSIONS / f"{stem}.md").write_text(
        render(entries, f"Session {stem}"), encoding="utf-8"
    )
    return stem


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=120
    )


def _commit_interval():
    try:
        return max(0, int(os.environ.get("CLEANVIBE_LOG_COMMIT_SECONDS", COMMIT_EVERY)))
    except ValueError:
        return COMMIT_EVERY


def _due():
    """True when the last commit touching sessions/ is at least the interval old."""
    last = _git("log", "-1", "--format=%ct", "--", "sessions").stdout.strip()
    if not last.isdigit():
        return True
    return time.time() - int(last) >= _commit_interval()


def commit(stem, final):
    """Commit sessions/ if it changed: hourly during a session, always at its end."""
    if not final and not _due():
        return
    _git("add", "--", "sessions")
    if _git("diff", "--cached", "--quiet", "--", "sessions").returncode != 0:
        done = _git("commit", "-q", "-m", f"session log {stem}", "--", "sessions")
        if done.returncode != 0:
            print(f"save_session_log: commit failed: {done.stderr.strip()}", file=sys.stderr)
    if final and _git("rev-parse", "--abbrev-ref", "@{u}").returncode == 0:
        pushed = _git("push", "-q")
        if pushed.returncode != 0:
            print(f"save_session_log: push failed: {pushed.stderr.strip()}", file=sys.stderr)


def main(argv):
    try:
        hook = json.loads(sys.stdin.read() or "{}")
        if not hook.get("transcript_path"):
            return 0
        stem = save(hook)
        # --push is what SessionEnd passes (the name predates the hourly throttle).
        commit(stem, final="--push" in argv)
    except Exception as exc:  # never break the session over a log
        print(f"save_session_log: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

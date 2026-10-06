"""Command-line entry point: `python -m pygit <command> [args]`.

Each command module registers functions in COMMANDS. A command takes the
argument list and returns an exit code; it writes bytes to `out` (stdout's
binary buffer) so output matches git byte for byte on every platform.
"""

from __future__ import annotations

import argparse
import sys

from pygit.errors import GitError, UsageError

COMMANDS = {}


def command(name):
    def register(fn):
        COMMANDS[name] = fn
        return fn
    return register


class ArgParser(argparse.ArgumentParser):
    """argparse that reports usage errors the way git does (exit 129)."""

    def error(self, message):
        raise UsageError(message)


def parser(cmd: str, **kw) -> ArgParser:
    return ArgParser(prog=f"git {cmd}", add_help=True, **kw)


def out(data: bytes) -> None:
    sys.stdout.buffer.write(data)


def _load_commands() -> None:
    # Imported for their @command registrations.
    from pygit.commands import plumbing  # noqa: F401
    for mod in ("porcelain", "branching", "merging", "transport"):
        try:
            __import__(f"pygit.commands.{mod}")
        except ModuleNotFoundError as e:
            if e.name != f"pygit.commands.{mod}":
                raise


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _load_commands()
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write("usage: python -m pygit <command> [<args>]\n\ncommands:\n")
        for name in sorted(COMMANDS):
            sys.stdout.write(f"   {name}\n")
        return 0 if argv else 1
    if argv[0] == "--version":
        from pygit import __version__
        sys.stdout.write(f"pygit version {__version__}\n")
        return 0
    name, args = argv[0], argv[1:]
    fn = COMMANDS.get(name)
    if fn is None:
        sys.stderr.write(f"git: '{name}' is not a git command. See 'python -m pygit --help'.\n")
        return 1
    try:
        rc = fn(args)
    except UsageError as e:
        sys.stderr.write(f"error: {e.message}\n")
        return e.code
    except GitError as e:
        sys.stdout.flush()
        sys.stderr.write(f"fatal: {e.message}\n")
        return e.code
    except BrokenPipeError:
        return 141
    finally:
        try:
            sys.stdout.flush()
        except BrokenPipeError:
            pass
    return rc or 0

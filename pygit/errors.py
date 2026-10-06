"""Errors that end a command the way git does: a message and an exit code."""


class GitError(Exception):
    """A fatal error. git prints "fatal: <msg>" and exits 128."""

    def __init__(self, message, code=128):
        super().__init__(message)
        self.message = message
        self.code = code


class UsageError(GitError):
    """Bad command-line usage (git exits 129)."""

    def __init__(self, message):
        super().__init__(message, code=129)

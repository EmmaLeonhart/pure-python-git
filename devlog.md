# devlog.md

Where "done" lives: dated entries for finished queue items, releases and
milestones.

## 2026-10-06

- 02:09 PST: thirty-minute intake ran (verdict WORK MODE: no chat, brief in
  `data_lake/`). Work mode started: INTENT.md written, private repo
  `EmmaLeonhart/pure-python-git` created and pushed, skills checked against
  cleanvibe's updates page (already current at v2.0.4), README, todo.md and
  queue.md written, work loop cron started.
- 02:19 PST: **stage 1 (object store) done.** Package `pygit/` with config,
  repo discovery and `init`, the loose object store with tree/commit/tag
  parsing and serialization, refs (loose, packed, symbolic, lock files,
  reflogs), revision parsing, and commands `init`, `hash-object`,
  `cat-file`, `ls-tree`, `rev-parse`, `mktree`, `commit-tree`,
  `update-ref`, `symbolic-ref`. 26 tests compare output with git byte for
  byte on a sample repo (odd file names, symlink, exec bit, annotated and
  nested tags, packed refs) and check that git `fsck --strict` accepts
  pygit-built history. CI on Linux/Windows/macOS × Python 3.9/3.13. Bugs
  found by the comparison and fixed: `cat-file -p` of a tree must quote
  paths; `ls-tree -d -r` must still recurse; a missing `<rev>:<path>` dies
  with git's message instead of returning 1; `init` printed `\r\n` on
  Windows. Stage 2 is broken down in queue.md.

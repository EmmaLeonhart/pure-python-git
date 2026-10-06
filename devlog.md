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
- 02:21 PST: CI on GitHub Actions does not start: the account's billing or
  spending limit blocks jobs. Recorded in INTENT.md as BLOCKED-ON-USER-ACTION;
  the suite runs locally on Python 3.13 and 3.11 before each push.
- 02:36 PST: **stage 2, first half: index, ignore rules, ls-files,
  write-tree, read-tree, update-index, check-ignore, add, rm, status.**
  Index v2/v3/v4 reader and v2 writer (`pygit/index.py`), a port of git's
  wildmatch and gitignore precedence (`pygit/ignore.py`), work-tree walking
  and racy-clean checks, exact and similarity-based rename detection for
  status, upstream ahead/behind. 20 new tests compare stdout, stderr, exit
  codes and resulting index with git. Bugs the comparison found: on Windows
  `os.open` writes in text mode unless `O_BINARY` is passed, which turned
  0x0a bytes in the index (and refs) into CRLF (fixed with a shared
  `lockfile.write_locked`); stderr also went through text mode; Git for
  Windows records dev/ino/uid/gid as 0 and compares them, so pygit now does
  the same; long status paragraph spacing, `-uno` wording, `./` for the cwd
  itself, ignored-directory naming in `add`.
- 02:48 PST: **stage 2 done: commit, log, and the diff engine.**
  `pygit/xdiff.py` ports git's xdiff (record classification, trim and
  cleanup of unmatched lines, Myers split with git's heuristics and cost
  limit, change compaction with the indent heuristic, hunk grouping and
  function-name context); it matches `git diff --no-index` on 430 random
  file pairs, including large ones that hit the cost heuristics. It was
  pulled forward from stage 3 because `commit`'s summary needs line
  counts. `commit` matches git's output and commit ids (summary with
  Author/Date lines, create/delete/rename/mode lines, "nothing to commit"
  status with "Initial commit", the empty-amend refusal, reflog messages).
  `log` covers the built-in formats, `--format` placeholders, ranges and
  path limiting with git's merge simplification. 58 tests pass. Stage 3
  is broken down in queue.md.
- 03:05 PST: **stage 3 done: diff, branch, tag, checkout/switch, reset.**
  `diff` renders patches (all header forms, binary, quoted names, the tab
  after `---`/`+++` names that contain spaces), `--stat` with git's width
  and scaling rules, numstat, name-only/status. The checkout engine
  (`pygit/checkout.py`) applies git's two-way rules and refuses before
  touching anything. Tests run every command in twin repos and compare
  output, HEAD, index, file contents and status after each step. Details
  git does that the tests caught: the detached-HEAD label resolves the
  checked-out name through the reflog (`HEAD detached at refs/heads/main`
  is real git output), `checkout <tree> -- paths` counts only rewritten
  paths, `checkout -B` may reset the current branch, advice text honors
  `advice.detachedHead`, nested-tag and ref-syntax hints. 71 tests pass.
  Stage 4 is broken down in queue.md.

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
- 03:30 PST: **stage 4 done: merging.** `pygit/xmerge.py` ports git's
  xmerge.c and matches `git merge-file` on 700 random cases in merge and
  diff3 style (git's merges run xdiff without the indent heuristic, so the
  port does too). `pygit/merge_ort.py` gives ort's results and messages for
  the common cases, with recursive virtual bases for criss-cross
  histories. `merge` covers fast-forward, merge commits, conflicts
  (MERGE_HEAD/MERGE_MSG/MERGE_MODE/ORIG_HEAD), `--ff-only`, `--no-ff`,
  `--no-commit`, `--abort`, `--continue`, and refusals (dirty index, work
  tree in the way, unmerged index). Status during a merge, `U` entries in
  `diff`, and committing the resolution (with whitespace cleanup, which
  keeps the `# Conflicts:` lines as `git commit --no-edit` does) match git.
  One test mistake to note: a test ran `git commit` without `--no-edit`
  on a merge, so git waited for an editor and the run hung for 10 minutes;
  the test environment now sets `GIT_EDITOR=false`. 80 tests pass.
  Stage 5 is broken down in queue.md.
- 03:38 PST: **stage 5, packs: reading, writing, verify-pack, index-pack,
  pack-objects, count-objects, pack-refs, prune, gc.** `pygit/pack.py`
  reads v1/v2 indexes and v2 packs with OFS/REF deltas (all objects of a
  `git gc --aggressive` repository read identically), writes packs with
  its own delta search (a window of 10 candidates sorted by type, name
  hash and size; chains up to depth 50; 16-byte block matching) and v2
  indexes. git's `index-pack` of a pygit pack produces a byte-identical
  index, and pygit's `index-pack` of a git pack matches git's index.
  `verify-pack -v` output is identical to git's. `gc` packs everything
  reachable from refs, reflogs, the index and the *_HEAD files, writes
  `packed-refs` exactly as `git pack-refs --all` does, drops old packs and
  packed loose objects, and prunes unreachable loose objects past the
  two-week grace period. 86 tests pass.
- 03:49 PST: **stage 5 done: clone, fetch, push, pull, remote. All five
  stages of the brief are implemented.** Local transport opens the other
  repository directly and sends what the receiver lacks as one pack.
  Twin tests play each scenario with git and with pygit as the client
  against identical copies of a server repository and compare output
  (paths normalized), exit codes, refs, config, FETCH_HEAD and objects:
  clone (plain, bare, empty, errors), fetch (updates, new branches,
  auto-followed tags, `--prune`, forced updates), push (new branch,
  up to date, fast-forward, non-fast-forward rejection with git's hint
  variants, `--force`, `--delete`, tags, `-u`, the remote's
  checked-out-branch refusal with its `remote:` lines), pull
  (fast-forward, the divergent-branch refusal, merge with
  `pull.rebase=false`) and remote. 91 tests pass on Python 3.13. README
  now documents the whole design and its limits; todo.md lists the limits
  to close next.
- 03:56 PST: the full suite (91 tests) also passes on Python 3.11.
  **Exact rename detection:** `pygit/diffcore.py` now ports git's
  diffcore-delta span hashing (spans end at a newline or 64 bytes; CR
  before LF ignored in text) and diffcore-rename's passes (exact,
  unique-basename at the halfway score, then the top-4-per-destination
  candidate matrix assigned greedily), including the size-ratio cutoff.
  Pairs and similarity percentages match `git diff --no-index -M` on 600
  random file sets; `tests/test_renames.py` keeps 80 of them. Removed the
  matching limit from README and todo.md. 92 tests pass.
- 04:53 PST: **`ls-tree` from subdirectories.** Paths are taken relative
  to the cwd (no paths in a subdirectory lists that directory), shown
  relative to it (the cwd itself as `./`, parents as `../`), with
  `--full-name` and `--full-tree`. `relative_to_cwd` gained the
  ancestor-directory case. 24 new subdirectory cases match git. 93 tests
  pass (the run took 20 minutes because other workloads were loading the
  machine; normally about 5).
- 05:50 PST: **`diff` and `log` path arguments from subdirectories.**
  `diff` looked for an undashed path relative to the repository root
  instead of the cwd; `log` did not accept undashed paths at all; both
  took `../x` and a bare `..` for revision ranges. Arguments are now
  revisions only if every end of a range resolves, and paths otherwise
  when they exist, as git decides. 94 tests pass.
- 06:08 PST: **`log` diff options and `show`.** `log -p`, `--stat`,
  `--numstat`, `--shortstat`, `--name-only`, `--name-status`, `--summary`
  (first-parent diffs, none for merges), with git's separators: a blank
  line between message and diff, `---` when both a stat and a patch are
  shown, none for `--oneline`, and a separator even when a commit's
  summary is empty. `show` handles commits (merges in combined mode:
  stats against the first parent, an empty patch for clean merges),
  annotated tags, trees and blobs. 95 tests pass.
- 07:01 PST: **combined diffs.** `pygit/combined.py` ports git's
  combine-diff.c (no-context diffs against each parent, lost lines
  coalesced by LCS, dense hunk selection, `@@@` headers, git's
  hunk-comment truncation). `diff` during a conflicted merge prints
  `diff --cc` for paths with both sides first, then the other pairs;
  stat-like formats list a conflicted path as `U` plus its stage-2
  diff, as git does. `show` of a merge prints the combined patch for
  paths that differ from every parent. 181 random conflicted merges were
  identical to git in `diff` and in `show` of the committed result; 40
  are kept in `tests/test_stage4_merge.py`. 97 tests pass.
- 07:17 PST: **line endings.** `pygit/attributes.py` reads
  `.gitattributes` with git's precedence and macros (new `check-attr`
  command); `pygit/convert.py` ports convert.c's CRLF rules (attribute and
  config actions, text detection for `auto`, the index-has-CR rule,
  safecrlf warnings, native CRLF on Windows builds). The clean direction
  runs wherever a work-tree file is hashed (add, status, diff, commit -a,
  update-index), with warnings on `add` and `diff` as git prints them; the
  smudge direction runs in every work-tree write (checkout, reset, merge,
  clone). `tests/test_eol.py` compares attributes, stored blobs, status,
  diff and checkout bytes with git for autocrlf false/true/input and
  core.eol=crlf. 101 tests pass.
- 07:30 PST: **`restore`** (`--source`, `--staged`, `--worktree`, both,
  bundled `-SW`), without overlay: tracked paths missing from the source
  leave the index (`--staged`) and the work tree (`--worktree`). Twin tests
  against git. 102 tests pass.
- 08:11 PST: **`cherry-pick`** (`pygit/commands/sequencing.py`): a tree
  merge with the commit's parent as base and git's labels, the original
  author and message, `-x`, `-n`, the "now empty" stop, conflicts with
  CHERRY_PICK_HEAD and MERGE_MSG, `--continue` (comment lines stripped),
  `--skip`, `--abort`, `--quit`, multi-commit picks with git's
  `.git/sequencer` (head, todo, abort-safety), and git's refusals. `commit`
  concludes a pick with its author; `status` shows the cherry-pick
  paragraph. The merge command's apply step became `apply_result`, shared
  with cherry-pick. 107 tests pass. (One earlier run was cut off by its
  timeout while other workloads slowed the machine; the rerun took 8
  minutes.)
- 08:33 PST: **`stash`** (`pygit/commands/stashing.py`): push/save with
  `-m`, list, show (`-p`), apply/pop (`--index`), drop, clear. The stash
  commits are byte-identical to git's (index commit; work-tree commit
  with parents HEAD and the index commit, its message without a final
  newline), refs/stash with its reflog as the stack, so either tool pops
  the other's stash. Apply merges with git's labels, keeps added files
  staged without `--index`, prints the long status (also on conflict, then
  keeps the entry). Revision parsing gained `<ref>@{n}`. 110 tests pass.
- 09:14 PST: **`rebase`** (non-interactive, `pygit/commands/rebasing.py`):
  up-to-date and fast-forward cases, `--onto`, picks with git's progress
  lines and reflog messages, commits that become empty dropped, conflicts
  stopping with git's `.git/rebase-merge` state (head-name, onto,
  orig-head, todo, done, msgnum, end, message, author-script,
  stopped-sha) and REBASE_HEAD, `--continue`, `--skip`, `--abort`,
  `--quit`, and git's refusals. `status` prints the rebase layout ("interactive
  rebase in progress; onto ...", done/next commands). Cherry-pick's apply
  step became `apply_commit`, shared by both. Note for tests: git's
  `rebase --continue` opens an editor, so that test sets `GIT_EDITOR=true`.
  The history-editing item of todo.md is done. 115 tests pass.
- 09:49 PST: **`revert`** on the cherry-pick machinery (`apply_commit`
  with `reverse=True`; the sequencer now handles `pick` and `revert`
  entries): git's message, `-n` (which, unlike cherry-pick, records
  REVERT_HEAD), the empty case (status, exit 1), conflicts with
  REVERT_HEAD and git's hints, `--continue` (no commit when nothing
  changed), `--skip`, `--abort`, several commits, the status paragraph,
  and `commit` concluding a revert. `reset` now also clears
  CHERRY_PICK_HEAD/REVERT_HEAD/SQUASH_MSG/AUTO_MERGE as git does.
  `tests/test_revert.py` (3 tests) and the cherry-pick tests pass. **The
  full suite has not run on this change:** Claude Code stopped the run
  because the machine was critically low on memory, and asks not to
  restart it unprompted; it is the first item in queue.md.
- 10:25 PST: **rebase skips commits already upstream.** `pygit/patchid.py`
  ports diff.c's in-process patch id (whitespace stripped, no hunk
  headers, three lines of context, no renames, blob ids for binaries);
  rebase drops topic commits whose id matches an upstream commit, with
  git's "skipped previously applied commit" warning and hints. Checked
  against `git patch-id` (12 of 13 text commits identical; the 13th is a
  mode-only commit, where `git patch-id` works from patch text without
  `---`/`+++` lines and so differs by design; equality between commits,
  which is what rebase uses, is unaffected). New rebase test passes, as
  do the other 5. The full suite still waits on memory (queue item 1).
- 10:51 PST: **`stash -u`**: the untracked (not ignored) files go into a
  parentless third-parent commit ("untracked files on ...") and leave the
  work tree; an untracked-only stash is allowed; apply restores them,
  refusing with git's "already exists, no checkout" error when one is in
  the way (entry kept), and prints merge-ort's "Already up to date." when
  the stash changed no tracked file. Stash commit ids still match git's.
  Stash tests (4) pass; the full suite still waits on memory.
- 11:22 PST: **`cherry-pick -m` / `revert -m`** (mainline parent as base;
  `-m 1` also accepted on a non-merge, as current git does; "does not
  have parent N"; kept in `sequencer/opts` for `--continue`). Revert
  messages follow git: "reversing changes made to <parent>" for merges,
  and `Reapply "X"` when reverting `Revert "X"`. Revert (4) and
  cherry-pick (5) tests pass; the full suite still waits on memory.
- 11:54 PST: **interactive rebase** (`rebase -i`) through a sequence
  editor (`GIT_SEQUENCE_EDITOR` / `sequence.editor`, run through `sh` as
  git does; pygit opens no editor itself): git's todo text and help block,
  pick/drop/fixup/squash and reordering, skipping of unnecessary leading
  picks, the squash summary (fixups are silent), stopped fixups/squashes
  amending on `--continue`, "nothing to do" returning to the start, and
  git's error for a todo that starts with squash/fixup (rebase left in
  progress). `status` gained git's "editing a commit while rebasing" case.
  Rebase tests (11) pass; the full suite still waits on memory.
- 12:21 PST: **rebase -i: reword, edit, exec, break**, and two fidelity
  fixes: a pick whose parent is already HEAD fast-forwards (the commit is
  kept, not rewritten), and skipped leading picks count in the progress
  numbers and the done list, as in git. edit/break stop with exit 0 and
  git's messages; exec runs through `sh` and reports failures as git
  does; reword prints the amend summary (the message stays, as no editor
  runs). Rebase tests (13) pass; the full suite still waits on memory.

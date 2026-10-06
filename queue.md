# queue.md: work in progress

Delete-only: a finished item is removed from here and logged in `devlog.md`
in the same commit.

## Stage 1: the object store

1. Package skeleton: `pygit/` with `__main__.py`, a CLI dispatcher, and
   `tests/` with a helper that runs the real `git` in temp directories.
   CI workflow (`.github/workflows/ci.yml`) on Linux/Windows/macOS.
2. `init`: create `.git/` (objects, refs/heads, refs/tags, HEAD, config)
   that `git` accepts (`git status`, `git fsck` clean).
3. Objects: hashing and loose object read/write (zlib), object types blob,
   tree, commit, tag; parse and serialize each. `hash-object [-w] [-t]
   [--stdin]` matches git's ids.
4. `cat-file -t/-s/-p/-e` and `cat-file <type> <obj>` byte-identical to git,
   on objects written by git and by pygit.
5. `ls-tree [-r] [-t] [-d] [--name-only] <tree-ish>` byte-identical to git
   (including path quoting and mode formatting).
6. Refs and HEAD: symbolic refs, loose refs, `packed-refs` reading;
   `rev-parse` for full/abbreviated ids, refs, `HEAD`, `^`, `~N`,
   `^{tree}`, `^{commit}`, `<rev>:<path>`.
7. Interop test: git builds a repo, pygit reads it; pygit writes
   objects/refs, `git fsck` and `git log` accept them. Update README design
   section for stage 1.

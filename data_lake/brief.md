# Brief

A git implementation in pure Python (standard library only) that reads and
writes real git repositories. This is a long project: work through it in
stages and keep going after each one.

1. The object store: blobs, trees, commits and tags, loose objects (zlib),
   refs and HEAD, and `hash-object`, `cat-file`, `ls-tree`, `rev-parse`.
2. The index (the real binary format, version 2), `add`, `rm`, `status`,
   `commit`, `log`, and `.gitignore` rules.
3. Branches and tags, `checkout`/`switch`, and `diff` (a Myers diff with
   unified output) between the work tree, the index and commits.
4. Merging: merge bases, fast-forward, three-way merge of trees and of file
   contents with conflict markers, and `merge --abort`.
5. Packfiles: read and write packs and their indexes (with deltas), `gc`,
   and `clone`/`fetch`/`push` between repositories on the local disk.

Every stage is tested against the real `git` binary: repositories made by
one must be read correctly by the other, and output that git defines (object
ids, `cat-file`, `ls-tree`, `diff`) must match it byte for byte. Document the
design and its limits in the README. If a stage turns out harder or
different than expected, adjust the plan and say why.

## The GitHub repo is public

This project is part of public research on how cleanvibe sessions work, so
its transcripts are meant to be shown. When work mode creates the GitHub
repo, create it **public** (`gh repo create <name> --public --source=. --push`),
not private. This overrides the private default in CLAUDE.md.

# todo.md: long-horizon work

The five stages of `data_lake/brief.md` are done (see `devlog.md`). What
remains is closing the limits listed in README.md, most valuable first:
the ones where pygit's output can differ from git's on ordinary use.

- **Interactive rebase.** `rebase -i` (needs an editor, so a todo-file
  option or `GIT_SEQUENCE_EDITOR` support first).
- **Pack fidelity.** Thin packs in `index-pack`, `.rev` files, `gc --auto`.

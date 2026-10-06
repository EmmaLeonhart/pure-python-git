# todo.md: long-horizon work

The five stages of `data_lake/brief.md` are done (see `devlog.md`). What
remains is closing the limits listed in README.md, most valuable first:
the ones where pygit's output can differ from git's on ordinary use.

- **Content filters.** `core.autocrlf`, `.gitattributes` `text`/`eol`/`binary`.
- **More history editing.** `restore`, `stash`, `cherry-pick`, `rebase`.
- **Pack fidelity.** Thin packs in `index-pack`, `.rev` files, `gc --auto`.

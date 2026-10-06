"""Stage 1 commands: init, hash-object, cat-file, ls-tree, rev-parse, and the
small ref/object plumbing (update-ref, symbolic-ref, commit-tree, mktree)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pygit import objects, revparse
from pygit.cli import command, out, parser
from pygit.errors import GitError
from pygit.objects import TYPES, Commit, TreeEntry, format_tree_line, parse_tree
from pygit.refs import Refs
from pygit.repo import find_repo, init


@command("init")
def cmd_init(args):
    p = parser("init")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--bare", action="store_true")
    p.add_argument("-b", "--initial-branch")
    p.add_argument("directory", nargs="?", default=".")
    a = p.parse_args(args)
    target = Path(a.directory)
    target.mkdir(parents=True, exist_ok=True)
    init(target, bare=a.bare, initial_branch=a.initial_branch, quiet=a.quiet)
    return 0


@command("hash-object")
def cmd_hash_object(args):
    p = parser("hash-object")
    p.add_argument("-t", dest="type", default="blob")
    p.add_argument("-w", dest="write", action="store_true")
    p.add_argument("--stdin", action="store_true")
    p.add_argument("--stdin-paths", action="store_true")
    p.add_argument("--literally", action="store_true")
    p.add_argument("--no-filters", action="store_true")
    p.add_argument("files", nargs="*")
    a = p.parse_args(args)
    obj_type = a.type.encode()
    if obj_type not in TYPES and not a.literally:
        raise GitError(f"invalid object type \"{a.type}\"")
    repo = find_repo() if a.write else None

    def emit(data: bytes):
        oid = repo.odb.write(obj_type, data) if a.write else objects.hash_bytes(obj_type, data)
        out(oid.encode() + b"\n")

    if a.stdin:
        emit(sys.stdin.buffer.read())
    paths = list(a.files)
    if a.stdin_paths:
        paths += [l for l in sys.stdin.read().splitlines() if l]
    for f in paths:
        try:
            emit(Path(f).read_bytes())
        except FileNotFoundError:
            raise GitError(f"could not open '{f}' for reading: No such file or directory")
    return 0


def _resolve_or_die(repo, spec: str) -> str:
    try:
        return revparse.resolve(repo, spec)
    except revparse.PathNotFound:
        raise
    except GitError:
        raise GitError(f"Not a valid object name {spec}")


@command("cat-file")
def cmd_cat_file(args):
    p = parser("cat-file")
    g = p.add_mutually_exclusive_group()
    g.add_argument("-t", dest="mode", action="store_const", const="t")
    g.add_argument("-s", dest="mode", action="store_const", const="s")
    g.add_argument("-e", dest="mode", action="store_const", const="e")
    g.add_argument("-p", dest="mode", action="store_const", const="p")
    p.add_argument("args", nargs="+")
    a = p.parse_args(args)
    repo = find_repo()
    if a.mode is None:
        if len(a.args) != 2:
            p.error("expected <type> <object>")
        want, spec = a.args
        oid = _resolve_or_die(repo, spec)
        t, _ = repo.odb.read(oid)
        if t.decode() != want:
            try:
                oid = revparse.peel(repo, oid, want.encode())
            except GitError:
                raise GitError(f"git cat-file {spec}: bad file")
        _, data = repo.odb.read(oid)
        out(data)
        return 0
    if len(a.args) != 1:
        p.error("too many arguments")
    spec = a.args[0]
    if a.mode == "e":
        oid = _resolve_or_die(repo, spec)
        try:
            repo.odb.read(oid)
        except GitError:
            return 1
        return 0
    oid = _resolve_or_die(repo, spec)
    t, data = repo.odb.read(oid)
    if a.mode == "t":
        out(t + b"\n")
    elif a.mode == "s":
        out(b"%d\n" % len(data))
    elif t == b"tree":
        for e in parse_tree(data):
            out(format_tree_line(e) + b"\n")
    else:
        out(data)
    return 0


def _ls_tree_walk(repo, tree_oid, prefix, a, specs):
    """Yield (entry, full path) for ls-tree, honoring -r/-t/-d and pathspecs."""
    for e in parse_tree(repo.odb.read(tree_oid)[1]):
        path = prefix + e.name
        if specs:
            exact = any(s == path for s in specs)
            inside = any(path.startswith(s.rstrip(b"/") + b"/") for s in specs)
            leads = any(s.startswith(path + b"/") for s in specs)
            if not (exact or inside or leads):
                continue
            if e.is_tree and leads and not exact and not inside:
                # On the way to a pathspec: descend, show only with -t.
                if a.t and not a.d:
                    yield e, path
                yield from _ls_tree_walk(repo, e.oid, path + b"/", a, specs)
                continue
        if e.is_tree and a.r:
            if a.t or a.d:
                yield e, path
            yield from _ls_tree_walk(repo, e.oid, path + b"/", a, specs)
        elif a.d and not e.is_tree:
            continue
        else:
            yield e, path


@command("ls-tree")
def cmd_ls_tree(args):
    p = parser("ls-tree")
    p.add_argument("-r", action="store_true")
    p.add_argument("-t", action="store_true")
    p.add_argument("-d", action="store_true")
    p.add_argument("-l", "--long", dest="long", action="store_true")
    p.add_argument("-z", action="store_true")
    p.add_argument("--name-only", "--name-status", dest="name_only", action="store_true")
    p.add_argument("--object-only", action="store_true")
    p.add_argument("--full-tree", action="store_true")
    p.add_argument("--full-name", action="store_true")
    p.add_argument("tree")
    p.add_argument("paths", nargs="*")
    # git takes `--abbrev` or `--abbrev=N`, never a separate value.
    abbrev = None
    rest = []
    for arg in args:
        if arg == "--abbrev":
            abbrev = 7
        elif arg.startswith("--abbrev="):
            abbrev = max(4, int(arg.split("=", 1)[1]))
        else:
            rest.append(arg)
    a = p.parse_args(rest)
    a.abbrev = abbrev
    repo = find_repo()
    tree = _resolve_or_die(repo, a.tree)
    try:
        tree = revparse.peel(repo, tree, b"tree")
    except GitError:
        raise GitError("not a tree object")
    # A spec "dir" names the tree entry itself; "dir/" lists its contents.
    specs = [s.replace("\\", "/").encode() for s in a.paths]
    end = b"\0" if a.z else b"\n"
    for e, path in _ls_tree_walk(repo, tree, b"", a, specs):
        shown = path if a.z else _quote(path)
        oid = e.oid.encode()
        if a.abbrev:
            oid = revparse.short_id(repo, e.oid, a.abbrev).encode()
        if a.object_only:
            line = oid
        elif a.name_only:
            line = shown
        elif a.long:
            size = b"-"
            if e.type == b"blob":
                size = b"%d" % len(repo.odb.read(e.oid)[1])
            line = b"%06o %s %s %7s\t%s" % (e.mode, e.type, oid, size, shown)
        else:
            line = b"%06o %s %s\t%s" % (e.mode, e.type, oid, shown)
        out(line + end)
    return 0


def _quote(path: bytes) -> bytes:
    from pygit.quote import quote_path
    return quote_path(path)


@command("rev-parse")
def cmd_rev_parse(args):
    verify = quiet = False
    short = None
    abbrev_ref = symbolic_full = False
    repo = None
    results = []

    def get_repo():
        nonlocal repo
        if repo is None:
            repo = find_repo()
        return repo

    revs = []
    for arg in args:
        if arg == "--verify":
            verify = True
        elif arg in ("-q", "--quiet"):
            quiet = True
        elif arg == "--short":
            short = 7
        elif arg.startswith("--short="):
            short = max(4, int(arg.split("=", 1)[1]))
        elif arg == "--abbrev-ref" or arg == "--abbrev-ref=strict":
            abbrev_ref = True
        elif arg == "--symbolic-full-name":
            symbolic_full = True
        elif arg == "--git-dir":
            r = get_repo()
            if r.worktree is not None and Path.cwd().resolve() == r.worktree and r.gitdir == r.worktree / ".git":
                results.append(b".git")
            else:
                results.append(str(r.gitdir).replace("\\", "/").encode())
        elif arg == "--absolute-git-dir":
            results.append(str(get_repo().gitdir).replace("\\", "/").encode())
        elif arg == "--show-toplevel":
            r = get_repo()
            if r.worktree is None:
                raise GitError("this operation must be run in a work tree")
            results.append(str(r.worktree).replace("\\", "/").encode())
        elif arg == "--show-prefix":
            r = get_repo()
            rel = Path.cwd().resolve().relative_to(r.worktree).as_posix() if r.worktree else ""
            results.append((rel + "/" if rel not in ("", ".") else "").encode())
        elif arg == "--is-inside-work-tree":
            r = get_repo()
            inside = r.worktree is not None and r.gitdir not in [Path.cwd().resolve(), *Path.cwd().resolve().parents]
            results.append(b"true" if inside else b"false")
        elif arg == "--is-inside-git-dir":
            r = get_repo()
            cwd = Path.cwd().resolve()
            results.append(b"true" if cwd == r.gitdir or r.gitdir in cwd.parents else b"false")
        elif arg == "--is-bare-repository":
            results.append(b"true" if get_repo().bare else b"false")
        elif arg == "--":
            break
        elif arg.startswith("-"):
            results.append(arg.encode())
        else:
            revs.append(arg)
    if verify and len(revs) != 1:
        if quiet:
            return 1
        raise GitError("Needed a single revision")
    for spec in revs:
        r = get_repo()
        try:
            oid = revparse.resolve(r, spec)
            if verify:
                r.odb.read(oid)
        except GitError as e:
            if verify:
                if quiet:
                    return 1
                raise GitError("Needed a single revision")
            raise e
        if abbrev_ref or symbolic_full:
            got = revparse.resolve_ref_name(r, spec if spec not in ("@", "") else "HEAD")
            name = None
            if got:
                full = got[1]
                target = Refs(r).symbolic_target(full)
                name = target or full
            if name is None:
                results.append(b"" if symbolic_full else spec.encode())
                continue
            if abbrev_ref:
                for pre in ("refs/heads/", "refs/tags/", "refs/remotes/", "refs/"):
                    if name.startswith(pre):
                        name = name[len(pre):]
                        break
            results.append(name.encode())
            continue
        if short:
            oid = revparse.short_id(r, oid, short)
        results.append(oid.encode())
    for line in results:
        out(line + b"\n")
    return 0


@command("update-ref")
def cmd_update_ref(args):
    p = parser("update-ref")
    p.add_argument("-m", dest="message", default="")
    p.add_argument("-d", dest="delete", action="store_true")
    p.add_argument("--no-deref", action="store_true")
    p.add_argument("ref")
    p.add_argument("newvalue", nargs="?")
    p.add_argument("oldvalue", nargs="?")
    a = p.parse_args(args)
    repo = find_repo()
    refs = Refs(repo)
    if a.delete:
        name = a.ref if a.no_deref else refs.resolve(a.ref)[1]
        refs.delete(name)
        return 0
    if a.newvalue is None:
        p.error("missing new value")
    new = _resolve_or_die(repo, a.newvalue)
    old = _resolve_or_die(repo, a.oldvalue) if a.oldvalue and a.oldvalue != "" and set(a.oldvalue) != {"0"} \
        else (objects.ZERO_ID if a.oldvalue else None)
    refs.update(a.ref, new, a.message, old=old, no_deref=a.no_deref)
    return 0


@command("symbolic-ref")
def cmd_symbolic_ref(args):
    p = parser("symbolic-ref")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--short", action="store_true")
    p.add_argument("-m", dest="message", default="")
    p.add_argument("-d", "--delete", action="store_true")
    p.add_argument("name")
    p.add_argument("target", nargs="?")
    a = p.parse_args(args)
    repo = find_repo()
    refs = Refs(repo)
    if a.delete:
        refs.delete(a.name)
        return 0
    if a.target is not None:
        if not a.target.startswith("refs/"):
            raise GitError(f"Refusing to point {a.name} outside of refs/")
        refs.set_symbolic(a.name, a.target, a.message)
        return 0
    target = refs.symbolic_target(a.name)
    if target is None:
        if a.quiet:
            return 1
        raise GitError(f"ref {a.name} is not a symbolic ref")
    if a.short:
        for pre in ("refs/heads/", "refs/tags/", "refs/remotes/"):
            if target.startswith(pre):
                target = target[len(pre):]
                break
    out(target.encode() + b"\n")
    return 0


@command("commit-tree")
def cmd_commit_tree(args):
    p = parser("commit-tree")
    p.add_argument("-p", dest="parents", action="append", default=[])
    p.add_argument("-m", dest="messages", action="append", default=[])
    p.add_argument("-F", dest="files", action="append", default=[])
    p.add_argument("tree")
    a = p.parse_args(args)
    repo = find_repo()
    from pygit.ident import ident
    tree = revparse.peel(repo, _resolve_or_die(repo, a.tree), b"tree")
    parents = [revparse.peel(repo, _resolve_or_die(repo, x), b"commit") for x in a.parents]
    if a.messages or a.files:
        parts = [m.encode() for m in a.messages]
        for f in a.files:
            parts.append(sys.stdin.buffer.read() if f == "-" else Path(f).read_bytes())
        msg = b"\n\n".join(x.rstrip(b"\n") if i < len(parts) - 1 else x for i, x in enumerate(parts))
        if a.messages and not msg.endswith(b"\n"):
            msg += b"\n"
    else:
        msg = sys.stdin.buffer.read()
    c = Commit(tree, parents, ident(repo, "author"), ident(repo, "committer"), msg)
    out(repo.odb.write(b"commit", c.serialize()).encode() + b"\n")
    return 0


@command("mktree")
def cmd_mktree(args):
    """Build a tree from ls-tree formatted lines on stdin."""
    p = parser("mktree")
    p.add_argument("-z", action="store_true")
    p.add_argument("--missing", action="store_true")
    a = p.parse_args(args)
    repo = find_repo()
    data = sys.stdin.buffer.read()
    entries = []
    for line in data.split(b"\0" if a.z else b"\n"):
        if not line:
            continue
        meta, _, name = line.partition(b"\t")
        mode, _, rest = meta.partition(b" ")
        _, _, oid = rest.partition(b" ")
        if not a.z and name.startswith(b'"'):
            from pygit.quote import unquote_path
            name = unquote_path(name)
        if not a.missing and not repo.odb.exists(oid.decode()):
            raise GitError(f"entry '{name.decode(errors='replace')}' object {oid.decode()} is unavailable")
        entries.append(TreeEntry(int(mode, 8), name, oid.decode()))
    out(repo.odb.write(b"tree", objects.serialize_tree(entries)).encode() + b"\n")
    return 0

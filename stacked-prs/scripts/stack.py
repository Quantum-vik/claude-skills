#!/usr/bin/env python3
"""Plan, check, document, cut and maintain a stack of small PRs from one large change.

Subcommands (run from the repo root; the plan defaults to .stack/stack.toml):
  snapshot              commit the staged index as a dangling commit (no branch moves) and print its sha
  init                  write a plan skeleton and list every changed file with its size
  check                 unassigned files, PR sizes against the cap, Python import order problems and cycles
  doc [--out FILE]      write the stack document (map, per-PR cards, file index, change procedure)
  cut                   create the PR branches with plumbing only; your checkout and working tree are untouched
  verify --cmd CMD      run CMD in a temporary worktree of each branch and report pass/fail
  sync N                merge branch N into N+1, N+1 into N+2, ... up to the top (stops on a conflict)
"""
import argparse
import ast
import collections
import datetime
import fnmatch
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib

PLAN_DEFAULT = ".stack/stack.toml"


def git(*args, env=None, check=True, data=None):
    r = subprocess.run(["git", *args], capture_output=True, env=env, input=data)
    if check and r.returncode:
        sys.exit(f"git {' '.join(args)} failed: {r.stderr.decode().strip()}")
    return r.stdout.decode("utf-8", "replace")


def matches(path, pattern):
    return path == pattern or (pattern.endswith("/") and path.startswith(pattern)) or fnmatch.fnmatch(path, pattern)


class Plan:
    def __init__(self, path):
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
        self.base, self.source = cfg["base"], cfg["source"]
        self.prefix, self.cap = cfg["branch_prefix"], cfg.get("cap", 700)
        self.exclude, self.prs = cfg.get("exclude", []), cfg.get("pr", [])
        for n, pr in enumerate(self.prs, 1):
            pr["n"], pr["branch"] = n, f"{self.prefix}{n:02d}-{pr['name']}"
        self.fork = git("merge-base", self.base, self.source).strip()
        self.changed = {}  # path -> (status, added, deleted)
        stats = {}
        for line in git("diff", "--numstat", "--no-renames", self.fork, self.source).splitlines():
            a, d, p = line.split("\t", 2)
            stats[p] = (int(a) if a != "-" else 0, int(d) if d != "-" else 0)
        for line in git("diff", "--name-status", "--no-renames", self.fork, self.source).splitlines():
            st, p = line.split("\t", 1)
            self.changed[p] = (st[0], *stats.get(p, (0, 0)))

    def pr_of(self, path):
        """Longest matching pattern wins; None = excluded, 0 = unassigned."""
        if any(matches(path, x) for x in self.exclude):
            return None
        best = (-1, 0)
        for pr in self.prs:
            for x in pr["paths"]:
                if matches(path, x) and len(x) > best[0]:
                    best = (len(x), pr["n"])
        return best[1]

    def files_of(self, n):
        return sorted(p for p in self.changed if self.pr_of(p) == n)

    def size(self, n):
        counted = deleted = 0
        for p in self.files_of(n):
            st, a, d = self.changed[p]
            if st == "D":
                deleted += d
            else:
                counted += a + d
        return counted, deleted


# ---------- Python import analysis ----------

def read_tree(rev):
    """All .py files at rev -> source text."""
    paths = [p for p in git("ls-tree", "-r", "--name-only", rev).splitlines() if p.endswith(".py")]
    out = subprocess.run(["git", "cat-file", "--batch"], input="".join(f"{rev}:{p}\n" for p in paths).encode(),
                         capture_output=True).stdout
    files, i = {}, 0
    for p in paths:
        nl = out.index(b"\n", i)
        size = int(out[i:nl].split()[2])
        files[p] = out[nl + 1:nl + 1 + size].decode("utf-8", "replace")
        i = nl + 1 + size + 1
    return files


def modname(path):
    m = path[:-3].replace("/", ".")
    return m[:-9] if m.endswith(".__init__") else m


class Tree:
    def __init__(self, files):
        self.files = files
        self.mods = {modname(p): p for p in files}
        self.dirs = {".".join(p.split("/")[:i]) for p in files for i in range(1, p.count("/") + 1)}
        self._info = {}

    def info(self, path):
        """(top-level names, has star import, [(module, name|None)])."""
        if path in self._info:
            return self._info[path]
        try:
            tree = ast.parse(self.files[path])
        except SyntaxError:
            self._info[path] = (set(), True, [])
            return self._info[path]
        names, star = set(), False

        def top(body):
            nonlocal star
            for n in body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(n.name)
                elif isinstance(n, ast.Assign):
                    names.update(x.id for t in n.targets for x in ast.walk(t) if isinstance(x, ast.Name))
                elif isinstance(n, (ast.AnnAssign, ast.AugAssign)) and isinstance(n.target, ast.Name):
                    names.add(n.target.id)
                elif isinstance(n, ast.Import):
                    names.update((a.asname or a.name).split(".")[0] for a in n.names)
                elif isinstance(n, ast.ImportFrom):
                    star |= any(a.name == "*" for a in n.names)
                    names.update(a.asname or a.name for a in n.names if a.name != "*")
                elif isinstance(n, (ast.If, ast.Try, ast.With)):
                    for b in ("body", "orelse", "finalbody"):
                        top(getattr(n, b, None) or [])
                    for h in getattr(n, "handlers", []):
                        top(h.body)

        top(tree.body)
        imports = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imports += [(a.name, None) for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                mod = n.module or ""
                if n.level:
                    pkg = modname(path).split(".")
                    if not path.endswith("__init__.py"):
                        pkg = pkg[:-1]
                    pkg = pkg[:len(pkg) - (n.level - 1)]
                    mod = ".".join(pkg + ([mod] if mod else []))
                imports += [(mod, None if a.name == "*" else a.name) for a in n.names]
        self._info[path] = (names, star, imports)
        return self._info[path]

    def resolves(self, mod, name):
        if mod not in self.mods and mod not in self.dirs:
            return False
        if name is None or f"{mod}.{name}" in self.mods or f"{mod}.{name}" in self.dirs:
            return True
        if mod not in self.mods:
            return False
        names, star, _ = self.info(self.mods[mod])
        return star or name in names

    def owner(self, mod, name):
        return self.mods.get(f"{mod}.{name}") if name and f"{mod}.{name}" in self.mods else self.mods.get(mod)


def constraints(plan):
    """Edges (a, b, why) meaning PR(a) must be <= PR(b), for changed Python files."""
    old, new = Tree(read_tree(plan.fork)), Tree(read_tree(plan.source))
    roots = {p.split("/")[0] if "/" in p else modname(p) for p in [*old.files, *new.files]}
    py = [p for p in plan.changed if p.endswith(".py")]
    edges = []
    for a in py:
        for tree, other, label in ((new, old, "new"), (old, new, "old")):
            if a not in tree.files:
                continue
            for mod, name in tree.info(a)[2]:
                if not mod or mod.split(".")[0] not in roots or other.resolves(mod, name):
                    continue
                b = tree.owner(mod, name)
                if not b or b == a or b not in plan.changed:
                    continue
                what = f"{mod}:{name}" if name else mod
                if label == "new":  # new a needs something only the new b has -> b first
                    edges.append((b, a, f"new {a} imports {what}, which only exists after {b} changes"))
                else:  # old a needs something the new b removes -> a must change no later than b
                    edges.append((a, b, f"old {a} imports {what}, which {b} removes"))
    return edges


def cycles(nodes, edges):
    graph = collections.defaultdict(set)
    for a, b, _ in edges:
        graph[a].add(b)
    index, low, stack, on, out, counter = {}, {}, [], set(), [], [0]
    sys.setrecursionlimit(100000)

    def visit(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in graph[v]:
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))

    for v in sorted(nodes):
        if v not in index:
            visit(v)
    return out


# ---------- subcommands ----------

def cmd_snapshot(_):
    tree = git("write-tree").strip()
    print(git("commit-tree", tree, "-p", "HEAD", "-m", "stack snapshot of the staged index").strip())


def cmd_init(args):
    if not os.path.exists(args.plan):
        os.makedirs(os.path.dirname(args.plan) or ".", exist_ok=True)
        with open(args.plan, "w") as f:
            f.write('base = "main"            # branch the bottom PR targets\n'
                    'source = "HEAD"          # commit holding the whole change (see `snapshot`)\n'
                    'branch_prefix = "stack/feature-"\n'
                    'cap = 700                # max added+deleted lines in kept files\n'
                    'exclude = []             # paths that stay out of the stack\n\n'
                    '[[pr]]\nname = "first-layer"\ntitle = "feat: first layer"\npaths = []\n')
        print(f"wrote {args.plan}; set base/source, then fill the [[pr]] blocks")
    plan = Plan(args.plan)
    by_dir = collections.defaultdict(list)
    for p, (st, a, d) in sorted(plan.changed.items()):
        by_dir[os.path.dirname(p) or "."].append(f"  {st} {a:>5} {d:>5}  {p}")
    for d, rows in sorted(by_dir.items()):
        print(f"{d}/")
        print("\n".join(rows))
    adds = sum(v[1] for v in plan.changed.values())
    dels = sum(v[2] for v in plan.changed.values())
    print(f"\n{len(plan.changed)} files, +{adds} -{dels}")


def cmd_check(args):
    plan, bad = Plan(args.plan), 0
    unassigned = [p for p in plan.changed if plan.pr_of(p) == 0]
    if unassigned:
        bad = 1
        print("UNASSIGNED (add to a PR or to exclude):", *unassigned, sep="\n  ")
    excluded = [p for p in plan.changed if plan.pr_of(p) is None]
    if excluded:
        print("excluded:", *excluded, sep="\n  ")
    for pr in plan.prs:
        files = plan.files_of(pr["n"])
        counted, deleted = plan.size(pr["n"])
        flag = "  OVER CAP" if counted > plan.cap else ""
        flag += "  EMPTY" if not files else ""
        print(f"{pr['n']:02d} {pr['name']:<32} {counted:>6} lines  {deleted:>6} deleted  {len(files):>3} files{flag}")
    edges = constraints(plan)
    wrong = [(a, b, w) for a, b, w in edges if (plan.pr_of(a) or 0) > (plan.pr_of(b) or 0) and plan.pr_of(b)]
    if wrong:
        bad = 1
        print(f"\nORDER PROBLEMS ({len(wrong)}): left file must land in the same or an earlier PR than the right file")
        for a, b, w in sorted(wrong, key=lambda e: (plan.pr_of(e[1]), e[0])):
            print(f"  PR {plan.pr_of(a):02d} {a}  ->  PR {plan.pr_of(b):02d} {b}\n      {w}")
    for comp in cycles(plan.changed, edges):
        prs = sorted({plan.pr_of(p) for p in comp if plan.pr_of(p)})
        if len(prs) > 1:
            bad = 1
            print(f"\nCYCLE across PRs {prs}: these files need each other; same PR, or split one file (expand/contract):")
            print(*(f"  PR {plan.pr_of(p):02d} {p}" for p in comp), sep="\n")
    print("\nOK" if not bad else "\nFIX the problems above, then re-run check")
    return bad


def cmd_doc(args):
    plan = Plan(args.plan)
    edges = constraints(plan)
    deps = collections.defaultdict(set)
    for a, b, _ in edges:
        pa, pb = plan.pr_of(a), plan.pr_of(b)
        if pa and pb and pa != pb:
            deps[pb].add(pa)
    used_by = collections.defaultdict(set)
    for b, ps in deps.items():
        for a in ps:
            used_by[a].add(b)
    L = [f"# Stacked PR plan: {plan.prefix.rstrip('-/')}", "",
         f"Generated {datetime.date.today()} from `{plan.source}` against `{plan.base}` (fork point `{plan.fork[:10]}`).",
         f"Cap: {plan.cap} lines per PR (added + deleted lines in files that remain; whole-file deletions are listed, "
         "not counted).", "",
         "## Stack type", "", "TODO: review slices (only the top branch must run) or runnable (every branch passes "
         "its checks). Say which, and why.", "",
         "## Stack map", "", "| # | Branch | Base | Title | Lines | Deleted | Files |", "|---|---|---|---|---|---|---|"]
    for pr in plan.prs:
        base = plan.base if pr["n"] == 1 else plan.prs[pr["n"] - 2]["branch"]
        counted, deleted = plan.size(pr["n"])
        over = " ⚠" if counted > plan.cap else ""
        L.append(f"| {pr['n']:02d} | `{pr['branch']}` | `{base}` | {pr['title']} | {counted}{over} | {deleted} | "
                 f"{len(plan.files_of(pr['n']))} |")
    L += ["", "## PRs", ""]
    for pr in plan.prs:
        n = pr["n"]
        counted, deleted = plan.size(n)
        L += [f"### {n:02d} · {pr['name']}", "", f"**Title:** {pr['title']}  ",
              f"**Branch:** `{pr['branch']}`  ", f"**Size:** {counted} lines, {deleted} deleted  ",
              f"**Purpose:** {pr.get('purpose', 'TODO')}  ",
              f"**Depends on:** {', '.join(f'PR {d:02d}' for d in sorted(deps[n])) or 'nothing in the stack'}  ",
              f"**Used by:** {', '.join(f'PR {u:02d}' for u in sorted(used_by[n])) or 'nothing in the stack'}  ",
              f"**Check:** {pr.get('check', 'TODO')}", "",
              "| Status | + | − | File |", "|---|---|---|---|"]
        L += [f"| {plan.changed[p][0]} | {plan.changed[p][1]} | {plan.changed[p][2]} | `{p}` |" for p in plan.files_of(n)]
        L.append("")
    L += ["## File → PR index", "", "| File | PR |", "|---|---|"]
    for p in sorted(plan.changed):
        n = plan.pr_of(p)
        L.append(f"| `{p}` | {'excluded' if n is None else f'{n:02d}' if n else 'UNASSIGNED'} |")
    L += ["", "## Making a change later", "",
          "1. Find the file in the index above; that PR owns it.",
          "2. Check out that PR's branch, commit the change (commit subject in the repo's format).",
          f"3. Carry it up the stack: `python3 {sys.argv[0]} sync <that PR number>` (merges, never rebases).",
          "4. Re-run that PR's check, then the top branch's full tests.",
          "5. A new file: add its path to the plan under the PR that first needs it and run `check`.",
          "6. Moving a file to another PR after the branches exist: change the plan, delete the local branches "
          "from that PR up, and `cut` again (only before anything is pushed, or with the reviewers' agreement).", ""]
    with open(args.out, "w") as f:
        f.write("\n".join(L))
    print(f"wrote {args.out}")


def cmd_cut(args):
    plan = Plan(args.plan)
    if any(plan.pr_of(p) == 0 for p in plan.changed):
        sys.exit("unassigned files; run check first")
    for pr in plan.prs:
        if git("rev-parse", "--verify", "--quiet", f"refs/heads/{pr['branch']}", check=False).strip():
            sys.exit(f"branch {pr['branch']} already exists; delete it or rename the plan first")
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": os.path.join(tmp, "index")}
        parent = plan.fork
        for pr in plan.prs:
            git("read-tree", parent, env=env)
            for p in plan.files_of(pr["n"]):
                entry = git("ls-tree", plan.source, "--", p).strip()
                if entry:
                    mode, _, sha = entry.split("\t")[0].split()
                    git("update-index", "--add", "--cacheinfo", f"{mode},{sha},{p}", env=env)
                else:
                    git("update-index", "--force-remove", "--", p, env=env)
            tree = git("write-tree", env=env).strip()
            parent = git("commit-tree", tree, "-p", parent, "-m", pr["title"]).strip()
            git("update-ref", f"refs/heads/{pr['branch']}", parent, "")
            print(f"{pr['n']:02d} {pr['branch']} {parent[:10]}")
    rest = git("diff", "--stat", parent, plan.source, "--", ".", *[f":!{x}" for x in plan.exclude]).strip()
    print("top branch == source (excluded paths aside)" if not rest else f"MISMATCH between top and source:\n{rest}")


def worktree(branch):
    path = tempfile.mkdtemp(prefix="stack-")
    os.rmdir(path)
    git("worktree", "add", "--quiet", path, branch)
    return path


def cmd_verify(args):
    plan, failed = Plan(args.plan), []
    for pr in plan.prs[args.start - 1:]:
        path = worktree(pr["branch"])
        try:
            r = subprocess.run(args.cmd, shell=True, cwd=path, capture_output=True, text=True)
            ok = r.returncode == 0
            print(f"{pr['n']:02d} {pr['branch']}: {'pass' if ok else 'FAIL'}")
            if not ok:
                failed.append(pr["n"])
                print("   " + "\n   ".join((r.stdout + r.stderr).strip().splitlines()[-15:]))
        finally:
            git("worktree", "remove", "--force", path)
    print("all pass" if not failed else f"failed: {failed}")
    return 1 if failed else 0


def cmd_sync(args):
    plan = Plan(args.plan)
    for pr in plan.prs[args.n:]:
        below = plan.prs[pr["n"] - 2]["branch"]
        path = worktree(pr["branch"])
        r = subprocess.run(["git", "merge", "--no-edit", below], cwd=path, capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"conflict merging {below} into {pr['branch']}; resolve and commit in {path}, "
                     f"`git worktree remove {path}`, then run sync {pr['n']}")
        git("worktree", "remove", path)
        print(f"{pr['n']:02d} {pr['branch']}: merged {below}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", default=PLAN_DEFAULT)
    sub = ap.add_subparsers(dest="cmd_name", required=True)
    sub.add_parser("snapshot").set_defaults(fn=cmd_snapshot)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    d = sub.add_parser("doc")
    d.add_argument("--out", default=".stack/STACK.md")
    d.set_defaults(fn=cmd_doc)
    sub.add_parser("cut").set_defaults(fn=cmd_cut)
    v = sub.add_parser("verify")
    v.add_argument("--cmd", required=True)
    v.add_argument("--from", dest="start", type=int, default=1)
    v.set_defaults(fn=cmd_verify)
    s = sub.add_parser("sync")
    s.add_argument("n", type=int)
    s.set_defaults(fn=cmd_sync)
    args = ap.parse_args()
    if shutil.which("git") is None:
        sys.exit("git not found")
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()

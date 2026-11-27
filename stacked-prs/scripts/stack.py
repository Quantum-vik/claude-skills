#!/usr/bin/env python3
"""Plan, check, document, cut and maintain a stack of small PRs from one large change.

Subcommands (run from the repo root; the plan defaults to .stack/stack.toml):
  snapshot              commit the staged index as a dangling commit (no branch moves) and print its sha
  init                  write a plan skeleton and list every changed file with its size
  check                 unassigned files, PR sizes against the cap, Python import order problems and cycles
  doc [--out F] [--prs] write the stack document: session guide, state, map, per-PR cards with a per-file
                        change summary, dependency reasons, problems, file index, change procedure
  cut                   create the PR branches with plumbing only; your checkout and working tree are untouched
  verify --cmd CMD      run CMD in a temporary worktree of each branch and report pass/fail
  sync N                merge branch N into N+1, N+1 into N+2, ... up to the top (stops on a conflict)
"""
import argparse
import ast
import collections
import datetime
import difflib
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib

PLAN_DEFAULT = ".stack/stack.toml"
SELF = os.path.abspath(__file__)


def git(*args, env=None, check=True):
    r = subprocess.run(["git", *args], capture_output=True, env=env)
    if check and r.returncode:
        sys.exit(f"git {' '.join(args)} failed: {r.stderr.decode().strip()}")
    return r.stdout.decode("utf-8", "replace")


def matches(path, pattern):
    return path == pattern or (pattern.endswith("/") and path.startswith(pattern)) or fnmatch.fnmatch(path, pattern)


class Plan:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
        self.base, self.source = cfg["base"], cfg["source"]
        self.prefix, self.cap = cfg["branch_prefix"], cfg.get("cap", 700)
        self.exclude, self.prs = cfg.get("exclude", []), cfg.get("pr", [])
        self.stack_type = cfg.get("stack_type", "")
        self.notes = cfg.get("notes", "")
        for n, pr in enumerate(self.prs, 1):
            pr["n"], pr["branch"] = n, f"{self.prefix}{n:02d}-{pr['name']}"
        self.source_sha = git("rev-parse", self.source).strip()
        self.fork = git("merge-base", self.base, self.source).strip()
        self.changed = {}  # path -> (status, added, deleted, binary)
        stats = {}
        for line in git("diff", "--numstat", "--no-renames", self.fork, self.source).splitlines():
            a, d, p = line.split("\t", 2)
            stats[p] = (int(a) if a != "-" else 0, int(d) if d != "-" else 0, a == "-")
        for line in git("diff", "--name-status", "--no-renames", self.fork, self.source).splitlines():
            st, p = line.split("\t", 1)
            self.changed[p] = (st[0], *stats.get(p, (0, 0, False)))

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
            st, a, d, _ = self.changed[p]
            if st == "D":
                deleted += d
            else:
                counted += a + d
        return counted, deleted

    def base_of(self, pr):
        return self.base if pr["n"] == 1 else self.prs[pr["n"] - 2]["branch"]


def read_blobs(rev, paths):
    """path -> text for the given paths at rev (missing paths are skipped)."""
    paths = sorted(paths)
    out = subprocess.run(["git", "cat-file", "--batch"], input="".join(f"{rev}:{p}\n" for p in paths).encode(),
                         capture_output=True).stdout
    files, i = {}, 0
    for p in paths:
        nl = out.index(b"\n", i)
        header = out[i:nl].split()
        if header[-1] == b"missing":
            i = nl + 1
            continue
        size = int(header[2])
        files[p] = out[nl + 1:nl + 1 + size].decode("utf-8", "replace")
        i = nl + 1 + size + 1
    return files


def read_tree(rev):
    return read_blobs(rev, [p for p in git("ls-tree", "-r", "--name-only", rev).splitlines() if p.endswith(".py")])


def modname(path):
    m = path[:-3].replace("/", ".")
    return m[:-9] if m.endswith(".__init__") else m


# ---------- Python import analysis ----------

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


def trees(plan):
    return Tree(read_tree(plan.fork)), Tree(read_tree(plan.source))


def constraints(plan, old, new):
    """Edges (a, b, symbol, why): PR(a) must be <= PR(b), for changed Python files."""
    roots = {p.split("/")[0] if "/" in p else modname(p) for p in [*old.files, *new.files]}
    edges = []
    for a in (p for p in plan.changed if p.endswith(".py")):
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
                    edges.append((b, a, what, f"new {a} imports {what}, which only exists after {b} changes"))
                else:  # old a needs something the new b removes -> a must change no later than b
                    edges.append((a, b, what, f"old {a} imports {what}, which {b} removes"))
    return edges


def cycles(nodes, edges):
    graph = collections.defaultdict(set)
    for a, b, *_ in edges:
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


def problems(plan, edges):
    """Lines describing everything check complains about; empty list = clean."""
    out = []
    unassigned = [p for p in plan.changed if plan.pr_of(p) == 0]
    if unassigned:
        out += ["UNASSIGNED (add to a PR or to exclude):", *(f"  {p}" for p in unassigned)]
    wrong = [e for e in edges if (plan.pr_of(e[0]) or 0) > (plan.pr_of(e[1]) or 0) and plan.pr_of(e[1])]
    if wrong:
        out.append(f"ORDER PROBLEMS ({len(wrong)}): left file must land in the same or an earlier PR than the right")
        for a, b, _, why in sorted(wrong, key=lambda e: (plan.pr_of(e[1]), e[0])):
            out += [f"  PR {plan.pr_of(a):02d} {a}  ->  PR {plan.pr_of(b):02d} {b}", f"      {why}"]
    for comp in cycles(plan.changed, edges):
        prs = sorted({plan.pr_of(p) for p in comp if plan.pr_of(p)})
        if len(prs) > 1:
            out.append(f"CYCLE across PRs {prs}: these files need each other; same PR, or split one (expand/contract):")
            out += [f"  PR {plan.pr_of(p):02d} {p}" for p in comp]
    return out


# ---------- change summaries ----------

def _sig(fn):
    returns = f" -> {ast.unparse(fn.returns)}" if fn.returns else ""
    return f"({ast.unparse(fn.args)}){returns}"


def symbols(src):
    """name -> dict(kind, start, end, sig, body, members) for top-level defs, classes and assignments."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None, set()
    lines = src.splitlines()

    def entry(kind, n, sig="", members=None):
        return dict(kind=kind, start=n.lineno, end=n.end_lineno, sig=sig, members=members,
                    body="\n".join(lines[n.lineno - 1:n.end_lineno]))

    out, imports = {}, set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[n.name] = entry("def", n, _sig(n))
        elif isinstance(n, ast.ClassDef):
            members = {m.name: entry("def", m, _sig(m)) for m in n.body
                       if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
            bases = ", ".join(ast.unparse(b) for b in n.bases)
            out[n.name] = entry("class", n, f"({bases})" if bases else "", members)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if isinstance(t, ast.Name):
                    out[t.id] = entry("const", n)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            imports.add(ast.unparse(n))
    return out, imports


def churn(a, b):
    """(added, removed) lines between two texts."""
    add = rem = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a.splitlines(), b.splitlines(), autojunk=False).get_opcodes():
        if tag != "equal":
            rem += i2 - i1
            add += j2 - j1
    return add, rem


def hunks(a, b):
    """New-side line ranges that changed (pure deletions shown as the line they sit before)."""
    out = []
    for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, a.splitlines(), b.splitlines(), autojunk=False).get_opcodes():
        if tag != "equal":
            out.append(f"L{j1 + 1}" if j2 - j1 <= 1 else f"L{j1 + 1}–{j2}")
    return out


def short(items, limit=12):
    items = list(items)
    return ", ".join(items[:limit]) + (f", … +{len(items) - limit} more" if len(items) > limit else "")


def where(s):
    return f"L{s['start']}–{s['end']}" if s["end"] != s["start"] else f"L{s['start']}"


def describe(plan, old_src, new_src):
    """Per changed file: list of markdown bullet strings describing the change."""
    summary = {}
    added_by_name, removed_by_name = collections.defaultdict(list), collections.defaultdict(list)
    parsed = {}
    for p, (st, a, d, binary) in plan.changed.items():
        if binary:
            summary[p] = ["binary file"]
            continue
        old, new = old_src.get(p), new_src.get(p)
        if p.endswith(".py"):
            parsed[p] = (symbols(old) if old is not None else ({}, set()),
                         symbols(new) if new is not None else ({}, set()))
            osyms, nsyms = parsed[p][0][0] or {}, parsed[p][1][0] or {}
            for name in set(nsyms) - set(osyms):
                if nsyms[name]["kind"] != "const":
                    added_by_name[name].append(p)
            for name in set(osyms) - set(nsyms):
                if osyms[name]["kind"] != "const":
                    removed_by_name[name].append(p)
    for p, (st, a, d, binary) in plan.changed.items():
        if binary:
            continue
        old, new = old_src.get(p), new_src.get(p)
        lines = []
        if p not in parsed or parsed[p][0][0] is None or parsed[p][1][0] is None:
            if old is None:
                lines.append(f"new file, {len((new or '').splitlines())} lines")
            elif new is None:
                lines.append(f"deleted, was {len(old.splitlines())} lines")
            else:
                lines.append(f"changed at {short(hunks(old, new), 10)}")
            summary[p] = lines
            continue
        (osyms, oimps), (nsyms, nimps) = parsed[p]

        def moved(name, here, table, word, body, side):
            others = [x for x in table.get(name, []) if x != here]
            if not others:
                return ""
            there = parsed[others[0]][side][0][name]["body"]
            add, rem = churn(there, body) if side == 0 else churn(body, there)
            return f" ({word} `{others[0]}`, " + ("unchanged)" if add == rem == 0 else f"then +{add}/−{rem})")

        if old is None:
            lines.append(f"new file, {len(new.splitlines())} lines")
        elif new is None:
            lines.append(f"deleted, was {len(old.splitlines())} lines")
        for name in sorted(set(nsyms) - set(osyms), key=lambda k: nsyms[k]["start"]):
            s = nsyms[name]
            lines.append(f"added {s['kind']} `{name}{s['sig']}` {where(s)}"
                         f"{moved(name, p, removed_by_name, 'moved from', s['body'], 0)}")
        for name in sorted(set(osyms) - set(nsyms), key=lambda k: osyms[k]["start"]):
            s = osyms[name]
            lines.append(f"removed {s['kind']} `{name}` (was {where(s)})"
                         f"{moved(name, p, added_by_name, 'moved to', s['body'], 1)}")
        for name in sorted(set(osyms) & set(nsyms), key=lambda k: nsyms[k]["start"]):
            o, n = osyms[name], nsyms[name]
            if o["body"] == n["body"]:
                continue
            add, rem = churn(o["body"], n["body"])
            text = f"changed {n['kind']} `{name}` {where(n)} (+{add}/−{rem})"
            if o["sig"] != n["sig"]:
                text += f"; signature `{o['sig']}` → `{n['sig']}`"
            if n["kind"] == "class":
                om, nm = o["members"] or {}, n["members"] or {}
                parts = [f"+`{m}`" for m in nm if m not in om] + [f"−`{m}`" for m in om if m not in nm]
                parts += [f"~`{m}`" + (f" sig `{om[m]['sig']}` → `{nm[m]['sig']}`" if om[m]["sig"] != nm[m]["sig"] else "")
                          for m in nm if m in om and om[m]["body"] != nm[m]["body"]]
                if parts:
                    text += f"; methods {short(parts, 10)}"
            lines.append(text)
        if old is None and nimps:
            lines.append(f"imports: {short(f'`{i}`' for i in sorted(nimps))}")
        if old is not None and new is not None:
            if nimps - oimps:
                lines.append(f"imports added: {short(f'`{i}`' for i in sorted(nimps - oimps))}")
            if oimps - nimps:
                lines.append(f"imports removed: {short(f'`{i}`' for i in sorted(oimps - nimps))}")
            if len(lines) == 0:
                lines.append(f"module-level code changed at {short(hunks(old, new), 10)}")
        summary[p] = lines
    return summary


def pr_numbers(plan):
    if shutil.which("gh") is None:
        return {}
    out = {}
    for pr in plan.prs:
        r = subprocess.run(["gh", "pr", "list", "--head", pr["branch"], "--state", "all", "--json", "number,state,url"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip() not in ("", "[]"):
            out[pr["n"]] = json.loads(r.stdout)[0]
    return out


# ---------- subcommands ----------

def cmd_snapshot(_):
    tree = git("write-tree").strip()
    print(git("commit-tree", tree, "-p", "HEAD", "-m", "stack snapshot of the staged index").strip())


def cmd_init(args):
    if not os.path.exists(args.plan):
        os.makedirs(os.path.dirname(args.plan) or ".", exist_ok=True)
        with open(args.plan, "w") as f:
            f.write('base = "origin/main"     # branch the bottom PR targets (fetch first)\n'
                    'source = "HEAD"          # commit holding the whole change (see `snapshot`)\n'
                    'branch_prefix = "stack/feature-"\n'
                    'cap = 700                # max added+deleted lines in kept files\n'
                    'exclude = []             # paths that stay out of the stack\n'
                    'stack_type = ""          # "review slices" or "runnable", with the reason\n'
                    'notes = ""               # accepted exceptions, decisions, open checks (markdown)\n\n'
                    '[[pr]]\nname = "first-layer"\ntitle = "feat: first layer"\npurpose = ""\ncheck = ""\n'
                    'paths = []\n')
        print(f"wrote {args.plan}; set base/source, then fill the [[pr]] blocks")
    plan = Plan(args.plan)
    by_dir = collections.defaultdict(list)
    for p, (st, a, d, _) in sorted(plan.changed.items()):
        by_dir[os.path.dirname(p) or "."].append(f"  {st} {a:>5} {d:>5}  {p}")
    for d, rows in sorted(by_dir.items()):
        print(f"{d}/")
        print("\n".join(rows))
    adds = sum(v[1] for v in plan.changed.values())
    dels = sum(v[2] for v in plan.changed.values())
    print(f"\n{len(plan.changed)} files, +{adds} -{dels}")


def cmd_check(args):
    plan = Plan(args.plan)
    excluded = [p for p in plan.changed if plan.pr_of(p) is None]
    if excluded:
        print("excluded:", *excluded, sep="\n  ")
    for pr in plan.prs:
        files = plan.files_of(pr["n"])
        counted, deleted = plan.size(pr["n"])
        flag = ("  OVER CAP" if counted > plan.cap else "") + ("  EMPTY" if not files else "")
        print(f"{pr['n']:02d} {pr['name']:<32} {counted:>6} lines  {deleted:>6} deleted  {len(files):>3} files{flag}")
    found = problems(plan, constraints(plan, *trees(plan)))
    print("", *found, sep="\n")
    print("OK" if not found else "FIX the problems above, then re-run check")
    return 1 if found else 0


def cmd_doc(args):
    plan = Plan(args.plan)
    old, new = trees(plan)
    edges = constraints(plan, old, new)
    found = problems(plan, edges)
    paths = list(plan.changed)
    changes = describe(plan, read_blobs(plan.fork, paths), read_blobs(plan.source, paths))
    numbers = pr_numbers(plan) if args.prs else {}
    needs = collections.defaultdict(lambda: collections.defaultdict(set))  # pr -> other pr -> {symbol}
    used_by = collections.defaultdict(set)
    for a, b, what, _ in edges:
        pa, pb = plan.pr_of(a), plan.pr_of(b)
        if pa and pb and pa != pb:
            needs[pb][pa].add(f"`{what}`")
            used_by[pa].add(pb)
    heads = {pr["n"]: git("rev-parse", "--verify", "--quiet", f"refs/heads/{pr['branch']}", check=False).strip()
             for pr in plan.prs}
    top = plan.prs[-1] if plan.prs else None
    top_matches = bool(top and heads[top["n"]]) and subprocess.run(
        ["git", "diff", "--quiet", heads[top["n"]], plan.source_sha, "--", ".", *[f":!{x}" for x in plan.exclude]]
    ).returncode == 0
    total_kept = sum(plan.size(pr["n"])[0] for pr in plan.prs)
    total_deleted = sum(plan.size(pr["n"])[1] for pr in plan.prs)
    run = f"python3 {SELF} --plan {plan.path}"

    L = [f"# Stacked PRs: {plan.prefix.rstrip('-/')}", "",
         f"_Generated {datetime.date.today()} by `stack.py doc` from source `{plan.source_sha[:12]}` against "
         f"`{plan.base}` (fork point `{plan.fork[:12]}`). Regenerate after any change: `{run} doc --out {args.out}`._",
         "", "## Read me first", "",
         "- **What this is:** one large change split into the PRs below. Each PR is based on the one above it in the "
         "map; each branch contains every PR below it, so the **top branch is the whole change**.",
         "- **Use this file instead of reading code.** Every PR card lists each file's change: symbols added, removed, "
         "changed (line ranges in the new file, +/− lines, signature changes), code moved between files, and import "
         "changes. Open a file only when you need its logic.",
         "- **Moved code** is marked `moved from/to <file>, unchanged` (skip it) or `then +a/−b` (read only the "
         "difference).",
         "- **Find the owner of a file:** [File → PR index](#file--pr-index). **Why a PR must land after another:** "
         "the `Lands after` line on its card names the exact imports; `⚠` marks a dependency on a later PR, which is an "
         "order problem.",
         "- **Before trusting it:** the source must still be `" + plan.source_sha[:12] + "`. If branches exist and "
         "someone committed on them, regenerate first.",
         f"- **Making a change:** see [Making a change](#making-a-change). Plan file: `{plan.path}`.", "",
         "## Starting state", "",
         "| Item | Value |", "|---|---|",
         f"| Base (PR 01 targets) | `{plan.base}` |",
         f"| Fork point | `{plan.fork[:12]}` |",
         f"| Source (whole change) | `{plan.source}` → `{plan.source_sha[:12]}` |",
         f"| Changed files | {len(plan.changed)} (+{sum(v[1] for v in plan.changed.values())} / "
         f"−{sum(v[2] for v in plan.changed.values())}) |",
         f"| In the stack | {len(plan.prs)} PRs, {total_kept} counted lines, {total_deleted} lines in deleted files |",
         f"| Cap | {plan.cap} lines per PR (added + deleted lines in kept files; deleted files not counted) |",
         f"| Branches cut | {sum(1 for h in heads.values() if h)} of {len(plan.prs)} exist locally"
         + (f"; top branch {'matches' if top_matches else '**differs from**'} the source" if any(heads.values()) else "")
         + " |",
         f"| Stack type | {plan.stack_type or '**TODO** (review slices or runnable, and why)'} |",
         f"| Excluded from the stack | {short(f'`{p}`' for p in plan.changed if plan.pr_of(p) is None) or 'none'} |",
         "", "## Branch tree", "", "```", plan.base]
    for i, pr in enumerate(plan.prs):
        L.append(f"{'     ' * i} └── {pr['branch']}   PR {pr['n']:02d}" + (f" #{numbers[pr['n']]['number']}"
                                                                           if pr["n"] in numbers else ""))
    L += ["```", "", "Merge bottom-up with merge commits (not squash). With \"delete branch on merge\", GitHub retargets "
          "the next PR to the base by itself.", "", "## Stack map", "",
          "| # | PR | Branch | Base | Title | Lines | Deleted | Files | Lands after |",
          "|---|---|---|---|---|---|---|---|---|"]
    for pr in plan.prs:
        n = pr["n"]
        counted, deleted = plan.size(n)
        num = f"[#{numbers[n]['number']}]({numbers[n]['url']}) {numbers[n]['state'].lower()}" if n in numbers else "—"
        L.append(f"| {n:02d} | {num} | `{pr['branch']}` | `{plan.base_of(pr)}` | {pr['title']} | "
                 f"{counted}{' ⚠' if counted > plan.cap else ''} | {deleted} | {len(plan.files_of(n))} | "
                 f"{', '.join(f'⚠{d:02d}' if d > n else f'{d:02d}' for d in sorted(needs[n])) or '—'} |")
    L += ["", "## Problems and decisions", ""]
    over = [pr for pr in plan.prs if plan.size(pr["n"])[0] > plan.cap]
    if over:
        L.append("Over the cap: " + ", ".join(f"PR {pr['n']:02d} ({plan.size(pr['n'])[0]})" for pr in over) + ".")
        L.append("")
    L += (["Open problems from `check`:", "", "```", *found, "```"] if found else ["`check` is clean."])
    L += ["", plan.notes or "**TODO:** accepted exceptions and why, decisions, open checks.", "", "## PRs", ""]
    for pr in plan.prs:
        n = pr["n"]
        counted, deleted = plan.size(n)
        need_txt = "; ".join(f"{'⚠ ' if d > n else ''}PR {d:02d} for {short(sorted(syms), 6)}"
                             for d, syms in sorted(needs[n].items()))
        L += [f"### {n:02d} · {pr['name']}", "",
              f"- **Title:** {pr['title']}",
              f"- **Branch:** `{pr['branch']}` → base `{plan.base_of(pr)}`"
              + (f" · PR [#{numbers[n]['number']}]({numbers[n]['url']})" if n in numbers else ""),
              f"- **Size:** {counted} lines{' (over cap)' if counted > plan.cap else ''}, {deleted} in deleted files, "
              f"{len(plan.files_of(n))} files",
              f"- **Purpose:** {pr.get('purpose') or '**TODO**'}",
              f"- **Check:** {pr.get('check') or '**TODO**'}",
              f"- **Lands after:** {need_txt or 'no other PR in the stack'}"
              + (" (⚠ = a later PR: an order problem, see Problems)" if any(d > n for d in needs[n]) else ""),
              f"- **Used by:** {', '.join(f'PR {u:02d}' for u in sorted(used_by[n])) or 'no later PR imports it'}",
              "", "| File | Status | + | − | What changed |", "|---|---|---|---|---|"]
        for p in plan.files_of(n):
            st, a, d, _ = plan.changed[p]
            what = "<br>".join(changes.get(p) or ["—"]).replace("|", "\\|")
            L.append(f"| `{p}` | {st} | {a} | {d} | {what} |")
        L.append("")
    L += ["## File → PR index", "", "| File | PR | Status | + | − |", "|---|---|---|---|---|"]
    for p in sorted(plan.changed):
        n = plan.pr_of(p)
        st, a, d, _ = plan.changed[p]
        L.append(f"| `{p}` | {'excluded' if n is None else f'{n:02d}' if n else '**UNASSIGNED**'} | {st} | {a} | {d} |")
    L += ["", "## Making a change", "",
          "| Situation | Do this |", "|---|---|",
          "| Fix in PR k | Check out branch k (or `git worktree add <dir> <branch>`), commit in the repo's subject "
          f"format, then `{run} sync k` to merge it up to the top. Re-run PR k's check and the top branch's tests. |",
          f"| New file | Add its path to `{plan.path}` under the first PR that needs it, `{run} check`, commit it on "
          "that branch, `sync`. |",
          "| Move a file to another PR | Before pushing: change the plan, delete the local branches from the "
          f"earlier of the two PRs up, `{run} cut`. After pushing: only with the reviewers' agreement. |",
          "| Source changed before cutting | Re-snapshot, update `source`, `check`, regenerate this file. |",
          "| A PR merged | Delete its branch on merge; the next PR retargets to the base. Regenerate with `--prs`. |",
          ""]
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write("\n".join(L))
    print(f"wrote {args.out} ({len(L)} lines)")


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
        below = plan.base_of(pr)
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
    d.add_argument("--prs", action="store_true", help="look up PR numbers with gh")
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

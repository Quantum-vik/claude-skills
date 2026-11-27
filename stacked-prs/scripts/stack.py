#!/usr/bin/env python3
"""Plan, check, document, cut and maintain a stack of small PRs from one large change.

Subcommands (run from the repo root; the plan defaults to .stack/stack.toml):
  snapshot              commit the staged index as a dangling commit (no branch moves) and print its sha
  init                  write a plan skeleton and list every changed file with its size
  check                 unassigned files, PR sizes against the cap, Python import order problems and cycles
  doc [--out F] [--prs] write the stack document: session guide, state, map, per-PR cards with a per-file
                        change summary, dependency reasons, commits since cut, drift, reviews, file index
  status [--prs]        one-screen health of the stack (sync needed, commits since cut, drift, reviews, CI)
                        and the next steps
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
        self.cut_path = os.path.join(os.path.dirname(path) or ".", "cut.json")
        self.cut = None
        if os.path.exists(self.cut_path):
            with open(self.cut_path) as f:
                self.cut = json.load(f)
        self.use_ref(self.source)

    def use_ref(self, ref):
        """Describe the change as fork point -> ref (the snapshot before cutting, the top branch after)."""
        self.source_sha = git("rev-parse", ref).strip()
        self.fork = self.cut["fork"] if self.cut else git("merge-base", self.base, self.source_sha).strip()
        self.changed = diff_stats(self.fork, self.source_sha)

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


def diff_stats(a, b):
    """path -> (status letter, added, deleted, binary) for the diff a..b."""
    stats, out = {}, {}
    for line in git("diff", "--numstat", "--no-renames", a, b).splitlines():
        x, y, p = line.split("\t", 2)
        stats[p] = (int(x) if x != "-" else 0, int(y) if y != "-" else 0, x == "-")
    for line in git("diff", "--name-status", "--no-renames", a, b).splitlines():
        st, p = line.split("\t", 1)
        out[p] = (st[0], *stats.get(p, (0, 0, False)))
    return out


def size_of(stats):
    """(counted lines in kept files, lines in deleted files)."""
    return (sum(a + d for st, a, d, _ in stats.values() if st != "D"),
            sum(d for st, _, d, _ in stats.values() if st == "D"))


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


def describe(changed, old_src, new_src):
    """Per changed file: list of markdown bullet strings describing the change."""
    summary = {}
    added_by_name, removed_by_name = collections.defaultdict(list), collections.defaultdict(list)
    parsed = {}
    for p, (st, a, d, binary) in changed.items():
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
    for p, (st, a, d, binary) in changed.items():
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


def ref_of(branch):
    for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"):
        sha = git("rev-parse", "--verify", "--quiet", ref, check=False).strip()
        if sha:
            return sha
    return ""


def branch_state(plan):
    """Per PR number: head, layer range, own commits since cut, commits it is missing from below, drift.

    Empty dict when no branch of the plan exists yet. A layer is merge-base(below, head)..head, the same
    three-dot diff GitHub shows for the PR."""
    heads = {pr["n"]: ref_of(pr["branch"]) for pr in plan.prs}
    if not any(heads.values()):
        return {}
    cut = (plan.cut or {}).get("branches", {})
    out, below = {}, plan.base
    for pr in plan.prs:
        n, head = pr["n"], heads[pr["n"]]
        info = dict(head=head, layer=None, stats={}, commits=None, behind=0, drift=[])
        if head:
            start = git("merge-base", below, head).strip()
            info["layer"] = (start, head)
            info["behind"] = int(git("rev-list", "--count", f"{head}..{below}").strip())
            if pr["branch"] in cut:
                info["commits"] = []
                log = git("log", "--no-merges", "--date=short", "--format=%x1e%h%x09%ad%x09%s", "--name-only",
                          f"{cut[pr['branch']]}..{head}", f"^{below}")
                for chunk in log.split("\x1e")[1:]:
                    first, *files = [x for x in chunk.splitlines() if x.strip()]
                    sha, date, subject = first.split("\t", 2)
                    info["commits"].append(dict(sha=sha, date=date, subject=subject, files=files))
            info["stats"] = diff_stats(start, head)
            info["drift"] = sorted((p, plan.pr_of(p)) for p in info["stats"] if plan.pr_of(p) != n)
            below = head
        out[n] = info
    return out


THREADS_QUERY = ("query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){"
                 "reviewThreads(first:100){nodes{isResolved isOutdated path line "
                 "comments(first:1){nodes{author{login} body url}}}}}}}")


def github(plan):
    """Per PR number: number, url, state, reviewDecision, isDraft, baseRefName, ci (pass, fail, pending) and
    unresolved review threads. Read-only; empty when gh is missing or not logged in."""
    if shutil.which("gh") is None:
        return {}
    repo = subprocess.run(["gh", "repo", "view", "--json", "owner,name"], capture_output=True, text=True)
    if repo.returncode:
        return {}
    repo = json.loads(repo.stdout)
    out = {}
    for pr in plan.prs:
        r = subprocess.run(["gh", "pr", "list", "--head", pr["branch"], "--state", "all", "--limit", "1", "--json",
                            "number,url,state,reviewDecision,isDraft,baseRefName,statusCheckRollup"],
                           capture_output=True, text=True)
        if r.returncode or r.stdout.strip() in ("", "[]"):
            continue
        d = json.loads(r.stdout)[0]
        ci = collections.Counter()
        for c in d.pop("statusCheckRollup") or []:
            v = (c.get("conclusion") or c.get("state") or "").upper()
            ci["pass" if v in ("SUCCESS", "NEUTRAL", "SKIPPED") else "fail" if v in (
                "FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE") else "pending"] += 1
        d["ci"] = ci
        t = subprocess.run(["gh", "api", "graphql", "-f", f"query={THREADS_QUERY}", "-F", f"o={repo['owner']['login']}",
                            "-F", f"r={repo['name']}", "-F", f"n={d['number']}"], capture_output=True, text=True)
        d["threads"] = []
        if t.returncode == 0:
            nodes = json.loads(t.stdout)["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
            for node in nodes:
                if node["isResolved"] or not node["comments"]["nodes"]:
                    continue
                c = node["comments"]["nodes"][0]
                body = " ".join(c["body"].split())
                d["threads"].append(dict(path=node["path"], line=node["line"], outdated=node["isOutdated"],
                                         author=(c.get("author") or {}).get("login", "?"), url=c["url"],
                                         body=body[:140] + ("…" if len(body) > 140 else "")))
        out[pr["n"]] = d
    return out


def ci_text(ci):
    if not ci:
        return "—"
    return "fail" if ci["fail"] else "pending" if ci["pending"] else "pass"


def next_steps(plan, state, gh):
    steps = []
    if not state:
        return ["Nothing is cut yet: `check` until clean, `doc`, then `cut` after approval."]
    for pr in plan.prs:
        n, s, g = pr["n"], state[pr["n"]], gh.get(pr["n"], {})
        if not s["head"]:
            steps.append(f"PR {n:02d}: branch not found locally or on origin (merged and deleted?).")
            continue
        if s["behind"] and n > 1:
            steps.append(f"PR {n:02d}: missing {s['behind']} commit(s) from PR {n - 1:02d} → `sync {n - 1}`.")
        elif s["behind"]:
            steps.append(f"PR 01: base `{plan.base}` has {s['behind']} new commit(s); merge it into branch 01 only "
                         "if needed, then `sync 1`.")
        for path, owner in s["drift"]:
            where_ = f"PR {owner:02d}" if owner else ("no PR (excluded)" if owner is None else "no PR (unassigned)")
            steps.append(f"PR {n:02d}: changes `{path}`, which the plan gives to {where_} → move it in the plan or "
                         "accept it in `notes`.")
        if g.get("threads"):
            steps.append(f"PR {n:02d}: {len(g['threads'])} unresolved review thread(s) → fix on branch {n:02d}, "
                         f"commit, `sync {n}`.")
        if g.get("reviewDecision") == "CHANGES_REQUESTED":
            steps.append(f"PR {n:02d}: changes requested.")
        if g.get("ci", {}).get("fail"):
            steps.append(f"PR {n:02d}: CI failing.")
    return steps or ["Nothing to do: every branch is in sync, no drift, no open review threads."]


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
    state = branch_state(plan)
    if state and state[plan.prs[-1]["n"]]["head"]:
        plan.use_ref(state[plan.prs[-1]["n"]]["head"])
        print(f"checking the top branch {plan.prs[-1]['branch']} (branches are cut)")
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
    state = branch_state(plan)
    top = plan.prs[-1] if plan.prs else None
    snapshot_sha = plan.source_sha
    if state and top and state[top["n"]]["head"]:
        plan.use_ref(state[top["n"]]["head"])  # after cutting, the branches are the source of truth
    old, new = trees(plan)
    edges = constraints(plan, old, new)
    found = problems(plan, edges)
    gh = github(plan) if args.prs else {}

    # per PR: the files and stats it really carries (its branch layer once cut, the plan before)
    layer_stats, changes = {}, {}
    for pr in plan.prs:
        n, s = pr["n"], state.get(pr["n"])
        if s and s["layer"]:
            stats, (a, b) = s["stats"], s["layer"]
        else:
            stats, (a, b) = {p: plan.changed[p] for p in plan.files_of(n)}, (plan.fork, plan.source_sha)
        layer_stats[n] = stats
        changes[n] = describe(stats, read_blobs(a, stats), read_blobs(b, stats))

    needs = collections.defaultdict(lambda: collections.defaultdict(set))  # pr -> other pr -> {symbol}
    used_by = collections.defaultdict(set)
    for a, b, what, _ in edges:
        pa, pb = plan.pr_of(a), plan.pr_of(b)
        if pa and pb and pa != pb:
            needs[pb][pa].add(f"`{what}`")
            used_by[pa].add(pb)
    total_kept = sum(size_of(layer_stats[pr["n"]])[0] for pr in plan.prs)
    total_deleted = sum(size_of(layer_stats[pr["n"]])[1] for pr in plan.prs)
    run = f"python3 {SELF} --plan {plan.path}"
    cut_branches = sum(1 for s in state.values() if s["head"])
    if state:
        mode = (f"**branches** (cut {plan.cut['date']} from `{plan.cut['source'][:12]}`); every card below is "
                "built from its branch" if plan.cut else "**branches** (no `cut.json`: commits since cut unknown)")
    else:
        mode = "**plan** (nothing cut yet); cards are built from the source and the plan"

    L = [f"# Stacked PRs: {plan.prefix.rstrip('-/')}", "",
         f"_Generated {datetime.datetime.now():%Y-%m-%d %H:%M} by `stack.py doc` from `{plan.source_sha[:12]}` "
         f"against `{plan.base}` (fork point `{plan.fork[:12]}`). Regenerate after any change: "
         f"`{run} doc{' --prs' if args.prs else ''} --out {args.out}`._",
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
         "- **After the branches are cut they are the source of truth.** Each card then shows the commits made on that "
         "branch since the cut, files it touches that the plan gives to another PR (drift), and, with `--prs`, the "
         "review decision, unresolved review threads and CI.",
         f"- **Is this file current?** Run `{run} status`: it prints the same health data live and the next steps. "
         "Regenerate this file whenever a branch moved since the timestamp above.",
         f"- **Making a change:** see [Making a change](#making-a-change). Plan file: `{plan.path}`.", "",
         "## Starting state", "",
         "| Item | Value |", "|---|---|",
         f"| Mode | {mode} |",
         f"| Base (PR 01 targets) | `{plan.base}` |",
         f"| Fork point | `{plan.fork[:12]}` |",
         f"| Planned source | `{plan.source}` → `{snapshot_sha[:12]}` |",
         f"| Whole change now | `{plan.source_sha[:12]}`: {len(plan.changed)} files "
         f"(+{sum(v[1] for v in plan.changed.values())} / −{sum(v[2] for v in plan.changed.values())}) |",
         f"| In the stack | {len(plan.prs)} PRs, {total_kept} counted lines, {total_deleted} lines in deleted files |",
         f"| Cap | {plan.cap} lines per PR (added + deleted lines in kept files; deleted files not counted) |",
         f"| Branches | {cut_branches} of {len(plan.prs)} found (local or origin) |",
         f"| Stack type | {plan.stack_type or '**TODO** (review slices or runnable, and why)'} |",
         f"| Excluded from the stack | {short(f'`{p}`' for p in plan.changed if plan.pr_of(p) is None) or 'none'} |",
         "", "## Next steps", "", *(f"- {x}" for x in next_steps(plan, state, gh)),
         "", "## Branch tree", "", "```", plan.base]
    for i, pr in enumerate(plan.prs):
        L.append(f"{'     ' * i} └── {pr['branch']}   PR {pr['n']:02d}"
                 + (f" #{gh[pr['n']]['number']}" if pr["n"] in gh else ""))
    L += ["```", "", "Merge bottom-up with merge commits (not squash). With \"delete branch on merge\", GitHub retargets "
          "the next PR to the base by itself.", "", "## Stack map", "",
          "| # | PR | Branch | Title | Lines | Deleted | Lands after | Since cut | In sync | Review | CI |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for pr in plan.prs:
        n, s, g = pr["n"], state.get(pr["n"]), gh.get(pr["n"])
        counted, deleted = size_of(layer_stats[n])
        num = f"[#{g['number']}]({g['url']}) {g['state'].lower()}" if g else "—"
        since = "—" if not s or s["commits"] is None else f"{len(s['commits'])} commit(s)"
        sync = "—" if not s or not s["head"] else ("✓" if not s["behind"] else f"behind {s['behind']}")
        review = "—" if not g else f"{(g.get('reviewDecision') or 'none').lower().replace('_', ' ')}, " \
                                   f"{len(g['threads'])} open thread(s)"
        L.append(f"| {n:02d} | {num} | `{pr['branch']}` | {pr['title']} | "
                 f"{counted}{' ⚠' if counted > plan.cap else ''} | {deleted} | "
                 f"{', '.join(f'⚠{d:02d}' if d > n else f'{d:02d}' for d in sorted(needs[n])) or '—'} | "
                 f"{since} | {sync} | {review} | {ci_text(g.get('ci')) if g else '—'} |")
    L += ["", "## Problems and decisions", ""]
    over = [pr for pr in plan.prs if size_of(layer_stats[pr["n"]])[0] > plan.cap]
    if over:
        L += ["Over the cap: " + ", ".join(f"PR {pr['n']:02d} ({size_of(layer_stats[pr['n']])[0]})" for pr in over)
              + ".", ""]
    L += (["Open problems from `check`:", "", "```", *found, "```"] if found else ["`check` is clean."])
    L += ["", plan.notes or "**TODO:** accepted exceptions and why, decisions, open checks.", "", "## PRs", ""]
    for pr in plan.prs:
        n, s, g = pr["n"], state.get(pr["n"]), gh.get(pr["n"])
        counted, deleted = size_of(layer_stats[n])
        need_txt = "; ".join(f"{'⚠ ' if d > n else ''}PR {d:02d} for {short(sorted(syms), 6)}"
                             for d, syms in sorted(needs[n].items()))
        L += [f"### {n:02d} · {pr['name']}", "",
              f"- **Title:** {pr['title']}",
              f"- **Branch:** `{pr['branch']}` → base `{plan.base_of(pr)}`"
              + (f" · PR [#{g['number']}]({g['url']}) {g['state'].lower()}{' (draft)' if g.get('isDraft') else ''}"
                 if g else ""),
              f"- **Size:** {counted} lines{' (over cap)' if counted > plan.cap else ''}, {deleted} in deleted files, "
              f"{len(layer_stats[n])} files",
              f"- **Purpose:** {pr.get('purpose') or '**TODO**'}",
              f"- **Check:** {pr.get('check') or '**TODO**'}",
              f"- **Lands after:** {need_txt or 'no other PR in the stack'}"
              + (" (⚠ = a later PR: an order problem, see Problems)" if any(d > n for d in needs[n]) else ""),
              f"- **Used by:** {', '.join(f'PR {u:02d}' for u in sorted(used_by[n])) or 'no later PR imports it'}"]
        if s and s["head"]:
            L.append(f"- **Branch head:** `{s['head'][:12]}`"
                     + (f"; **behind PR {n - 1:02d} by {s['behind']} commit(s): run `sync {n - 1}`**"
                        if s["behind"] and n > 1 else "; in sync with the PR below" if n > 1 else ""))
        if s and s["drift"]:
            L.append("- **Drift:** " + "; ".join(
                f"`{p}` belongs to " + (f"PR {o:02d}" if o else "no PR") for p, o in s["drift"]))
        if s and s["commits"]:
            L += ["- **Commits since cut:**",
                  *(f"  - `{c['sha']}` {c['date']} {c['subject']} — {short((f'`{x}`' for x in c['files']), 5)}"
                    for c in s["commits"])]
        elif s and s["commits"] == []:
            L.append("- **Commits since cut:** none")
        if g:
            L.append(f"- **Review:** {(g.get('reviewDecision') or 'no decision').lower().replace('_', ' ')}; CI "
                     f"{ci_text(g['ci'])} ({g['ci']['pass']} pass, {g['ci']['fail']} fail, {g['ci']['pending']} pending)")
            if g["threads"]:
                L += [f"- **Unresolved review threads ({len(g['threads'])}):**",
                      *(f"  - `{t['path']}:{t['line'] or '?'}`{' (outdated)' if t['outdated'] else ''} "
                        f"{t['author']}: {t['body']} ([open]({t['url']}))" for t in g["threads"])]
        L += ["", "| File | Status | + | − | What changed |", "|---|---|---|---|---|"]
        for p in sorted(layer_stats[n]):
            st, a, d, _ = layer_stats[n][p]
            what = "<br>".join(changes[n].get(p) or ["—"]).replace("|", "\\|")
            L.append(f"| `{p}` | {st} | {a} | {d} | {what} |")
        L.append("")
    touched = collections.defaultdict(list)
    for n, stats in layer_stats.items():
        for p in stats:
            touched[p].append(n)
    L += ["## File → PR index", "", "| File | Owner (plan) | Changed in | Status | + | − |", "|---|---|---|---|---|---|"]
    for p in sorted(set(plan.changed) | set(touched)):
        n = plan.pr_of(p)
        st, a, d, _ = plan.changed.get(p, ("·", 0, 0, False))
        owner = "excluded" if n is None else f"{n:02d}" if n else "**UNASSIGNED**"
        where_ = ", ".join(f"{x:02d}" for x in touched.get(p, [])) or "—"
        L.append(f"| `{p}` | {owner} | {where_} | {st} | {a} | {d} |")
    L += ["", "## Making a change", "",
          "| Situation | Do this |", "|---|---|",
          f"| Review comment on PR k | `{run} status --prs` (or this file's card) shows the thread with file:line. Fix on "
          f"branch k, commit, `{run} sync k`, regenerate. Reply to or resolve the thread only with the author's go. |",
          "| Fix in PR k | Check out branch k (or `git worktree add <dir> <branch>`), commit in the repo's subject "
          f"format, then `{run} sync k` to merge it up to the top. Re-run PR k's check and the top branch's tests. |",
          f"| New file | Add its path to `{plan.path}` under the first PR that needs it, commit it on that branch, "
          "`sync`, regenerate (drift disappears once plan and branch agree). |",
          "| Move a file to another PR | Before pushing: change the plan, delete the local branches from the "
          f"earlier of the two PRs up, `{run} cut`. After pushing: only with the reviewers' agreement. |",
          "| Base moved (release got new commits) | Merge the base into branch 01 only if the PRs need it "
          "(`git merge --no-edit <base>`), then `sync 1`. |",
          "| A PR merged | Delete its branch on merge; the next PR retargets to the base. Regenerate with `--prs`. |",
          ""]
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write("\n".join(L))
    print(f"wrote {args.out} ({len(L)} lines)")


def cmd_status(args):
    plan = Plan(args.plan)
    state = branch_state(plan)
    gh = github(plan) if args.prs else {}
    print(f"base {plan.base} · fork {plan.fork[:10]} · {'cut ' + plan.cut['date'] if plan.cut else 'not cut'}")
    if state:
        print(f"{'#':<3}{'branch head':<13}{'since cut':<11}{'in sync':<14}{'drift':<7}{'PR':<16}{'review':<20}"
              f"{'threads':<9}CI")
        for pr in plan.prs:
            n, s, g = pr["n"], state[pr["n"]], gh.get(pr["n"], {})
            since = "?" if s["commits"] is None else str(len(s["commits"]))
            sync = "missing" if not s["head"] else "yes" if not s["behind"] else f"behind {s['behind']}"
            num = f"#{g['number']} {g['state'].lower()}" if g else "-"
            print(f"{n:02d} {s['head'][:10] or '-':<13}{since:<11}{sync:<14}{len(s['drift']):<7}{num:<16}"
                  f"{(g.get('reviewDecision') or '-').lower():<20}{len(g.get('threads', [])) if g else '-'!s:<9}"
                  f"{ci_text(g.get('ci')) if g else '-'}")
    print("\nNext steps:", *(f"  - {x}" for x in next_steps(plan, state, gh)), sep="\n")


def cmd_cut(args):
    plan = Plan(args.plan)
    if any(plan.pr_of(p) == 0 for p in plan.changed):
        sys.exit("unassigned files; run check first")
    for pr in plan.prs:
        if git("rev-parse", "--verify", "--quiet", f"refs/heads/{pr['branch']}", check=False).strip():
            sys.exit(f"branch {pr['branch']} already exists; delete it or rename the plan first")
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": os.path.join(tmp, "index")}
        parent, heads = plan.fork, {}
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
            heads[pr["branch"]] = parent
            print(f"{pr['n']:02d} {pr['branch']} {parent[:10]}")
    with open(plan.cut_path, "w") as f:
        json.dump(dict(date=f"{datetime.datetime.now():%Y-%m-%d %H:%M}", source=plan.source_sha, fork=plan.fork,
                       branches=heads), f, indent=1)
    print(f"recorded the cut in {plan.cut_path}")
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
    d.add_argument("--prs", action="store_true", help="also read PR state, reviews, threads and CI with gh")
    d.set_defaults(fn=cmd_doc)
    st = sub.add_parser("status")
    st.add_argument("--prs", action="store_true", help="also read PR state, reviews, threads and CI with gh")
    st.set_defaults(fn=cmd_status)
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

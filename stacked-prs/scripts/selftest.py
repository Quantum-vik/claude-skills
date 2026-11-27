#!/usr/bin/env python3
"""End-to-end self-test for stack.py. Builds throwaway repos (with a local bare "origin") in a temp folder and
exercises every command and check. No network, no GitHub. Run: python3 selftest.py  (exit 0 = all passed)."""
import json
import re
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
STACK = os.path.join(HERE, "stack.py")
sys.path.insert(0, HERE)
import stack  # noqa: E402

PASSED = []


def sh(*cmd, cwd=None, ok=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if ok and r.returncode:
        raise AssertionError(f"{' '.join(cmd)} failed:\n{r.stdout}{r.stderr}")
    return r


def st(*args, ok=True):
    r = subprocess.run([sys.executable, STACK, *args], capture_output=True, text=True)
    if ok is True and r.returncode:
        raise AssertionError(f"stack.py {' '.join(args)} failed:\n{r.stdout}{r.stderr}")
    if ok is False and not r.returncode:
        raise AssertionError(f"stack.py {' '.join(args)} should have failed:\n{r.stdout}")
    return r.stdout + r.stderr


def check(name, cond, detail=""):
    if not cond:
        raise AssertionError(f"{name}: {detail}")
    PASSED.append(name)


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def plan_file(source, prs, extra=""):
    body = ['base = "main"', f'source = "{source}"', 'branch_prefix = "stack/t-"', "cap = 50",
            'exclude = ["local.cfg", "docs/*.md"]', extra, ""]
    for name, title, paths in prs:
        body += ["[[pr]]", f'name = "{name}"', f'title = "{title}"', f"paths = {json.dumps(paths)}", ""]
    write(".stack/stack.toml", "\n".join(body))


def stack_plan():
    stack.RENAMED.clear()
    return stack.Plan(".stack/stack.toml")


def repo(root):
    os.makedirs(root)
    os.chdir(root)
    for c in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"], ["git", "config", "commit.gpgsign", "false"]):
        sh(*c)
    write(".git/info/exclude", ".stack/\n")


def main():
    tmp = tempfile.mkdtemp(prefix="stack-selftest-")
    origin = os.path.join(tmp, "origin.git")
    sh("git", "init", "-q", "--bare", "-b", "main", origin)
    repo(os.path.join(tmp, "work"))
    sh("git", "remote", "add", "origin", origin)

    # base: an app with a migration, a module with old(), a file to rename, local config
    write("app/__init__.py", "")
    write("app/migrations/__init__.py", "")
    write("app/migrations/0001_initial.py", "dependencies = []\n")
    write("pkg/__init__.py", "")
    write("pkg/a.py", "def old():\n    return 1\n")
    write("pkg/b.py", "from pkg.a import old\nX = old()\n")
    write("pkg/moveme.py", "def helper():\n    return 'h'\n")
    write("local.cfg", "x\n")
    write("docs/v1.txt", "one\n")
    write("docs/sub/a.md", "a\n")
    sh("git", "add", "-A")
    sh("git", "commit", "-qm", "init")
    sh("git", "push", "-q", "origin", "main")

    # the large change: a swaps old() for new(), b follows, c is new, a migration chain, a rename, local edit
    write("pkg/a.py", "def new():\n    return 2\n")
    write("pkg/b.py", "from pkg.a import new\nX = new()\n")
    write("pkg/c.py", "from pkg.b import X\nY = X + 1\n")
    write("app/migrations/0002_add.py", "dependencies = [('app', '0001_initial')]\n")
    write("app/migrations/0003_more.py", "dependencies = [('app', '0002_add')]\n")
    sh("git", "mv", "pkg/moveme.py", "pkg/moved.py")
    write("local.cfg", "y\n")
    write("pkg/café.py", "Z = 'ü'\n")          # non-ASCII path
    write("docs/v[1].txt", "bracket\n")        # looks like a pathspec glob
    write("docs/sub/a.md", "changed\n")        # excluded through a glob that crosses folders
    sh("git", "add", "-A")
    snap = st("snapshot").strip()
    check("snapshot returns a commit", len(snap) == 40 and sh("git", "cat-file", "-t", snap).stdout.strip() == "commit")
    check("snapshot moves no branch", sh("git", "rev-parse", "main").stdout.strip() != snap)

    # 1) a bad plan: order problem + cycle, migration order, rename split, unassigned, naming rules
    plan_file(snap, [("first", "feat: b", ["pkg/b.py", "app/migrations/0003_more.py", "pkg/moveme.py"]),
                     ("second", "bad title", ["pkg/a.py", "app/migrations/0002_add.py", "pkg/moved.py"])],
              extra='branch_pattern = "stack/t-\\\\d\\\\d-[a-z]+"\ntitle_pattern = "(feat|fix): .+"')
    out = st("check", ok=False)
    check("check: unassigned file", "UNASSIGNED" in out and "pkg/c.py" in out, out)
    check("check: import order problem", "new pkg/b.py imports pkg.a:new" in out, out)
    check("check: cycle across PRs", "CYCLE across PRs [1, 2]" in out, out)
    check("check: migration dependency order", "migration app/migrations/0003_more.py depends on app.0002_add" in out,
          out)
    check("check: rename split", "RENAME SPLIT: pkg/moveme.py (PR 01) -> pkg/moved.py (PR 02)" in out, out)
    check("check: title rule", 'TITLE RULE: PR 02 "bad title"' in out, out)
    check("check: excluded file listed", "local.cfg" in out, out)

    # 2) a good plan
    plan_file(snap, [("core", "feat: swap a and b", ["pkg/a.py", "pkg/b.py", "app/migrations/0002_add.py",
                                                     "pkg/moveme.py", "pkg/moved.py", "pkg/café.py",
                                                     "docs/v[1].txt"]),
                     ("extra", "feat: c and more", ["pkg/c.py", "app/migrations/0003*"])],
              extra='branch_pattern = "stack/t-\\\\d\\\\d-[a-z]+"\ntitle_pattern = "(feat|fix): .+"')
    out = st("check")
    check("check: clean plan is OK", out.strip().endswith("OK"), out)
    out = st("doc")
    doc = open(".stack/STACK.md").read()
    check("doc (plan mode) written", "## Read me first" in doc and "**plan** (nothing cut yet)" in doc, doc[:400])
    check("doc: per-file summary", "added def `new()` L1–2" in doc and "removed def `old`" in doc)
    check("doc: lands-after reason", "PR 01 for `pkg.b:X`" in doc or "Lands after:** no other" in doc)

    # 3) cut
    out = st("cut")
    check("cut: top equals source", "top branch == source" in out, out)
    check("cut: records cut.json", os.path.exists(".stack/cut.json"))
    check("cut: non-ASCII path carried", sh("git", "show", "stack/t-01-core:pkg/café.py").stdout == "Z = 'ü'\n")
    check("cut: bracket path taken literally", sh("git", "show", "stack/t-01-core:docs/v[1].txt").stdout == "bracket\n"
          and sh("git", "show", "stack/t-01-core:docs/v1.txt").stdout == "one\n")
    check("cut: excluded glob not reported as mismatch", "MISMATCH" not in out, out)
    renamed = stack_plan().changed
    check("rename counted once", renamed["pkg/moved.py"][0] == "R" and renamed["pkg/moveme.py"][1:3] == (0, 0),
          renamed.get("pkg/moved.py"))
    check("cut: working tree untouched", "local.cfg" in sh("git", "status", "--short").stdout)
    st("cut", ok=False)
    PASSED.append("cut: refuses to overwrite branches")
    b1, b2 = "stack/t-01-core", "stack/t-02-extra"

    # 4) verify pass / fail / timeout
    out = st("verify", "--cmd", f"{sys.executable} -c 'import pkg.c'", ok=None)
    check("verify: PR 01 fails without pkg.c, PR 02 passes", "01 stack/t-01-core: FAIL" in out
          and "02 stack/t-02-extra: pass" in out, out)
    out = st("verify", "--cmd", "sleep 5", "--timeout", "1", "--from", "2", ok=None)
    check("verify: timeout", "timed out after 1s" in out, out)

    # 5) status after cut: not pushed
    out = st("status")
    check("status: not pushed", "→ `publish`" in out or "not pushed" in out, out)

    # 6) push both (as publish would), then a local fix on PR 01 -> unpushed + PR 02 behind
    sh("git", "push", "-q", "origin", f"{b1}:{b1}", f"{b2}:{b2}")
    sh("git", "fetch", "-q", "origin")
    wt = os.path.join(tmp, "wt1")
    sh("git", "worktree", "add", "-q", wt, b1)
    write(os.path.join(wt, "pkg/a.py"), "def new():\n    \"\"\"doc\"\"\"\n    return 2\n")
    sh("git", "commit", "-qam", "fix: document new", cwd=wt)
    sh("git", "worktree", "remove", wt)
    out = st("status")
    check("status: PR 02 behind PR 01", "missing 1 commit(s) from PR 01 → `sync 1`" in out, out)
    check("status: unpushed commit", "PR 01: 1 unpushed commit(s)" in out, out)

    # 7) someone else pushes to PR 02 on origin -> remote ahead
    other = os.path.join(tmp, "other")
    sh("git", "clone", "-q", origin, other)
    sh("git", "-C", other, "config", "user.email", "o@o")
    sh("git", "-C", other, "config", "user.name", "o")
    sh("git", "-C", other, "checkout", "-q", b2)
    write(os.path.join(other, "pkg/c.py"), "from pkg.b import X\nY = X + 2\n")
    sh("git", "-C", other, "commit", "-qam", "fix: teammate change")
    sh("git", "-C", other, "push", "-q", "origin", b2)
    out = st("status", "--fetch")
    check("status: origin ahead", "PR 02: origin has 1 commit(s) you lack" in out, out)
    sh("git", "fetch", "-q", "origin")
    wt = os.path.join(tmp, "wt2")
    sh("git", "worktree", "add", "-q", wt, b2)
    sh("git", "merge", "-q", "--no-edit", f"origin/{b2}", cwd=wt)
    sh("git", "worktree", "remove", wt)

    # 8) sync, then drift: PR 02 touches PR 01's file and adds an unplanned file
    out = st("sync", "1")
    check("sync merges upward", "02 stack/t-02-extra: merged stack/t-01-core" in out, out)
    wt = os.path.join(tmp, "wt3")
    sh("git", "worktree", "add", "-q", wt, b2)
    write(os.path.join(wt, "pkg/b.py"), "from pkg.a import new\nX = new()\n# touched\n")
    write(os.path.join(wt, "pkg/d.py"), "Z = 1\n")
    sh("git", "add", "-A", cwd=wt)
    sh("git", "commit", "-qm", "feat: extra", cwd=wt)
    sh("git", "worktree", "remove", wt)
    out = st("status")
    check("status: in sync after sync", "missing" not in out.split("Next steps")[1], out)
    check("status: drift to another PR", "changes `pkg/b.py`, which the plan gives to PR 01" in out, out)
    check("status: drift to no PR", "`pkg/d.py`, which the plan gives to no PR (unassigned)" in out, out)
    st("doc")
    doc = open(".stack/STACK.md").read()
    check("doc (branch mode)", "**branches** (cut" in doc and "- **Drift:**" in doc and "Commits since cut" in doc)
    check("doc: commits since cut list", "feat: extra" in doc and "fix: teammate change" in doc, doc[-3000:])

    # 9) publish without GitHub refuses cleanly
    out = st("publish", ok=False)
    check("publish needs gh in a GitHub repo", "publish needs gh" in out, out)

    # 10) squash merge of PR 01 into main, then restack PR 02 onto main
    sh("git", "reset", "-q", "--hard")  # the throwaway checkout still holds the staged change
    sh("git", "checkout", "-q", "main")
    sh("git", "merge", "-q", "--squash", b1)
    sh("git", "commit", "-qm", "feat: swap a and b (squashed)")
    old_b2 = sh("git", "rev-parse", b2).stdout.strip()
    layer2 = stack.branch_state(stack_plan(), frozenset({1}))[2]["stats"]
    check("squash-merged PR 01: PR 02 still measured from PR 01", "pkg/a.py" not in layer2 and "pkg/c.py" in layer2,
          sorted(layer2))
    out = st("restack", "--from", "2")
    check("restack: dry run by default", "Dry run" in out and sh("git", "rev-parse", b2).stdout.strip() == old_b2, out)
    out = st("restack", "--from", "2", "--yes")
    check("restack: rewrites PR 02", "02 stack/t-02-extra:" in out, out)
    check("restack: content guard quiet when nothing was lost", "content differs" not in out, out)
    since = stack.branch_state(stack_plan(), frozenset({1}))[2]["commits"]
    check("restack: commits since cut stay the PR's own", [c["subject"] for c in since] == [
        "feat: extra", "fix: teammate change"], since)
    own = sh("git", "diff", "--name-only", "main...stack/t-02-extra").stdout.split()
    check("restack: PR 02 now shows only its own files", "pkg/a.py" not in own and "pkg/c.py" in own, own)
    check("restack: content kept", sh("git", "show", f"{b2}:pkg/c.py").stdout == "from pkg.b import X\nY = X + 2\n")

    # 10b) origin rewritten with the same changes (as GitHub does when it rebases a linked stack) -> pull takes it
    sh("git", "push", "-q", "--force", "origin", f"{b2}:{b2}")
    sh("git", "-C", other, "fetch", "-q", "origin")
    sh("git", "-C", other, "checkout", "-q", "-B", b2, f"origin/{b2}")
    sh("git", "-C", other, "commit", "-q", "--amend", "--no-edit", "--date=2001-01-01T00:00:00")
    sh("git", "-C", other, "push", "-q", "--force", "origin", b2)
    out = st("status", "--fetch")
    check("status: rewritten origin detected", "origin was rewritten" in out and "→ `pull`" in out, out)
    out = st("pull")
    check("pull: dry run lists the branch", "Dry run" in out and b2 in out, out)
    st("pull", "--yes")
    check("pull: local now equals origin", sh("git", "rev-parse", b2).stdout == sh("git", "rev-parse",
                                                                                     f"origin/{b2}").stdout)
    wt = os.path.join(tmp, "wt4")
    sh("git", "worktree", "add", "-q", wt, b2)
    write(os.path.join(wt, "pkg/e.py"), "E = 1\n")
    sh("git", "add", "-A", cwd=wt)
    sh("git", "commit", "-qm", "feat: local only", cwd=wt)
    sh("git", "worktree", "remove", wt)
    sh("git", "-C", other, "commit", "-q", "--amend", "--no-edit", "--date=2002-01-01T00:00:00")
    sh("git", "-C", other, "push", "-q", "--force", "origin", b2)
    sh("git", "fetch", "-q", "origin")
    out = st("pull", "--yes")
    check("pull: keeps a branch with local-only commits", "1 local commit(s) not on origin" in out
          and "feat: local only" in sh("git", "log", "-1", "--format=%s", b2).stdout, out)

    # 11) unit pieces: next steps for merged / squash / order / base mismatch, PR body merge and size cap
    os.chdir(os.path.join(tmp, "work"))
    plan = stack.Plan(".stack/stack.toml")
    check("expected base after PR 01 merged", stack.expected_base(plan, 2, frozenset({1})) == "main")
    check("expected base while PR 01 open", stack.expected_base(plan, 2, frozenset()) == b1)
    gh = {1: dict(state="MERGED", number=1, baseRefName="main", threads=[], ci={}),
          2: dict(state="OPEN", number=2, baseRefName=b1, threads=[], ci={}, reviewDecision=None)}
    steps = "\n".join(stack.next_steps(plan, stack.branch_state(plan, frozenset({1})), gh))
    check("next steps: retarget after merge", f"targets `{b1}` on GitHub, expected `main`" in steps, steps)
    sh("git", "branch", "-q", "-f", "orphan-test", b1)
    gh_wrong = {2: dict(state="MERGED", number=2, baseRefName=b1, threads=[], ci={})}
    steps = "\n".join(stack.next_steps(plan, stack.branch_state(plan, frozenset({2})),
                                       {**gh_wrong, 1: dict(state="OPEN", number=1, baseRefName="main", threads=[],
                                                            ci={}, reviewDecision=None)}))
    check("next steps: wrong merge order", "merged while a lower PR is still open" in steps, steps)
    check("next steps: squash merge detected", "squash- or rebase-merged" in "\n".join(
        stack.next_steps(plan, stack.branch_state(plan, frozenset({1})), gh)))
    ctx = stack.context(plan, False)
    first, second = stack.render_body(plan, ctx, plan.prs[0]), stack.render_body(plan, ctx, plan.prs[1])
    check("PR body: bottom PR carries the stack overview", "## Stack overview: 2 PRs" in first
          and plan.prs[1]["title"] in first and "## This PR" in first)
    check("PR body: other PRs carry only their own card", "Stack overview" not in second
          and plan.prs[0]["title"] not in second and "Layer 2 of 2" in second and "overview" in second)
    ctx["gh"] = {1: dict(number=101, url="https://example.invalid/pull/101", state="OPEN", isDraft=True),
                 2: dict(number=102, url="https://example.invalid/pull/102", state="OPEN", isDraft=True)}
    first, second = stack.render_body(plan, ctx, plan.prs[0]), stack.render_body(plan, ctx, plan.prs[1])
    check("PR body: PR numbers are code, never #links", "`#101`" in first and "`#102`" in first
          and "`#101`" in second and not re.search(r"(?<!`)#10[12]", first + second) and "/pull/" not in first + second)
    ctx["gh"] = {}
    check("PR body: no review line or file table", "**Review:**" not in second and "| File |" not in second
          and "| File |" in "\n".join(stack.render_card(plan, ctx, plan.prs[1])))
    plan.prs[1]["purpose"] = "x" * 70000
    body = stack.render_body(plan, ctx, plan.prs[1])
    check("PR body: cut to GitHub's limit", len(body) <= stack.BODY_LIMIT and body.endswith(stack.BODY_END))
    steps = "\n".join(stack.next_steps(plan, stack.branch_state(plan, frozenset({2})),
                                       {2: dict(state="MERGED", number=2, baseRefName=b1, threads=[], ci={})}))
    check("next steps: wrong order with an unopened lower PR", "merged while a lower PR is still open" in steps, steps)
    tree = stack.Tree({"x.py": "def (:\n"})
    names, star, imports = tree.info("x.py")
    check("syntax error: no names, no star, reported", not names and not star and tree.bad == {"x.py"})
    big = stack.BODY_START + "\n" + "y" * 70000 + "\n" + stack.BODY_END
    fitted = stack.fitted_body(plan, ctx, plan.prs[1], "author notes " * 2000 + big)
    check("PR body: whole description within GitHub's limit", len(fitted) <= 65536 and "author notes" in fitted)
    generated = f"{stack.BODY_START}\nnew\n{stack.BODY_END}"
    tricky = f"see {stack.BODY_END} below\n{stack.BODY_START}\nold\n{stack.BODY_END}\ntail"
    check("PR body: markers matched in order", stack.merge_body(tricky, generated)
          == f"see {stack.BODY_END} below\n{generated}\ntail")
    check("PR body: keeps author text", stack.merge_body(f"intro\n{stack.BODY_START}\nold\n{stack.BODY_END}\nouttro",
                                                         generated) == f"intro\n{generated}\nouttro")
    check("PR body: appends when no block", stack.merge_body("intro", generated) == f"intro\n\n{generated}")

    print(f"PASS: {len(PASSED)} checks")
    for name in PASSED:
        print(f"  ✓ {name}")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"FAIL after {len(PASSED)} passed checks: {e}")
        sys.exit(1)

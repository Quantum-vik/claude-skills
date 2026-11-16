# claude-skills

Personal [Claude Code](https://docs.claude.com/en/docs/claude-code) skills. Each folder is one skill.

| Skill | What it does |
|---|---|
| [`lld-pdf`](lld-pdf/) | Builds an A4 PDF in a fixed LLD / tech-spec format (cover table, contents with page numbers, Part bars, numbered sections, Mermaid and Graphviz figures, page footer) from one HTML source |

## Install

Link a skill into your skills folder:

```sh
git clone https://github.com/Quantum-vik/claude-skills.git ~/WorkPersonal/claude-skills
ln -s ~/WorkPersonal/claude-skills/lld-pdf ~/.claude/skills/lld-pdf
```

Each skill's `SKILL.md` lists what it needs installed.

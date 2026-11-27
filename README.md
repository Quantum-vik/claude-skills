# claude-skills

Personal [Claude Code](https://docs.claude.com/en/docs/claude-code) skills. Each folder is one skill.

| Skill | What it does |
|---|---|
| [`lld-pdf`](lld-pdf/) | Builds an A4 PDF in a fixed LLD / tech-spec format (cover table, contents with page numbers, Part bars, numbered sections, Mermaid and Graphviz figures, page footer) from one HTML source |
| [`api-contract`](api-contract/) | Writes an API contract in a fixed Markdown format (conventions, shared shapes, endpoint summary, one section per endpoint with real captured requests and responses, jobs and events, end-to-end flows, edge cases, FE/BE mismatches, open questions) from the code, with a checker for missing sections, placeholders and leaked secrets |
| [`stacked-prs`](stacked-prs/) | Splits one large change into a stack of small dependent PRs under a line cap: plans the layers, checks Python import order and cycles between PRs, writes the stack document, cuts the branches without touching the working tree, verifies each branch and merges fixes upward |

## Install

Link a skill into your skills folder:

```sh
git clone https://github.com/Quantum-vik/claude-skills.git ~/WorkPersonal/claude-skills
ln -s ~/WorkPersonal/claude-skills/lld-pdf ~/.claude/skills/lld-pdf
ln -s ~/WorkPersonal/claude-skills/api-contract ~/.claude/skills/api-contract
ln -s ~/WorkPersonal/claude-skills/stacked-prs ~/.claude/skills/stacked-prs
```

Each skill's `SKILL.md` lists what it needs installed.

## License

[MIT](LICENSE)

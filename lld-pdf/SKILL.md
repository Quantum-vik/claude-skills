---
name: lld-pdf
description: Build an A4 PDF in the house LLD format (cover table, contents with page numbers, blue Part bars, numbered sections, Mermaid/Graphviz figures, page footer) from one HTML source. Use when asked for an LLD, low-level design, tech spec, RCA or design doc as a PDF, or for a document "in the LLD format".
---

# LLD PDF

One HTML file → one A4 PDF. The format is fixed: the CSS and page skeleton live in `assets/template.html`, the
build in `scripts/`. You write content; you never restyle.

Kit (paths relative to this skill's folder, `~/.claude/skills/lld-pdf/`):

| File | Role |
|---|---|
| `assets/template.html` | Starting document: the house CSS plus every component, with `[PLACEHOLDER]` text |
| `assets/diagrams/` | Example `.mmd` (Mermaid) and `.dot` (Graphviz) sources |
| `scripts/build.py` | Renders diagrams, inlines them, prints twice to fill contents page numbers, reports sparse pages |
| `scripts/print.mjs` | Prints HTML to PDF through Chromium (A4, margins, footer) |
| `reference/components.md` | Every component, its markup and when to use it |
| `reference/diagrams.md` | Diagram conventions, sizing and the Mermaid/Graphviz setup |

Needs `chromium`, `node` ≥ 22, `pdftotext`/`pdfinfo`/`pdftoppm` (poppler), `dot` (only for `.dot` figures), and
internet (Mermaid loads from jsdelivr).

## Steps

1. **Gather the facts first.** Read the code, PR, tickets and docs the document is about. Every number, path and
   name in the PDF comes from a source you opened; a fact you could not confirm goes in as an open question, not as
   a claim. Done when you can name the source of every table row you plan to write.
2. **Place the source.** Create a folder for the document where the repo keeps its design docs (for example
   `LLD/<feature>/`); with no convention, beside the feature's other docs; ask when neither is obvious. Copy `assets/template.html` there as `<name>.html` and
   create `diagrams/` beside it.
3. **Write the content** into the copy, using only the components in `reference/components.md`:
   - Cover: `.tag`, `h1`, `.sub`, and the `table.meta` rows (ticket, status, repository + branch, date, author,
     audience, what it covers).
   - Group sections into Parts (`<h2>Part A — …</h2>`); number sections continuously across Parts
     (`<h3 id="s1">1. …</h3>`, `s2`, …).
   - One `.toc .row` per section, its `data-for` matching the section's `id`; one `.part` line per Part.
   - Figures are numbered by Part letter: `Figure A1`, `Figure B2`.
   Done when every section has a contents row and every placeholder from the template is gone.
4. **Draw the figures** as `diagrams/<letter><n>_<name>.mmd` or `.dot`, following `reference/diagrams.md`, and
   reference each from a `<div class="dia" data-src="…">` inside a `<figure>` with a `<figcaption>`.
5. **Build:**
   `python3 ~/.claude/skills/lld-pdf/scripts/build.py <name>.html <Name-MV-1234>.pdf --footer "<Short title> · MV-1234"`
   It exits non-zero on a Mermaid or Graphviz error and warns about any contents row whose section it cannot find.
6. **Check every page.** `pdftoppm -r 50 -png <pdf> <scratch>/p` and look at each PNG. Done when:
   - the build printed no warnings and every contents row has a page number;
   - no page except the last is mostly empty (the build lists pages under 120 words);
   - no table or diagram runs past the right margin, and diagram text is readable;
   - no Part bar or heading sits alone at the bottom of a page.
   Fix layout with the levers in `reference/components.md` (diagram `--w`/`--h`, table column widths, moving a
   figure), rebuild, and look again.
7. **Deliver** the PDF path and page count. Keep the `.html` and `diagrams/` beside the PDF so it can be rebuilt;
   `_build/` holds intermediates.

## House style

- Lead each section with its answer in a `p.lead`; then tables, figures, short paragraphs.
- Tables over prose for anything compared, listed or measured; `thead` on every table so it repeats across pages.
- Specific values: numbers with units, file paths as `code`, `file:line` when pointing at code.
- `.note` for a takeaway, `.warn` for a risk or a known gap; one or two per section at most.

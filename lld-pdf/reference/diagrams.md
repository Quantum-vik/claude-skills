# Diagrams

Sources live in the document's `diagrams/` folder, named `<part letter><n>_<topic>.mmd|.dot` (`a1_arch.mmd`,
`b3_erd_target.dot`), and appear in the PDF through `<div class="dia" data-src="<file>">` inside a `<figure>`.
`build.py` renders each one to SVG and inlines it, so the PDF is self-contained.

## Which tool

| Diagram | Tool | Source starts with |
|---|---|---|
| Architecture, flow, decision | Mermaid | `flowchart LR` or `flowchart TD` |
| Request or job sequence | Mermaid | `sequenceDiagram` |
| Classes | Mermaid, or `.uml-grid` cards in HTML | `classDiagram` |
| State machine | Mermaid | `stateDiagram-v2` |
| ERD (tables, keys, relations) | Graphviz | `digraph` with `shape=plaintext` HTML-table nodes |

Mermaid is pinned to 10.9.1 with the neutral theme and Noto Sans 13 px; Graphviz is the local `dot`. Both are set
up in `scripts/build.py`; the `.mmd` and `.dot` files carry only the diagram.

## Conventions

- **One idea per figure.** A before/after comparison is two `subgraph`s side by side in one flowchart
  (`direction TB` inside each, `A ~~~ B` between them), not two figures.
- **Short labels.** Node text of 2–3 short lines (`<br/>` between them); put detail in the caption or a table.
- **Name real things.** Function, table and file names in `code`-like form where they matter (`classify_query_route`).
- **Caption every figure** with its number and what the reader should take from it:
  `Figure B1 — Ingestion before and after: structure is kept from parse to store`.

## Sizing

The page text area is about 182 mm wide. Wide Mermaid flowcharts shrink to fit and their text gets small; when
labels are hard to read on the rendered page:

1. switch `LR` to `TD` so the diagram grows down, not across;
2. cap it with `style="--w:150mm"` or `style="--h:120mm"` on the `.dia` div;
3. split it into two figures.

Sequence diagrams: keep participants to 5 or fewer and messages to about 12; beyond that, split by phase.

## Errors

`build.py` stops on a Mermaid parse error and prints the diagram name and message. Common causes: unquoted text
with `()`, `:` or `#` in a node label (wrap it in quotes: `A["calls f(x)"]`), and `end` used as a node id.

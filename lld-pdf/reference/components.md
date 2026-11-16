# Components

Every element the house CSS styles. Use these and nothing else; inline styles only for the sizing levers named
below. Units are print units (`mm`, `pt`).

## Cover

| Component | Markup | Use |
|---|---|---|
| Tag line | `<div class="tag">Engineering Tech Spec · Low-Level Design (LLD)</div>` | Document type, small grey pill above the title |
| Title | `<h1>Feature — Low-Level Design</h1>` | One per document |
| Subtitle | `<div class="sub">What it covers, in one line</div>` | Under the title |
| Meta table | `<table class="meta">` with `<tr><td>Ticket</td><td>MV-1234</td><td style="width:26mm;font-weight:600;background:var(--tint)">Status</td><td>Draft for review</td></tr>`; full-width rows use `<td colspan="3">` | Ticket, status, repository + branch, date, author, audience, scope rows. The first column is styled by `.meta td:first-child`; the third column's label needs the inline style shown |

## Contents

```html
<h4 style="margin-top:2mm">Contents</h4>
<div class="toc">
  <div class="part">Part A — Current implementation</div>
  <div class="row"><a href="#s1">1. Summary</a><span class="dots"></span><span class="pg" data-for="s1"></span></div>
</div>
```

- Two columns, dotted leaders. `data-for` must equal the section's `id`; `build.py` fills the number.
- The build finds a section's page by its heading text, so keep the contents label identical to the `h3` text.

## Structure

| Component | Markup | Rules |
|---|---|---|
| Part bar | `<h2>Part A — Name</h2>` | White text on the blue accent bar. Parts group sections; letters A, B, C … |
| Section | `<h3 id="s1">1. Name</h3>` | Blue, underlined. Numbered continuously across Parts; `id` = `s` + number |
| Sub-heading | `<h4>Name</h4>` | Inside a section; not in the contents |
| Lead | `<p class="lead">…</p>` | First paragraph of a section: the answer or the point |
| Paragraph, lists | `<p>`, `<ul>`, `<ol>` | Short; lists only for parallel items or steps |

## Content blocks

| Component | Markup | Use |
|---|---|---|
| Table | `<table><thead><tr><th>…</th></tr></thead><tbody>…</tbody></table>` | Default for anything compared or listed. `thead` repeats on each page; rows never split |
| Code inline | `<code>name</code>` | Identifiers, paths, settings |
| Code block | `<pre><code>…</code></pre>` | Commands, payloads, short snippets; escape `<` `>` `&` |
| Note | `<div class="note">…</div>` | Blue left rule: a takeaway or decision |
| Warning | `<div class="warn">…</div>` | Amber left rule: a risk, gap or caveat |
| Figure | `<figure><div class="dia" data-src="a1_arch.mmd"></div><figcaption>Figure A1 — What it shows</figcaption></figure>` | Every diagram; never split across pages |
| Two columns | `<div class="two"><div>…</div><div>…</div></div>` | Two small tables or lists side by side |
| Split | `<div class="split" style="--left:70mm"><figure>…</figure><div>…</div></div>` | A narrow figure beside its explanation |
| Class boxes | `<div class="uml-grid"><div class="uml"><div class="hd"><span class="st">«model»</span><b>Name</b></div><ul><li>field: type</li></ul></div></div>` | UML-style class or table cards, 3 per row; add `opt` (`class="uml opt"`) for optional ones |

## Sizing levers

| Problem | Lever |
|---|---|
| Diagram too small or too wide | `<div class="dia" data-src="…" style="--w:150mm">` (width) or `style="--h:110mm"` (height); A4 text width is about 182 mm |
| Wide table (6+ columns) | Put `style="width:…mm"` on the `<th>` cells to set column widths; keep cell text short; split into two tables if a row still wraps past 6 lines |
| Mostly empty page before a figure | Move the figure after the next paragraph, or reduce its `--h` so it fits the space left |
| Heading alone at a page bottom | Headings already avoid breaking after themselves; if one still strands, move the block above it or shorten it |

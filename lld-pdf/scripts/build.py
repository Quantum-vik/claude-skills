#!/usr/bin/env python3
"""Turn one HTML file into an A4 LLD PDF.

Usage: python3 build.py <doc.html> <out.pdf> --footer "Short title · MV-1234" [--diagrams <dir>]

<div class="dia" data-src="NAME.mmd|NAME.dot"></div> placeholders are replaced by SVGs rendered from
<diagrams>/NAME (.mmd via Mermaid in headless Chromium, .dot via Graphviz). The page is printed twice:
pass 1 finds each heading's page, pass 2 fills <span class="pg" data-for="ID"></span> in the contents.
Needs: chromium, node >= 22, pdftotext + pdfinfo (poppler), dot (only for .dot), internet for the Mermaid CDN.
"""
import argparse, html, json, pathlib, re, shutil, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
MERMAID = 'https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.esm.min.mjs'


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kw).stdout


def render_mermaid(names, dia, build):
    if not names:
        return {}
    srcs = [(dia / n).read_text() for n in names]
    page = build / 'mermaid-render.html'
    page.write_text(f"""<!doctype html><html><body><pre id="out"></pre><script type="module">
import mermaid from '{MERMAID}';
mermaid.initialize({{startOnLoad:false, theme:'neutral', securityLevel:'loose',
  flowchart:{{useMaxWidth:false, htmlLabels:true, nodeSpacing:28, rankSpacing:38, padding:10}},
  sequence:{{useMaxWidth:false, mirrorActors:false, wrap:true, width:118, actorMargin:26, messageMargin:26,
             boxMargin:6, noteMargin:6, messageFontSize:13, actorFontSize:13}},
  class:{{useMaxWidth:false}},
  themeVariables:{{fontFamily:'Noto Sans, Helvetica, Arial, sans-serif', fontSize:'13px'}}}});
const B={json.dumps(srcs)}; const out=[];
for (let i=0;i<B.length;i++){{ try{{ out.push((await mermaid.render('m'+i,B[i])).svg); }}catch(e){{ out.push('ERROR '+e); }} }}
document.getElementById('out').textContent=JSON.stringify(out);
</script></body></html>""")
    dom = run(['chromium', '--headless=new', '--disable-gpu', '--no-sandbox', '--virtual-time-budget=60000',
               '--dump-dom', f'file://{page}'], timeout=240)
    m = re.search(r'<pre id="out">(.*?)</pre>', dom, re.S)
    if not m or not m.group(1).strip():
        sys.exit('Mermaid did not run (no output). Is the jsdelivr CDN reachable?')
    svgs = json.loads(html.unescape(m.group(1)))
    errs = [f'  {n}: {s[:300]}' for n, s in zip(names, svgs) if s.startswith('ERROR')]
    if errs:
        sys.exit('Mermaid errors:\n' + '\n'.join(errs))
    return dict(zip(names, svgs))


def sized(svg):
    svg = svg[svg.index('<svg'):]
    head = re.match(r'<svg[^>]*>', svg).group(0)
    w, h = [float(x) for x in re.search(r'viewBox="([^"]*)"', head).group(1).split()[2:]]
    new = re.sub(r'\s(width|height|style)="[^"]*"', '', head).replace('<svg', f'<svg width="{w:.0f}" height="{h:.0f}"', 1)
    return svg.replace(head, new, 1)


def inline_diagrams(text, dia, build):
    names = list(dict.fromkeys(re.findall(r'<div class="dia" data-src="([^"]+)"', text)))
    missing = [n for n in names if not (dia / n).is_file()]
    if missing:
        sys.exit(f'Diagram files not found in {dia}: {missing}')
    rendered = render_mermaid([n for n in names if n.endswith('.mmd')], dia, build)
    for n in names:
        if not n.endswith('.mmd'):
            try:
                rendered[n] = run(['dot', '-Tsvg', str(dia / n)])
            except subprocess.CalledProcessError as e:
                sys.exit(f'Graphviz error in {n}:\n{e.stderr}')
    return re.sub(r'<div class="dia" data-src="([^"]+)"([^>]*)></div>',
                  lambda m: f'<div class="dia"{m.group(2)}>{sized(rendered[m.group(1)])}</div>', text)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('doc'); ap.add_argument('out')
    ap.add_argument('--footer', required=True, help='left footer text, e.g. "Short title · MV-1234"')
    ap.add_argument('--diagrams', help='diagram folder (default: <doc dir>/diagrams)')
    a = ap.parse_args()
    doc, out = pathlib.Path(a.doc).resolve(), pathlib.Path(a.out).resolve()
    dia = pathlib.Path(a.diagrams).resolve() if a.diagrams else doc.parent / 'diagrams'
    build = out.parent / '_build'
    build.mkdir(parents=True, exist_ok=True)
    src = doc.read_text()

    need = ['chromium', 'node', 'pdftotext', 'pdfinfo'] + (['dot'] if re.search(r'data-src="[^"]+\.dot"', src) else [])
    gone = [t for t in need if not shutil.which(t)]
    if gone:
        sys.exit(f'Missing tools: {", ".join(gone)} (chromium, node>=22, graphviz for .dot, poppler for pdftotext/pdfinfo)')

    print_html = build / f'{out.stem}.print.html'

    def print_pdf(text):
        print_html.write_text(text)
        try:
            run(['node', str(HERE / 'print.mjs'), str(print_html), str(out), a.footer], timeout=240)
        except subprocess.CalledProcessError as e:
            sys.exit(f'print.mjs failed:\n{e.stderr}')

    def page_texts():
        n = int(re.search(r'Pages:\s+(\d+)', run(['pdfinfo', str(out)])).group(1))
        return [re.sub(r'\s+', ' ', run(['pdftotext', '-f', str(p), '-l', str(p), str(out), '-'])) for p in range(1, n + 1)]

    text = re.sub(r'<script type="module">.*?</script>', '', inline_diagrams(src, dia, build), flags=re.S)
    print_pdf(text)
    pages = page_texts()
    clean = lambda t: re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', t))).strip()
    heads = dict(re.findall(r'<h[23] id="([^"]+)"[^>]*>(.*?)</h[23]>', text))
    # blank the contents block on page 1 (each toc label once, whatever column order pdftotext used)
    # so a toc entry never counts as the heading it points to
    labels = re.findall(r'<div class="part">(.*?)</div>', text) + re.findall(r'<a href="#[^"]+">(.*?)</a>', text)
    if 'Contents' in pages[0]:
        head, tail = pages[0].split('Contents', 1)
        for lab in labels:
            tail = tail.replace(clean(lab), ' ', 1)
        pages[0] = head + tail
    warnings = []

    def page_of(i):
        needle = clean(heads.get(i, ''))
        for n, t in enumerate(pages, start=1):
            if needle and needle in t:
                return n
        warnings.append(i)
        return '?'
    cmap = {}

    def fill(m):
        cmap[m.group(1)] = page_of(m.group(1))
        return f'<span class="pg">{cmap[m.group(1)]}</span>'
    text = re.sub(r'<span class="pg" data-for="([^"]+)"></span>', fill, text)
    print_pdf(text)
    pages = page_texts()
    sparse = [i + 1 for i, t in enumerate(pages[:-1]) if len(t.split()) < 120]

    print(f'{out}: {len(pages)} pages')
    print('contents map:', ', '.join(f'{k}->{v}' for k, v in cmap.items()) or '(no contents entries)')
    if warnings:
        print('WARNING: heading not found, page shown as "?":', ', '.join(f'{i} ({heads.get(i, "no <h2|h3 id>")!r})' for i in warnings))
    print('sparse pages (<120 words, last excluded):', sparse or 'none')
    print('Mermaid/dot errors: none')


if __name__ == '__main__':
    main()

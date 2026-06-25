#!/usr/bin/env python3
"""
mahadurot_collator.py

Parse a Mahadurot critical edition file (YAML front matter + inline variant
notation) and produce collation statistics + an interactive HTML report.

The inline notation uses [text]{witnesses} for variant readings:
    [[[reading_a]{N,B,F} [reading_b]{O,M}]]]
    [reading]{M | om. F,P,N,B,O}   ← om. = omission

Usage:
    python3 mahadurot_collator.py edition.txt [output.html]
"""

import re, sys, yaml
from pathlib import Path
from collections import defaultdict, Counter

# ── File loading ─────────────────────────────────────────────────────────────

def load_edition(path):
    """Split YAML front matter from body text."""
    raw = Path(path).read_text(encoding='utf-8')
    parts = re.split(r'\n?---\n', raw, maxsplit=2)
    if len(parts) < 3:
        raise ValueError("Cannot find YAML front matter — expected --- delimiters")
    header = yaml.safe_load(parts[1])
    body   = parts[2]
    return header, body

def get_sigla(header):
    """Return (ordered symbol list, symbol→long_title dict, corrigenda set).

    Corrigenda/addenda are witnesses whose symbol contains * or +, or whose
    short_title contains 'corr' or 'add'.  They are layers of correction on a
    base manuscript, not independent witnesses, and are excluded from pairwise
    agreement statistics.
    """
    entries = header.get('sigla', [])
    symbols = [e['symbol'] for e in entries]
    labels  = {e['symbol']: e.get('long_title', e['symbol']) for e in entries}
    corrigenda = {
        e['symbol'] for e in entries
        if any(c in e['symbol'] for c in ('*', '+'))
        or any(t in e.get('short_title', '').lower() for t in ('corr', 'add'))
    }
    return symbols, labels, corrigenda

# ── Parsing ──────────────────────────────────────────────────────────────────

def parse_witness_spec(spec, sym_set):
    """
    Parse a witness specification string.
    Returns (present: list, absent: list).

    Examples:
        "N,B,F,M,P"         → (['N','B','F','M','P'], [])
        "M | om. F,P,N,B,O" → (['M'], ['F','P','N','B','O'])
        "O | om. P,F,N,B"   → (['O'], ['P','F','N','B'])
    """
    spec = spec.strip()
    if '|' in spec:
        left, right = spec.split('|', 1)
        right = right.strip()
        if right.lower().startswith('om.'):
            right = right[3:].strip()
        present = [s.strip() for s in left.split(',')  if s.strip()]
        absent  = [s.strip() for s in right.split(',') if s.strip()]
    else:
        present = [s.strip() for s in spec.split(',') if s.strip()]
        absent  = []
    present = [p for p in present if p in sym_set]
    absent  = [a for a in absent  if a in sym_set]
    return present, absent

def clean_text(t):
    """Strip markup from reading text, leaving plain Hebrew words."""
    t = re.sub(r'\{[^}]+\}', '', t)
    t = re.sub(r'//[^\[\]\s]*', '', t)
    t = re.sub(r'[\[\]]+', ' ', t)
    return ' '.join(t.split())

def extract_readings(body, symbols):
    """
    Scan body for all [text]{witness_spec} patterns.
    Returns list of reading dicts, sorted by position.
    """
    sym_set = set(symbols)
    results = []

    for m in re.finditer(r'\{([^}]+)\}', body):
        spec = m.group(1)
        tokens = re.split(r'[,\s|.]+', spec)
        if not any(t.strip() in sym_set for t in tokens):
            continue

        close_pos = m.start() - 1
        if close_pos < 0 or body[close_pos] != ']':
            continue

        depth = 1
        open_pos = None
        for i in range(close_pos - 1, -1, -1):
            c = body[i]
            if c == ']':
                depth += 1
            elif c == '[':
                depth -= 1
                if depth == 0:
                    open_pos = i
                    break
        if open_pos is None:
            continue

        raw_text = body[open_pos + 1 : close_pos]
        present, absent = parse_witness_spec(spec, sym_set)
        if not present and not absent:
            continue

        results.append({
            'open':    open_pos,
            'close':   m.end(),
            'text':    clean_text(raw_text),
            'present': present,
            'absent':  absent,
        })

    results.sort(key=lambda r: r['open'])
    return results

def group_loci(readings, body):
    """
    Group readings into loci using two conditions — both must hold:
    1. Positional: only whitespace/brackets between the two readings in the body
    2. Witness-disjoint: no witness appears in both readings
    """
    if not readings:
        return []

    loci, group = [], [readings[0]]

    for r in readings[1:]:
        prev = group[-1]

        between = body[prev['close'] : r['open']]
        close_enough = bool(re.fullmatch(r'[\s\[\]]*', between))

        group_witnesses = set()
        for gr in group:
            group_witnesses.update(gr['present'] + gr['absent'])
        r_witnesses = set(r['present'] + r['absent'])
        disjoint = not r_witnesses.intersection(group_witnesses)

        if close_enough and disjoint:
            group.append(r)
        else:
            loci.append(group)
            group = [r]

    loci.append(group)
    return loci

def get_context(body, group, chars=60):
    """Extract a few words of plain context before/after a locus group."""
    before_raw = body[max(0, group[0]['open'] - chars * 3) : group[0]['open']]
    after_raw  = body[group[-1]['close'] : group[-1]['close'] + chars * 3]
    before = clean_text(before_raw)[-chars:]
    after  = clean_text(after_raw)[:chars]
    return before, after

def build_locus_records(locus_groups, body, symbols):
    """
    Convert grouped readings into structured locus records.
    Each record has a witness_map {sigil → reading text} and reading_groups list.
    Readings are compared exactly — no spelling normalization.
    """
    records = []
    for idx, group in enumerate(locus_groups):
        witness_map = {}

        for r in group:
            for sig in r['present']:
                witness_map[sig] = r['text']
            for sig in r['absent']:
                witness_map[sig] = ''   # omission = empty string

        if len(witness_map) < 2:
            continue

        # Only keep loci with real variation
        if len(set(witness_map.values())) < 2:
            continue

        # Group witnesses by exact reading text
        reading_groups_dict = defaultdict(list)
        for sig, txt in witness_map.items():
            reading_groups_dict[txt].append(sig)

        reading_groups = [
            {'text': txt, 'witnesses': sorted(sigs)}
            for txt, sigs in reading_groups_dict.items()
        ]
        reading_groups.sort(key=lambda rg: (-len(rg['witnesses']), rg['text'] == ''))

        before, after = get_context(body, group)

        has_omission = any(rg['text'] == '' for rg in reading_groups)
        records.append({
            'id':             idx + 1,
            'reading_groups': reading_groups,
            'witness_map':    witness_map,
            'context_before': before,
            'context_after':  after,
            'has_omission':   has_omission,
        })

    return records

# ── Statistics ───────────────────────────────────────────────────────────────

def compute_pairs(loci, symbols, corrigenda=None):
    """Compute pairwise agreement rates, excluding corrigenda witnesses."""
    corrigenda = corrigenda or set()
    active = [s for s in symbols if s not in corrigenda]
    pairs = defaultdict(lambda: {'agree': 0, 'total': 0})
    for locus in loci:
        wm = locus['witness_map']
        covered = [s for s in active if s in wm]
        for i in range(len(covered)):
            for j in range(i + 1, len(covered)):
                a, b = covered[i], covered[j]
                pairs[(a, b)]['total'] += 1
                if wm[a] == wm[b]:
                    pairs[(a, b)]['agree'] += 1
    return pairs

def compute_singletons(loci):
    counts = Counter()
    for locus in loci:
        for rg in locus['reading_groups']:
            if len(rg['witnesses']) == 1:
                counts[rg['witnesses'][0]] += 1
    return counts

def compute_splits(loci):
    splits = Counter()
    for locus in loci:
        groups = [frozenset(rg['witnesses'])
                  for rg in locus['reading_groups'] if rg['text'] != '']
        if len(groups) == 2:
            a, b = sorted(groups, key=lambda g: tuple(sorted(g)))
            splits[(tuple(sorted(a)), tuple(sorted(b)))] += 1
    return splits

def agreement_rate(p):
    return p['agree'] / p['total'] if p['total'] else 0

# ── HTML rendering ───────────────────────────────────────────────────────────

def render_html(header, symbols, labels, loci, pairs, singletons, splits, out_path, corrigenda=None):
    title_info = header.get('title', {})
    book_name  = title_info.get('book_name', out_path.stem)
    editor     = title_info.get('editor', '')
    n_loci     = len(loci)

    # ── Loci section
    loci_html = ''
    for locus in loci:
        rg_html = ''
        for rg in locus['reading_groups']:
            sigs_str = ' '.join(
                f'<span class="sig">{s}</span>' for s in rg['witnesses'])
            display  = rg['text'] if rg['text'] else '<em class="om">om.</em>'
            rg_html += (
                f'<div class="reading">'
                f'<span class="rdg-text">{display}</span>'
                f'<span class="rdg-sigs">{sigs_str}</span>'
                f'</div>')

        n_groups = len(locus['reading_groups'])
        cls = ('locus-multi' if n_groups > 2
               else 'locus-split' if n_groups == 2
               else 'locus-single')

        ctx_b = locus['context_before']
        ctx_a = locus['context_after']
        ctx_html = ''
        if ctx_b or ctx_a:
            ctx_html = (f'<div class="ctx">…{ctx_b} '
                        f'<span class="ctx-here">◆</span> {ctx_a}…</div>')

        omission_attr = ' data-omission="1"' if locus.get('has_omission') else ''
        annot_html = (
            f'<div class="annot" id="annot-{locus["id"]}">'
            f'<div class="annot-labels">'
            f'<button class="annot-btn" data-locus="{locus["id"]}" data-label="significant"  onclick="setLabel(this)">significant</button>'
            f'<button class="annot-btn" data-locus="{locus["id"]}" data-label="possibly"     onclick="setLabel(this)">possibly significant</button>'
            f'<button class="annot-btn" data-locus="{locus["id"]}" data-label="not"          onclick="setLabel(this)">not significant</button>'
            f'</div>'
            f'<textarea class="annot-note" data-locus="{locus["id"]}" placeholder="Notes…" oninput="saveNote(this)"></textarea>'
            f'</div>'
        )
        loci_html += (
            f'<div class="locus {cls}" id="locus-{locus["id"]}" data-groups="{n_groups}"{omission_attr}>'
            f'<div class="locus-id">#{locus["id"]}<span class="annot-badge" id="badge-{locus["id"]}" style="display:none"></span></div>'
            f'{ctx_html}'
            f'<div class="readings">{rg_html}</div>'
            f'{annot_html}'
            f'</div>')

    # ── Pairwise table
    pair_rows = ''
    sorted_pairs = sorted(
        [(a, b, p) for (a, b), p in pairs.items() if p['total'] > 0],
        key=lambda x: -agreement_rate(x[2]))
    for a, b, p in sorted_pairs:
        rate  = agreement_rate(p)
        pct   = f'{rate*100:.1f}%'
        bar_w = int(rate * 100)
        col   = ('#2ca02c' if rate >= 0.90
                 else '#fd8d3c' if rate >= 0.75
                 else '#d62728')
        pair_rows += (
            f'<tr><td class="sig">{a}</td><td class="x">×</td>'
            f'<td class="sig">{b}</td>'
            f'<td class="pct">{pct}</td>'
            f'<td><div class="bar" style="width:{bar_w}%;background:{col}"></div></td>'
            f'<td class="frac">{p["agree"]}/{p["total"]}</td></tr>')

    # ── Singleton table
    sing_rows = ''
    for sig, n in singletons.most_common():
        pct = f'{n/n_loci*100:.0f}%' if n_loci else '0%'
        sing_rows += (
            f'<tr><td class="sig">{sig}</td>'
            f'<td class="label-col">{labels.get(sig, sig)}</td>'
            f'<td>{n}</td><td>{pct}</td></tr>')

    # ── Split patterns
    split_rows = ''
    for (a, b), count in splits.most_common(10):
        split_rows += (
            f'<tr><td>[{", ".join(a)}] vs [{", ".join(b)}]</td>'
            f'<td>{count}×</td></tr>')

    # ── Witness list
    corrigenda = corrigenda or set()
    wit_rows = ''
    for s in symbols:
        if s in corrigenda:
            wit_rows += (
                f'<tr style="opacity:.65">'
                f'<td><span class="sig sig-corr">{s}</span></td>'
                f'<td class="label-col">{labels.get(s, s)} <em style="color:#a07050">(corrigendum — excluded from statistics)</em></td></tr>')
        else:
            wit_rows += (
                f'<tr><td class="sig-wrap"><span class="sig">{s}</span></td>'
                f'<td class="label-col">{labels.get(s, s)}</td></tr>')

    html = f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Collation — {book_name}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: #faf9f7; font-family: Georgia, serif; color: #2c2825; font-size: 14px; }}
header {{ background: #2c4a3e; color: #fff; padding: 14px 24px; }}
header h1 {{ font-size: 1.05rem; font-weight: normal; }}
header p  {{ font-size: .76rem; opacity: .8; margin-top: 3px; }}
.layout {{ display: flex; height: calc(100vh - 55px); }}
#main {{ flex: 1; padding: 18px 22px; overflow-y: auto; }}
#sidebar {{ width: 290px; background: #fff; border-left: 1px solid #e2ddd6;
            padding: 14px; overflow-y: auto; font-size: .78rem; flex-shrink: 0; }}
h2 {{ font-size: .76rem; font-weight: bold; color: #2c4a3e; text-transform: uppercase;
      letter-spacing: .05em; border-bottom: 1px solid #e2ddd6;
      padding-bottom: 5px; margin: 16px 0 8px; }}
h2:first-child {{ margin-top: 0; }}

.filter-bar {{ margin-bottom: 14px; display: flex; gap: 6px; flex-wrap: wrap; }}
.filter-btn {{ padding: 4px 10px; border: 1px solid #2c4a3e; border-radius: 4px;
               background: #fff; color: #2c4a3e; cursor: pointer; font-size: .72rem; }}
.filter-btn.active {{ background: #2c4a3e; color: #fff; }}

.locus {{ background: #fff; border: 1px solid #e2ddd6; border-radius: 6px;
          margin-bottom: 8px; padding: 9px 11px; }}
.locus-split {{ border-left: 3px solid #fd8d3c; }}
.locus-multi  {{ border-left: 3px solid #d62728; }}
.locus-id {{ font-size: .68rem; color: #bbb; font-family: monospace; margin-bottom: 5px; }}
.ctx {{ font-size: .78rem; color: #9d9087; margin-bottom: 6px; direction: rtl;
        line-height: 1.5; }}
.ctx-here {{ color: #2c4a3e; font-size: .7rem; }}
.readings {{ display: flex; flex-direction: column; gap: 4px; }}
.reading {{ display: flex; justify-content: space-between; align-items: baseline;
            gap: 8px; padding: 3px 0; border-bottom: 1px dotted #e2ddd6; }}
.reading:last-child {{ border-bottom: none; }}
.rdg-text {{ flex: 1; direction: rtl; line-height: 1.5; }}
.rdg-sigs {{ display: flex; gap: 3px; flex-wrap: wrap; flex-shrink: 0; }}
.sig {{ background: #2c4a3e; color: #fff; border-radius: 3px;
         padding: 1px 5px; font-family: monospace; font-size: .7rem; }}
.om {{ color: #aaa; font-style: italic; font-size: .85em; }}

table {{ width: 100%; border-collapse: collapse; }}
td, th {{ padding: 3px 5px; text-align: left; }}
tr:nth-child(even) {{ background: #f5f3f0; }}
.pct {{ font-weight: bold; width: 42px; }}
.x {{ color: #aaa; width: 14px; text-align: center; }}
.bar {{ height: 6px; border-radius: 3px; min-width: 2px; }}
.frac {{ color: #9d9087; font-size: .7rem; }}
.label-col {{ color: #7a736c; font-size: .72rem; max-width: 160px;
               white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}

.annot {{ margin-top: 8px; padding-top: 8px; border-top: 1px dashed #e2ddd6; }}
.annot-labels {{ display: flex; gap: 5px; flex-wrap: wrap; margin-bottom: 5px; }}
.annot-btn {{
  padding: 3px 9px; border-radius: 12px; border: 1px solid #c8c0b4;
  background: #fff; color: #7a736c; font-size: .7rem; cursor: pointer;
  font-family: Georgia, serif; transition: all .12s;
}}
.annot-btn:hover {{ border-color: #2c4a3e; color: #2c4a3e; }}
.annot-btn.active {{ color: #fff; border-color: transparent; }}
.annot-btn[data-label="significant"].active  {{ background: #2c4a3e; }}
.annot-btn[data-label="possibly"].active     {{ background: #c07a2a; }}
.annot-btn[data-label="not"].active          {{ background: #9e9e9e; }}
.sig-corr {{ background: #a07050; color: #fff; border-radius: 3px;
              padding: 1px 5px; font-family: monospace; font-size: .7rem; }}
.annot-note {{
  width: 100%; margin-top: 4px; font-size: .75rem; font-family: Georgia, serif;
  border: 1px solid #e2ddd6; border-radius: 4px; padding: 4px 7px; resize: vertical;
  min-height: 34px; color: #2c2825; background: #faf9f7;
}}
.annot-note:focus {{ outline: none; border-color: #2c4a3e; }}
.locus.annotated {{ border-left-width: 4px !important; }}
.annot-badge {{
  display: inline-block; font-size: .62rem; padding: 1px 6px; border-radius: 8px;
  color: #fff; margin-left: 6px; vertical-align: middle; font-family: monospace;
}}
#annot-toolbar {{
  background: #fff; border-bottom: 1px solid #e2ddd6;
  padding: 7px 16px; display: flex; gap: 8px; align-items: center; font-size: .76rem;
  flex-shrink: 0;
}}
#annot-toolbar span {{ color: #7a736c; }}
.tb-btn {{
  padding: 4px 11px; border-radius: 5px; border: 1px solid #2c4a3e;
  background: #fff; color: #2c4a3e; font-size: .73rem; cursor: pointer;
  font-family: Georgia, serif;
}}
.tb-btn:hover {{ background: #2c4a3e; color: #fff; }}
#annot-progress {{ margin-left: auto; font-size: .72rem; color: #7a736c; }}
</style>
</head>
<body>
<header>
  <h1>{book_name}</h1>
  <p>{editor} · {n_loci} variant loci · {len(symbols)} witnesses</p>
</header>
<div id="annot-toolbar">
  <span>Annotate:</span>
  <button class="tb-btn" onclick="exportLabels()">⬇ Export JSON</button>
  <label class="tb-btn" style="cursor:pointer">⬆ Import JSON
    <input type="file" accept=".json" style="display:none" onchange="importLabels(event)">
  </label>
  <button class="tb-btn" onclick="clearAll()" style="border-color:#c0392b;color:#c0392b">Clear all</button>
  <span id="annot-progress"></span>
</div>
<div class="layout">
<div id="main">
  <div class="filter-bar">
    <button class="filter-btn active" onclick="filter('all',this)">All ({n_loci})</button>
    <button class="filter-btn" onclick="filter('split',this)">2 readings</button>
    <button class="filter-btn" onclick="filter('multi',this)">3+ readings</button>
    <button class="filter-btn" onclick="filter('labeled',this)">Labeled</button>
    <button class="filter-btn" onclick="filter('unlabeled',this)">Unlabeled</button>
  </div>
  <div id="loci-list">{loci_html}</div>
</div>
<div id="sidebar">
  <h2>Witnesses</h2>
  <table>{wit_rows}</table>

  <h2>Pairwise agreement</h2>
  <table>
    <colgroup>
      <col style="width:28px"><col style="width:12px"><col style="width:28px">
      <col style="width:42px"><col><col style="width:44px">
    </colgroup>
    {pair_rows}
  </table>

  <h2>Unique readings (singletons)</h2>
  <table>
    <tr><th>Sig.</th><th>Witness</th><th>n</th><th>%</th></tr>
    {sing_rows or '<tr><td colspan="4" style="color:#aaa">None found</td></tr>'}
  </table>

  <h2>Top split patterns</h2>
  <table>{split_rows or '<tr><td style="color:#aaa">None found</td></tr>'}</table>
</div>
</div>
<script>
const STORE_KEY = 'annot_' + document.title.replace(/\s+/g,'_');
const LABEL_COLOR = {{
  significant: '#2c4a3e',
  possibly:    '#c07a2a',
  not:         '#9e9e9e',
}};
const LABEL_NAME = {{
  significant: 'significant',
  possibly:    'possibly significant',
  not:         'not significant',
}};

let annotations = {{}};
try {{ annotations = JSON.parse(localStorage.getItem(STORE_KEY) || '{{}}'); }} catch(e) {{}}

window.addEventListener('DOMContentLoaded', () => {{
  let changed = false;
  document.querySelectorAll('.locus').forEach(el => {{
    const id = el.id.replace('locus-', '');
    if (!annotations[id] || !annotations[id].label) {{
      const defaultLabel = el.dataset.omission === '1' ? 'significant' : 'possibly';
      annotations[id] = {{ ...(annotations[id] || {{}}), label: defaultLabel }};
      changed = true;
    }}
    if (annotations[id].label) applyLabel(id, annotations[id].label);
    if (annotations[id].note) {{
      const ta = document.querySelector(`.annot-note[data-locus="${{id}}"]`);
      if (ta) ta.value = annotations[id].note;
    }}
  }});
  if (changed) save();
  updateProgress();
}});

function save() {{
  try {{ localStorage.setItem(STORE_KEY, JSON.stringify(annotations)); }} catch(e) {{}}
  updateProgress();
}}

function setLabel(btn) {{
  const id    = btn.dataset.locus;
  const label = btn.dataset.label;
  const current = annotations[id] && annotations[id].label;
  if (current === label) {{
    annotations[id] = {{ ...(annotations[id] || {{}}), label: null }};
    applyLabel(id, null);
  }} else {{
    annotations[id] = {{ ...(annotations[id] || {{}}), label }};
    applyLabel(id, label);
  }}
  save();
}}

function applyLabel(id, label) {{
  document.querySelectorAll(`.annot-btn[data-locus="${{id}}"]`).forEach(b => {{
    b.classList.toggle('active', b.dataset.label === label);
  }});
  const card = document.getElementById('locus-' + id);
  if (card) {{
    card.classList.toggle('annotated', !!label);
    if (label) card.style.borderLeftColor = LABEL_COLOR[label] || '';
    else card.style.borderLeftColor = '';
  }}
  const badge = document.getElementById('badge-' + id);
  if (badge) {{
    if (label) {{
      badge.textContent = LABEL_NAME[label] || label;
      badge.style.background = LABEL_COLOR[label];
      badge.style.display = 'inline-block';
    }} else {{
      badge.style.display = 'none';
    }}
  }}
}}

function saveNote(ta) {{
  const id = ta.dataset.locus;
  annotations[id] = {{ ...(annotations[id] || {{}}), note: ta.value }};
  save();
}}

function updateProgress() {{
  const total   = document.querySelectorAll('.locus').length;
  const labeled = Object.values(annotations).filter(a => a.label).length;
  const el = document.getElementById('annot-progress');
  if (el) el.textContent = labeled + ' / ' + total + ' labeled';
}}

function exportLabels() {{
  const blob = new Blob([JSON.stringify(annotations, null, 2)], {{type:'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'annotations_' + document.title.replace(/[^a-z0-9]/gi,'_') + '.json';
  a.click();
}}

function importLabels(evt) {{
  const file = evt.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {{
    try {{
      annotations = JSON.parse(e.target.result);
      save();
      for (const [id, ann] of Object.entries(annotations)) {{
        if (ann.label) applyLabel(id, ann.label);
        if (ann.note) {{
          const ta = document.querySelector(`.annot-note[data-locus="${{id}}"]`);
          if (ta) ta.value = ann.note;
        }}
      }}
      updateProgress();
    }} catch(err) {{ alert('Could not parse JSON: ' + err); }}
  }};
  reader.readAsText(file);
}}

function clearAll() {{
  if (!confirm('Clear all annotations?')) return;
  annotations = {{}};
  save();
  document.querySelectorAll('.annot-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.locus').forEach(c => {{
    c.classList.remove('annotated'); c.style.borderLeftColor = '';
  }});
  document.querySelectorAll('.annot-badge').forEach(b => b.style.display='none');
  document.querySelectorAll('.annot-note').forEach(t => t.value='');
}}

function filter(mode, btn) {{
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.locus').forEach(el => {{
    const g = +el.dataset.groups;
    const id = el.id.replace('locus-','');
    const hasLabel = !!(annotations[id] && annotations[id].label);
    let show = true;
    if      (mode === 'split')     show = g === 2;
    else if (mode === 'multi')     show = g > 2;
    else if (mode === 'labeled')   show = hasLabel;
    else if (mode === 'unlabeled') show = !hasLabel;
    el.style.display = show ? '' : 'none';
  }});
}}
</script>
</body>
</html>"""

    out_path.write_text(html, encoding='utf-8')
    print(f"Saved: {out_path}")

# ── Stemma graph ─────────────────────────────────────────────────────────────

def render_stemma(symbols, labels, pairs, singletons, n_loci, title, out_path):
    """Generate a D3.js force-directed stemma graph."""

    avg_agree = {}
    for sig in symbols:
        rates = [agreement_rate(p) for (a, b), p in pairs.items()
                 if (a == sig or b == sig) and p['total'] >= 3]
        avg_agree[sig] = sum(rates) / len(rates) if rates else 0

    def group(sig):
        a = avg_agree.get(sig, 0)
        if a >= 0.75: return 1
        if a >= 0.50: return 2
        return 3

    group_color = {1: '#1a6b3c', 2: '#c77d2a', 3: '#9d0208'}
    group_label = {1: 'Core family', 2: 'Intermediate', 3: 'Outlier'}

    max_sing = max((singletons.get(s, 0) for s in symbols), default=1)

    nodes_js = ',\n  '.join(
        '{{id:"{s}",label:"{s}",full:"{full}",singletons:{n},group:{g}}}'.format(
            s=s,
            full=labels.get(s, s).replace('"', ''),
            n=singletons.get(s, 0),
            g=group(s))
        for s in symbols)

    links_js = ',\n  '.join(
        '{{source:"{a}",target:"{b}",agreement:{r:.3f}}}'.format(
            a=a, b=b, r=agreement_rate(p))
        for (a, b), p in pairs.items()
        if p['total'] >= 3)

    seen_groups = sorted({group(s) for s in symbols})
    legend_html = ''.join(
        f'<div class="legend-item"><div class="legend-dot" style="background:{group_color[g]}"></div>'
        f'<span>{group_label[g]}</span></div>'
        for g in seen_groups)

    ms_html = ''
    for s in sorted(symbols, key=lambda x: singletons.get(x, 0)):
        n   = singletons.get(s, 0)
        pct = int(n / n_loci * 100) if n_loci else 0
        col = group_color[group(s)]
        ms_html += (
            f'<div class="ms-detail">'
            f'<div class="sigil" style="color:{col}">{s}</div>'
            f'<div class="ms-name">{labels.get(s, s)}</div>'
            f'<div class="bar-wrap"><div class="bar-fill" style="width:{max(pct,1)}%;background:{col}"></div></div>'
            f'<div class="ms-stat">Unique readings: {n}/{n_loci} ({pct}%)</div>'
            f'</div>')

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stemma — {title}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: #faf9f7; font-family: Georgia, serif; color: #2c2825; }}
header {{ background: #2c4a3e; color: #fff; padding: 14px 24px; }}
header h1 {{ font-size: 1.05rem; font-weight: normal; }}
header p  {{ font-size: .76rem; opacity: .8; margin-top: 3px; }}
.layout {{ display: flex; height: calc(100vh - 55px); }}
#graph-area {{ flex: 1; position: relative; }}
#sidebar {{ width: 270px; background: #fff; border-left: 1px solid #e2ddd6;
            padding: 16px; overflow-y: auto; font-size: .8rem; }}
svg {{ width: 100%; height: 100%; }}
.node circle {{ cursor: pointer; stroke: #fff; stroke-width: 2.5; }}
.node text {{ pointer-events: none; font-family: monospace; font-weight: bold;
              font-size: 12px; fill: #fff; text-anchor: middle;
              dominant-baseline: middle; }}
.link {{ stroke-opacity: .7; }}
.tooltip {{ position: absolute; background: #2c2825; color: #fff;
             padding: 7px 10px; border-radius: 6px; font-size: .76rem;
             pointer-events: none; opacity: 0; transition: opacity .15s;
             max-width: 220px; line-height: 1.5; }}
h2 {{ font-size: .76rem; font-weight: bold; color: #2c4a3e; text-transform: uppercase;
      letter-spacing: .05em; border-bottom: 1px solid #e2ddd6;
      padding-bottom: 5px; margin: 16px 0 10px; }}
h2:first-child {{ margin-top: 0; }}
.legend-item {{ display: flex; align-items: center; gap: 8px; margin-bottom: 7px; }}
.legend-dot {{ width: 13px; height: 13px; border-radius: 50%; flex-shrink: 0; }}
.ms-detail {{ margin-bottom: 10px; padding: 8px; background: #faf9f7;
               border-radius: 5px; border: 1px solid #e2ddd6; }}
.sigil {{ font-family: monospace; font-weight: bold; font-size: .88rem; }}
.ms-name {{ color: #7a736c; font-size: .72rem; margin: 2px 0 4px; }}
.bar-wrap {{ background: #e5e7eb; border-radius: 4px; height: 5px; }}
.bar-fill {{ height: 5px; border-radius: 4px; }}
.ms-stat {{ font-size: .7rem; color: #aaa; margin-top: 3px; }}
.edge-grad {{ height: 7px; border-radius: 4px;
  background: linear-gradient(to right, #d62728, #fd8d3c, #1a6b3c); }}
.edge-labels {{ display: flex; justify-content: space-between;
                font-size: .7rem; color: #7a736c; margin-top: 3px; }}
#thr-wrap {{ margin-top: 12px; }}
#thr-wrap label {{ font-size: .76rem; color: #7a736c; display: block; margin-bottom: 4px; }}
#thr {{ width: 100%; accent-color: #2c4a3e; }}
.note {{ font-size: .72rem; color: #9d9087; margin-top: 14px; line-height: 1.5;
          border-top: 1px solid #e2ddd6; padding-top: 10px; }}
</style>
</head>
<body>
<header>
  <h1>Stemmatic Network — {title}</h1>
  <p>{n_loci} variant loci · {len(symbols)} witnesses · node size = text stability</p>
</header>
<div class="layout">
  <div id="graph-area">
    <svg id="svg"></svg>
    <div class="tooltip" id="tip"></div>
  </div>
  <div id="sidebar">
    <h2>Groups</h2>
    {legend_html}

    <div class="edge-grad" style="margin-top:14px"></div>
    <div class="edge-labels"><span>Low agreement</span><span>High</span></div>

    <div id="thr-wrap">
      <label>Show edges ≥ <span id="thr-val">60</span>%</label>
      <input type="range" id="thr" min="0" max="99" value="60" step="1">
    </div>

    <h2 style="margin-top:18px">Witnesses</h2>
    {ms_html}

    <div class="note">
      Node size ∝ stability (larger = fewer unique readings).<br>
      Drag nodes to explore. Scroll to zoom.
    </div>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"></script>
<script>
const nodes = [
  {nodes_js}
];
const allLinks = [
  {links_js}
];
const groupColor = {{{','.join(f'{k}:"{v}"' for k, v in group_color.items())}}};
const agreeColor = d3.scaleSequential().domain([0.4, 1.0]).interpolator(d3.interpolateRdYlGn);
const maxSing = {max_sing} || 1;
const rScale = d3.scaleSqrt().domain([0, maxSing]).range([28, 12]);

const area = document.getElementById('graph-area');
const svg  = d3.select('#svg');
const tip  = document.getElementById('tip');
let W, H;

function resize() {{
  W = area.clientWidth; H = area.clientHeight;
  svg.attr('viewBox', `0 0 ${{W}} ${{H}}`);
}}
resize();
window.addEventListener('resize', () => {{ resize(); sim.alpha(0.1).restart(); }});

const g = svg.append('g');
svg.call(d3.zoom().scaleExtent([0.3,4]).on('zoom', e => g.attr('transform', e.transform)));

const linkG = g.append('g');
const nodeG = g.append('g');
let linkSel;

let threshold = 0.60;
document.getElementById('thr').addEventListener('input', function() {{
  threshold = +this.value / 100;
  document.getElementById('thr-val').textContent = this.value;
  updateLinks();
}});

function updateLinks() {{
  const filtered = allLinks.filter(l => l.agreement >= threshold);
  linkSel = linkG.selectAll('line').data(filtered, d =>
    (d.source.id||d.source) + (d.target.id||d.target));
  linkSel.exit().remove();
  linkSel = linkSel.enter().append('line').attr('class','link')
    .merge(linkSel)
    .attr('stroke', d => agreeColor(d.agreement))
    .attr('stroke-width', d => Math.pow((d.agreement - 0.3) / 0.7, 1.5) * 14 + 1)
    .on('mouseover', (e,d) => {{
      const s = d.source.id||d.source, t = d.target.id||d.target;
      showTip(e, `${{s}} × ${{t}}<br><strong>${{Math.round(d.agreement*100)}}% agreement</strong>`);
    }}).on('mouseout', hideTip);
  sim.force('link').links(filtered);
  sim.alpha(0.3).restart();
}}

const nodeSel = nodeG.selectAll('.node').data(nodes, d => d.id)
  .enter().append('g').attr('class','node')
  .call(d3.drag()
    .on('start', (e,d) => {{ if(!e.active) sim.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; }})
    .on('drag',  (e,d) => {{ d.fx=e.x; d.fy=e.y; }})
    .on('end',   (e,d) => {{ if(!e.active) sim.alphaTarget(0); d.fx=null; d.fy=null; }}))
  .on('mouseover', (e,d) => showTip(e,
    `<strong>${{d.label}}</strong> — ${{d.full}}<br>Unique readings: ${{d.singletons}}/{n_loci}`))
  .on('mouseout', hideTip);

nodeSel.append('circle').attr('r', d => rScale(d.singletons)).attr('fill', d => groupColor[d.group]);
nodeSel.append('text').text(d => d.label);

const sim = d3.forceSimulation(nodes)
  .force('link', d3.forceLink([]).id(d => d.id)
    .distance(d => (1 - d.agreement) * 350 + 60)
    .strength(d => d.agreement * 0.9))
  .force('charge', d3.forceManyBody().strength(-280))
  .force('center', d3.forceCenter(0, 0))
  .force('collision', d3.forceCollide(d => rScale(d.singletons) + 10))
  .on('tick', () => {{
    if (linkSel) {{
      linkSel.attr('x1',d=>d.source.x).attr('y1',d=>d.source.y)
             .attr('x2',d=>d.target.x).attr('y2',d=>d.target.y);
    }}
    nodeSel.attr('transform', d => `translate(${{d.x}},${{d.y}})`);
    g.attr('transform', `translate(${{W/2}},${{H/2}})`);
  }});

updateLinks();

function showTip(e, html) {{
  tip.innerHTML = html; tip.style.opacity = 1;
  tip.style.left = (e.pageX+14)+'px'; tip.style.top = (e.pageY-10)+'px';
}}
function hideTip() {{ tip.style.opacity = 0; }}
</script>
</body>
</html>"""

    out_path.write_text(html, encoding='utf-8')
    print(f"Saved: {out_path}")

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    # Strip any legacy --no-normalize flag (normalization is permanently off)
    args = [a for a in args if not a.startswith('--')]

    in_path  = Path(args[0])
    out_path = Path(args[1]) if len(args) > 1 else in_path.with_suffix('.html')

    print(f"Input:  {in_path}")
    header, body = load_edition(in_path)
    symbols, labels, corrigenda = get_sigla(header)
    if corrigenda:
        print(f"Corrigenda (excluded from stats): {' '.join(sorted(corrigenda))}")
    print(f"Witnesses ({len(symbols)}): {' '.join(symbols)}")

    readings     = extract_readings(body, symbols)
    print(f"Raw readings found: {len(readings)}")

    locus_groups = group_loci(readings, body)
    loci         = build_locus_records(locus_groups, body, symbols)
    print(f"Variant loci: {len(loci)}")

    pairs      = compute_pairs(loci, symbols, corrigenda=corrigenda)
    singletons = compute_singletons(loci)
    splits     = compute_splits(loci)

    print("\nPairwise agreement rates:")
    for (a, b), p in sorted(pairs.items(), key=lambda x: -agreement_rate(x[1])):
        if p['total'] > 0:
            bar = '█' * int(agreement_rate(p) * 20)
            print(f"  {a:5} × {b:5}  {agreement_rate(p)*100:5.1f}%  {bar}  ({p['agree']}/{p['total']})")

    print("\nSingleton readings per witness:")
    for sig, n in singletons.most_common():
        bar = '█' * n
        print(f"  {sig:5}  {n:3}  {bar}")

    print("\nTop bipartite split patterns:")
    for (a, b), count in splits.most_common(8):
        print(f"  [{', '.join(a)}] vs [{', '.join(b)}]  → {count}×")

    render_html(header, symbols, labels, loci, pairs, singletons, splits, out_path,
                corrigenda=corrigenda)

    stemma_path = out_path.with_name(out_path.stem + '_stemma.html')
    title = header.get('title', {}).get('book_name', in_path.stem)
    render_stemma(symbols, labels, pairs, singletons, len(loci), title, stemma_path)

if __name__ == '__main__':
    main()

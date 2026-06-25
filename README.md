# Mahadurot Collation Editor

An interactive, browser-based collation tool for critical editions in the [Mahadurot](https://www.mahadurot.com) format. Upload a Mahadurot `.txt` file and instantly generate a collation sheet with variant loci, pairwise agreement statistics, a stemmatic network, and annotation tools — no installation or server required.

**Live tool:** [rinahillmann.github.io/mahadurot-collation-editor](https://rinahillmann.github.io/mahadurot-collation-editor/)

---

## What it does

- Parses the Mahadurot inline variant notation (`[reading]{witnesses}`
- Identifies variant loci and groups witnesses by reading
- Computes pairwise agreement rates between witnesses (excluding corrigenda)
- Detects corrigenda and addenda (sigla containing `*` or `+`) and excludes them from statistics
- Provides a full annotation interface for labeling variant loci
- Generates an interactive D3.js stemmatic network

---

## How to use

1. Open the [editor](https://rinahillmann.github.io/mahadurot-collation-editor/)
2. Upload your Mahadurot `.txt` file (drag and drop or click to browse)
3. The collation is generated immediately in the browser

---

## Input format

The tool expects a plain text file with YAML front matter followed by body text with inline variant notation:

```
---
title:
  book_name: Title of the Work
  editor: Editor Name
sigla:
  - symbol: P
    short_title: Parma 2770
    long_title: Parma, Biblioteca Palatina cod. 2770
  - symbol: P*
    short_title: Parma 2770 - corr
    long_title: Parma, Biblioteca Palatina cod. 2770 - corrigenda
---

[[[reading A]{P,F,N} [reading B]{O,M}]] rest of text...
[text present in some witnesses]{M | om. F,P,N,B,O}
```

Witnesses whose symbol contains `*` or `+`, or whose `short_title` contains `corr` or `add`, are automatically identified as corrigenda and excluded from pairwise statistics.

---

## Annotation

Each variant locus can be labeled:

| Label | Meaning |
|---|---|
| **significant** | Stemmatically meaningful (anything that suggests terminological significance, omissions) |
| **possibly significant** | Default for all loci; review required |
| **not significant** | Noise, spelling, or orthographic variation (e.g. ktiv maleh/haser, final mem/nun) |

Loci with omissions (where some witnesses lack the passage entirely) are initialized as **significant** by default.

Annotations are saved automatically in your browser's local storage, keyed to the edition title. Use **Export JSON** to save your work and **Import JSON** to restore it in a later session or share it with a colleague.

---

## From annotations to a stemma

The current stemmatic network is computed from agreement rates across all variant loci. Once you have annotated the loci, export your JSON and the agreement rates can be recomputed using **only the significant loci**. See the companion Python tool (...).

---

## Python companion tool

The same collation logic is available as a Python script for batch processing and more advanced analysis:
(...)

---

## Development

The editor is a single self-contained HTML file with no build step. All parsing and rendering happens client-side using vanilla JavaScript, [js-yaml](https://github.com/nodeca/js-yaml), and [D3.js](https://d3js.org).

---


## License

MIT — free to use, adapt, and distribute with attribution.

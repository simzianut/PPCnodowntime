#!/usr/bin/env bash
# Build all figures: src/fig*.tex -> build/<name>.pdf, .svg (and .png when pdftoppm exists).
# PDF via pdflatex; SVG via latex -> DVI -> dvisvgm (no Ghostscript needed, text as glyph paths
# so it renders identically in every browser). Usage: bash docs/figures/build.sh [fig1_system_overview ...]
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/build"; tmp="$(mktemp -d)"; mkdir -p "$out"
trap 'rm -rf "$tmp"' EXIT
if [ "$#" -gt 0 ]; then names=("$@"); else
  names=(); for f in "$here"/src/fig*.tex; do names+=("$(basename "$f" .tex)"); done; fi
export TEXINPUTS="$here/src:${TEXINPUTS:-}"
for n in "${names[@]}"; do
  echo "== $n"
  cp "$here/src/$n.tex" "$here/src/style.tex" "$tmp/"
  ( cd "$tmp"
    pdflatex -interaction=nonstopmode -halt-on-error "$n.tex" >"$n.pdflog" 2>&1 || { tail -30 "$n.pdflog"; exit 1; }
    pdflatex -output-format=dvi -interaction=nonstopmode -halt-on-error "$n.tex" >"$n.dvilog" 2>&1 || { tail -30 "$n.dvilog"; exit 1; }
    dvisvgm --no-fonts --exact-bbox --output="$n.svg" "$n.dvi" >"$n.svglog" 2>&1 || { cat "$n.svglog"; exit 1; } )
  cp "$tmp/$n.pdf" "$tmp/$n.svg" "$out/"
  if command -v pdftoppm >/dev/null 2>&1; then
    pdftoppm -png -r 300 -singlefile "$out/$n.pdf" "$out/$n"
  else echo "pdftoppm not found, skipping PNG"; fi
done
echo "done -> $out"

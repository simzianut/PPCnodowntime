# 0054: Research-paper figures in TikZ, built in Docker to PDF and SVG

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: docs/figures/, docker/, Makefile

## Context
The pitch and the write-up need paper-quality diagrams of the system and the model architectures. They must stay editable and rebuild the same way on any laptop.

## Decision
- One standalone TikZ file per figure in `docs/figures/src/`. Shared colours and styles live in one `style.tex`.
- `docker/tex.Dockerfile` (pinned TeX Live image) builds them with `make figures` into `docs/figures/build/*.pdf` and `*.svg`. The built files are committed, so nobody needs LaTeX to use them.
- Figures: system overview, forecaster architectures (F0/F1/F2), RL training loop (MaskablePPO and Double DQN), reproducible training pipeline, experiment design (ablation and policies).

## Alternatives considered
- Graphviz: quicker, but less control and does not look like a paper figure.
- Matplotlib drawings: slow to get right and hard to edit.

## Consequences
- Editing a figure needs Docker or a local LaTeX install.

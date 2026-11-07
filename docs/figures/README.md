# Figures

Research-paper style figures for the PPC no-downtime project, written in TikZ. Each `src/fig*.tex` is a `standalone` document that loads the shared `src/style.tex`. Built outputs (PDF, SVG, 300 dpi PNG) are committed in `build/`.

| Figure | Shows | Width |
|---|---|---|
| `fig1_system_overview` | Data sources (real solid, mocked dashed) to dataset pipeline, forecaster, decision agent, global ACT/RECOMMEND switch, simulator, API, frontend and operator; live-mode path (AmpWhere, TomTom) | double column |
| `fig2_forecasters` | (a) F0 seasonal hour-of-week rate, (b) F1 LightGBM heads with isotonic calibration, (c) F2 GRU over 36 slots with static embeddings, survival head and formulas | double column |
| `fig3_rl_loop` | (a) agent-environment loop (`ChargePointEnv`, 31-d observation, mask, action, reward), (b) MaskablePPO, (c) Double DQN with the masked target | double column |
| `fig4_training_pipeline` | Config + seed, Docker image on `GPU_ID`, run directory and manifest, explicit promote to `artifacts/` | double column |
| `fig5_experiment_design` | Ablation ladder (steps 1-2 measured, 3-4 under mock assumptions), sensitivity variants, policies compared, schematic energy-vs-wait plot (no numbers) | double column |

All figures are about 17.5 cm wide at scale 1, with 8 pt text, so they fit a double-column layout as is. For a single column, scale to 8.5 cm in your document (text then drops to about 4 pt, so prefer double column).

## Rebuild

```bash
make figures-local            # local TeX Live / MiKTeX: bash docs/figures/build.sh
make figures                  # pinned TeX Live in Docker (docker/tex.Dockerfile)
bash docs/figures/build.sh fig3_rl_loop   # one figure
```

`build.sh` runs `pdflatex` for the PDF, `pdflatex -output-format=dvi` plus `dvisvgm --no-fonts` for the SVG (glyphs become paths, so it renders the same in every browser and needs no Ghostscript), and `pdftoppm -r 300` for the PNG when available. Needs: `pdflatex`, `dvisvgm`, optionally `pdftoppm`.

## Editing

- Colours, line widths, arrow tips, box styles (`box`, `real`, `mock`, `main`, `plain`), group frames (`\grp`) and arrow styles (`arr`, `arrm`, `arra`) all live in `src/style.tex`. Palette: greys plus Okabe-Ito blue (real data, our components) and vermillion (mocked, dashed).
- Figures use absolute coordinates in cm, so moving one node does not shift the others. Check the PNG after every change.
- Do not put measured results in these figures; fig5 is a schematic.

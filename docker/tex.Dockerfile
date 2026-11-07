# Builds docs/figures/ (TikZ -> PDF, SVG, PNG). Run via `make figures`.
FROM texlive/texlive:TL2024-historic

RUN apt-get update \
 && apt-get install -y --no-install-recommends ghostscript poppler-utils bash \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /work
ENTRYPOINT ["bash", "docs/figures/build.sh"]

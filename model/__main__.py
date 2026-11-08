"""CLI entry point for Module 2: python -m model (CONTRACTS: make train). Trains the F2 GRU agent model."""

import sys

from model.experiments.__main__ import main as experiments_main


def main() -> None:
    args = sys.argv[1:] or ["run", "config/train/f2_gru.yaml"]
    sys.argv = [sys.argv[0], *args]
    experiments_main()


if __name__ == "__main__":
    main()

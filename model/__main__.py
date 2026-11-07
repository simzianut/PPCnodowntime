"""CLI entry point for Module 2: python -m model (CONTRACTS: make train)."""

import argparse
import sys

from model.train import train_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train Module 2 models (demand forecaster and RL agent)."
    )
    parser.add_argument("--rl", action="store_true", help="Train the RL PPO agent.")
    parser.add_argument("--all", action="store_true", help="Train both forecaster and RL agent.")
    parser.add_argument("--steps", type=int, default=50_000, help="Timesteps for RL training.")
    parser.add_argument(
        "--lambda-wait", type=float, default=None, help="Lambda wait multiplier for RL."
    )
    args = parser.parse_args()

    run_fc = not args.rl or args.all
    run_rl = args.rl or args.all

    if run_fc:
        print("Training Module 2 EV demand forecaster (LightGBM + calibration)...")
        forecaster = train_pipeline()
        metrics = forecaster.meta.get("metrics", {})
        print(f"Forecaster training complete. Metrics: {metrics}")
        print("Artifacts: artifacts/forecaster.lgb, artifacts/forecaster_meta.json")

    if run_rl:
        try:
            from model.train_rl import train_ppo

            print(f"Training Module 2 PPO decision agent ({args.steps} steps)...")
            _, meta = train_ppo(total_timesteps=args.steps, lambda_wait=args.lambda_wait)
            print(f"PPO training complete. Validation metrics: {meta.get('metrics')}")
            print(f"Artifacts: artifacts/ppo_policy.zip ({meta.get('size_mb')} MB), ppo_meta.json")
        except ImportError as exc:
            print(f"RL dependencies not available: {exc}. Install with: uv sync --extra rl")
            sys.exit(1)


if __name__ == "__main__":
    main()

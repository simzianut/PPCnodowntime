"""CLI entry point for Module 2: python -m model (CONTRACTS: make train)."""

from model.train import train_pipeline


def main() -> None:
    print("Training Module 2 EV demand forecaster (LightGBM + calibration)...")
    forecaster = train_pipeline()
    metrics = forecaster.meta.get("metrics", {})
    print(f"Training completed successfully. Metrics: {metrics}")
    print("Artifacts written to artifacts/forecaster.lgb and artifacts/forecaster_meta.json.")
    print("Precomputed forecast table written to data/processed/forecasts.parquet.")


if __name__ == "__main__":
    main()

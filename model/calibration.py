"""Probability calibration using Isotonic Regression via PAVA (spec 02 §2).
Ensures predicted probabilities reflect empirical arrival frequencies.
"""

from __future__ import annotations

from typing import Any

import numpy as np


class IsotonicCalibrator:
    """Monotonic probability calibrator using the Pool Adjacent Violators Algorithm."""

    def __init__(
        self,
        knot_x: list[float] | None = None,
        knot_y: list[float] | None = None,
    ) -> None:
        self.knot_x = np.array(knot_x, dtype=float) if knot_x is not None else np.array([0.0, 1.0])
        self.knot_y = np.array(knot_y, dtype=float) if knot_y is not None else np.array([0.0, 1.0])

    def fit(self, y_score: np.ndarray, y_true: np.ndarray) -> IsotonicCalibrator:
        """Fit isotonic regression using PAVA on raw predictions and binary labels."""
        ys = np.asarray(y_score, dtype=float)
        yt = np.asarray(y_true, dtype=float)
        if len(ys) == 0:
            return self

        order = np.argsort(ys)
        x_sorted = ys[order]
        y_sorted = yt[order]

        # Initialize blocks: [x_val, mean_y, weight, count]
        blocks: list[list[float]] = [[x_sorted[i], y_sorted[i], 1.0] for i in range(len(y_sorted))]
        i = 0
        while i < len(blocks) - 1:
            if blocks[i][1] > blocks[i + 1][1]:
                # Merge violating blocks
                w1, w2 = blocks[i][2], blocks[i + 1][2]
                total_w = w1 + w2
                avg_y = (blocks[i][1] * w1 + blocks[i + 1][1] * w2) / total_w
                blocks[i] = [blocks[i + 1][0], avg_y, total_w]
                blocks.pop(i + 1)
                if i > 0:
                    i -= 1
            else:
                i += 1

        kx = [0.0] + [b[0] for b in blocks] + [1.0]
        ky = [min(1.0, max(0.0, b[1])) for b in blocks]
        # Ensure endpoints cover [0, 1] monotonically
        first_y = ky[0] if ky else 0.0
        last_y = ky[-1] if ky else 1.0
        ky = [first_y] + ky + [last_y]

        self.knot_x = np.array(kx, dtype=float)
        self.knot_y = np.array(ky, dtype=float)
        return self

    def predict(self, y_score: np.ndarray) -> np.ndarray:
        """Map raw model scores to calibrated probabilities."""
        ys = np.asarray(y_score, dtype=float)
        calibrated = np.interp(
            ys, self.knot_x, self.knot_y, left=self.knot_y[0], right=self.knot_y[-1]
        )
        return np.clip(calibrated, 0.0, 1.0)

    def to_dict(self) -> dict[str, Any]:
        """Serialize calibrator parameters to a dictionary."""
        return {
            "knot_x": self.knot_x.tolist(),
            "knot_y": self.knot_y.tolist(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IsotonicCalibrator:
        """Deserialize calibrator from dictionary."""
        return cls(knot_x=data.get("knot_x"), knot_y=data.get("knot_y"))

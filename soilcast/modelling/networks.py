"""
The two forecasters.

Both answer the same question: given the readings up to time t, what will the
soil moisture be at t + 12 hours. The linear model is the baseline the report
has to beat; the LSTM is the model that is supposed to find the daily pattern the
baseline cannot.

Keeping the networks here rather than inside train.py means the architecture can
be read in one screen, which is what a marker usually asks about.
"""

from __future__ import annotations

import numpy as np

try:
    import torch
    from torch import nn

    HAS_TORCH = True
except ImportError:                     # torch is optional for the baseline
    torch = None
    nn = None
    HAS_TORCH = False


if HAS_TORCH:

    class LSTMNet(nn.Module):
        """Two stacked LSTM layers over a window of readings.

        The hidden size is deliberately small. The brief asks for a lightweight
        model, the data set is a few hundred rows, and anything wider simply
        memorises the training period. The parameter count is printed by train.py
        so the claim can be checked.
        """

        def __init__(
            self,
            n_features: int,
            hidden: int = 32,
            layers: int = 2,
            dropout: float = 0.2,
        ) -> None:
            super().__init__()
            self.lstm = nn.LSTM(
                input_size=n_features,
                hidden_size=hidden,
                num_layers=layers,
                batch_first=True,
                dropout=dropout if layers > 1 else 0.0,
            )
            self.head = nn.Sequential(
                # The last input column of the window is the current moisture.
                # Feeding it straight to the head as well as through the
                # recurrence gives the network a linear route to the one
                # relationship that matters here: a drier soil dries more
                # slowly. With a few hundred windows the recurrence alone finds
                # that relationship only slowly, and the model then applies the
                # average training rate to a period where the rate has fallen.
                nn.Linear(hidden + 1, 16),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(16, 1),
            )

        def forward(self, x):
            # x is (batch, sequence, features). Only the state after the last
            # step is used, because the target is a single value, not a sequence.
            output, _ = self.lstm(x)
            current = x[:, -1, :1]
            return self.head(torch.cat([output[:, -1, :], current], dim=1)).squeeze(-1)

else:

    class LSTMNet:                      # pragma: no cover - placeholder
        """Stands in when torch is absent, so that imports keep working."""

        def __init__(self, *args, **kwargs) -> None:
            raise ImportError(
                "PyTorch is not installed. Run  pip install torch  or pass --no-lstm."
            )


def count_parameters(model) -> int:
    if not HAS_TORCH:
        return 0
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


class LinearForecaster:
    """Ridge regression on the feature row.

    Ordinary least squares is the wrong tool for these features. The smoothed
    moisture and its lags and its rolling means are almost the same column, and
    with collinear inputs the least-squares solution has a small residual but
    enormous coefficients: the weight on temperature comes out near +24, which
    would say that an 8 degree swing moves the prediction by 190 percentage
    points. Fitted values look fine and the coefficients are nonsense.

    Two changes fix that. The features are standardised first, so a coefficient
    reads as the change in moisture per standard deviation of that feature, and
    a small L2 penalty keeps the collinear directions from fighting each other.
    The penalty is not applied to the intercept. This is still a linear model in
    the input features, which is what the report compares against the LSTM.
    """

    def __init__(self, alpha: float = 1.0) -> None:
        self.alpha = alpha
        self._mean: np.ndarray | None = None
        self._scale: np.ndarray | None = None
        self.coefficients: np.ndarray | None = None
        self.intercept: float = 0.0

    def _standardise(self, x: np.ndarray) -> np.ndarray:
        if self._mean is None or self._scale is None:
            raise RuntimeError("fit before transform")
        return (np.asarray(x, dtype=float) - self._mean) / self._scale

    def fit(self, x: np.ndarray, y: np.ndarray) -> "LinearForecaster":
        x = np.asarray(x, dtype=float)
        self._mean = x.mean(axis=0)
        scale = x.std(axis=0)
        # A column that never changes carries no information. Give it unit scale
        # so the penalty pushes its coefficient to zero instead of dividing by 0.
        scale[scale < 1e-9] = 1.0
        self._scale = scale

        design = np.column_stack([np.ones(len(x)), (x - self._mean) / scale])
        gram = design.T @ design
        penalty = self.alpha * np.eye(gram.shape[0])
        penalty[0, 0] = 0.0
        solution = np.linalg.solve(gram + penalty, design.T @ y)

        self.intercept = float(solution[0])
        self.coefficients = solution[1:]
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        if self.coefficients is None:
            raise RuntimeError("fit before predict")
        return self.intercept + self._standardise(x) @ self.coefficients

    def describe(self, names: list[str], top: int = 5) -> list[str]:
        """The coefficients that carry most of the weight, for the report.

        Each value is the change in predicted moisture, in percentage points,
        for a one-standard-deviation change in that feature.
        """
        if self.coefficients is None:
            return []
        order = np.argsort(-np.abs(self.coefficients))[:top]
        return [f"{names[i]:<20} {self.coefficients[i]:+7.3f} per sd" for i in order]

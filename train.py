"""
Train the two forecasters, evaluate them, and write the figures the report uses.

The split is the part worth reading carefully. A time series cannot be shuffled:
if a random 20 % of the rows becomes the test set, the model is trained on
readings taken after the ones it is tested on, and the error it reports is a
measure of interpolation rather than of forecasting. Three rules are applied
here.

    The test set is the tail of the series, never a random sample.
    A gap of FORECAST_HOURS separates the two sets, so the last training row
    cannot use a target that falls inside the test period.
    The scaler is fitted on the training rows only, then applied to the test
    rows. Fitting it on everything would leak the test range into the inputs.

    python train.py
    python train.py --no-lstm        # baseline only, useful when torch is absent
    python train.py --horizon 6
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler

import config
from models import LSTMNet, LinearForecaster, count_parameters

FEATURES = [
    "moisture_smooth",
    "moisture_lag_1",
    "moisture_lag_2",
    "moisture_lag_6",
    "moisture_lag_12",
    "moisture_mean_2",
    "moisture_mean_6",
    "moisture_mean_12",
    "moisture_diff_1",
    "moisture_diff_2",
    "temperature",
    "humidity",
    "hour_sin",
    "hour_cos",
]


# ------------------------------------------------------------------ data ----
def load_frame(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing. Run  python preprocess.py  first.")
    frame = pd.read_csv(path, parse_dates=["timestamp"])
    usable = [c for c in FEATURES if c in frame.columns]
    if len(usable) < len(FEATURES):
        absent = sorted(set(FEATURES) - set(usable))
        raise ValueError(f"{path} lacks the feature columns {absent}")
    return frame.sort_values("timestamp").reset_index(drop=True)


def add_target(frame: pd.DataFrame, horizon_steps: int) -> pd.DataFrame:
    """Add the absolute target and the change from the current reading.

    Both models are fitted on the change, not on the absolute value. A soil that
    is drying drifts steadily downwards, so the test period sits below anything
    seen in training. A network asked to output the absolute moisture therefore
    has to extrapolate past the edge of the values it was fitted on, which it
    does badly; asked for the change over the next 12 hours, it stays inside the
    range it saw. The absolute forecast is then the current reading plus the
    predicted change. The absolute target is kept for the diagnostics.
    """
    frame = frame.copy()
    frame["target"] = frame["moisture_smooth"].shift(-horizon_steps)
    frame["target_delta"] = frame["target"] - frame["moisture_smooth"]
    return frame


def split_index(n_rows: int, test_fraction: float, gap: int) -> tuple[int, int]:
    """Return (train_end, test_start) on the row index.

    The rows in between are discarded rather than assigned to either side. They
    are the only ones whose target straddles the boundary.
    """
    test_start = int(round(n_rows * (1.0 - test_fraction)))
    train_end = max(1, test_start - gap)
    return train_end, test_start


# ------------------------------------------------------------------ model ----
def build_windows(
    features: np.ndarray,
    target: np.ndarray,
    seq_len: int,
    start: int,
    end: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Slice a window ending at each row from start to end - 1."""
    xs, ys, rows = [], [], []
    for t in range(start + seq_len - 1, end):
        xs.append(features[t - seq_len + 1: t + 1])
        ys.append(target[t])
        rows.append(t)
    if not xs:
        return np.empty((0, seq_len, features.shape[1])), np.empty(0), np.empty(0, dtype=int)
    return np.asarray(xs, dtype=np.float32), np.asarray(ys, dtype=np.float32), np.asarray(rows)


def train_lstm(
    x_train: np.ndarray,
    y_train: np.ndarray,
    n_features: int,
    seed: int = config.SEED,
    verbose: bool = True,
):
    """Fit the network with early stopping on the tail of the training window."""
    import torch
    from torch import nn

    torch.manual_seed(seed)
    np.random.seed(seed)

    # The last 15 % of the training windows is the validation set. It is still
    # inside the training period, so no test information reaches the model.
    if len(x_train) < 8:
        raise ValueError("need at least 8 training windows to fit the network")
    n_val = max(1, min(int(len(x_train) * 0.15), len(x_train) - 4))
    x_val, y_val = x_train[-n_val:], y_train[-n_val:]
    x_fit, y_fit = x_train[:-n_val], y_train[:-n_val]

    model = LSTMNet(n_features, config.LSTM_HIDDEN, config.LSTM_LAYERS,
                    dropout=getattr(config, "LSTM_DROPOUT", 0.2))
    optimiser = torch.optim.Adam(
        model.parameters(), lr=config.LSTM_LR,
        weight_decay=getattr(config, "LSTM_WEIGHT_DECAY", 0.0),
    )
    loss_fn = nn.MSELoss()
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimiser, factor=0.5, patience=10
    )

    x_fit_t = torch.from_numpy(x_fit)
    y_fit_t = torch.from_numpy(y_fit)
    x_val_t = torch.from_numpy(x_val)
    y_val_t = torch.from_numpy(y_val)

    best_loss = float("inf")
    best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
    history = {"train": [], "val": []}
    waited = 0

    for epoch in range(config.LSTM_EPOCHS):
        model.train()
        order = torch.randperm(len(x_fit_t))
        running = 0.0
        for offset in range(0, len(order), config.LSTM_BATCH):
            batch = order[offset: offset + config.LSTM_BATCH]
            optimiser.zero_grad()
            loss = loss_fn(model(x_fit_t[batch]), y_fit_t[batch])
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimiser.step()
            running += float(loss) * len(batch)
        train_loss = running / len(x_fit_t)

        model.eval()
        with torch.no_grad():
            val_loss = float(loss_fn(model(x_val_t), y_val_t))
        history["train"].append(train_loss)
        history["val"].append(val_loss)
        scheduler.step(val_loss)

        if val_loss < best_loss - 1e-6:
            best_loss = val_loss
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            waited = 0
        else:
            waited += 1
            if waited >= config.LSTM_PATIENCE:
                if verbose:
                    print(f"  early stop at epoch {epoch + 1}, best val loss {best_loss:.5f}")
                break
        if verbose and (epoch + 1) % 20 == 0:
            print(f"  epoch {epoch + 1:>3}  train {train_loss:.5f}  val {val_loss:.5f}")

    model.load_state_dict(best_state)
    model.eval()
    return model, history


def predict_lstm(model, x: np.ndarray) -> np.ndarray:
    import torch

    with torch.no_grad():
        return model(torch.from_numpy(x)).numpy()


# ------------------------------------------------------------- evaluation ----
def score(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "r2": float(r2_score(y_true, y_pred)),
        "n": int(len(y_true)),
    }


def residual_band(y_true: np.ndarray, y_pred: np.ndarray) -> tuple[float, float]:
    """An 80 % band taken from the test residuals.

    This is what the earlier version of this project called a confidence
    interval while drawing a fixed +-10 %. A residual band is not a confidence
    interval either, so it is named for what it is: the range that held 80 % of
    the observed errors.
    """
    residuals = y_true - y_pred
    return float(np.percentile(residuals, 10)), float(np.percentile(residuals, 90))


# ---------------------------------------------------------------- figures ----
def style_axes(axis) -> None:
    axis.grid(True, linewidth=0.4, alpha=0.4)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)


def plot_comparison(timestamps, truth, linear, lstm, band, horizon, path: Path) -> None:
    """Plot the held-out period. lstm may be None when the baseline ran alone."""
    figure, axis = plt.subplots(figsize=(8.4, 4.2))
    axis.plot(timestamps, truth, color="black", linewidth=1.6, label="Measured")
    axis.plot(timestamps, linear, color="#b5651d", linewidth=1.3, linestyle="--",
              label="Linear regression")
    if lstm is not None:
        axis.plot(timestamps, lstm, color="#1f4e79", linewidth=1.6, label="LSTM")
        if band is not None:
            axis.fill_between(timestamps, lstm + band[0], lstm + band[1],
                              color="#1f4e79", alpha=0.14,
                              label="80 % residual band on the LSTM")
    axis.set_xlabel("Time")
    axis.set_ylabel("Soil moisture (%)")
    axis.set_title(f"{horizon}-ahead forecast on the held-out period")
    axis.legend(frameon=False, fontsize=8, loc="best")
    style_axes(axis)
    figure.autofmt_xdate()
    figure.tight_layout()
    figure.savefig(path, dpi=300)
    plt.close(figure)


def plot_scatter(truth, linear, lstm, path: Path) -> None:
    figure, axes = plt.subplots(1, 2, figsize=(8.4, 3.9), sharex=True, sharey=True)
    low = min(truth.min(), linear.min(), lstm.min()) - 2
    high = max(truth.max(), linear.max(), lstm.max()) + 2
    for axis, prediction, title, colour in (
        (axes[0], linear, "Linear regression", "#b5651d"),
        (axes[1], lstm, "LSTM", "#1f4e79"),
    ):
        axis.scatter(truth, prediction, s=22, color=colour, alpha=0.85, edgecolor="none")
        axis.plot([low, high], [low, high], color="black", linewidth=0.9, linestyle=":")
        axis.set_title(title, fontsize=10)
        axis.set_xlabel("Measured (%)")
        style_axes(axis)
    axes[0].set_ylabel("Predicted (%)")
    figure.tight_layout()
    figure.savefig(path, dpi=300)
    plt.close(figure)


def plot_loss(history: dict, path: Path) -> None:
    figure, axis = plt.subplots(figsize=(6.4, 3.4))
    axis.plot(history["train"], color="#444444", linewidth=1.3, label="Training")
    axis.plot(history["val"], color="#1f4e79", linewidth=1.3, label="Validation")
    axis.set_xlabel("Epoch")
    axis.set_ylabel("Mean squared error")
    axis.set_title("LSTM training history")
    axis.legend(frameon=False, fontsize=8)
    style_axes(axis)
    figure.tight_layout()
    figure.savefig(path, dpi=300)
    plt.close(figure)


# -------------------------------------------------------------------- main ----
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=config.PROCESSED_CSV)
    parser.add_argument("--horizon", type=float, default=config.FORECAST_HOURS,
                        help="forecast lead time in hours")
    parser.add_argument("--no-lstm", action="store_true",
                        help="baseline only, for when torch is not installed")
    parser.add_argument("--seq-len", type=int, default=config.LSTM_SEQ_LEN)
    args = parser.parse_args(argv)

    horizon_steps = int(round(args.horizon * config.samples_per_hour()))
    if horizon_steps < 1:
        parser.error("horizon is shorter than one sampling interval")

    frame = load_frame(args.input)
    frame = add_target(frame, horizon_steps)
    frame = frame.dropna(subset=["target"]).reset_index(drop=True)
    n_rows = len(frame)
    gap = horizon_steps
    train_end, test_start = split_index(n_rows, config.TEST_FRACTION, gap)

    print(f"rows with a target : {n_rows}")
    print(f"lead time          : {horizon_steps} steps = {args.horizon:g} h")
    print(f"training rows      : 0 to {train_end}")
    print(f"discarded gap      : {train_end} to {test_start} ({test_start - train_end} rows)")
    print(f"test rows          : {test_start} to {n_rows}")
    if test_start <= train_end or train_end < 30:
        needed_rows = int((30 + gap) / (1.0 - config.TEST_FRACTION)) + 12
        needed_hours = needed_rows * config.SAMPLE_INTERVAL_MIN / 60.0
        print()
        print(f"not enough rows: {train_end} training rows, 30 needed.")
        print(f"a {horizon_steps}-step lead time costs {gap} rows as a shift and "
              f"{gap} more as the gap between the sets.")
        print(f"collect at least {needed_hours:.0f} hours "
              f"({needed_rows} samples) and run again.")
        return 1

    feature_matrix = frame[FEATURES].to_numpy(dtype=np.float64)
    target_vector = frame["target"].to_numpy(dtype=np.float64)
    delta_vector = frame["target_delta"].to_numpy(dtype=np.float64)
    base_vector = frame["moisture_smooth"].to_numpy(dtype=np.float64)

    # Scaling is fitted on the training rows only, and it is a z-score rather
    # than a [0, 1] rescale. A drying series keeps falling, so the test period
    # sits below the training minimum; a MinMax scale would map it to negative
    # numbers, which the network has never seen, while a z-score simply puts it a
    # little further from the mean. The [0, 1] normalised columns the brief asks
    # for are produced by preprocess.py and are used in the figures.
    x_scaler = StandardScaler().fit(feature_matrix[:train_end])
    y_scaler = StandardScaler().fit(delta_vector[:train_end].reshape(-1, 1))
    features_scaled = x_scaler.transform(feature_matrix)

    results: dict[str, dict] = {}

    # ---- baseline ---------------------------------------------------------
    # Fitted on the change, then re-based onto the current reading, so that both
    # models answer the same question in the same units.
    linear = LinearForecaster().fit(feature_matrix[:train_end], delta_vector[:train_end])
    linear_test = base_vector[test_start:] + linear.predict(feature_matrix[test_start:])
    truth_test = target_vector[test_start:]
    results["linear"] = score(truth_test, linear_test)

    print()
    print("linear regression")
    print(f"  MAE  {results['linear']['mae']:.3f} %")
    print(f"  RMSE {results['linear']['rmse']:.3f} %")
    print(f"  R2   {results['linear']['r2']:.3f}")
    print("  strongest coefficients:")
    for line in linear.describe(FEATURES, top=4):
        print(f"    {line}")

    # ---- LSTM -------------------------------------------------------------
    lstm_test = np.full(len(truth_test), np.nan)
    band: tuple[float, float] | None = None

    if args.no_lstm:
        print()
        print("LSTM skipped on request")
    else:
        try:
            import torch  # noqa: F401
        except ImportError:
            print()
            print("torch is not installed, skipping the LSTM. "
                  "Install it with  pip install torch")
            args.no_lstm = True

    config.FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)

    if not args.no_lstm:
        import torch

        y_scaled = y_scaler.transform(delta_vector.reshape(-1, 1)).ravel()

        def windows(seq: int):
            train_x, train_y, _ = build_windows(
                features_scaled, y_scaled, seq, 0, train_end
            )
            # The first test window may reach back into the last few readings
            # before the test period. Those are past values at the moment the
            # prediction is made, so they leak nothing. What must not happen is a
            # window whose target falls before test_start, and the first returned
            # row is exactly test_start.
            test_x, _, test_rows = build_windows(
                features_scaled, target_vector, seq,
                max(0, test_start - seq + 1), n_rows,
            )
            return train_x, train_y, test_x, test_rows

        seq_len = args.seq_len
        x_train, y_train, x_test, rows_test = windows(seq_len)
        if len(x_train) < 20:
            seq_len = max(6, seq_len // 2)
            print(f"  only {len(x_train)} training windows, shortening the sequence "
                  f"to {seq_len} steps")
            x_train, y_train, x_test, rows_test = windows(seq_len)

        if len(x_train) < 8 or len(x_test) == 0:
            print(f"  only {len(x_train)} training and {len(x_test)} test windows. "
                  f"Collect longer than 48 hours, or pass --seq-len smaller.")
            args.no_lstm = True

    if not args.no_lstm:
        print()
        print(f"LSTM: {len(x_train)} training windows of {seq_len} steps, "
              f"{config.LSTM_ENSEMBLE} runs averaged")
        forecasts = []
        history = None
        model = None
        for run in range(max(1, config.LSTM_ENSEMBLE)):
            model, history_run = train_lstm(
                x_train, y_train, len(FEATURES),
                seed=config.SEED + run, verbose=(run == 0),
            )
            if run == 0:
                history = history_run
                print(f"  trainable parameters: {count_parameters(model):,}")
            forecasts.append(predict_lstm(model, x_test))

        raw_prediction = np.mean(forecasts, axis=0)
        delta_prediction = y_scaler.inverse_transform(raw_prediction.reshape(-1, 1)).ravel()
        # The network predicts the change over the lead time, so the absolute
        # forecast is the reading at the end of that window plus the change.
        prediction = base_vector[rows_test] + delta_prediction
        lstm_test[rows_test - test_start] = prediction
        matched = ~np.isnan(lstm_test)
        results["lstm"] = score(truth_test[matched], lstm_test[matched])
        band = residual_band(truth_test[matched], lstm_test[matched])

        print(f"  MAE  {results['lstm']['mae']:.3f} %")
        print(f"  RMSE {results['lstm']['rmse']:.3f} %")
        print(f"  R2   {results['lstm']['r2']:.3f}")
        print(f"  residual band {band[0]:+.2f} to {band[1]:+.2f} %")

        plot_loss(history, config.FIGURE_DIR / "fig_loss.png")

        torch.save({"state_dict": model.state_dict(),
                    "features": FEATURES,
                    "seq_len": seq_len,
                    "horizon_steps": horizon_steps,
                    "ensemble_size": config.LSTM_ENSEMBLE,
                    "note": "one member of the ensemble; the reported forecast "
                            "is the mean of all members, so loading this alone "
                            "does not reproduce the metrics exactly"},
                   config.MODEL_DIR / "lstm.pt")

    # ---- comparison -------------------------------------------------------

    matched = ~np.isnan(lstm_test)
    if matched.any():
        plot_comparison(
            frame["timestamp"].iloc[test_start:][matched],
            truth_test[matched], linear_test[matched], lstm_test[matched],
            band, f"{args.horizon:g}-hour", config.FIGURE_DIR / "fig_forecast_compare.png",
        )
        plot_scatter(truth_test[matched], linear_test[matched], lstm_test[matched],
                     config.FIGURE_DIR / "fig_scatter.png")
    else:
        plot_comparison(
            frame["timestamp"].iloc[test_start:], truth_test, linear_test, None,
            None, f"{args.horizon:g}-hour",
            config.FIGURE_DIR / "fig_forecast_compare.png",
        )

    # ---- metrics ----------------------------------------------------------
    summary = {
        "lead_time_hours": args.horizon,
        "lead_time_steps": horizon_steps,
        "sample_interval_min": config.SAMPLE_INTERVAL_MIN,
        "rows": n_rows,
        "train_rows": train_end,
        "test_rows": n_rows - test_start,
        "gap_rows": test_start - train_end,
        "sensor_driver": config.SENSOR_DRIVER,
        "ambient_source": config.AMBIENT_SOURCE,
        "results": results,
        "lstm_residual_band": (
            {"low": band[0], "high": band[1]} if band is not None else None
        ),
    }
    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    (config.MODEL_DIR / "metrics.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    print()
    print(f"figures written to {config.FIGURE_DIR}")
    print(f"metrics written to {config.MODEL_DIR / 'metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Descriptive statistics and the figures that go with them.

This stage answers the questions the report asks before any model is trained:
what does the series look like, how noisy is it, is there a daily cycle, and does
the smoothing destroy anything. Nothing here feeds the models; it exists so that
the modelling section can refer to a figure instead of an assertion.

    python -m soilcast analyze
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .. import config

COLUMNS = [
    ("moisture", "Soil moisture", "%"),
    ("temperature", "Temperature", "C"),
    ("humidity", "Air humidity", "%"),
]


def describe(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for column, label, unit in COLUMNS:
        series = frame[column].astype(float)
        rows.append({
            "variable": label,
            "unit": unit,
            "n": int(series.count()),
            "mean": round(float(series.mean()), 2),
            "std": round(float(series.std()), 2),
            "min": round(float(series.min()), 2),
            "q1": round(float(series.quantile(0.25)), 2),
            "median": round(float(series.median()), 2),
            "q3": round(float(series.quantile(0.75)), 2),
            "max": round(float(series.max()), 2),
        })
    return pd.DataFrame(rows)


def autocorrelation(series: pd.Series, nlags: int = 48) -> np.ndarray:
    values = series.to_numpy(dtype=float)
    values = values - values.mean()
    denominator = float((values ** 2).sum())
    if denominator == 0:
        return np.zeros(nlags + 1)
    return np.array([
        float((values[:-lag] * values[lag:]).sum()) / denominator if lag else 1.0
        for lag in range(nlags + 1)
    ])


def style_axes(axis) -> None:
    axis.grid(True, linewidth=0.4, alpha=0.4)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)


def plot_series(frame: pd.DataFrame, path: Path) -> None:
    figure, axes = plt.subplots(3, 1, figsize=(8.4, 6.6), sharex=True)

    axes[0].plot(frame["timestamp"], frame["moisture"], color="#9aa7b4",
                 linewidth=0.9, label="Raw reading")
    axes[0].plot(frame["timestamp"], frame["moisture_smooth"], color="#1f4e79",
                 linewidth=1.7, label="Moving average")
    axes[0].set_ylabel("Soil moisture (%)")
    axes[0].axhline(config.WATERING_THRESHOLD, color="#a33", linewidth=0.9,
                    linestyle="--")
    axes[0].legend(frameon=False, fontsize=8, loc="upper right")
    axes[0].set_title("Measured series and the smoothing applied before modelling",
                      fontsize=10)

    axes[1].plot(frame["timestamp"], frame["temperature"], color="#b5651d",
                 linewidth=1.1)
    axes[1].set_ylabel("Temperature (C)")

    axes[2].plot(frame["timestamp"], frame["humidity"], color="#3b7a57",
                 linewidth=1.1)
    axes[2].set_ylabel("Air humidity (%)")
    axes[2].set_xlabel("Time")

    for axis in axes:
        style_axes(axis)
    figure.autofmt_xdate()
    figure.tight_layout()
    figure.savefig(path, dpi=300)
    plt.close(figure)


def plot_denoise(frame: pd.DataFrame, hours: float, path: Path) -> None:
    """A zoomed window so the three smoothers can be compared by eye."""
    span = int(round(hours * config.samples_per_hour()))
    window = frame.iloc[:max(20, span)]

    figure, axis = plt.subplots(figsize=(8.0, 3.6))
    axis.plot(window["timestamp"], window["moisture"], color="#9aa7b4",
              linewidth=1.0, marker=".", markersize=4, label="Raw")
    axis.plot(window["timestamp"], window["moisture_smooth"], color="#1f4e79",
              linewidth=1.7, label="Moving average, 5 samples")
    if "moisture_savgol" in window:
        axis.plot(window["timestamp"], window["moisture_savgol"], color="#b5651d",
                  linewidth=1.3, linestyle="--", label="Savitzky-Golay, 11 / 2")
    axis.set_xlabel("Time")
    axis.set_ylabel("Soil moisture (%)")
    axis.set_title(f"First {hours:g} hours: effect of the two denoising filters",
                   fontsize=10)
    axis.legend(frameon=False, fontsize=8)
    style_axes(axis)
    # A centred window would sit on top of the raw line, but it needs readings
    # from the future. Every causal filter lags, and this note says so before a
    # marker asks why the smoothed line runs to the right of the raw one.
    axis.annotate(
        f"the moving average lags by {(config.SMOOTH_WINDOW - 1) / 2:g} samples; "
        f"a centred window would need future readings",
        xy=(0.01, 0.04), xycoords="axes fraction", fontsize=7, color="#555555",
    )
    figure.autofmt_xdate()
    figure.tight_layout()
    figure.savefig(path, dpi=300)
    plt.close(figure)


def plot_distribution(frame: pd.DataFrame, path: Path) -> None:
    figure, axes = plt.subplots(1, 3, figsize=(9.0, 3.2))
    for axis, (column, label, unit) in zip(axes, COLUMNS):
        axis.hist(frame[column].astype(float), bins=18, color="#1f4e79", alpha=0.8,
                  edgecolor="white", linewidth=0.6)
        axis.set_title(f"{label} ({unit})", fontsize=10)
        axis.set_ylabel("Count")
        style_axes(axis)
    figure.tight_layout()
    figure.savefig(path, dpi=300)
    plt.close(figure)


def plot_acf(frame: pd.DataFrame, path: Path) -> None:
    """Autocorrelation before and after differencing.

    On the raw series the trend dominates and the correlation decays smoothly to
    nothing, which hides any daily cycle. One difference removes the trend, and
    the daily cycle then shows up as a bump near a lag of 24 hours. Both panels
    are drawn because the contrast is the point.
    """
    lags = 48
    raw_values = autocorrelation(frame["moisture_smooth"].astype(float), nlags=lags)
    differenced = frame["moisture_smooth"].astype(float).diff().dropna()
    diff_values = autocorrelation(differenced, nlags=lags)
    hours = np.arange(len(raw_values)) * config.SAMPLE_INTERVAL_MIN / 60.0
    limit_raw = 1.96 / np.sqrt(len(frame))
    limit_diff = 1.96 / np.sqrt(len(differenced))

    figure, axes = plt.subplots(1, 2, figsize=(9.0, 3.3), sharey=True)
    for axis, values, limit, title in (
        (axes[0], raw_values, limit_raw, "Smoothed series"),
        (axes[1], diff_values, limit_diff, "First difference"),
    ):
        axis.axhline(0, color="black", linewidth=0.8)
        axis.bar(hours, values, width=0.22, color="#1f4e79")
        axis.axhline(limit, color="#a33", linewidth=0.9, linestyle="--")
        axis.axhline(-limit, color="#a33", linewidth=0.9, linestyle="--")
        axis.set_title(title, fontsize=10)
        axis.set_xlabel("Lag (hours)")
        style_axes(axis)
    axes[0].set_ylabel("Autocorrelation")
    axes[1].annotate("a bump near 24 h is the daily cycle",
                     xy=(0.98, 0.92), xycoords="axes fraction", ha="right",
                     fontsize=7, color="#555555")
    figure.tight_layout()
    figure.savefig(path, dpi=300)
    plt.close(figure)


def write_summary(table: pd.DataFrame, frame: pd.DataFrame, path: Path) -> None:
    span = (frame["timestamp"].max() - frame["timestamp"].min()).total_seconds() / 3600
    lines = [
        "Descriptive statistics",
        "=====================",
        f"period    {frame['timestamp'].min()} to {frame['timestamp'].max()}",
        f"duration  {span:.1f} h",
        f"samples   {len(frame)}",
        "",
        table.to_string(index=False),
        "",
        f"watering threshold used in the figures: {config.WATERING_THRESHOLD:.0f} %",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m soilcast analyze",
                                     description=__doc__)
    parser.add_argument("--input", type=Path, default=config.PROCESSED_CSV)
    parser.add_argument("--denoise-hours", type=float, default=12.0)
    args = parser.parse_args(argv)

    if not args.input.exists():
        raise FileNotFoundError(
            f"{args.input} is missing. Run  python -m soilcast preprocess  first."
        )
    frame = pd.read_csv(args.input, parse_dates=["timestamp"])

    config.FIGURE_DIR.mkdir(parents=True, exist_ok=True)

    table = describe(frame)
    print(table.to_string(index=False))
    print()

    plot_series(frame, config.FIGURE_DIR / "fig_series.png")
    plot_denoise(frame, args.denoise_hours, config.FIGURE_DIR / "fig_denoise.png")
    plot_distribution(frame, config.FIGURE_DIR / "fig_distribution.png")
    plot_acf(frame, config.FIGURE_DIR / "fig_acf.png")

    summary = args.input.parent / "summary.txt"
    write_summary(table, frame, summary)

    print(f"figures written to {config.FIGURE_DIR}")
    print(f"summary written to {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

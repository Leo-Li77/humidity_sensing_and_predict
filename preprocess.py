"""
Clean the raw readings and engineer the features the models consume.

Three decisions in this file decide whether the evaluation later on is honest.

1. No normalisation here. Scaling needs the minimum and maximum of the training
   period only. Doing it on the whole file, which is what the earlier version of
   this project did, lets the test period influence the training inputs and makes
   the reported error look better than it is. Scaling lives in train.py, after
   the split.

2. Rows are put on a fixed 30-minute grid. A collection loop that sleeps for
   interval * 60 seconds drifts, because the sensor read itself takes time, so
   the raw timestamps are never exactly 30 minutes apart. Resampling first means
   every lag below refers to a known real interval.

3. Every feature looks backwards. Lags, rolling means and differences all read
   data from before the row they sit on, so a row at time t never contains
   anything measured after t.

    python preprocess.py
    python preprocess.py --input data/raw.csv --output data/processed.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter

import config

REQUIRED = ["timestamp", "moisture", "temperature", "humidity"]

# Lags in samples, at 30 minutes each: 30 min, 1 h, 3 h, 6 h.
LAGS = [1, 2, 6, 12]
# Rolling windows in samples: 1 h, 3 h, 6 h.
ROLLING = [2, 6, 12]


def load(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Run  python collect.py  first."
        )
    frame = pd.read_csv(path)
    missing = [c for c in REQUIRED if c not in frame.columns]
    if missing:
        raise ValueError(f"{path} is missing the columns {missing}")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    frame = frame.dropna(subset=["timestamp"])
    for column in ("moisture", "temperature", "humidity"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.sort_values("timestamp").reset_index(drop=True)


def to_grid(frame: pd.DataFrame, interval_min: int) -> tuple[pd.DataFrame, dict]:
    """Put the readings on a fixed grid and fill short gaps."""
    before = len(frame)
    grid = f"{interval_min}min"

    series = frame.set_index("timestamp")
    # A duplicate timestamp would break resample, and a burst of them usually
    # means the loop was restarted. Averaging is the least destructive answer.
    series = series[~series.index.duplicated(keep="first")]
    resampled = series.resample(grid).mean(numeric_only=True)

    gaps = int(resampled["moisture"].isna().sum())
    # Two intervals is one hour. Anything longer is a real outage and is left
    # missing on purpose, so that the next stage can drop those rows instead of
    # inventing an hour of data.
    resampled = resampled.interpolate(method="time", limit=2, limit_area="inside")
    resampled = resampled.ffill(limit=2)

    stats = {
        "rows_in": before,
        "rows_out": int(resampled["moisture"].notna().sum()),
        "gaps_filled": gaps - int(resampled["moisture"].isna().sum()),
        "still_missing": int(resampled["moisture"].isna().sum()),
        "start": resampled.index.min(),
        "end": resampled.index.max(),
    }
    return resampled.reset_index(), stats


def denoise(frame: pd.DataFrame, window: int, savgo_window: int,
            savgo_order: int) -> pd.DataFrame:
    """Moving average for the short-scale jitter, Savitzky-Golay for the trend.

    The moving average is what the report names as the denoising method, since
    it is the one the brief asks for. Savitzky-Golay keeps the shape of the
    curve better and is kept as a second column so the two can be compared in a
    figure.
    """
    frame["moisture_smooth"] = (
        frame["moisture"].rolling(window=window, min_periods=1, center=False).mean()
    )

    length = len(frame)
    span = savgo_window if savgo_window % 2 == 1 else savgo_window + 1
    if length > span:
        frame["moisture_savgol"] = savgol_filter(
            frame["moisture"].to_numpy(dtype=float),
            window_length=span,
            polyorder=min(savgo_order, span - 1),
            mode="interp",
        )
    else:
        frame["moisture_savgol"] = frame["moisture_smooth"]

    return frame


def add_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Backward-looking features only."""
    base = frame["moisture_smooth"].astype(float)

    for lag in LAGS:
        frame[f"moisture_lag_{lag}"] = base.shift(lag)
    for window in ROLLING:
        frame[f"moisture_mean_{window}"] = base.rolling(window, min_periods=1).mean()

    frame["moisture_diff_1"] = base.diff()
    frame["moisture_diff_2"] = base.diff(2)

    # The time of day matters because evaporation follows the light. Sine and
    # cosine keep 23:00 next to 00:00, which a raw hour number would not.
    hours = frame["timestamp"].dt.hour + frame["timestamp"].dt.minute / 60.0
    frame["hour_sin"] = np.sin(hours / 24.0 * 2 * np.pi)
    frame["hour_cos"] = np.cos(hours / 24.0 * 2 * np.pi)

    return frame


def write_report(stats: dict, frame: pd.DataFrame, path: Path) -> None:
    span_h = (stats["end"] - stats["start"]).total_seconds() / 3600
    step = frame["timestamp"].diff().dropna().dt.total_seconds().div(60)
    lines = [
        "Data quality report",
        "===================",
        f"first reading      {stats['start']}",
        f"last reading       {stats['end']}",
        f"span               {span_h:.1f} h",
        f"rows read          {stats['rows_in']}",
        f"rows on grid       {stats['rows_out']}",
        f"gaps interpolated  {stats['gaps_filled']}",
        f"rows still empty   {stats['still_missing']}",
        f"median step        {step.median():.1f} min",
        f"max step           {step.max():.1f} min",
        "",
        f"moisture  min {frame['moisture'].min():.1f}  "
        f"max {frame['moisture'].max():.1f}  "
        f"mean {frame['moisture'].mean():.1f}",
        f"temperature min {frame['temperature'].min():.1f}  "
        f"max {frame['temperature'].max():.1f}",
        f"humidity  min {frame['humidity'].min():.1f}  "
        f"max {frame['humidity'].max():.1f}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=config.RAW_CSV)
    parser.add_argument("--output", type=Path, default=config.PROCESSED_CSV)
    parser.add_argument("--interval", type=int, default=config.SAMPLE_INTERVAL_MIN)
    args = parser.parse_args(argv)

    raw = load(args.input)
    print(f"read {len(raw)} rows from {args.input}")

    frame, stats = to_grid(raw, args.interval)
    print(f"on a {args.interval}-minute grid: {stats['rows_out']} rows, "
          f"{stats['gaps_filled']} gaps filled, {stats['still_missing']} still empty")

    frame = denoise(frame, config.SMOOTH_WINDOW, config.SAVGO_WINDOW,
                    config.SAVGO_ORDER)
    frame = add_features(frame)

    before = len(frame)
    frame = frame.dropna(subset=["moisture_smooth"] +
                         [f"moisture_lag_{lag}" for lag in LAGS])
    dropped = before - len(frame)
    if dropped:
        print(f"dropped {dropped} leading rows that had no history to lag on")

    if len(frame) < 40:
        print(f"warning: only {len(frame)} usable rows. A 48-hour run gives about "
              f"60 after lags; collect longer if the LSTM results look unstable.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    print(f"wrote {len(frame)} rows and {len(frame.columns)} columns to {args.output}")

    report = args.output.parent / "quality_report.txt"
    write_report(stats, frame, report)
    print(f"wrote {report}")

    print()
    print(frame[["timestamp", "moisture", "moisture_smooth"]].head(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

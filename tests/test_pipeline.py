"""
Checks on the parts that are easy to get subtly wrong.

The split and the lag construction are the two places where a modelling mistake
looks like a good result, so they are tested rather than assumed. Run with

    python -m unittest discover -s tests -v
    python -m pytest tests -q
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

# Run from the project root. The insert is a fallback for an editor that
# starts the tests from inside tests/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from soilcast import config                          # noqa: E402
from soilcast.modelling.train import split_index      # noqa: E402
from soilcast.processing import preprocess            # noqa: E402
from soilcast.sensing import sensor                   # noqa: E402


class SplitTest(unittest.TestCase):
    def test_gap_separates_the_sets(self) -> None:
        for n_rows in (100, 240, 1000):
            for gap in (12, 24, 30):
                train_end, test_start = split_index(n_rows, 0.2, gap)
                self.assertLessEqual(train_end + gap, test_start,
                                     "the last training target reaches into the test set")
                self.assertGreater(test_start, train_end)

    def test_test_set_holds_the_tail(self) -> None:
        train_end, test_start = split_index(100, 0.2, 24)
        self.assertGreaterEqual(test_start, 70)
        self.assertLessEqual(test_start, 80)


class LagTest(unittest.TestCase):
    def test_lag_reads_only_backwards(self) -> None:
        frame = pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=40, freq="30min"),
            "moisture": np.linspace(80, 40, 40),
            "temperature": 20.0,
            "humidity": 50.0,
        })
        out = preprocess.denoise(frame, 3, 11, 2)
        out = preprocess.add_features(out)

        # The lag of the smoothed series is the smoothed value of an earlier row,
        # never a later one.
        for lag in (1, 2, 6):
            expected = out["moisture_smooth"].shift(lag)
            pd.testing.assert_series_equal(
                out[f"moisture_lag_{lag}"].reset_index(drop=True),
                expected.reset_index(drop=True),
                check_names=False,
            )


class SensorTest(unittest.TestCase):
    def test_simulator_stays_in_range(self) -> None:
        probe = sensor.SimulatedSensor(seed=7)
        values = [probe.read() for _ in range(50)]
        self.assertTrue(all(0.0 <= v <= 100.0 for v in values))

    def test_simulator_dries_out(self) -> None:
        probe = sensor.SimulatedSensor(start_percent=85.0, tau_hours=33.0, noise=0.0,
                                       diurnal_amplitude=0.0)
        first = probe.read()
        # Fake a long elapsed time rather than sleeping for it.
        probe._t0 -= 48 * 3600
        later = probe.read()
        self.assertLess(later, first, "the simulated soil should be drier after 48 h")


class GridTest(unittest.TestCase):
    def test_resampling_produces_a_regular_step(self) -> None:
        stamps = pd.date_range("2026-01-01 00:00", periods=30, freq="31min")
        frame = pd.DataFrame({
            "timestamp": stamps,
            "moisture": np.linspace(80, 50, 30),
            "temperature": 20.0,
            "humidity": 50.0,
        })
        grid, stats = preprocess.to_grid(frame, config.SAMPLE_INTERVAL_MIN)
        steps = grid["timestamp"].diff().dropna().dt.total_seconds().div(60)
        self.assertTrue((steps == config.SAMPLE_INTERVAL_MIN).all())
        self.assertGreater(stats["rows_out"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

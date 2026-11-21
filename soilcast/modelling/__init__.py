"""
The two forecasters and the honest way to score them.

    networks.py  the LSTM architecture and the ridge baseline, small enough to
                 read in one screen
    train.py     split, scale, fit, evaluate, plot, write models/metrics.json

PyTorch is optional. Without it the baseline still fits and the LSTM is skipped,
which is what the Raspberry Pi install does.
"""

from .networks import HAS_TORCH, LinearForecaster, LSTMNet, count_parameters

__all__ = ["HAS_TORCH", "LinearForecaster", "LSTMNet", "count_parameters"]

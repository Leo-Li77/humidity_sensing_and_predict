"""
soilcast — soil moisture monitoring and forecasting.

Four stages behind one command, from the probe to the forecast:

    sensing     read the probe and the ambient source, append to data/raw.csv
    processing  resample, denoise, build features; descriptive figures
    modelling   the ridge baseline and the LSTM, with the honest split
    web         the browser console, so none of the above needs a shell

The pipeline is a chain of files, not of function calls: each stage reads what
the previous one wrote, so any stage can be re-run on its own.

    python -m soilcast all --demo
    python -m soilcast train --no-lstm
    python -m soilcast web
"""

__version__ = "1.0.0"

__all__ = ["__version__"]

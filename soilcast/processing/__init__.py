"""
From raw rows to the two files the modelling stage reads.

    ambient_table.py  downloads the public climate data set, reduces it to one
                      mean per hour of the day
    preprocess.py     resamples, fills short gaps, denoises, builds features
    analyze.py        descriptive statistics and the diagnostic figures

Nothing here normalises: scaling needs the training period only, and that is
decided in the modelling stage, after the split.
"""

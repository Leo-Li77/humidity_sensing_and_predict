"""
The browser console.

    app.py    a Flask blueprint: one endpoint per stage, JSON only
    jobs.py   the collection thread and the pipeline child processes
    store.py  data/raw.csv, data/history.db and models/metrics.json
    serve.py  the entry point: flags, addresses, waitress

The page is plain HTML, CSS and SVG built in the browser. No CDN is involved,
so the console keeps working with the internet down, which is the situation in
most labs.
"""

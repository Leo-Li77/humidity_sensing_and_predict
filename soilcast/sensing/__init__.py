"""
Where the numbers come from.

    sensor.py   three probe drivers behind one read(), so nothing downstream
                knows whether a real FC-28 or the simulator is attached
    ambient.py  temperature and humidity, from a table, a DHT sensor or a curve
    collect.py  the sampling loop, the only writer of data/raw.csv
"""

from .ambient import AmbientSource
from .sensor import MoistureSensor, build_sensor

__all__ = ["AmbientSource", "MoistureSensor", "build_sensor"]

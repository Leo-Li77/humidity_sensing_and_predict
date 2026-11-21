"""
Ambient temperature and relative humidity.

The FC-28 measures soil moisture only. The other two columns have to come from
somewhere, and this module is the single place that decides where. The choice is
recorded in config.AMBIENT_SOURCE and repeated in the README, because a marker
reading a table has to be able to tell which columns are measured and which are
not.

    dataset    hour-of-day mean from data/external/ambient.csv
    sensor     a DHT11 or DHT22 wired to DHT_GPIO_PIN
    synthetic  a diurnal curve with a little noise
"""

from __future__ import annotations

import csv
import math
from datetime import datetime

from .. import config


def _synthetic(moment: datetime) -> tuple[float, float]:
    """A plausible indoor curve: warm and dry in the afternoon, cool at night."""
    hour = moment.hour + moment.minute / 60.0
    phase = (hour - 9.0) / 24.0 * 2 * math.pi
    temperature = 22.0 + 4.0 * math.sin(phase)
    humidity = 58.0 - 12.0 * math.sin(phase)
    return round(temperature, 2), round(humidity, 2)


class AmbientSource:
    """Returns temperature and humidity for a timestamp."""

    def __init__(self, source: str | None = None) -> None:
        self.source = (source or config.AMBIENT_SOURCE).lower()
        self._table: dict[int, tuple[float, float]] = {}
        self._dht = None
        self._last: tuple[float, float] | None = None

        if self.source == "dataset":
            self._table = self._load_table()
        elif self.source == "sensor":
            self._dht = self._open_dht()
        elif self.source != "synthetic":
            raise ValueError(f"unknown ambient source: {self.source!r}")

    # ------------------------------------------------------------------ #
    @staticmethod
    def _load_table() -> dict[int, tuple[float, float]]:
        """Read the 24 hourly means written by processing/ambient_table.py."""
        path = config.AMBIENT_CSV
        if not path.exists():
            raise FileNotFoundError(
                f"{path} is missing. Run  python -m soilcast ambient  first, or "
                f"set AMBIENT_SOURCE = 'synthetic' in config.py."
            )
        table: dict[int, tuple[float, float]] = {}
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                table[int(row["hour"])] = (
                    float(row["temperature"]),
                    float(row["humidity"]),
                )
        if not table:
            raise ValueError(f"{path} holds no rows")
        return table

    @staticmethod
    def _open_dht():
        import adafruit_dht
        from board import D4

        pin = D4 if config.DHT_GPIO_PIN == 4 else config.DHT_GPIO_PIN
        return adafruit_dht.DHT22(pin, use_pulseio=False)

    # ------------------------------------------------------------------ #
    def read(self, moment: datetime) -> tuple[float, float]:
        """Return (temperature in Celsius, relative humidity in percent).

        A DHT sensor drops a read every few dozen samples because its timing is
        tight. Rather than abort a 48-hour run for that, the previous pair is
        returned and the gap is left to the preprocessing stage.
        """
        if self.source == "dataset":
            self._last = self._table[moment.hour]
        elif self.source == "sensor":
            try:
                self._last = (round(self._dht.temperature, 2),
                              round(self._dht.humidity, 2))
            except (RuntimeError, OSError):
                if self._last is None:
                    self._last = _synthetic(moment)
        else:
            self._last = _synthetic(moment)
        return self._last

    def describe(self) -> str:
        if self.source == "dataset":
            return "measured indoors, taken from a public data set by hour of day"
        if self.source == "sensor":
            return "measured with an on-board DHT sensor"
        return "synthetic diurnal curve, not measured"

    def close(self) -> None:
        if self._dht is not None:
            self._dht.exit()

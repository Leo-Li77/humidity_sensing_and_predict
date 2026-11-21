"""
Moisture sensor drivers.

Every driver exposes read(), which returns soil moisture as a percentage where 0
is bone dry and 100 is fully soaked. Nothing downstream knows which driver is in
use, so a run with the simulator and a run with the real probe produce the same
file format.

Hardware imports are done inside the constructor. On a laptop those libraries are
not installed, and importing the module must still work.
"""

from __future__ import annotations

import math
import random
import time
from abc import ABC, abstractmethod

from .. import config


class MoistureSensor(ABC):
    """Common interface for every probe."""

    name = "abstract"

    @abstractmethod
    def read(self, elapsed_hours: float | None = None) -> float:
        """Return the current soil moisture in percent, clamped to 0-100.

        elapsed_hours is the time the sampling loop believes has passed. The
        simulator uses it so that a fast test run produces the same drying curve
        as a real 48-hour run; the hardware drivers ignore it.
        """

    def close(self) -> None:
        """Release hardware resources. The simulator needs nothing."""

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(100.0, value))


class ADS1115Sensor(MoistureSensor):
    """FC-28 analog output read through an ADS1115 on I2C.

    This is the only digital path that produces a continuous reading. The FC-28
    analog pin outputs a voltage that rises as the soil dries, so the raw count
    is mapped to percent with the two calibration points in config.py.
    """

    name = "ads1115"

    def __init__(self, channel: int | None = None) -> None:
        try:
            import board
            import busio
            from adafruit_ads1x15.ads1115 import ADS1115, P0, P1, P2, P3
            from adafruit_ads1x15.analog_in import AnalogIn
        except ImportError as error:
            # Reached either because the libraries are absent or because the chip
            # is not wired at all. The raw ModuleNotFoundError says nothing about
            # the second case, which is the common one here: the board only has
            # the digital output connected.
            raise ImportError(
                "the ads1115 driver needs an ADS1115 on I2C and the Adafruit "
                "libraries. Install with  pip install adafruit-blinka "
                "adafruit-circuitpython-ads1x15  and check  i2cdetect -y 1  shows "
                "0x48. If there is no ADC on the board, use --driver gpio: the "
                "digital output still reports wet or dry."
            ) from error

        pins = {0: P0, 1: P1, 2: P2, 3: P3}
        channel = config.ADS1115_CHANNEL if channel is None else channel

        self._i2c = busio.I2C(board.SCL, board.SDA)
        self._ads = ADS1115(self._i2c)
        self._ads.gain = 1
        self._channel = AnalogIn(self._ads, pins[channel])

    def read(self, elapsed_hours: float | None = None) -> float:
        span = config.ADC_DRY - config.ADC_WET
        if span == 0:
            raise ValueError("ADC_DRY and ADC_WET must differ")
        percent = (config.ADC_DRY - self._channel.value) / span * 100.0
        return self._clamp(percent)


class GPIOSensor(MoistureSensor):
    """FC-28 digital output on a GPIO pin.

    The comparator on the module has already turned the analog reading into a
    single bit, so this driver can only report 100 or 0. It is the wiring the
    course notes describe: VCC to 5 V, GND to GND, D0 to GPIO 4, and the
    potentiometer turned to the point where the detection LED just lights.

    The polarity is the part that is easy to get wrong. The comparator pulls D0
    LOW while the soil is wetter than the setting and lets it sit HIGH once the
    soil has dried past it, so a low pin means wet, not dry. Read the other way
    round the series comes out inverted, and every downstream figure is wrong in
    a way that still looks plausible.

    One consequence is worth stating here rather than in a comment somewhere
    else: with VCC at 5 V the high level on D0 is close to 5 V, while a GPIO pin
    is specified for 3.3 V. It works in practice and the course notes ask for it,
    but a divider between D0 and the pin, or running the module from 3.3 V, is
    the version that cannot damage anything.
    """

    name = "gpio"

    def __init__(self, pin: int | None = None) -> None:
        from gpiozero import DigitalInputDevice

        self._device = DigitalInputDevice(pin or config.MOISTURE_GPIO_PIN)

    def read(self, elapsed_hours: float | None = None) -> float:
        # LOW is the comparator's "wet enough" state, so it maps to 100 %.
        return 0.0 if self._device.value else 100.0

    def close(self) -> None:
        self._device.close()


class SimulatedSensor(MoistureSensor):
    """A drying curve that behaves like a real pot.

    Evaporation is roughly proportional to the water still present, so the
    moisture decays exponentially towards a residual level rather than towards
    zero: the last few percent are bound to the soil and leave very slowly. The
    constants are chosen so that a plant starting at 85 % reads about 33 % after
    two days and about 12 % after five, which is what the probe shows in
    practice. A small diurnal term makes the day drier than the night, and
    gaussian noise stands in for probe jitter.
    """

    name = "simulate"

    def __init__(
        self,
        start_percent: float = 85.0,
        residual_percent: float = 8.0,
        tau_hours: float = 42.0,
        noise: float = 1.2,
        diurnal_amplitude: float = 2.5,
        seed: int = config.SEED,
    ) -> None:
        self._start = start_percent
        self._residual = residual_percent
        self._tau = tau_hours
        self._noise = noise
        self._diurnal = diurnal_amplitude
        self._rng = random.Random(seed)
        self._t0 = time.monotonic()

    def read(self, elapsed_hours: float | None = None) -> float:
        if elapsed_hours is None:
            elapsed_h = (time.monotonic() - self._t0) / 3600.0
        else:
            elapsed_h = elapsed_hours
        base = self._residual + (self._start - self._residual) * math.exp(
            -elapsed_h / self._tau
        )
        # Drying is faster in the warm part of the day. The hour is taken from
        # the elapsed time so that repeated runs stay deterministic.
        hour = (elapsed_h % 24) + 8.0
        wobble = self._diurnal * math.sin((hour - 9.0) / 24.0 * 2 * math.pi)
        return self._clamp(base + wobble + self._rng.gauss(0.0, self._noise))


def build_sensor(driver: str | None = None) -> MoistureSensor:
    """Return the driver named in the argument or in config.py."""
    name = (driver or config.SENSOR_DRIVER).lower()
    if name == "ads1115":
        return ADS1115Sensor()
    if name == "gpio":
        return GPIOSensor()
    if name == "simulate":
        return SimulatedSensor()
    raise ValueError(f"unknown sensor driver: {name!r}")

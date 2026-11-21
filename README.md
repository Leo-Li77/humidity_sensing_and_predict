# ProjectSem3_2026 — Soil Moisture Monitoring and Forecasting on a Raspberry Pi

A potted plant, an FC-28 soil moisture probe and a Raspberry Pi. The probe is read
every 30 minutes for several days without watering, the readings are cleaned, and
two models forecast the soil moisture 12 hours ahead: a linear regression as the
baseline and a small LSTM as the model that is supposed to find the daily pattern
the baseline cannot.

Semester 3 project, NUIST / SETU.

---

## What is measured and what is not

This matters more than any result below, so it is stated first.

| Column | Source | Measured on site |
|---|---|---|
| `moisture` | FC-28 probe on the Raspberry Pi | yes |
| `temperature` | public indoor climate data set, reduced to hourly means | no |
| `humidity` | same data set | no |

The FC-28 measures soil moisture only. Temperature and relative humidity come from
the UCI Occupancy Detection set, which records an office room once a minute and is
reduced here to one mean per hour of the day by `python -m soilcast ambient`. The
value used for a reading is therefore the average for that hour, not the air
temperature at that instant. `config.AMBIENT_SOURCE` switches this to an on-board
DHT sensor or to a synthetic curve, and whatever is set is recorded in
`models/metrics.json` and must be repeated in the report.

## Hardware

There are two ways to wire the same probe, and they produce different data. Pick
one before starting a run.

| Wiring | FC-28 | Extra part | Driver | Data |
|---|---|---|---|---|
| digital | VCC 5 V, GND, **D0 → GPIO 4** | none | `--driver gpio` | 100 or 0 |
| analog | VCC 3.3 V, GND, **AO → ADS1115 A0** | ADS1115 on I2C | `--driver ads1115` | 0-100 % |

| Part | Connection | Note |
|---|---|---|
| Raspberry Pi 4 | — | any model with a 40-pin header works |
| FC-28 soil moisture probe | see the table above | the module has both outputs |
| ADS1115 ADC | I2C: SDA GPIO2, SCL GPIO3, ADDR to GND (0x48) | only for the analog wiring |
| Potted plant | — | not watered for the duration of the run |

The FC-28 has two outputs and they are not equivalent.

- **D0** is a single bit set by the comparator on the board. Turn the
  potentiometer until the detection LED just lights: that point becomes the
  "needs water" threshold. It reports wet or dry and nothing in between, so it
  cannot produce a drying curve, and the models cannot be fitted on it. This is
  the wiring the course notes describe.
- **A0** is the analog voltage, which falls as the soil gets wetter. It gives a
  continuous reading, but the Pi has no ADC of its own, so it has to go through an
  ADS1115 or an MCP3008.

**The two wirings are mutually exclusive on VCC.** With VCC at 5 V the analog
output can swing to nearly 5 V, which would exceed the supply of an ADC running
at 3.3 V and can damage it. So: D0 + 5 V, or A0 + 3.3 V. There is also a smaller
point about the digital wiring — with VCC at 5 V the high level on D0 is close to
5 V while a GPIO pin is specified for 3.3 V. It works in practice; a 1 kΩ + 1 kΩ
divider between D0 and the pin, or powering the module from 3.3 V and
re-adjusting the potentiometer, removes the risk.

For the analog wiring, calibrate once before the run: hold the probe in air and
note the raw ADC count, then in a glass of water and note it again. Put both
numbers in `soilcast/config.py` as `ADC_DRY` and `ADC_WET`. The defaults are
typical for a 3.3 V supply and will be wrong for another probe.

## Install

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Everything below runs from the project root and needs no install step beyond the
packages above, because `python -m soilcast` finds the package in the working
directory. `pip install -e .` is optional: it puts a plain `soilcast` command on
the path, so `soilcast train` replaces `python -m soilcast train`.

On the Raspberry Pi the GPIO stack comes from apt, not from pip. `lgpio` is the
pin factory gpiozero uses on Bookworm, and it is built against the system Python,
so the venv has to be created with `--system-site-packages` or gpiozero will find
no pin factory:

```bash
sudo apt install python3-lgpio python3-gpiozero
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements-pi.txt
.venv/bin/pip install adafruit-blinka adafruit-circuitpython-ads1x15   # analog wiring only
```

`scripts/start_ui.sh` does exactly this on its first run, so double-checking the
flag by hand is only needed after a manual install.

## Run

One package, one command per stage:

```bash
python -m soilcast --help          # the stages and what each one does
```

The whole pipeline, with the simulator standing in for the probe:

```bash
python -m soilcast all --demo
```

That writes five days of readings with the clock advanced by hand, cleans them,
draws the diagnostic figures and fits both models in well under a minute. Use it
to check the install before committing five days of real time to a real run.

> **The data currently committed to `data/` came from the simulator.** It is there
> so the pipeline can be checked end to end without hardware, and it does not
> describe a real plant. Overwrite `data/raw.csv` with a real collection before
> submitting, and replace the numbers in Results.

A real run:

```bash
python -m soilcast collect --driver ads1115 --hours 120 --interval 30
python -m soilcast all --reuse
```

`--hours 120` is five days. The brief asks for at least two, which leaves about 60
usable rows after the lags and the target shift; five days leaves around 200 and
the LSTM trains on something worth training on.

Stage by stage:

| Command | What it does | Writes |
|---|---|---|
| `python -m soilcast ambient` | download and reduce the climate data set | `data/external/ambient.csv` |
| `python -m soilcast collect` | read the probe on a fixed interval | `data/raw.csv` |
| `python -m soilcast preprocess` | resample, fill gaps, denoise, build features | `data/processed.csv` |
| `python -m soilcast analyze` | descriptive statistics and diagnostic figures | `figures/fig_series.png` and others |
| `python -m soilcast train` | fit both models, evaluate, plot | `figures/fig_*.png`, `models/metrics.json` |
| `python -m soilcast all` | all five, in order | all of the above |

Each stage reads only the file the previous one wrote, so any stage can be re-run
on its own after a change.

Flags worth knowing (every stage also answers `--help`):

```
collect     --hours --interval --driver --samples --speed --fast --append
preprocess  --input --output --interval
analyze     --input --denoise-hours
train       --input --horizon --no-lstm --seq-len
all         --demo --reuse --collect HOURS --driver --no-lstm --skip-ambient
web         --host --port --no-browser --password
```

### Winding the clock forward

With the simulator, `--speed N` divides the waiting by `N` and leaves the readings
exactly where they were: one per interval. Five simulated days take twelve
minutes at `--speed 600`, and two at `--speed 3600`, while the file is
indistinguishable from a real run.

```bash
python -m soilcast collect --speed 600 --output data/demo.csv
```

`--fast`, or `--speed 0`, skips the waiting altogether. Speeding up a real probe
is refused, and should be: the soil would not dry any faster, so the rows would
simply be closer together than the moisture changed.

## Web console

Everything above also works without a command line. `python -m soilcast web` serves
a single page that can start a collection, stop it, import a file, run the
preprocessing, run the analysis and fit both models, and it keeps a record of
every run in `data/history.db`.

### Start it

Windows, double-click:

```
scripts\start_ui.bat
```

The first run creates `.venv` and installs the packages, which takes a few minutes
and a few hundred megabytes. After that the file starts the console and opens the
browser. Nothing is typed.

Raspberry Pi:

```bash
chmod +x scripts/start_ui.sh
./scripts/start_ui.sh
```

### Reach it from another device

The server listens on `0.0.0.0`, so anything on the same network can open it. On
start it prints the address to use, which is usually `http://192.168.x.x:5057`.
Five browsers at once is comfortable: the page polls twice every second, and
waitress serves it from eight threads.

The page is plain HTML, CSS and SVG built in the browser. No CDN is involved, so
it keeps working with the internet down, which is the situation in most labs.

### Keep it running on the Pi

```bash
sudo cp deploy/soil-console.service /etc/systemd/system/
sudo systemctl enable --now soil-console
sudo systemctl status soil-console
```

Edit the paths and the user in that file before copying it. With this in place the
console comes back after a reboot and nobody has to be logged in.

### Putting it on the internet

The five-client requirement is satisfied by a local network, and a home network is
the safe default. If it really has to be reachable from outside, pick one of these
rather than forwarding a port to a Flask app:

| Approach | What it gives you | Caveat |
|---|---|---|
| Cloudflare Tunnel | a public hostname, no port forwarding, no public IP | needs an account, free tier is enough |
| Tailscale or ZeroTier | a private network between your own devices only | everyone needs the client installed |
| Router port forwarding | direct access | needs a public IP, exposes the port to scanning |

Whichever is used, **set a password first**:

```bash
SOIL_PASSWORD=something-long ./scripts/start_ui.sh
```

With that variable set, the console answers 401 until the browser prompt is
satisfied. Without it, anyone who reaches the port can start a collection or
overwrite `data/raw.csv`.

### What the page does

| Screen | What it is for |
|---|---|
| Overview | latest reading, a gauge against the watering threshold, the moisture curve, the ambient curves |
| Collect | driver, interval, duration and clock speed; progress; a live stream of samples; stop. The clock control only appears for the simulator |
| Data | the raw table, upload a CSV, download raw or processed, clear with an archive |
| Analysis | descriptive statistics, a histogram, an autocorrelation of the differenced series |
| Model | train, then compare MAE, RMSE and R² for the two models |
| History | every run with its result, filterable, removable |

The charts are drawn as SVG in the browser from the same numbers the scripts
produce, so the screen and `metrics.json` cannot disagree.

## Data format

Both CSV files share four columns:

| Column | Type | Unit | Note |
|---|---|---|---|
| `timestamp` | `%Y-%m-%d %H:%M:%S` | — | local time, one row per interval |
| `moisture` | float | percent | 0 is bone dry, 100 is soaked |
| `temperature` | float | Celsius | see the source table above |
| `humidity` | float | percent RH | see the source table above |

`processed.csv` adds the engineered columns:

| Column | Meaning |
|---|---|
| `moisture_smooth` | moving average over 5 samples |
| `moisture_savgol` | Savitzky-Golay filter, window 11, order 2 |
| `moisture_lag_n` | the smoothed value n samples earlier, n in 1, 2, 6, 12 |
| `moisture_mean_n` | rolling mean over n samples |
| `moisture_diff_1`, `_2` | first difference over one and two samples |
| `hour_sin`, `hour_cos` | time of day, so that 23:00 sits next to 00:00 |
| `target` | the moisture 12 hours later, used only by the training stage |

## How the evaluation avoids fooling itself

Four decisions, each a place where a time-series project commonly reports a better
number than it deserves.

**The split is chronological, never random.** The test set is the last 20 % of the
series. A random split trains on readings taken after the ones under test, which
measures interpolation rather than forecasting.

**A gap separates the two sets.** A row at time *t* predicts moisture at *t + 12 h*,
so the last training rows would otherwise reach into the test period.
`split_index()` drops `horizon_steps` rows between the two sets, and those rows are
used by neither side.

**The scaler is fitted on training rows only.** Fitting MinMax on the whole file,
which the first version of this project did, lets the test range set the training
inputs. Scaling happens in `soilcast/modelling/train.py` after the split, not in
`soilcast/processing/preprocess.py`.

**Every feature looks backwards.** Lags, rolling means and differences all read
rows before the one they sit on. There is no `bfill`, which would copy a future
value into the past. `tests/test_pipeline.py` checks the lag construction.

The `80 % residual band` on the forecast figure is the 10th to 90th percentile of
the test residuals. It is not a confidence interval, and it is named as a residual
band for that reason.

## Results

> **The moisture column in these numbers comes from the simulator.** They were
> produced with `python -m soilcast all --demo`, which stands in for the probe; the
> temperature and humidity columns are the real data set. Re-run with
> `--driver ads1115` on a real plant before submitting, and keep `sensor_driver` in
> `models/metrics.json` consistent with what the report claims.

Five days at 30-minute intervals, 240 readings, 204 rows with a 12-hour target,
139 training rows, 41 test rows, and 24 rows discarded as the gap. Lead time 24
steps.

| Model | Parameters | MAE (%) | RMSE (%) | R² |
|---|---|---|---|---|
| Ridge regression on the engineered features | 15 | 0.473 | 0.582 | 0.954 |
| LSTM, one layer of 16 units, 5 runs averaged | 2 353 | 1.014 | 1.109 | 0.834 |

The baseline wins. That is worth stating rather than hiding, and the reason is
visible in the data: over a 12-hour lead time the moisture is almost a linear
function of the moisture now, and the ridge model has that value as an input with
a straight line to the output. The LSTM has to rediscover the same relationship
from 92 windows. It gets close and does not beat it.

Three changes were needed before the network was usable at all, and each one is a
lesson rather than a tuning detail.

**Predict the change, not the absolute value.** A drying series keeps falling, so
the test period sits below anything in training. A network asked for the absolute
moisture has to extrapolate past the edge of its training values and fails badly,
scoring a negative R². Asked for the 12-hour change it stays inside its range, and
the forecast is the current reading plus that change.

**Use a z-score, not a MinMax rescale.** The brief asks for [0, 1] normalisation and
`soilcast/processing/preprocess.py` produces those columns for the figures. For the
model input a MinMax scale maps the test period to negative numbers, which the
network has never seen; a z-score only puts it further from the mean.

**Give the window a full day.** Twelve hours of history cannot show a complete
day-night cycle, and the daily pattern is exactly what the LSTM is there to find.
Extending the window from 24 to 48 samples was the largest single gain in the whole
exercise, at the cost of 24 training windows.

## Repository layout

Code is in one package, `soilcast/`, and every subpackage is one stage of the
pipeline. Data and results sit beside it, because they are outputs rather than
code and should be readable without opening the package.

```
soilcast/
├── cli.py            # python -m soilcast <stage>
├── config.py         # every path, threshold and constant
├── sensing/          # where the numbers come from
│   ├── sensor.py       three probe drivers behind one read()
│   ├── ambient.py      temperature and humidity: table, DHT, or curve
│   └── collect.py      the sampling loop, the only writer of data/raw.csv
├── processing/       # raw rows to the two files the models read
│   ├── ambient_table.py  downloads the climate data set, reduces it to 24 means
│   ├── preprocess.py     resampling, gap filling, denoising, features
│   └── analyze.py        descriptive statistics and diagnostic figures
├── modelling/        # the forecasters and the honest way to score them
│   ├── networks.py     the LSTM architecture and the ridge baseline
│   └── train.py        split, scale, fit, evaluate, plot
└── web/              # the console
    ├── app.py          one endpoint per stage, JSON only
    ├── jobs.py         the collection thread and the pipeline subprocesses
    ├── store.py        data/raw.csv, data/history.db, models/metrics.json
    ├── serve.py        flags, addresses, waitress
    ├── templates/      index.html, the single page
    └── static/         styles, the SVG chart code, the page logic
```

| Path | Contents |
|---|---|
| `tests/test_pipeline.py` | checks on the split, the lags and the grid |
| `scripts/start_ui.bat` | double-click launcher for Windows |
| `scripts/start_ui.sh` | launcher for the Raspberry Pi |
| `deploy/soil-console.service` | systemd unit so the console survives a reboot |
| `pyproject.toml` | packaging, and the optional `soilcast` entry point |
| `requirements.txt` | the laptop install; `requirements-pi.txt` leaves out torch |
| `data/` | `raw.csv`, `processed.csv`, the quality report and the summary |
| `figures/` | every figure the report includes |
| `models/` | `metrics.json` and the saved weights |

## Limitations

- **There is no ADC on this board, so the analog output cannot be used.** The Pi
  has no analog input of its own; reading A0 needs an ADS1115 or an MCP3008. Only
  the digital wiring is available here, and it reports 100 or 0 and nothing in
  between. That is enough for a "water the plant" alarm and not enough for a
  drying curve, so the data committed to `data/` comes from the simulator. Adding
  the ADC is the whole of what stands between this pipeline and a real run.
- **Temperature and humidity are not measured on site.** They come from an office
  room recorded in 2015. The soil moisture is real; the air around it is a stand-in,
  and the report says so.
- **Five days is the minimum, not the target.** At 48 hours the 12-hour shift and
  the 24-hour window leave too little to fit on: 60 rows with a target, 24 of them
  training rows. The script refuses to run rather than print a meaningless score.
  Even five days gives 139 training rows and 92 training windows, which is why the
  network was reduced to 2 353 weights.
- **The LSTM does not beat the baseline here.** With a nearly linear target and a
  few dozen windows, a regularised regression is hard to beat. The LSTM is included
  because the brief asks for it, and the comparison is reported as it fell out
  rather than tuned until it won.
- **The LSTM forecasts one point, not a curve.** Feeding a 12-hour model its own
  output accumulates error quickly, so that is not done. A one-step model plus
  recursion would be the way to draw a 24-hour curve; it is left out rather than
  done badly.
- **The FC-28 is not a laboratory instrument.** Its output drifts with soil
  temperature, salinity and probe corrosion, and it needs recalibrating over weeks
  of use. Readings in one run are comparable to each other, not to another pot.
- **The simulator is a model, not data.** Its curve is exponential with a diurnal
  term, chosen to look like the probe. Any result produced with `--driver simulate`
  describes the simulator.


## Author

Li Jinyu from NUIST

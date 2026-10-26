# Running the v2 simulation from the CLI

Initialise the database once, then run a short simulation:

```powershell
gridsync reset-db
gridsync run-simulation --hours 1 --houses 3
```

Use `--days` for whole days, and combine it with `--hours` when needed:

```powershell
gridsync run-simulation --start 2026-07-15 --location Brisbane --days 1 --hours 2 --solver highs
```

This example runs for 26 hours. `--days 1` on its own runs for 24 hours;
omitting both duration options keeps the one-hour default. A date-only `--start`
begins at 4:00 am in the selected location's timezone. A `--start` value with
a time uses UTC (unless it includes an explicit offset).

By default, every house uses the seeded `house-1--6.6KW-5KW-INV` configuration.
Use `--house-config ID` to put every house on another seeded configuration, or
`--random-configs N` to assign houses across `N` distinct seeded configurations:

```powershell
gridsync run-simulation --houses 50 --random-configs 10 --assignment-seed 42 --days 1
```

The random mode gives each selected config at least one house, then assigns the
remaining houses with weights favouring smaller solar systems and fewer pool
pumps. It prints the count assigned to each config. `--assignment-seed` makes
the config assignment repeatable; it does not seed synthetic demand generation.
The current seeded configs all have solar, so this mode does not sample homes
without solar. `--house-config` and `--random-configs` cannot be combined.
Use `--objective Balanced` (or `Emissions`) to change the optimisation
objective; the default is `Cost`.

`run-simulation` fetches weather, PV, AEMO emissions and prices for the window,
then fills missing household demand rows with synthetic load profiles. One
random realised profile is generated per selected house config for each day,
at five-minute intervals, with a daily half-hour forecast profile. Houses using
the same config share that profile; the simulation multiplies it by their count
and sums across configs. Existing demand rows are preserved. The
default solver is Gurobi, with HiGHS fallback when it is unavailable or fails.
Use `--solver highs` to select HiGHS explicitly. Without `--start`, the simulation begins at 4:00 am in the
selected location's timezone on the latest day that fits the requested run
length and another 24 hours of forecast data before the conservative AEMO
monthly archive cutoff. Recent explicit dates may fail until that month's
archive is available. Run
`gridsync run-simulation --help` for other options.

Price and emissions forecasts always use the saved regional LightGBM models.
Run `gridsync reset-db` after upgrading to recreate the schema and remove old
baseline forecasts and simulation results. Baseline-only simulations are no
longer supported. Realised prices and emissions remain observed data.

`aemo_forecast_model_service.py` selects and caches the region's latest eligible
April model. January-March uses the preceding year's model; the 2026 model remains
eligible until April 2027 even though its training evaluation ended in August.
Missing or incompatible model exports fail explicitly. Models and JSON metadata
are read from `src/database_v2/seed_data/lightgbm`. Weights are gzip-compressed
native LightGBM text (`price.txt.gz` and `emissions.txt.gz`), with JSON metadata.
Python's built-in gzip module decompresses them once when loading into the
model cache; no extra installation is needed.
`aemo_forecast_features.py` reconstructs the trained feature schema
using only historically available observations; `aemo_forecast_service.py`
batches inference and reuses source tables. Price corrections apply to wholesale
AUD/MWh before conversion and network tariffs. Emissions corrections are clipped
at zero. Existing `forecast_sources` values describe the underlying price
baseline's AEMO or historical origin.

Price models retain the original 28 inputs. Only emissions models use seven
additional regional generation features (35 inputs total): solar/wind
availability, forecast demand, residual demand, renewable share, forecast age and
a missing-input flag. `aemo_generation_forecast_service.py` reads cached monthly
`PREDISPATCHREGIONSUM` archives and selects the newest forecast run/revision
eligible at the solve's four-minute grace cutoff. Both the nominal run and
`LASTCHANGED` are checked; absent fields or unpublished horizons stay missing.
Availability describes semi-scheduled renewable availability, not guaranteed
dispatch or rooftop PV. Price forecasts do not fetch this additional table.

Direct service callers can use `AemoPriceService(use_lightgbm=False)` or
`AemoEmissionsService(use_lightgbm=False)` for baseline-only forecasts.
LightGBM and its native runtime are installed through the project's Conda
environment. See [local setup](../../deployment/01-local-setup.md).
The saved models are ready for inference; simulations do not require training
dependencies.

If the database contains synthetic demand generated before the load-profile
unit correction, run `gridsync reset-db` before simulating so those
preserved rows are regenerated in kW.

PV forecasts are stored once per daily ECMWF cycle and house, with the time
that cycle became usable (about 06:44 UTC). Each half-hour solve selects the
newest cycle available at its run time and reads its next 48 targets.

Use `--no-prepare-data` to reuse weather, PV, emissions and prices already in
the database; the command still fills missing demand. The CLI prints each MPC
solve and its six live dispatches as each 30-minute block completes, then prints
realised energy, cost and emissions totals at the end.

`--fixed-buy` and `--fixed-sell` (also on the Setup page) set optional fixed
tariffs. Each is a final price in AUD/kWh that replaces the AEMO spot plus
network price for every interval, rather than being added to it. An omitted
value keeps the market price, and `0` is a valid fixed price. The fixed prices
are used for optimisation, terminal valuation, perfect foresight, the reactive
battery and both no-battery baselines. They are stored on the request and are
part of its id, so results from a different tariff are never reused. A sell
price above the buy price makes the optimiser unbounded and is rejected, except
for the Emissions objective. Run `gridsync reset-db` after updating, because the
`simulation_request` table gained two columns.
SOC appears in kWh and as a percentage of nominal battery capacity. The summary
also reports the selected objective (`Cost`, `Emissions`, or `Balanced`),
economic cost scaled to a 48-period day, and the theoretical emissions cost
using the Balanced objective's carbon price, regardless of the selected
objective. Printed timestamps use the selected location's local timezone.
**UPDATE (16/10/2026)**: Fixed tariffs were corrected to only affect baseline
comparisons and not the prices that the optimiser sees in simulations with a battery.

The engine persists rolling decisions, live dispatches and simulation summaries
in the database. The CLI also prints both no-battery baselines: the community
sharing its PV and each house importing/exporting individually, with grid costs,
export revenue and emissions for each.
It also evaluates the existing reactive self-consumption controller on the same
realised five-minute data. This baseline charges from current PV surplus and
discharges into current demand, using its own SOC and rolling 24-hour cycle
budget, with the same battery and starting SOC as the smart controller. Its
dispatches and summary are stored as `reactive_battery` in the existing output
tables. The CLI compares economic cost, emissions and Balanced cost, and reports
the smart controller's improvement using the selected objective. A positive
improvement means the smart controller achieved a lower objective value.
Perfect foresight is saved as `perfect_foresight` in the existing
`live_controller_output_5m` and `simulation_summary` tables. Summaries retain
the emissions cost, so Balanced cost can be read as grid import cost minus
export revenue plus degradation and emissions cost. Repeating a completed
configuration prints its stored summaries without rerunning the simulation.
The updated schema assumes a reset database.

After a new rolling simulation, the engine solves one perfect-foresight comparison
over every realised five-minute interval in the completed run. It uses the same
battery constraints and selected `--objective`, including each period's carbon
price for `Balanced`, then prints its energy, cost, emissions and final SOC
beside the rolling objective value and gap. This is an offline benchmark: it
sees the entire realised series before deciding its battery dispatch.
The optimiser runner supports `run(input, all_decisions=True)` to return a list
of existing `OptimiserOutput` decisions, including each period's next SOC.
Its default still returns the first decision for rolling control.

The evaluation also estimates the value of energy left above minimum SOC in
the rolling controller only. At the simulation end time, it uses the next normal
48-period forecast and solves from terminal SOC and minimum SOC, separately
for `Cost` and `Emissions`. Both starts use the rolling controller's same realised
24-hour throughput history. This is a set of optimiser solves, with no extra
live dispatch or simulated day. Data preparation includes the next forecast
issuance; `--no-prepare-data` requires that forecast to be already stored.

The difference (minimum-SOC objective minus terminal-SOC objective) is saved
in `simulation_summary.estimated_terminal_cost_value_aud` and
`estimated_terminal_emissions_value_tonnes`, together with `terminal_soc_kwh`
and `terminal_value_horizon_hours`. The valuation forecast starts at the request's
`end_time_utc`. These estimates can be negative, for example if a fuller battery
limits profitable charging at negative prices. Realised totals remain unchanged.
`ResultsDao.get_summaries()` exposes `realised_economic_cost_aud`,
`comparison_economic_cost_aud` and `comparison_emissions_tonnes`. The comparison
metrics subtract the corresponding terminal credit for rolling only. The main
rolling summary prints both marginal values, then adjusts its economic cost,
average daily economic cost and grid import emissions. It labels these as
estimates and also shows the realised totals before credit. A compact table at
the end lists total economic cost, emissions and Balanced cost for rolling,
greedy, perfect foresight and both batteryless baselines. Rolling's estimated
terminal credits are marked in the table. Balanced cost adds realised economic
cost to realised emissions priced at each period's Balanced carbon price.
The reactive comparison also uses these adjusted Cost and Emissions metrics.
The two credits come from
separate future policies and are not combined into a Balanced credit; Balanced
costs and objective gaps remain explicitly labelled as realised.

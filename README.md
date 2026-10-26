# NeighbourhoodWatt

NeighbourhoodWatt is a desktop simulation and optimisation tool for exploring
shared community batteries in residential neighbourhoods across Australia's
National Electricity Market (NEM). It compares coordinated solar and battery
operation with reactive and batteryless baselines, showing energy costs,
grid imports and exports, and emissions over time.

## Features

- Configure a neighbourhood's houses, rooftop solar, shared battery, location,
  tariffs, network constraints, and optimisation objective.
- Compare Cost, Emissions, and Balanced objectives using a rolling-horizon
  Pyomo optimiser and a five-minute reactive controller.
- Fetch historical market and weather inputs and use bundled regional
  LightGBM models for price and emissions forecasting.
- Replay simulations and inspect community, operation, and debrief charts
  in a PyQt6 desktop interface.
- Store configurations, downloaded inputs, and simulation results locally in
  SQLite, with reusable cached runs.
- Run simulations from the `gridsync` command line and build a Windows executable.

## Getting started

Install [Miniforge](https://github.com/conda-forge/miniforge), then open a
Miniforge prompt and run:

```sh
git clone https://github.com/Conor-Kik/NeighbourhoodWatt.git
cd NeighbourhoodWatt
conda env create -f environment.yml
conda activate neighbourhoodwatt
gridsync reset-db
gridsync run
```

Python 3.12 and the required native libraries are installed by the environment.
Internet access is required to fetch uncached market and weather data. The
repository includes the forecast models and synthetic-load distributions used
at runtime; the original household meter-reading dataset is not bundled.

`gridsync reset-db` clears the local database, including saved simulations.
Run it for initial setup or when intentionally rebuilding the database.

For a short command-line simulation using the open-source HiGHS solver:

```sh
gridsync run-simulation --hours 1 --houses 3 --solver highs
```

The standard environment and Windows build use HiGHS and do not bundle Gurobi.
The source retains the existing optional Gurobi integration for separately
licensed installations. See [third-party notices](THIRD_PARTY_NOTICES.md).

## Documentation

- [Local setup and development tools](deployment/01-local-setup.md)
- [Managing the Conda environment](deployment/02-using-conda.md)
- [Command-line simulation options](src/backend_v2/README.md)
- [Building the Windows executable](deployment/05-executable-delployment.md)
- [Testing](tests/README.md)
- [Bundled forecast models](src/database_v2/seed_data/lightgbm/README.md)
- [Data sources and third-party notices](THIRD_PARTY_NOTICES.md)

## Project structure

```text
src/
  backend_v2/                   Simulation, optimisation, data pipelines
  database_v2/                  SQLite schema, access layers, seed data
  frontend_v2/                  PyQt6 interface, replay, charts
  team_21_the_grid_optimists/    Existing gridsync CLI entry point
tests/                          Automated tests and GUI integration tests
build/                          PyInstaller specification and runtime hook
deployment/                     Installation, development, and build guides
```

The internal CLI module name is retained for compatibility. The installable
distribution is named `neighbourhoodwatt`; the command remains `gridsync`.

## Contributors and origins

Created by **The Grid Optimists**:

| Contributor | GitHub |
| --- | --- |
| Alexander Mewis | [Alex-Mewis](https://github.com/Alex-Mewis) |
| Conor Kikkert | [Conor-Kik](https://github.com/Conor-Kik) |
| Hugh Torpie | [hughtorpie](https://github.com/hughtorpie) |
| Jonathan Smith | [jostev](https://github.com/jostev) |
| Kyle Schwegmann | [KyleSch8989](https://github.com/KyleSch8989) |
| Robbie Crouch | [Robb1eCodes](https://github.com/Robb1eCodes) |

NeighbourhoodWatt began in **DECO3801 - Design Computing Studio 3: Build**
at **The University of Queensland**, Semester 2, 2026. It was developed in
response to **project brief 30, "Neighbourhood Watt: Collaborative Optimisation
of Solar and Storage"**, in the *DECO3801/DECO7381 Project Briefs* document.
This acknowledgement records the project's origins; it does not imply
university endorsement.

Thanks also to Pasindu Marasinghe ([mottretor](https://github.com/mottretor))
for the original repository scaffolding. Robbie Crouch's original commits also
appear under the name Francis Crouch.

This repository starts from a clean snapshot of the program. The original
course repository retains the development history and assessment records.

## Licence

Copyright (C) 2026 The Grid Optimists contributors.

The original program code and project documentation are licensed under the
**GNU General Public License version 3 only (GPL-3.0-only)**. See [LICENSE](LICENSE).
This choice supports distribution with the GPL version of PyQt6. Redistributed
modified versions of GPL-covered code must preserve the licence and provide
the corresponding source as required by the GPL.

External libraries, datasets, and data-derived resources retain their own terms;
see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

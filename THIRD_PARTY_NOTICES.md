# Third-party software and data

The GPL-3.0-only licence in `LICENSE` applies to the original NeighbourhoodWatt
program code and project documentation. It does not replace the terms attached
to external software, downloaded data, or data-derived resources. Dependencies
are installed separately through `environment.yml`; their upstream copyright
and licence notices remain applicable.

## Project brief

`PROJECT_BRIEF.pdf` contains project brief 30, "Neighbourhood Watt: Collaborative
Optimisation of Solar and Storage", from the University of Queensland's
*DECO3801/DECO7381 Project Briefs* (original pages 40-41). It is reproduced as a
reference document. Rights in the original brief remain with its authors;
the project's GPL licence does not apply to this third-party document.

## User interface and solvers

- **PyQt6**, by Riverbank Computing: the standard open-source distribution is
  licensed under GPLv3. A separate commercial licence is also offered.
  [PyQt licensing](https://www.riverbankcomputing.com/software/pyqt).
- **Qt**: a separate dependency of PyQt6 with its own licence terms. Preserve
  the notices and source/relinking provisions applicable to the exact Qt
  libraries when distributing a compiled application.
  [Qt licensing](https://doc.qt.io/qt-6/licensing.html).
- **Pyomo**: mathematical optimisation modelling.
  [Project and licence](https://github.com/Pyomo/pyomo).
- **HiGHS/highspy**: the open-source solver included in the standard setup.
  [Project and licence](https://github.com/ERGO-Code/HiGHS).
- **Gurobi/gurobipy**: an optional, separately licensed proprietary solver.
  Neither its binaries nor licence files are included in this repository.
  The standard Conda environment and PyInstaller build omit Gurobi. Existing
  source-level integration is retained for installations whose licences permit
  its use. The project GPL grant does not grant rights to Gurobi or an exception
  to any third-party GPL obligations. Do not distribute a combined Gurobi build
  without resolving the applicable licences.
  [Gurobi licensing](https://www.gurobi.com/solutions/licensing/).

## Market, weather, and reference data

- **Australian Energy Market Operator (AEMO)** supplies market prices,
  dispatch/generation information, and archived forecast inputs through
  [NEMWeb](https://www.nemweb.com.au/) and AEMO's public data services.
  The bundled LightGBM price/emissions weights and their metadata are project
  outputs trained from these inputs. AEMO retains rights in its source data;
  see [AEMO's terms of use](https://www.aemo.com.au/privacy-and-legal-notices).
- **Smart-Grid Smart-City Customer Trial Data**, published through the
  Australian Government's data portal, provided household demographics and
  electricity readings used to derive the synthetic-load distributions in
  `src/database_v2/seed_data/load_profiles_cluster_kernels_distributions.npz`.
  The original household records and meter-reading dataset are not bundled.
  The derived distributions and any reused source data remain subject to the
  source dataset's terms. See the
  [dataset record](https://www.data.gov.au/data/dataset/smart-grid-smart-city-customer-trial-data).
- **Australian Energy Regulator (AER)**: reference carbon values in the seed
  data are based on the May 2024 *Valuing emissions reduction - Final guidance
  and explanatory statement*.
  [Source report](https://www.aer.gov.au/system/files/2024-05/AER%20-%20Valuing%20emissions%20reduction%20-%20Final%20guidance%20and%20explanatory%20statement%20-%20May%202024.pdf).
- **Open-Meteo**, **ECMWF Open Data**, and **Dynamical** provide weather,
  irradiance, and forecast data used by the weather/PV pipelines.
  [Open-Meteo terms](https://open-meteo.com/en/terms),
  [ECMWF Open Data](https://www.ecmwf.int/en/forecasts/datasets/open-data),
  [Dynamical](https://dynamical.org/).
- **Australian network distributors**: representative network tariffs and
  source URLs are recorded in `src/database_v2/seed_data/tariffs.json`.
  Those publications retain their original terms.
- **Australian Postcodes**, by schappim: postcode/suburb lookups are obtained
  from the separately installed Python package at the pinned revision in
  `environment.yml`. Preserve its upstream notices when redistributing the
  package or its postcode data.
  [Source repository](https://github.com/schappim/australian-postcodes).

## Other dependencies

The project uses LightGBM, scikit-learn, pandas, NumPy, SciPy, xarray, pvlib,
Pydantic, Typer/Rich, platformdirs, timezonefinder, requests-cache,
openmeteo-requests, retry-requests, nemosis, ckanapi, cfgrib/ecCodes,
ecmwf-opendata, dynamical-catalog, kmedoids, FlatBuffers, tzdata, and the
development tools listed in `environment.yml`. Refer to the installed versions
and their upstream notices for their exact licences and transitive dependencies.

When distributing an executable, collect the notices and meet the source and
other redistribution requirements for the exact dependencies included in that
build. This file records attribution and provenance; it does not relicense
external data or dependencies.

# Community optimiser

## Overview

This directory works out how a shared community battery should charge and
discharge. It balances electricity demand, solar generation, prices, and carbon
intensity while respecting battery limits. It can minimise cost, emissions, or
a combination of both, and provides tools to evaluate the results.

## What each file does

| File | Brief explanation |
| --- | --- |
| [optimiser_builder.py](optimiser_builder.py) | Defines the planning problem in Pyomo. Creates decisions for charging, discharging, grid imports and exports, unused solar, and stored energy. Adds energy balance, battery efficiency, storage, power, and rolling 24-hour cycling limits, then defines the chosen objective. |
| [optimiser_runner.py](optimiser_runner.py) | Builds and solves the model using Gurobi or HiGHS, with fallback to HiGHS if Gurobi is unavailable or raises a solve error. Checks the solver result and packages battery actions and metrics. Normally returns the first planned decision for rolling control; it can also return the whole plan. |
| [optimiser_constants.py](optimiser_constants.py) | Stores the standard timing settings: half-hour planning periods, five-minute live control steps, and the number of planning periods per day. |
| [optimiser_printer.py](optimiser_printer.py) | Makes simulation results readable in the command line. Prints planned actions, actual five-minute dispatches, battery charge levels, costs, emissions, and comparisons between scenarios. |
| [perfect_foresight.py](perfect_foresight.py) | Builds a hindsight benchmark from a completed simulation's actual data. Solves the full run once at five-minute resolution with future demand, solar, prices, and emissions already known. This helps assess the rolling controller's performance. |
| [terminal_value.py](terminal_value.py) | Estimates the future value of energy left in the battery at the end of a run. Compares future optimisation starting from the remaining charge against starting from minimum charge, using the same forecast and battery usage history. |

## How the pieces fit together

1. The surrounding simulation supplies forecasts, battery settings, and current battery state to the runner.
2. The builder turns those inputs into variables, operating constraints, and an objective.
3. The runner asks a solver for the best feasible plan and returns the first decision for rolling control.
4. The surrounding simulation executes five-minute control steps and replans at the next half-hour boundary.
5. The printer explains the results. Perfect foresight provides a hindsight comparison, and terminal valuation estimates the benefit of stored energy remaining at the end.

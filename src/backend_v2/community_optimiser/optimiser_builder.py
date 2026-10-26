"""Defines the battery decisions, operating limits and goal for the solver to use."""

import pyomo.environ as pyo

from database_v2.models.optimiser_models import OptimiserInput
from database_v2.models.request_models import ObjectiveMode


def build_community_optimiser(data: OptimiserInput) -> pyo.ConcreteModel:
    """Construct the optimisation model from validated input data."""
    model = pyo.ConcreteModel(name="community_optimiser")

    periods = range(data.horizon_periods)
    horizon_periods = data.horizon_periods
    history_periods = len(data.battery_throughput_history)
    period_hours = data.period_hours

    # Keep the short model names used by the specification. T contains each
    # forecast period. S contains the boundaries around those periods, so there
    # is one more battery state than there are battery actions.
    model.T = pyo.Set(initialize=list(periods))
    model.S = pyo.Set(initialize=list(range(horizon_periods + 1)))
    model.T_subsequent = pyo.Set(initialize=list(range(1, horizon_periods)))

    min_soc = data.battery_constants.min_soc_kwh
    max_soc = data.battery_constants.max_soc_kwh
    efficiency = data.battery_constants.efficiency
    degradation_cost = data.battery_constants.degradation_aud_per_kwh

    # Copy the input lists into model values. These names match the symbols in
    # the specification: demand, generation, prices, and carbon intensity.
    model.D = pyo.Param(
        model.T,
        initialize=lambda m, t: data.demand_aggregate_kw[t],
    )
    model.G = pyo.Param(
        model.T,
        initialize=lambda m, t: data.pv_aggregate_kw[t],
    )
    model.C_in = pyo.Param(
        model.T,
        initialize=lambda m, t: data.tariffs_buy_aud_per_kwh[t],
    )
    model.C_out = pyo.Param(
        model.T,
        initialize=lambda m, t: data.tariffs_sell_aud_per_kwh[t],
    )
    model.E = pyo.Param(
        model.T,
        initialize=lambda m, t: data.carbon_intensity_t_per_mwh[t],
    )

    # V and Q are charge and discharge power. SOC is stored energy, while Y
    # records how much battery use falls inside the moving 24-hour window.
    model.V = pyo.Var(model.T, domain=pyo.NonNegativeReals)
    model.Q = pyo.Var(model.T, domain=pyo.NonNegativeReals)
    model.SOC = pyo.Var(
        model.S,
        domain=pyo.NonNegativeReals,
        bounds=(min_soc, max_soc),
    )
    model.Y = pyo.Var(model.T, domain=pyo.NonNegativeReals)

    # F_in and F_out are grid import and export. U is unused solar generation.
    model.F_in = pyo.Var(model.T, domain=pyo.NonNegativeReals)
    model.F_out = pyo.Var(model.T, domain=pyo.NonNegativeReals)
    model.U = pyo.Var(model.T, domain=pyo.NonNegativeReals)

    model.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)

    # Use an equality here instead of fixing SOC[0]. The solver can then report
    # how much the result would improve if the battery started with more energy.
    model.initial_soc_constraint = pyo.Constraint(
        expr=model.SOC[0] == data.initial_soc_kwh
    )

    def soc_update_rule(m, t):
        """Calculate stored energy after charging, discharging and losses.

        Charging stores less energy than it draws; discharging removes more than it
        supplies.
        """
        return m.SOC[t + 1] == m.SOC[t] + period_hours * (
            efficiency * m.V[t] - m.Q[t] / efficiency
        )

    model.soc_update_constraint = pyo.Constraint(model.T, rule=soc_update_rule)

    def battery_power_limit_rule(m, t):
        """Keep combined charging and discharging within the battery power limit.

        The shared limit also applies when a period includes both directions.
        """
        return m.V[t] + m.Q[t] <= data.battery_constants.max_power_kw

    model.battery_power_constraint = pyo.Constraint(
        model.T, rule=battery_power_limit_rule
    )

    def bus_balance_rule(m, t):
        """Balance community demand, battery use and grid flows against available solar.

        Unused solar is subtracted from generation before balancing the flows.
        """
        return m.D[t] + m.V[t] + m.F_out[t] == (m.G[t] - m.U[t]) + m.Q[t] + m.F_in[t]

    model.bus_balance_constraint = pyo.Constraint(model.T, rule=bus_balance_rule)

    def curtailment_limit_rule(m, t):
        """Prevent unused solar from exceeding the available solar generation."""
        return m.U[t] <= m.G[t]

    model.curtailment_constraint = pyo.Constraint(model.T, rule=curtailment_limit_rule)

    def initial_rolling_window_rule(m):
        """Combine recent battery use with the first planned action for the 24-hour
        total.

        History runs from oldest to newest, so the new action replaces the first sample.
        """
        curr_throughput = (efficiency * m.V[0] + m.Q[0] / efficiency) * period_hours
        return m.Y[0] == sum(data.battery_throughput_history[1:]) + curr_throughput

    model.initial_rolling_window_constraint = pyo.Constraint(
        rule=initial_rolling_window_rule
    )

    def rolling_window_update_rule(m, i):
        """Update 24-hour battery use by adding the new action and removing expired use.

        Measured history expires first; earlier planned actions expire on longer
        horizons.
        """
        curr_throughput = (efficiency * m.V[i] + m.Q[i] / efficiency) * period_hours
        if i < history_periods:
            expired_throughput = data.battery_throughput_history[i]
        else:
            expired_period = i - history_periods
            expired_throughput = (
                efficiency * m.V[expired_period] + m.Q[expired_period] / efficiency
            ) * period_hours
        return m.Y[i] == m.Y[i - 1] + curr_throughput - expired_throughput

    model.rolling_window_update_constraint = pyo.Constraint(
        model.T_subsequent, rule=rolling_window_update_rule
    )

    def cycle_limit_rule(m, t):
        """Keep battery energy use within the rolling 24-hour cycle allowance.

        The allowance counts both charging and discharging, using twice nominal capacity
        per allowed cycle.
        """
        max_cycling = (
            2.0
            * data.battery_constants.max_cycles_24h
            * data.battery_constants.capacity_kwh
        )
        return m.Y[t] <= max_cycling

    model.cycle_limit_constraint = pyo.Constraint(model.T, rule=cycle_limit_rule)

    # Cost includes grid purchases, grid sales, and wear caused by battery use.
    total_cost = sum(
        (
            model.C_in[t] * model.F_in[t]
            - model.C_out[t] * model.F_out[t]
            + degradation_cost * (efficiency * model.V[t] + model.Q[t] / efficiency)
        )
        * period_hours
        for t in model.T
    )

    # Carbon intensity is supplied per MWh, so divide by 1000 to match kWh.
    total_emissions = sum(
        model.E[t] * model.F_in[t] * period_hours / 1000.0 for t in model.T
    )

    model.financial_cost = pyo.Expression(expr=total_cost)
    model.emissions = pyo.Expression(expr=total_emissions)
    carbon_prices = data.carbon_prices_aud_per_t
    if carbon_prices is None:
        carbon_prices = [data.carbon_price_aud_per_t] * horizon_periods
    model.carbon_cost = pyo.Expression(
        expr=sum(
            carbon_prices[t] * model.E[t] * model.F_in[t] * period_hours / 1000.0
            for t in model.T
        )
    )

    if data.objective_mode == ObjectiveMode.COST:
        model.objective = pyo.Objective(expr=model.financial_cost, sense=pyo.minimize)
    elif data.objective_mode == ObjectiveMode.EMISSIONS:
        model.objective = pyo.Objective(expr=model.emissions, sense=pyo.minimize)
    elif data.objective_mode == ObjectiveMode.BALANCED:
        model.objective = pyo.Objective(
            expr=model.financial_cost + model.carbon_cost, sense=pyo.minimize
        )
    else:
        raise ValueError(f"Unknown objective mode: {data.objective_mode}")

    return model

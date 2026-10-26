"""Community uses matched terminal-adjusted estimates for every objective."""

import numpy as np
import pytest

from database_v2.data_access_objects.frontend_dao import FrontendDao
from frontend_v2.bindings.bindings import ReadBinding
from frontend_v2.bindings.selectors.categorical import select_cost_breakdown
from frontend_v2.bindings.selectors.community import (
    select_community_financial_cost,
    select_community_goal_benefit,
    select_community_goal_comparison,
    select_community_goal_context,
    select_community_grid_emissions,
    select_run_settings,
)
from frontend_v2.bindings.selectors.tabular import (
    NOT_AVAILABLE,
    select_emissions_avoided,
    select_foresight_gap,
    select_savings,
    select_terminal_value,
    select_total_demand,
    select_total_grid_import,
)


def records(key, rows):
    dtype = getattr(FrontendDao, key).dtype
    return np.array(
        [tuple(row.get(name, 0) for name in dtype.names) for row in rows], dtype=dtype
    )


def make_data(mode="Cost"):
    data = {
        "get_current_request": [{"row": 0, "request_id": "run-a"}],
        "get_all_simulation_requests": [
            {
                "request_id": "run-a",
                "location_name": "Brisbane",
                "start_time_utc": "2026-01-01T00:00:00+00:00",
                "end_time_utc": "2026-01-02T00:00:00+00:00",
                "objective_mode": mode,
                "house_count": 2,
                "fixed_buy_aud_per_kwh": None,
                "fixed_sell_aud_per_kwh": 0.05,
            }
        ],
        "get_all_locations": [
            {"location_name": "Brisbane", "timezone": "Australia/Brisbane"}
        ],
        "get_current_run_houses": [
            {"house_config_id": "house-a", "request_id": "run-a", "quantity": 2}
        ],
        "get_current_operating_costs": [
            {
                "scenario_name": name,
                "request_id": "run-a",
                "realised_economic_cost_aud": cost,
                "comparison_economic_cost_aud": -999,
                "emissions_tonnes": emissions,
                "emissions_cost_aud": carbon,
            }
            for name, cost, emissions, carbon in (
                ("optimised_battery", 100, 0.4, 20),
                ("reactive_battery", 130, 0.5, 25),
                ("no_battery_shared", 150, 0.6, 30),
                ("perfect_foresight", -1000, 999, 0),
                ("no_battery_individual", 0, 0, 0),
            )
        ],
        "get_current_energy_flows": [
            {
                "request_id": "run-a",
                "timestamp_utc": "2026-01-01T00:00:00+00:00",
                "demand_kw": 8,
                "pv_kw": 4,
                "grid_import_kw": 4,
                "charge_kw": 2,
            }
        ],
    }
    return {key: records(key, rows) for key, rows in data.items()}


def translate(selector, data):
    binding = ReadBinding(selector)
    return binding.translate({key: data[key] for key in binding.needs})


def test_balanced_cards_use_components_from_the_same_balanced_estimate():
    data = make_data("Balanced")
    rows = data["get_current_operating_costs"]
    rows["estimated_terminal_cost_value_aud"][0] = 999
    rows["estimated_terminal_emissions_value_tonnes"][0] = 999
    rows["estimated_terminal_balanced_value_aud"][0] = 10
    rows["estimated_terminal_balanced_financial_value_aud"][0] = 6
    rows["estimated_terminal_balanced_emissions_value_tonnes"][0] = 0.04
    assert translate(select_community_financial_cost, data).rows[0] == (
        "$94.00",
        "$47.00",
    )
    assert translate(select_community_grid_emissions, data).rows[0] == (
        "360.0 kg",
        "180.0 kg",
    )
    context = translate(select_community_goal_context, data)
    facts = dict(zip(context.headers, context.rows[0], strict=True))
    assert facts["result"] == "$110.00"
    # Same-strategy financial and carbon components reconcile with the goal.
    assert (100 - 6) + (20 - 4) == 110


@pytest.mark.parametrize(
    "selector,field",
    [
        (
            select_community_financial_cost,
            "estimated_terminal_balanced_financial_value_aud",
        ),
        (
            select_community_grid_emissions,
            "estimated_terminal_balanced_emissions_value_tonnes",
        ),
    ],
)
def test_missing_balanced_component_does_not_use_another_strategy(selector, field):
    data = make_data("Balanced")
    data["get_current_operating_costs"][field][0] = float("nan")
    assert translate(selector, data).rows[0][0] == NOT_AVAILABLE


@pytest.mark.parametrize(
    "mode,credit,amount",
    [
        ("Cost", "estimated_terminal_cost_value_aud", "$40.00"),
        ("Emissions", "estimated_terminal_emissions_value_tonnes", "110.0 kg CO₂e"),
        ("Balanced", "estimated_terminal_balanced_value_aud", "$45.00 AUD equivalent"),
    ],
)
def test_reactive_detail_uses_same_terminal_adjustment_as_bars(mode, credit, amount):
    data = make_data(mode)
    data["get_current_operating_costs"][credit][0] = 0.01 if mode == "Emissions" else 10
    result = translate(select_community_goal_comparison, data)
    assert f"{amount} lower" in result.detail
    assert "than reactive control (terminal-adjusted)" in result.detail


@pytest.mark.parametrize(
    "smart,reactive,expected",
    [
        (-20, -10, "$10.00 lower"),
        (100, 90, "$10.00 higher"),
        (100, 100, "same adjusted financial cost"),
        (100, 100.001, "same adjusted financial cost"),
        (100, float("nan"), "unavailable"),
    ],
)
def test_reactive_detail_handles_worse_equal_and_missing_results(
    smart, reactive, expected
):
    data = make_data()
    data["get_current_operating_costs"]["realised_economic_cost_aud"][:2] = (
        smart,
        reactive,
    )
    assert expected in translate(select_community_goal_comparison, data).detail


@pytest.mark.parametrize(
    "mode,metric,values,benefit",
    [
        ("Cost", "Financial cost (AUD)", [100, 130, 150], "+$50.00"),
        ("Emissions", "Grid emissions (kg CO₂e)", [400, 500, 600], "+200.0 kg"),
        ("Balanced", "Balanced score (AUD equivalent)", [120, 155, 180], "+$60.00"),
    ],
)
def test_goal_uses_matching_units_values_and_baseline(mode, metric, values, benefit):
    data = make_data(mode)
    result = translate(select_community_goal_comparison, data)
    assert list(result.y_values) == [metric]
    assert list(result.y_values[metric]) == values
    assert result.x_labels == [
        "Smart\nbattery",
        "Reactive\nbattery",
        "Shared solar\nwithout battery",
    ]
    card = translate(select_community_goal_benefit, data)
    assert card.rows[0][0] == benefit
    assert "Improvement" in card.rows[0][1]


def test_balanced_banner_shows_component_amounts_and_goal_context():
    payload = translate(select_community_goal_context, make_data("Balanced"))
    facts = dict(zip(payload.headers, payload.rows[0], strict=True))
    assert facts["goal"] == "Balanced"
    assert "Brisbane" in facts["context"] and "2 houses" in facts["context"]
    assert facts["run_id"] == "run-a" and "run-a" not in facts["context"]
    assert "buy market price" in facts["context"]
    assert "sell 5.00 c/kWh (fixed)" in facts["context"]
    assert [facts[key] for key in ("financial", "carbon", "terminal", "result")] == [
        "$100.00",
        "$20.00",
        "$0.00",
        "$120.00",
    ]
    assert "not an additional bill" in facts["note"]
    assert "not a fixed 50/50 split" in facts["details"]
    assert "Subtracts the estimated value" in facts["note"]
    assert "Reactive control is assumed" in facts["details"]


@pytest.mark.parametrize(
    "mode,field,credit,recorded,carbon,terminal,result",
    [
        (
            "Cost",
            "estimated_terminal_cost_value_aud",
            10,
            "$100.00",
            "",
            "$10.00",
            "$90.00",
        ),
        (
            "Cost",
            "estimated_terminal_cost_value_aud",
            -10,
            "$100.00",
            "",
            "-$10.00",
            "$110.00",
        ),
        (
            "Emissions",
            "estimated_terminal_emissions_value_tonnes",
            0.1,
            "400.0 kg",
            "",
            "100.0 kg",
            "300.0 kg",
        ),
        (
            "Balanced",
            "estimated_terminal_balanced_value_aud",
            15,
            "$100.00",
            "$20.00",
            "$15.00",
            "$105.00",
        ),
        (
            "Balanced",
            "estimated_terminal_balanced_value_aud",
            float("nan"),
            "$100.00",
            "$20.00",
            "—",
            "—",
        ),
    ],
)
def test_goal_breakdown_preserves_recorded_components_and_matching_credit(
    mode, field, credit, recorded, carbon, terminal, result
):
    data = make_data(mode)
    data["get_current_operating_costs"][field][0] = credit
    payload = translate(select_community_goal_context, data)
    facts = dict(zip(payload.headers, payload.rows[0], strict=True))
    assert [facts[key] for key in ("financial", "carbon", "terminal", "result")] == [
        recorded,
        carbon,
        terminal,
        result,
    ]


def test_raw_cards_have_equal_household_averages_and_do_not_change_debrief():
    data = make_data()
    before = data["get_current_operating_costs"].copy()
    assert translate(select_community_financial_cost, data).rows == [
        ("$100.00", "$50.00")
    ]
    assert translate(select_community_grid_emissions, data).rows == [
        ("400.0 kg", "200.0 kg")
    ]
    data["get_current_operating_costs"]["comparison_economic_cost_aud"] = 10000
    assert translate(select_community_financial_cost, data).rows[0][0] == "$100.00"
    assert translate(select_community_goal_benefit, data).rows[0][0] == "+$50.00"
    data["get_current_operating_costs"]["comparison_economic_cost_aud"] = before[
        "comparison_economic_cost_aud"
    ]
    np.testing.assert_array_equal(data["get_current_operating_costs"], before)


@pytest.mark.parametrize(
    "mode,field,credit,expected,benefit",
    [
        ("Cost", "estimated_terminal_cost_value_aud", 10, 90, "+$60.00"),
        ("Cost", "estimated_terminal_cost_value_aud", -10, 110, "+$40.00"),
        (
            "Emissions",
            "estimated_terminal_emissions_value_tonnes",
            0.1,
            300,
            "+300.0 kg",
        ),
        ("Balanced", "estimated_terminal_balanced_value_aud", 15, 105, "+$75.00"),
    ],
)
def test_matching_terminal_estimate_changes_all_goal_comparisons(
    mode, field, credit, expected, benefit
):
    data = make_data(mode)
    rows = data["get_current_operating_costs"]
    rows[field][0] = credit
    rows[field][1] = 999  # Reactive is explicitly assumed to finish at minimum SOC.
    chart = translate(select_community_goal_comparison, data)
    assert next(iter(chart.y_values.values()))[0] == pytest.approx(expected)
    assert translate(select_community_goal_benefit, data).rows[0][0] == benefit
    if mode == "Cost":
        assert (
            translate(select_community_financial_cost, data).rows[0][0]
            == f"${expected:.2f}"
        )
    elif mode == "Emissions":
        assert (
            translate(select_community_grid_emissions, data).rows[0][0]
            == f"{expected:.1f} kg"
        )
    if mode == "Balanced":
        # Different future policies' credits must never be added into the score.
        rows["estimated_terminal_cost_value_aud"][0] = 999
        rows["estimated_terminal_emissions_value_tonnes"][0] = 999
        assert translate(select_community_goal_comparison, data) == chart
        context = translate(select_community_goal_context, data)
        facts = dict(zip(context.headers, context.rows[0], strict=True))
        assert [
            facts[key] for key in ("financial", "carbon", "terminal", "result")
        ] == [
            "$100.00",
            "$20.00",
            "$15.00",
            "$105.00",
        ]


@pytest.mark.parametrize("credit", [float("nan"), float("inf")])
def test_missing_terminal_estimate_does_not_silently_use_recorded_total(credit):
    data = make_data("Balanced")
    data["get_current_operating_costs"]["estimated_terminal_balanced_value_aud"][0] = (
        credit
    )
    assert translate(select_community_goal_benefit, data).rows[0][0] == "—"
    chart = translate(select_community_goal_comparison, data)
    assert "Smart\nbattery" not in chart.x_labels
    assert "rerun" in translate(select_community_goal_context, data).rows[0][-2]


@pytest.mark.parametrize(
    "cost,shown,state",
    [
        (200, "-$50.00", "Worse outcome"),
        (150, "$0.00", "No change"),
        (150.001, "$0.00", "No change"),
        (-10, "+$160.00", "Improvement"),
    ],
)
def test_comparison_sign_and_display_precision(cost, shown, state):
    data = make_data()
    data["get_current_operating_costs"]["realised_economic_cost_aud"][0] = cost
    value, detail = translate(select_community_goal_benefit, data).rows[0]
    assert value == shown
    assert state in detail


@pytest.mark.parametrize(
    "mode,field",
    [
        ("Cost", "realised_economic_cost_aud"),
        ("Emissions", "emissions_tonnes"),
        ("Balanced", "emissions_cost_aud"),
    ],
)
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_smart_result_is_unavailable_and_never_zero(mode, field, invalid):
    data = make_data(mode)
    data["get_current_operating_costs"][field][0] = invalid
    assert translate(select_community_goal_benefit, data).rows[0][0] == "—"
    assert (
        "Smart\nbattery"
        not in translate(select_community_goal_comparison, data).x_labels
    )


def test_missing_baseline_does_not_hide_recorded_totals():
    data = make_data()
    data["get_current_operating_costs"] = data["get_current_operating_costs"][:2]
    assert translate(select_community_goal_benefit, data).rows[0][0] == "—"
    assert translate(select_community_financial_cost, data).rows[0][0] == "$100.00"
    assert len(translate(select_community_goal_comparison, data).x_labels) == 2


@pytest.mark.parametrize("empty", [True, False])
def test_missing_or_zero_house_counts_only_remove_averages(empty):
    data = make_data()
    if empty:
        data["get_current_run_houses"] = data["get_current_run_houses"][:0]
    else:
        data["get_current_run_houses"]["quantity"] = 0
    assert translate(select_community_financial_cost, data).rows == [
        ("$100.00", "Average unavailable")
    ]
    assert translate(select_community_grid_emissions, data).rows == [
        ("400.0 kg", "Average unavailable")
    ]


def test_zero_carbon_is_valid_and_stored_weighting_is_used_directly():
    data = make_data("Balanced")
    data["get_current_operating_costs"]["emissions_cost_aud"][0] = 0
    assert (
        translate(select_community_goal_comparison, data).y_values[
            "Balanced score (AUD equivalent)"
        ][0]
        == 100
    )
    # Already aggregated annual carbon rates must not be replaced by one current rate.
    data["get_current_operating_costs"]["emissions_cost_aud"][0] = 7.31
    assert translate(select_community_goal_comparison, data).y_values[
        "Balanced score (AUD equivalent)"
    ][0] == pytest.approx(107.31)
    data["get_current_operating_costs"]["emissions_tonnes"][0] = 0
    assert translate(select_community_grid_emissions, data).rows[0][0] == "0.0 kg"


def test_missing_carbon_column_is_not_treated_as_zero():
    data = make_data("Balanced")
    rows = data["get_current_operating_costs"]
    data["get_current_operating_costs"] = rows[
        [name for name in rows.dtype.names if name != "emissions_cost_aud"]
    ]
    assert translate(select_community_goal_benefit, data).rows[0][0] == "—"
    assert translate(select_community_financial_cost, data).rows[0][0] == "$100.00"


def test_unknown_objective_and_previous_run_results_do_not_leak():
    data = make_data("Unknown")
    assert translate(select_community_goal_comparison, data).y_values == {}
    assert (
        "Objective unavailable"
        in translate(select_community_goal_context, data).rows[0][1]
    )
    data = make_data()
    data["get_current_operating_costs"]["request_id"] = "previous-run"
    assert translate(select_community_financial_cost, data).rows[0][0] == "—"
    assert translate(select_community_goal_comparison, data).x_labels == []


@pytest.mark.parametrize(
    "tonnes,carbon,expected",
    [
        (0.4, 20, "$50.00/t CO₂e"),
        # 0.1 t at $40/t plus 0.3 t at $60/t across an annual rate change.
        (0.4, 22, "$55.00/t CO₂e"),
        (0.4, 0, "$0.00/t CO₂e"),
        (0, 0, "unavailable · no grid emissions"),
        (float("nan"), 20, "unavailable"),
        (0.4, float("inf"), "unavailable"),
        (-0.4, 20, "unavailable"),
    ],
)
def test_effective_carbon_price_uses_stored_emissions_weighted_components(
    tonnes, carbon, expected
):
    data = make_data("Balanced")
    data["get_current_operating_costs"]["emissions_tonnes"][0] = tonnes
    data["get_current_operating_costs"]["emissions_cost_aud"][0] = carbon
    payload = translate(select_community_goal_context, data)
    facts = dict(zip(payload.headers, payload.rows[0], strict=True))
    assert expected in facts["carbon_rate"]
    assert "weighted by emissions rather than time" in facts["details"]
    assert "including year changes" in facts["details"]


def _debrief_data(mode, optimised_cost=100, credit=None, baseline=150):
    data = make_data(mode)
    rows = data["get_current_operating_costs"]
    rows["realised_economic_cost_aud"][0] = optimised_cost
    rows["comparison_economic_cost_aud"][2] = baseline
    if credit is not None:
        rows["estimated_terminal_balanced_financial_value_aud"][0] = credit
    return data


def test_negative_savings_are_labelled_additional_cost_with_both_outcomes():
    data = _debrief_data("Cost", optimised_cost=200)
    tile = translate(select_savings, data).rows[0]
    assert tile[0] == "$50.00 additional cost"
    assert "Without a battery: pay $150.00" in tile[1]
    assert "With the smart battery: pay $200.00" in tile[1]


def test_savings_say_earn_when_the_smart_battery_ends_in_credit():
    tile = translate(select_savings, _debrief_data("Cost", optimised_cost=-5)).rows[0]
    assert (
        tile[0] == "$155.00 saved" and "With the smart battery: earn $5.00" in tile[1]
    )
    assert "%" not in tile[0] + tile[1]


def test_foresight_gap_says_behind_or_ahead():
    data = _debrief_data("Cost")
    rows = data["get_current_operating_costs"]
    rows["realised_economic_cost_aud"][3] = 40
    assert translate(select_foresight_gap, data).rows[0][0] == "$60.00 behind"
    rows["realised_economic_cost_aud"][3] = 130
    assert translate(select_foresight_gap, data).rows[0][0] == "$30.00 ahead"


def test_balanced_savings_use_the_balanced_estimate_or_are_unavailable():
    available = translate(select_savings, _debrief_data("Balanced", credit=6))
    assert available.rows[0][0] == "$56.00 saved"
    assert (
        translate(select_terminal_value, _debrief_data("Balanced", credit=6)).rows[0][0]
        == "$6.00"
    )
    missing = translate(select_savings, _debrief_data("Balanced", credit=float("nan")))
    assert missing.rows[0][0] == NOT_AVAILABLE


def test_balanced_emissions_avoided_does_not_use_the_emissions_estimate():
    data = _debrief_data("Balanced")
    rows = data["get_current_operating_costs"]
    rows["estimated_terminal_emissions_value_tonnes"][0] = 999
    rows["estimated_terminal_balanced_emissions_value_tonnes"][0] = 0.1
    assert translate(select_emissions_avoided, data).rows[0][0] == "300 kg less"
    # More grid emissions than no battery reads as 'more', not a negative number
    rows["emissions_tonnes"][0] = 1.0
    assert translate(select_emissions_avoided, data).rows[0][0] == "300 kg more"


def test_cost_breakdown_stacks_bill_cost_and_wear_with_hindsight():
    data = make_data("Cost")
    rows = data["get_current_operating_costs"]
    rows["grid_import_aud"][0], rows["grid_export_aud"][0] = 70, 20
    rows["battery_degradation_aud"][0] = 5
    payload = translate(select_cost_breakdown, data)
    assert payload.x_labels == [
        "Optimised",
        "Reactive",
        "No battery (shared)",
        "Perfect foresight (hindsight)",
    ]
    bill, wear = payload.y_values.values()
    assert (bill[0], wear[0]) == (50, 5)


def test_run_settings_card_lists_houses_and_battery_under_the_goal():
    data = make_data("Balanced")
    data["get_all_simulation_requests"]["battery_config_id"][0] = "bat"
    data["get_all_battery_configs"] = records(
        "get_all_battery_configs",
        [{"battery_config_id": "bat", "capacity_kwh": 400, "max_power_kw": 100}],
    )
    data["get_current_run_houses"]["house_config_id"][0] = "house-a"
    payload = translate(select_run_settings, data)
    facts = dict(zip(payload.headers, payload.rows[0], strict=True))
    assert facts["goal"] == "Run settings" and "Balanced goal" in facts["explanation"]
    assert facts["note"] == "Battery: 400 kWh · 100 kW max power"
    assert (
        "2 × house-a" in facts["details"] and "Capacity (kWh): 400" in facts["details"]
    )
    assert facts["result"] == "" and facts["financial"] == ""


def test_run_totals_are_whole_run_energy_not_power():
    data = make_data("Cost")
    # One 30-minute step at 8 kW demand and 4 kW import is 4 kWh and 2 kWh
    assert translate(select_total_demand, data).rows[0][0] == "4 kWh"
    assert translate(select_total_grid_import, data).rows[0][0] == "2 kWh"


@pytest.mark.parametrize("mode", ["Cost", "Emissions"])
def test_nonbalanced_breakdown_keeps_financial_components(mode):
    payload = translate(select_cost_breakdown, make_data(mode))
    assert len(payload.y_values) == 2
    assert payload.title == "Economic operating cost"
    assert payload.y_label == "AUD"


@pytest.mark.parametrize("credit", [4, 0, -4])
def test_balanced_breakdown_subtracts_matching_terminal_credit(credit):
    data = make_data("Balanced")
    rows = data["get_current_operating_costs"]
    rows["grid_import_aud"][:3] = [0, 80, 40]
    rows["grid_export_aud"][:3] = [100, 20, 10]
    rows["battery_degradation_aud"][:3] = [90, 5, 0]
    rows["emissions_cost_aud"][:3] = [22, 25, 30]
    rows["realised_economic_cost_aud"][:3] = [-10, 65, 30]
    rows["estimated_terminal_balanced_value_aud"][0] = credit
    rows["estimated_terminal_balanced_value_aud"][1:] = 999
    rows["estimated_terminal_cost_value_aud"][0] = 999
    financial_credit = credit * 0.6
    rows["estimated_terminal_balanced_financial_value_aud"][0] = financial_credit
    payload = translate(select_cost_breakdown, data)
    assert payload.title == "Balanced operating score"
    assert payload.y_label == "AUD equivalent"
    assert payload.x_labels == [
        "Optimised",
        "Reactive",
        "No battery (shared)",
        "Perfect foresight (hindsight)",
    ]
    np.testing.assert_allclose(
        list(payload.y_values.values()),
        [
            [-100 - financial_credit, 60, 30, 0],
            [90, 5, 0, 0],
            [22 - (credit - financial_credit), 25, 30, 0],
        ],
    )
    np.testing.assert_allclose(
        np.sum(list(payload.y_values.values()), axis=0), [12 - credit, 90, 60, 0]
    )
    community = translate(select_community_goal_comparison, data)
    np.testing.assert_allclose(
        np.sum(list(payload.y_values.values()), axis=0)[:3],
        next(iter(community.y_values.values())),
    )
    assert payload.subtitle == "Terminal-adjusted estimates · lower is better"
    assert "remaining-energy" in payload.detail
    assert len(payload.y_values) == 3
    assert not any("adjustment" in label.lower() for label in payload.y_values)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_balanced_breakdown_omits_missing_carbon_instead_of_showing_zero(invalid):
    data = make_data("Balanced")
    data["get_current_operating_costs"]["emissions_cost_aud"][0] = invalid
    payload = translate(select_cost_breakdown, data)
    assert payload.x_labels == [
        "Reactive",
        "No battery (shared)",
        "Perfect foresight (hindsight)",
    ]
    assert "Methods with unavailable values are omitted" in payload.detail


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_balanced_breakdown_omits_missing_terminal_estimate(invalid):
    data = make_data("Balanced")
    rows = data["get_current_operating_costs"]
    rows["estimated_terminal_balanced_value_aud"][0] = invalid
    rows["estimated_terminal_cost_value_aud"][0] = 10
    rows["estimated_terminal_balanced_financial_value_aud"][0] = 5
    payload = translate(select_cost_breakdown, data)
    assert payload.x_labels == [
        "Reactive",
        "No battery (shared)",
        "Perfect foresight (hindsight)",
    ]
    assert "Methods with unavailable values are omitted" in payload.detail
    assert translate(select_foresight_gap, data).rows[0][0] == NOT_AVAILABLE


def test_balanced_breakdown_total_matches_foresight_tile_for_reported_example():
    data = make_data("Balanced")
    rows = data["get_current_operating_costs"]
    rows["grid_import_aud"][0] = 768.03
    rows["grid_export_aud"][0] = 0
    rows["battery_degradation_aud"][0] = 127.66
    rows["realised_economic_cost_aud"][0] = 895.69
    rows["emissions_cost_aud"][0] = 354.81
    rows["estimated_terminal_balanced_value_aud"][0] = 1.42
    rows["estimated_terminal_balanced_financial_value_aud"][0] = 1.00
    payload = translate(select_cost_breakdown, data)
    total = np.sum(list(payload.y_values.values()), axis=0)[0]
    assert total == pytest.approx(1249.08)
    bill, wear, carbon = list(payload.y_values.values())
    assert [bill[0], wear[0], carbon[0]] == pytest.approx([767.03, 127.66, 354.39])
    assert translate(select_community_financial_cost, data).rows[0][0] == "$894.69"
    assert bill[0] + wear[0] == pytest.approx(894.69)
    assert (
        f"Smart battery: ${total:,.2f}"
        in translate(select_foresight_gap, data).rows[0][1]
    )


@pytest.mark.parametrize("invalid", [float("nan"), float("inf")])
def test_balanced_breakdown_requires_matching_financial_terminal_component(invalid):
    data = make_data("Balanced")
    rows = data["get_current_operating_costs"]
    rows["estimated_terminal_balanced_financial_value_aud"][0] = invalid
    rows["estimated_terminal_cost_value_aud"][0] = 10
    assert translate(select_cost_breakdown, data).x_labels == [
        "Reactive",
        "No battery (shared)",
        "Perfect foresight (hindsight)",
    ]


@pytest.mark.parametrize(
    "mode,field,credit_field,credit,foresight,expected,metric",
    [
        (
            "Cost",
            "realised_economic_cost_aud",
            "estimated_terminal_cost_value_aud",
            10,
            80,
            "$10.00 behind",
            "Financial cost (AUD)",
        ),
        (
            "Emissions",
            "emissions_tonnes",
            "estimated_terminal_emissions_value_tonnes",
            0.1,
            0.2,
            "100.0 kg behind",
            "Grid emissions (kg CO₂e)",
        ),
        (
            "Balanced",
            "realised_economic_cost_aud",
            "estimated_terminal_balanced_value_aud",
            20,
            120,
            "$10.00 behind",
            "Balanced score (AUD equivalent)",
        ),
    ],
)
def test_foresight_uses_selected_objective_and_matching_terminal_estimate(
    mode, field, credit_field, credit, foresight, expected, metric
):
    data = make_data(mode)
    rows = data["get_current_operating_costs"]
    # Unrelated terminal estimates and the old comparison cannot decide the gap.
    for name in (
        "estimated_terminal_cost_value_aud",
        "estimated_terminal_emissions_value_tonnes",
        "estimated_terminal_balanced_value_aud",
        "estimated_terminal_balanced_financial_value_aud",
    ):
        rows[name][0] = 999
        rows[name][3] = float("nan")  # Hindsight receives no terminal credit.
    rows[credit_field][0] = credit
    rows[field][3] = foresight
    rows["emissions_cost_aud"][[0, 3]] = [60, 10]
    value, detail = translate(select_foresight_gap, data).rows[0]
    assert value == expected
    assert metric in detail and "terminal-adjusted" in detail
    assert "pay" not in detail and "earn" not in detail


@pytest.mark.parametrize(
    "mode,field",
    [
        ("Cost", "estimated_terminal_cost_value_aud"),
        ("Emissions", "estimated_terminal_emissions_value_tonnes"),
        ("Balanced", "estimated_terminal_balanced_value_aud"),
    ],
)
@pytest.mark.parametrize("invalid", [float("nan"), float("inf")])
def test_foresight_requires_matching_terminal_estimate(mode, field, invalid):
    data = make_data(mode)
    data["get_current_operating_costs"][field][0] = invalid
    assert translate(select_foresight_gap, data).rows[0][0] == NOT_AVAILABLE


@pytest.mark.parametrize(
    "mode,field,equal,ahead,expected",
    [
        ("Cost", "realised_economic_cost_aud", 100, 130, "$30.00 ahead"),
        ("Emissions", "emissions_tonnes", 0.4, 0.5, "100.0 kg ahead"),
        (
            "Balanced",
            "realised_economic_cost_aud",
            120,
            130,
            "$10.00 ahead",
        ),
    ],
)
def test_foresight_equal_and_ahead_use_goal_units(mode, field, equal, ahead, expected):
    data = make_data(mode)
    rows = data["get_current_operating_costs"]
    rows[field][3] = equal
    assert translate(select_foresight_gap, data).rows[0][0] == "Level"
    rows[field][3] = ahead
    assert translate(select_foresight_gap, data).rows[0][0] == expected


def test_foresight_unknown_goal_and_previous_run_data_are_unavailable():
    assert (
        translate(select_foresight_gap, make_data("Unknown")).rows[0][0]
        == NOT_AVAILABLE
    )
    data = make_data()
    data["get_current_operating_costs"]["request_id"][3] = "previous-run"
    assert translate(select_foresight_gap, data).rows[0][0] == NOT_AVAILABLE


@pytest.mark.parametrize("mode", ["Cost", "Emissions"])
@pytest.mark.parametrize("credit", [1.15, 0.0, -1.15])
def test_financial_breakdown_matches_community_financial_basis(mode, credit):
    data = make_data(mode)
    rows = data["get_current_operating_costs"]
    rows["grid_import_aud"][:3] = [650.45, 673.31, 1205.74]
    rows["grid_export_aud"][:3] = 0
    rows["battery_degradation_aud"][:3] = [127.44, 128.10, 0]
    rows["realised_economic_cost_aud"][:3] = [777.89, 801.41, 1205.74]
    rows["estimated_terminal_cost_value_aud"][0] = credit
    rows["estimated_terminal_cost_value_aud"][1:] = 999
    rows["estimated_terminal_balanced_value_aud"][0] = 999
    payload = translate(select_cost_breakdown, data)
    totals = np.sum(list(payload.y_values.values()), axis=0)
    np.testing.assert_allclose(totals, [777.89 - credit, 801.41, 1205.74, 0])
    assert (
        translate(select_community_financial_cost, data).rows[0][0]
        == f"${totals[0]:,.2f}"
    )
    assert payload.subtitle == "Terminal-adjusted estimates · lower is better"
    assert len(payload.y_values) == 2
    assert not any("adjustment" in label.lower() for label in payload.y_values)
    if mode == "Cost":
        comparison = translate(select_community_goal_comparison, data)
        np.testing.assert_allclose(totals[:3], next(iter(comparison.y_values.values())))
        if credit == 1.15:
            assert totals[0] == pytest.approx(776.74)
            assert totals[1] - totals[0] == pytest.approx(24.67)
            assert "$24.67 lower financial cost" in comparison.detail


@pytest.mark.parametrize("mode", ["Cost", "Emissions"])
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_financial_breakdown_omits_missing_financial_estimate(mode, invalid):
    data = make_data(mode)
    rows = data["get_current_operating_costs"]
    rows["estimated_terminal_cost_value_aud"][0] = invalid
    rows["estimated_terminal_balanced_financial_value_aud"][0] = 5
    payload = translate(select_cost_breakdown, data)
    assert payload.x_labels == [
        "Reactive",
        "No battery (shared)",
        "Perfect foresight (hindsight)",
    ]
    assert "Methods with unavailable values are omitted" in payload.detail
    assert translate(select_community_financial_cost, data).rows[0][0] == NOT_AVAILABLE


@pytest.mark.parametrize("mode", ["Cost", "Balanced", "Emissions"])
@pytest.mark.parametrize("unused_credit", [999.0, float("nan")])
def test_breakdown_includes_unadjusted_reactive_and_hindsight(mode, unused_credit):
    data = make_data(mode)
    rows = data["get_current_operating_costs"]
    rows["grid_import_aud"][:4] = [95, 123, 150, 86]
    rows["grid_export_aud"][:4] = 0
    rows["battery_degradation_aud"][:4] = [5, 7, 0, 4]
    rows["realised_economic_cost_aud"][:4] = [100, 130, 150, 90]
    rows["emissions_cost_aud"][:4] = [20, 25, 30, 10]
    rows["emissions_tonnes"][3] = 0.2
    rows["estimated_terminal_cost_value_aud"][0] = 10
    rows["estimated_terminal_balanced_value_aud"][0] = 15
    rows["estimated_terminal_balanced_financial_value_aud"][0] = 9
    # Neither comparator receives any of its stored terminal estimates.
    for field in (
        "estimated_terminal_cost_value_aud",
        "estimated_terminal_balanced_value_aud",
        "estimated_terminal_balanced_financial_value_aud",
    ):
        rows[field][[1, 3]] = unused_credit
    payload = translate(select_cost_breakdown, data)
    assert payload.x_labels == [
        "Optimised",
        "Reactive",
        "No battery (shared)",
        "Perfect foresight (hindsight)",
    ]
    totals = np.sum(list(payload.y_values.values()), axis=0)
    expected = [105, 155, 180, 100] if mode == "Balanced" else [90, 130, 150, 90]
    np.testing.assert_allclose(totals, expected)
    bill, wear = list(payload.y_values.values())[:2]
    assert (bill[1], wear[1], bill[3], wear[3]) == (123, 7, 86, 4)
    if mode in ("Cost", "Balanced"):
        detail = translate(select_foresight_gap, data).rows[0][1]
        assert f"Perfect foresight: ${totals[3]:,.2f}" in detail
        community = translate(select_community_goal_comparison, data)
        np.testing.assert_allclose(totals[:3], next(iter(community.y_values.values())))
        assert len(community.x_labels) == 3
    assert "Only the smart battery receives an estimated remaining-energy credit" in (
        payload.detail
    )
    assert "below perfect foresight" in payload.detail


@pytest.mark.parametrize("mode", ["Cost", "Balanced", "Emissions"])
def test_missing_hindsight_does_not_create_a_zero_bar(mode):
    data = make_data(mode)
    rows = data["get_current_operating_costs"]
    data["get_current_operating_costs"] = rows[
        rows["scenario_name"] != "perfect_foresight"
    ]
    payload = translate(select_cost_breakdown, data)
    assert payload.x_labels == ["Optimised", "Reactive", "No battery (shared)"]
    assert all(len(values) == 3 for values in payload.y_values.values())


@pytest.mark.parametrize(
    "field",
    [
        "grid_import_aud",
        "grid_export_aud",
        "battery_degradation_aud",
        "emissions_cost_aud",
    ],
)
def test_hindsight_with_missing_required_component_is_omitted(field):
    data = make_data("Balanced")
    data["get_current_operating_costs"][field][3] = float("nan")
    assert (
        "Perfect foresight (hindsight)"
        not in translate(select_cost_breakdown, data).x_labels
    )

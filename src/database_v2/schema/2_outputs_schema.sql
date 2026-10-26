-- Output diagnostics? e.g. shadow, controller reasons?
-- 30 minute outputs too?
-- Reference models outputs?

CREATE TABLE IF NOT EXISTS optimiser_output_30m (
    request_id CHAR(16) NOT NULL,
    timestamp_utc CHAR(19) NOT NULL,

    soc_kwh REAL NOT NULL,
    charge_kw REAL NOT NULL,
    discharge_kw REAL NOT NULL,
    grid_import_kw REAL NOT NULL,
    grid_export_kw REAL NOT NULL,
    curtailed_pv_kw REAL NOT NULL,

    PRIMARY KEY (request_id, timestamp_utc),
    FOREIGN KEY (request_id) REFERENCES simulation_request(request_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_optimiser_output_30m
ON optimiser_output_30m(timestamp_utc, request_id);

CREATE TABLE IF NOT EXISTS live_controller_output_5m (
    request_id CHAR(16) NOT NULL,
    scenario_name VARCHAR(50) NOT NULL
    CHECK (scenario_name IN (
        'optimised_battery',
        'reactive_battery',
        'perfect_foresight'
    )),
    timestamp_utc CHAR(19) NOT NULL,

    soc_kwh REAL NOT NULL,
    charge_kw REAL NOT NULL,
    discharge_kw REAL NOT NULL,
    grid_import_kw REAL NOT NULL,
    grid_export_kw REAL NOT NULL,
    curtailed_pv_kw REAL NOT NULL,
    cycle_throughput_kwh REAL NOT NULL,
    reasons TEXT,

    PRIMARY KEY (request_id, scenario_name, timestamp_utc),
    FOREIGN KEY (request_id) REFERENCES simulation_request(request_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_live_controller_output_5m
ON live_controller_output_5m(timestamp_utc, scenario_name, request_id);



CREATE TABLE IF NOT EXISTS simulation_summary (
    request_id CHAR(16) NOT NULL,
    scenario_name VARCHAR(50) NOT NULL
    CHECK (scenario_name IN (
        'optimised_battery',
        'reactive_battery',
        'no_battery_shared',
        'no_battery_individual',
        'perfect_foresight'
    )),

    battery_degradation_aud REAL NOT NULL,
    grid_import_aud REAL NOT NULL,
    grid_export_aud REAL NOT NULL,
    emissions_tonnes REAL NOT NULL,
    emissions_cost_aud REAL NOT NULL DEFAULT 0,
    terminal_soc_kwh REAL,
    estimated_terminal_cost_value_aud REAL,
    estimated_terminal_emissions_value_tonnes REAL,
    terminal_value_horizon_hours REAL,

    PRIMARY KEY (request_id, scenario_name),
    FOREIGN KEY (request_id) REFERENCES simulation_request(request_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_simulation_summary
ON simulation_summary(request_id);

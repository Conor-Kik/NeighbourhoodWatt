CREATE TABLE IF NOT EXISTS realised_demand_5m (
    timestamp_utc CHAR(19) NOT NULL,
    house_config_id VARCHAR(50) NOT NULL,

    demand_kw REAL NOT NULL,

    PRIMARY KEY (house_config_id, timestamp_utc),
    FOREIGN KEY (house_config_id) REFERENCES house_config(house_config_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_realised_demand
ON realised_demand_5m(house_config_id, timestamp_utc);

CREATE TABLE IF NOT EXISTS realised_pv_5m (
    timestamp_utc CHAR(19) NOT NULL,
    house_config_id VARCHAR(50) NOT NULL,
    location_name VARCHAR(50) NOT NULL,

    pv_kw REAL NOT NULL,

    PRIMARY KEY (house_config_id, location_name, timestamp_utc),
    FOREIGN KEY (house_config_id) REFERENCES house_config(house_config_id) ON DELETE CASCADE,
    FOREIGN KEY (location_name) REFERENCES location(location_name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_realised_pv
ON realised_pv_5m(house_config_id, location_name, timestamp_utc);

CREATE TABLE IF NOT EXISTS realised_pricing_5m (
    timestamp_utc CHAR(19) NOT NULL,
    location_name VARCHAR(50) NOT NULL,

    buy_tariff_aud REAL NOT NULL,
    sell_tariff_aud REAL NOT NULL,

    PRIMARY KEY (location_name, timestamp_utc),
    FOREIGN KEY (location_name) REFERENCES location(location_name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_realised_pricing
ON realised_pricing_5m(location_name, timestamp_utc);

CREATE TABLE IF NOT EXISTS realised_emissions_5m (
    timestamp_utc CHAR(19) NOT NULL,
    location_name VARCHAR(50) NOT NUll,

    carbon_intensity_t_per_mwh REAL NOT NULL,

    PRIMARY KEY (location_name, timestamp_utc),
    FOREIGN KEY (location_name) REFERENCES location(location_name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_realised_emissions
ON realised_emissions_5m(location_name, timestamp_utc);

CREATE TABLE IF NOT EXISTS forecast_demand_30m (
    run_time_utc CHAR(19) NOT NULL,
    target_time_utc CHAR(19) NOT NULL,
    house_config_id VARCHAR(50) NOT NULL,

    demand_kw REAL NOT NULL,

    PRIMARY KEY (house_config_id, run_time_utc, target_time_utc),
    FOREIGN KEY (house_config_id) REFERENCES house_config(house_config_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_forecast_demand
ON forecast_demand_30m(house_config_id, run_time_utc, target_time_utc);

CREATE TABLE IF NOT EXISTS forecast_pv_cycle_30m (
    available_at_utc CHAR(19) NOT NULL,
    target_time_utc CHAR(19) NOT NULL,
    house_config_id VARCHAR(50) NOT NULL,
    location_name VARCHAR(50) NOT NULL,

    pv_kw REAL NOT NULL,

    PRIMARY KEY (house_config_id, location_name, available_at_utc, target_time_utc),
    FOREIGN KEY (house_config_id) REFERENCES house_config(house_config_id) ON DELETE CASCADE,
    FOREIGN KEY (location_name) REFERENCES location(location_name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_forecast_pv_cycle_30m
ON forecast_pv_cycle_30m(house_config_id, location_name, available_at_utc, target_time_utc);

CREATE TABLE IF NOT EXISTS forecast_pricing_30m (
    run_time_utc CHAR(19) NOT NULL,
    target_time_utc CHAR(19) NOT NULL,
    location_name VARCHAR(50) NOT NULL,

    buy_tariff_aud REAL NOT NULL,
    sell_tariff_aud REAL NOT NULL,
    forecast_source TEXT NOT NULL DEFAULT 'unknown'
        CHECK (forecast_source IN ('aemo', 'historical', 'unknown')),

    PRIMARY KEY (location_name, run_time_utc, target_time_utc),
    FOREIGN KEY (location_name) REFERENCES location(location_name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_forecast_pricing
ON forecast_pricing_30m(location_name, run_time_utc, target_time_utc);

CREATE TABLE IF NOT EXISTS forecast_emissions_30m (
    run_time_utc CHAR(19) NOT NULL,
    target_time_utc CHAR(19) NOT NULL,
    location_name VARCHAR(50) NOT NUll,

    carbon_intensity_t_per_mwh REAL NOT NULL,

    PRIMARY KEY (location_name, run_time_utc, target_time_utc),
    FOREIGN KEY (location_name) REFERENCES location(location_name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_forecast_emissions
ON forecast_emissions_30m(location_name, run_time_utc, target_time_utc);

CREATE TABLE IF NOT EXISTS carbon_price (
    calendar_year INT PRIMARY KEY,
    carbon_price_aud_per_t REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_openmeteo (
    timestamp_utc CHAR(19) NOT NULL,
    location_name VARCHAR(50) NOT NULL,
    ghi FLOAT,
    dhi FLOAT,
    dni FLOAT,
    air_temperature FLOAT,
    wind_speed FLOAT,
    PRIMARY KEY (location_name, timestamp_utc),
    FOREIGN KEY (location_name) REFERENCES location(location_name)
);

CREATE INDEX IF NOT EXISTS idx_openmeteo
ON raw_openmeteo(location_name, timestamp_utc);

CREATE TABLE IF NOT EXISTS raw_ecmwf (
    timestamp_utc CHAR(19) NOT NULL,
    available_at_utc CHAR(19) NOT NULL,
    location_name VARCHAR(50) NOT NULL,
    ghi FLOAT,
    air_temperature FLOAT,
    wind_speed FLOAT,
    PRIMARY KEY (location_name, timestamp_utc, available_at_utc),
    FOREIGN KEY (location_name) REFERENCES location(location_name)
);

CREATE INDEX IF NOT EXISTS idx_ecmwf
ON raw_ecmwf(location_name, timestamp_utc);

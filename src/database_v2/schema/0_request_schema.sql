CREATE TABLE IF NOT EXISTS location (
    location_name VARCHAR(50) PRIMARY KEY,
    latitude FLOAT NOT NULL,
    longitude FLOAT NOT NULL,
    timezone VARCHAR(50) NOT NULL,
    nem_region CHAR(4) NOT NULL
);

CREATE TABLE IF NOT EXISTS battery_config (
    battery_config_id VARCHAR(50) PRIMARY KEY,
    capacity_kwh REAL NOT NULL,
    max_power_kw REAL NOT NULL,
    efficiency REAL NOT NULL,
    min_soc_frac REAL NOT NULL,
    max_soc_frac REAL NOT NULL,
    replacement_cost_aud REAL NOT NULL,
    lifetime_cycles INT NOT NULL,
    max_cycles_24h REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS house_config (
    house_config_id VARCHAR(50) PRIMARY KEY,

    pv_peak_capacity FLOAT,  -- kW
    panel_tilt FLOAT,  -- degrees, 0-90 from horizontal
    panel_azimuth FLOAT,   -- degrees, 0-360 compass bearing

    pv_inverter_ac_kw FLOAT,
    pv_inverter_efficiency FLOAT,

    -- variables used to get load profiles.
    has_aircon BOOLEAN,
    has_poolpump BOOLEAN,
    has_ev BOOLEAN
);

CREATE TABLE IF NOT EXISTS simulation_request (
    request_id CHAR(16) PRIMARY KEY,
    objective_mode VARCHAR(50) NOT NULL,
    location_name VARCHAR(50) NOT NULL,
    start_time_utc CHAR(19) NOT NULL,
    end_time_utc CHAR(19) NOT NULL,
    battery_config_id VARCHAR(50) NOT NULL,
    fixed_buy_aud_per_kwh REAL,   -- NULL = AEMO market price
    fixed_sell_aud_per_kwh REAL,  -- NULL = AEMO market price

    FOREIGN KEY (location_name) REFERENCES location(location_name),
    FOREIGN KEY (battery_config_id) REFERENCES battery_config(battery_config_id)
);

CREATE TABLE IF NOT EXISTS simulation_house (
    request_id CHAR(16) NOT NULL,
    house_config_id VARCHAR(50) NOT NULL,
    quantity INT NOT NULL,

    PRIMARY KEY (request_id, house_config_id),
    FOREIGN KEY (request_id) REFERENCES simulation_request(request_id) ON DELETE CASCADE,
    FOREIGN KEY (house_config_id) REFERENCES house_config(house_config_id)
);

-- Sourced from https://simplemaps.com/data/au-cities
INSERT OR IGNORE INTO location (
    location_name,
    latitude,
    longitude,
    timezone,
    nem_region
) VALUES
('Brisbane', -27.4678, 153.0278, 'Australia/Brisbane', 'QLD1'),
('Sydney', -33.8678, 151.2100, 'Australia/Sydney', 'NSW1'),
('Melbourne', -37.8142, 144.9631, 'Australia/Melbourne', 'VIC1'),
('Adelaide', -34.9275, 138.600, 'Australia/Adelaide', 'SA1'),
('Hobart', -42.8833, 147.3167, 'Australia/Hobart', 'TAS1');

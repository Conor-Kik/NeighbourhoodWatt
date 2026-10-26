"""Sets the planning and live-control intervals used by the battery simulation."""

OPTIMISER_PERIOD_HOURS = 0.5
LIVE_CONTROL_STEP_MINUTES = 5.0
PERIODS_PER_DAY = int(24.0 / OPTIMISER_PERIOD_HOURS)

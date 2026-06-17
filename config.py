"""Shared configuration for the Smart Beverage Pasteurization & Bottling Line digital twin.

Every module (M1-M5) imports constants from here so that pin names, set-points,
fault codes and alarm codes stay consistent across the whole system.
"""

from __future__ import annotations

# Load variables from a local .env file if python-dotenv is available, so that
# ANTHROPIC_API_KEY / USE_MQTT are picked up without exporting them manually.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# 1. Global timing
# ---------------------------------------------------------------------------
# The whole system advances one "tick" per update period. The README assumes 1 s.
# For a live demo you can speed the wall-clock loop up with TICK_INTERVAL_S while
# keeping the simulated period at 1 s.
UPDATE_PERIOD_S = 1.0          # simulated time represented by one tick
TICK_INTERVAL_S = 1.0         # real seconds between ticks in the background loop

# ---------------------------------------------------------------------------
# 2. Process set-points and physical limits (M1 / M2 share these)
# ---------------------------------------------------------------------------
TANK_LEVEL_LOW = 30.0          # %  -> open inlet valve below this
TANK_LEVEL_HIGH = 80.0         # %  -> close inlet valve above this
TANK_LEVEL_MIN_PUMP = 10.0     # %  -> do not run feed pump below this (dry-run guard)

PASTEUR_SETPOINT = 72.0        # degC target pasteurization temperature
PASTEUR_SAFE_MIN = 68.0        # degC lower safe bound
PASTEUR_SAFE_MAX = 78.0        # degC upper safe bound

COOLER_SETPOINT = 20.0         # degC target product temperature after cooling
COOLER_OPEN_ABOVE = 25.0       # degC open cooling valve above this

FILL_DURATION_TICKS = 3        # ticks the fill valve stays open per bottle
AMBIENT_TEMP = 25.0            # degC ambient temperature

# Number of consecutive abnormal ticks before the PLC latches an alarm.
ALARM_DEBOUNCE_TICKS = 3

# ---------------------------------------------------------------------------
# 3. Fault injection codes (Dashboard -> Plant Simulator)
# ---------------------------------------------------------------------------
FAULT_NONE = 0
FAULT_TEMP_STUCK = 1           # pasteur_temp sensor frozen
FAULT_PUMP_FAIL = 2            # pump runs but no flow / no feedback
FAULT_TEMP_EXCURSION = 3       # pasteurization temperature drifts out of range
FAULT_MQTT_STALE = 4           # data layer stops refreshing -> stale data

FAULT_LABELS = {
    FAULT_NONE: "Normal",
    FAULT_TEMP_STUCK: "Temperature sensor stuck",
    FAULT_PUMP_FAIL: "Feed pump failure (no flow)",
    FAULT_TEMP_EXCURSION: "Pasteurization temperature excursion",
    FAULT_MQTT_STALE: "Data link stale (MQTT)",
}

# ---------------------------------------------------------------------------
# 4. Alarm codes (PLC Controller -> Dashboard / AI)
# ---------------------------------------------------------------------------
ALARM_NONE = 0
ALARM_SENSOR_TEMP_STUCK = 10
ALARM_PUMP_NO_FLOW = 20
ALARM_TEMP_OUT_OF_RANGE = 30
ALARM_DATA_STALE = 40

ALARM_LABELS = {
    ALARM_NONE: "No alarm",
    ALARM_SENSOR_TEMP_STUCK: "SENSOR_TEMP_STUCK",
    ALARM_PUMP_NO_FLOW: "PUMP_NO_FLOW",
    ALARM_TEMP_OUT_OF_RANGE: "TEMP_OUT_OF_RANGE",
    ALARM_DATA_STALE: "DATA_STALE",
}

# Human readable, operator-facing alarm descriptions (used by M4 and as a hint to M5).
ALARM_DESCRIPTIONS = {
    ALARM_NONE: "All process values are within normal range.",
    ALARM_SENSOR_TEMP_STUCK: (
        "Pasteurization temperature reading is frozen while the heater command "
        "is changing. The temperature sensor is likely faulty."
    ),
    ALARM_PUMP_NO_FLOW: (
        "The feed pump is commanded ON but there is no flow and no pump feedback. "
        "The pump or its drive has likely failed."
    ),
    ALARM_TEMP_OUT_OF_RANGE: (
        "Pasteurization temperature is outside the safe range "
        f"({PASTEUR_SAFE_MIN}-{PASTEUR_SAFE_MAX} degC) for several cycles. "
        "Product safety may be compromised."
    ),
    ALARM_DATA_STALE: (
        "Live data from the plant has stopped updating. The MQTT data link or "
        "publisher may be down."
    ),
}

# ---------------------------------------------------------------------------
# 5. PLC state-machine states
# ---------------------------------------------------------------------------
PLC_IDLE = "IDLE"
PLC_STARTING = "STARTING"
PLC_RUNNING = "RUNNING"
PLC_FAULT = "FAULT"
PLC_STOPPING = "STOPPING"

# ---------------------------------------------------------------------------
# 6. Production stages (for display)
# ---------------------------------------------------------------------------
STAGE_NAMES = {
    "S1": "Raw / Balance Tank",
    "S2": "Pasteurizer",
    "S3": "Cooler",
    "S4": "Filler",
    "S5": "Capper / Conveyor",
}

# ---------------------------------------------------------------------------
# 7. Tag names published to the data layer (M3)
# ---------------------------------------------------------------------------
# Numeric tags that are stored as dedicated columns in the historian and used
# for trend charts on the dashboard.
NUMERIC_TAGS = [
    "tank_level",
    "pasteur_temp",
    "cooler_temp",
    "flow_rate",
    "bottle_count",
    "heater_power_cmd",
]

# ---------------------------------------------------------------------------
# 8. MQTT / data-layer settings (M3) and AI settings (M5)
# ---------------------------------------------------------------------------
MQTT_HOST = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC_TAGS = "btl/tags"          # plant + control + alarm tags snapshot
MQTT_TOPIC_CMD = "btl/cmd"            # operator commands from dashboard
DATA_STALE_TIMEOUT_S = 5.0            # mark data stale if no update within this window

DB_PATH = "historian.db"              # SQLite historian file
CSV_EXPORT_PATH = "history_export.csv"
HISTORY_WINDOW_S = 300                # default trend window shown on the dashboard

# Anthropic / Claude settings for the AI assistant (M5).
# The API key is read from the ANTHROPIC_API_KEY environment variable; if it is
# missing the assistant automatically falls back to a built-in rule-based engine.
ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
ANTHROPIC_MAX_TOKENS = 400

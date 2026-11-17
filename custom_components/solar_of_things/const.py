"""Constants for the Solar of Things integration."""

DOMAIN = "solar_of_things"

# ─── Configuration keys ────────────────────────────────────────────────────────
CONF_IOT_TOKEN = "iot_token"  # legacy / advanced manual entry
CONF_STATION_ID = "station_id"
CONF_DEVICE_ID = "device_id"
CONF_TIME_ZONE = "time_zone"

# Credential-based auth (preferred)
CONF_USER_ID = "user_id"  # Siseli account / user-ID login (not email)
CONF_PASSWORD = "password"

# Runtime-stored token state (written back to config entry)
CONF_REFRESH_TOKEN = "refresh_token"
CONF_ACCESS_TOKEN_EXPIRES = "access_token_expires"  # ISO-8601 string
CONF_REFRESH_TOKEN_EXPIRES = "refresh_token_expires"  # ISO-8601 string

# ─── API bases ─────────────────────────────────────────────────────────────────
# Both auth and data endpoints live on the production server solar.siseli.com.
# The portal JS bundle embeds both test/prod AppIDs; AppID rBrTRfAPXz is the
# one accepted by solar.siseli.com (confirmed by live API testing 2026-03-07).
API_BASE_URL = "https://solar.siseli.com"  # data endpoints
API_AUTH_BASE_URL = "https://solar.siseli.com"  # auth / login endpoints

# ─── Auth endpoints (discovered from portal JS bundle) ─────────────────────────
# The login endpoint requires IOT-Open-AppID signing (see api.py _sign_request).
API_LOGIN = "/apis/login/account"  # POST + signed headers
API_REFRESH_TOKEN = "/apis/login/refresh/access/token"  # POST

# ─── IOT Open Platform app credentials (embedded in portal umi.js) ────────────
# rBrTRfAPXz is the production AppID accepted by solar.siseli.com.
# JO4DAiNeys is the test AppID (accepted only by test.solar.siseli.com).
IOT_APP_ID = "rBrTRfAPXz"
IOT_APP_SECRET_ENC = "I4D0KRr2339z3pQ/at91V9BpFAOe54DaTafwSm6suIQ="

# ─── Data endpoints ────────────────────────────────────────────────────────────
API_TIME_SERIES = "/apis/deviceState/simple/attribute/keys/history/v1"
API_MONTHLY_SUMMARY = "/apis/stationOverView/stateAttributeSummary/category/yearly"
API_LATEST_STATE = "/apis/deviceState/simple/state/latest/v1"
# Remote device config endpoints (discovered 2026-03-07 from live API testing).
# These accept a plain IOT-Token header (no IOT-Open-Sign) and use the device ID
# as a query parameter.  Write sends one setting key+value per call.
API_SETTINGS_GET = "/apis/remote/device/configs/cache/get"  # ?deviceId=<id>
API_SETTINGS_SET = "/apis/remote/device/config/write"  # ?deviceId=<id>
API_DEVICE_LIST = "/apis/device/list"

# ─── Token refresh window ──────────────────────────────────────────────────────
# Refresh the access token this many seconds *before* its stated expiry.
# Mirrors the portal JS which refreshes when ≤300 s remain.
TOKEN_REFRESH_LEAD_SECONDS = 300  # 5 minutes

# ─── Sensor keys ───────────────────────────────────────────────────────────────
SENSOR_KEYS = [
    "pvInputPower",
    "pv2InputPower",
    "totalPvPower",
    "totalPvEnergy",
    "acOutputActivePower",
    "acOutputEnergy",
    "batteryDischargeCurrent",
    "batteryChargingCurrent",
    "batteryVoltage",
    "feedInPower",
    "batteryPower",
    "batteryChargingPower",
    "batteryChargingEnergy",
    "batteryDischargePower",
    "batteryDischargeEnergy",
    "batterySOC",
    "gridPower",
    "loadPower",
]

SENSOR_DEFINITIONS = {
    "pvInputPower": {
        "name": "PV Input Power",
        "unit": "W",
        "device_class": "power",
        "icon": "mdi:solar-power",
    },
    "pv2InputPower": {
        "name": "PV2 Input Power",
        "unit": "W",
        "device_class": "power",
        "icon": "mdi:solar-power",
    },
    "totalPvPower": {
        "name": "Total PV Input Power",
        "unit": "W",
        "device_class": "power",
        "icon": "mdi:solar-power-variant",
    },
    "totalPvEnergy": {
        "name": "Total PV Input Energy",
        "unit": "kWh",
        "device_class": "energy",
        "icon": "mdi:solar-power-variant",
    },
    "acOutputActivePower": {
        "name": "AC Output Power",
        "unit": "W",
        "device_class": "power",
        "icon": "mdi:power-plug",
    },
    "acOutputEnergy": {
        "name": "AC Output Energy",
        "unit": "kWh",
        "device_class": "energy",
        "icon": "mdi:lightning-bolt-circle",
    },
    "batteryDischargeCurrent": {
        "name": "Battery Discharge Current",
        "unit": "A",
        "device_class": "current",
        "icon": "mdi:battery-arrow-down",
    },
    "batteryChargingCurrent": {
        "name": "Battery Charging Current",
        "unit": "A",
        "device_class": "current",
        "icon": "mdi:battery-arrow-up",
    },
    "batteryVoltage": {
        "name": "Battery Voltage",
        "unit": "V",
        "device_class": "voltage",
        "icon": "mdi:battery",
    },
    "batteryPower": {
        "name": "Battery Power",
        "unit": "W",
        "device_class": "power",
        "icon": "mdi:battery-charging",
    },
    "batteryChargingPower": {
        "name": "Battery Charging Power",
        "unit": "W",
        "device_class": "power",
        "icon": "mdi:battery-arrow-up",
    },
    "batteryChargingEnergy": {
        "name": "Battery Charging Energy",
        "unit": "kWh",
        "device_class": "energy",
        "icon": "mdi:battery-arrow-up",
    },
    "batteryDischargePower": {
        "name": "Battery Discharge Power",
        "unit": "W",
        "device_class": "power",
        "icon": "mdi:battery-arrow-down",
    },
    "batteryDischargeEnergy": {
        "name": "Battery Discharge Energy",
        "unit": "kWh",
        "device_class": "energy",
        "icon": "mdi:battery-arrow-down",
    },
    "batterySOC": {
        "name": "Battery State of Charge",
        "unit": "%",
        "device_class": "battery",
        "icon": "mdi:battery",
    },
    "feedInPower": {
        "name": "Grid Feed-in Power",
        "unit": "W",
        "device_class": "power",
        "icon": "mdi:transmission-tower-export",
    },
    "gridPower": {
        "name": "Grid Import Power",
        "unit": "W",
        "device_class": "power",
        "icon": "mdi:transmission-tower-import",
    },
    "loadPower": {
        "name": "Load Power",
        "unit": "W",
        "device_class": "power",
        "icon": "mdi:home-lightning-bolt",
    },
    "pvInputTotalEnergy": {
        "name": "PV Input Total Energy",
        "unit": "Wh",
        "device_class": "energy",
        "icon": "mdi:solar-power",
    },
    "pvInputEnergy": {
        "name": "PV Input Energy",
        "unit": "Wh",
        "device_class": "energy",
        "icon": "mdi:solar-power",
    },
    # Monthly summary sensors
    "monthly_pv_generated": {
        "name": "Monthly PV Generated",
        "unit": "kWh",
        "device_class": "energy",
        "icon": "mdi:solar-power",
    },
    "monthly_grid_import": {
        "name": "Monthly Grid Import",
        "unit": "kWh",
        "device_class": "energy",
        "icon": "mdi:transmission-tower-import",
    },
    "monthly_total_consumption": {
        "name": "Monthly Total Consumption",
        "unit": "kWh",
        "device_class": "energy",
        "icon": "mdi:home-lightning-bolt",
    },
    "monthly_solar_percentage": {
        "name": "Monthly Solar Coverage",
        "unit": "%",
        "icon": "mdi:percent",
    },
}

# Operating-mode select maps HA option strings to integer values
OUTPUT_MODE_MAP: dict[str, int] = {
    "Utility -> Solar -> Battery (USB)": 0,
    "Solar + Utility -> Battery (SUB)": 1,
    "Solar + Battery -> Utility (SBU)": 2,
    # "Power Export Control (PEC)": 3,
}
OUTPUT_MODE_REVERSE: dict[int, str] = {v: k for k, v in OUTPUT_MODE_MAP.items()}
OUTPUT_MODE_SBU = 2

# AC input voltage range select (acInputRangeSetting)
AC_INPUT_RANGE_MAP: dict[str, int] = {
    "Appliance (APL)": 0,
    "UPS": 1,
}
AC_INPUT_RANGE_REVERSE: dict[int, str] = {v: k for k, v in AC_INPUT_RANGE_MAP.items()}

# Charger-priority select
CHARGER_PRIORITY_MAP: dict[str, int] = {
    "Solar First (CSO)": 0,
    "Solar + Utility (SNU)": 1,
    "Solar Only (OSO)": 2,
    # "Solar residual (SOR)": 3,
}
CHARGER_PRIORITY_REVERSE: dict[int, str] = {
    v: k for k, v in CHARGER_PRIORITY_MAP.items()
}

# ─── Battery Type select map ──────────────────────────────────────────────────
BATTERY_TYPE_MAP: dict[str, int] = {
    "AGM": 0,
    "FLD": 1,
    "USE": 2,
    "LIA": 3,
    "PYL": 4,
    "TQF": 5,
    "GRO": 6,
    "LIB": 7,
    "LIC": 8,
    "FEL": 9,
}
BATTERY_TYPE_REVERSE: dict[int, str] = {v: k for k, v in BATTERY_TYPE_MAP.items()}

# ─── Output Frequency select map ──────────────────────────────────────────────
RATED_FREQUENCY_MAP: dict[str, int] = {
    "50 Hz": 50,
    "60 Hz": 60,
}
RATED_FREQUENCY_REVERSE: dict[int, str] = {v: k for k, v in RATED_FREQUENCY_MAP.items()}

# ─── Output Voltage select map ────────────────────────────────────────────────
RATED_VOLTAGE_MAP: dict[str, int] = {
    "220 V": 220,
    "230 V": 230,
    "240 V": 240,
}
RATED_VOLTAGE_REVERSE: dict[int, str] = {v: k for k, v in RATED_VOLTAGE_MAP.items()}

# ─── Grid Connection Type select map ──────────────────────────────────────────
GRID_CONNECTION_PROTOCOL_TYPE_MAP: dict[str, int] = {
    "195.5–253 VAC 49–51 Hz": 1,
    "184–264.5 VAC 47.5–51.5 Hz": 2,
    "184–264.5 VAC 57–62 Hz": 3,
    "170–264.5 VAC 47.5–53.5 Hz": 4,
    "100–280 VAC 47.5–53.5 Hz": 5,
}
GRID_CONNECTION_PROTOCOL_TYPE_REVERSE: dict[int, str] = {
    v: k for k, v in GRID_CONNECTION_PROTOCOL_TYPE_MAP.items()
}

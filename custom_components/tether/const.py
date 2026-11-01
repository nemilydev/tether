"""Constants for the Tether integration."""
from typing import Final

DOMAIN: Final = "tether"

CONF_CONNECTION_TYPE: Final = "connection_type"
CONF_LOCAL_HOST: Final = "host"
CONF_LOCAL_PORT: Final = "port"
CONF_USE_HTTPS: Final = "use_https"
CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_DEFAULT_TIME_SEC: Final = "default_time_sec"
CONNECTION_LOCAL: Final = "local"
CONNECTION_STANDARD: Final = "standard"
DEFAULT_LOCAL_PORT: Final = 20010

# Configuration keys
CONF_DEVELOPER_TOKEN: Final = "developer_token"
CONF_CALLBACK_URL: Final = "callback_url"
CONF_USER_ID: Final = "user_id"
CONF_USER_NAME: Final = "user_name"
CONF_STROKE_CONTROL_TYPE: Final = "stroke_control_type"
CONF_UTOKEN: Final = "utoken"
CONF_CONNECTION_INFO: Final = "connection_info"
CONF_SKIP_PAIRING_CHECK: Final = "skip_pairing_check"

# hass.data[DOMAIN] keys
PENDING_PAIRINGS: Final = "_pending_pairings"
CALLBACK_VIEW_REGISTERED: Final = "_callback_view_registered"
PAIRING_WAIT_TIMEOUT: Final = 10  # seconds to wait for the callback after Submit
STORAGE_VERSION: Final = 1
X_PLATFORM: Final = "Home Assistant Tether Integration"

# HTTP callback endpoint registered in Home Assistant
CALLBACK_PATH: Final = "/api/tether/callback"

# Lovense API endpoints
API_BASE_URL: Final = "https://api.lovense-api.com/api"
API_GET_QRCODE: Final = f"{API_BASE_URL}/lan/getQrCode"
API_COMMAND_SERVER: Final = f"{API_BASE_URL}/lan/v2/command"

# Device commands
CMD_FUNCTION: Final = "Function"
CMD_POSITION: Final = "Position"
CMD_PATTERN: Final = "Pattern"
CMD_PRESET: Final = "Preset"

# Intensity ranges (based on the official Lovense API)
VIBRATE_MAX: Final = 20
PUMP_MAX: Final = 3

# Maximum level accepted by each Lovense Function action
FUNCTION_LIMITS: Final = {
    "Vibrate": 20,
    "Vibrate1": 20,
    "Vibrate2": 20,
    "Vibrate3": 20,
    "Rotate": 20,
    "Pump": 3,
    "Thrusting": 20,
    "Fingering": 20,
    "Suction": 20,
    "Depth": 3,
    "Oscillate": 20,
    "Stroke": 100,
}

# Built-in Lovense presets
PRESETS: Final = ["pulse", "wave", "fireworks", "earthquake"]

# Update intervals
SCAN_INTERVAL: Final = 30  # seconds
REQUEST_TIMEOUT: Final = 10  # seconds
CALLBACK_RECONCILE_COOLDOWN: Final = 2  # seconds between callback-triggered reads

# Stroke control options
STROKE_CONTROL_LIGHTS: Final = "lights"  # Voice-friendly light entities
STROKE_CONTROL_NUMBERS: Final = "numbers"  # Precise number entities
STROKE_CONTROL_BOTH: Final = "both"  # Both types (for advanced users)

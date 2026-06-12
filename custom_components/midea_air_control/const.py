"""Constants for the Midea Air Control (AirControlBase) integration."""

DOMAIN = "midea_air_control"

# How often to poll the cloud API for device state (seconds).
SCAN_INTERVAL_SEC = 30

# After sending a command the cloud takes several seconds (~10s) to report the
# new state back. Until then we keep the optimistic value instead of letting a
# poll revert it. Kept above the observed cloud lag and below SCAN_INTERVAL_SEC.
API_DELAY_SEC = 15

# The cloud forgets a unit's settings while it is off and reports defaults
# (cool / 20C). We remember these fields from the last powered-on state so we
# can restore them on turn-on and show them while off, persisted across restarts.
STORAGE_VERSION = 1
LAST_ON_FIELDS = ("mode", "setTemp", "wind")

# Temperature setpoint bounds (Celsius).
MIN_TEMP = 17
MAX_TEMP = 30

# Custom symbol shown on each AC, served from the integration folder.
DEVICE_IMAGE_FILE = "midea_climate_device.png"
DEVICE_IMAGE_URL = f"/{DOMAIN}/{DEVICE_IMAGE_FILE}"

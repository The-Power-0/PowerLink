"""PowerLink Home Assistant integration constants."""
DOMAIN = "powerlink"
STORAGE_VERSION = 1
STORAGE_KEY = "powerlink.devices"
APP_ID = "com.power.powerplay"
SESSION_TTL_SECONDS = 12 * 60 * 60
SERVICE_DISCOVER = "discover_devices"
SERVICE_REGISTER = "register_device"
SERVICE_OPEN = "open_device"
SERVICE_PREPARE = "prepare_handoff"
SERVICE_HANDOFF = "handoff"
SERVICE_CAPTURE = "capture_handoff"

"""Constants for the Yeelight Cube Matrix integration"""

DOMAIN = "cubematrix"

DEFAULT_MODULES = "matrix"
DEFAULT_DIRECTION = "horizontal"
DIRECTIONS = ["horizontal", "vertical"]

CONF_MODULES = "modules"
CONF_DIRECTION = "direction"

# Folder for preset files, under the HA config folder
PRESET_DIR = "cubematrix"
STORE_KEY = "cubematrix.presets"
STORE_VERSION = 1

# The model name the lamp reports, compared without case
MODEL = "cubematrix"

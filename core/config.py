import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUSTOM_COMMANDS_FILE = os.path.join(BASE_DIR, "custom_commands.json")
APK_PATH = os.path.join(BASE_DIR, "app-release.apk")
PORT = 10555
HOST = "0.0.0.0"

# Load env variables manually from .env if present
env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                try:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()
                except Exception as e:
                    print(f"Error parsing line in .env: {line} - {e}")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

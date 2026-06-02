import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUSTOM_COMMANDS_FILE = os.path.join(BASE_DIR, "custom_commands.json")
APK_PATH = os.path.join(BASE_DIR, "app-release.apk")
PORT = 10555
HOST = "0.0.0.0"

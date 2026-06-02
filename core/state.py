import asyncio
import json
import os
from fastapi import WebSocket
from core.config import CUSTOM_COMMANDS_FILE

# WebSocket Connections
active_connection: WebSocket = None
active_helper_connection: WebSocket = None
main_accessibility_active: bool = False
helper_accessibility_active: bool = False

# Device Size & Details
device_width: int | None = None
device_height: int | None = None
client_version: str | None = None
latest_version: str = "1.0.0"

# Screenshots & Event Synchronization
latest_screenshot: bytes | None = None
screenshot_event = asyncio.Event()

# Memory Logs
phone_logs: list[str] = []

# Agent Background Loop state
agent_running: bool = False
agent_task = None
agent_logs: list[str] = []
agent_goal: str = ""

def load_custom_commands() -> dict:
    if os.path.exists(CUSTOM_COMMANDS_FILE):
        try:
            with open(CUSTOM_COMMANDS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading custom commands: {e}")
    return {}

def save_custom_commands(commands: dict):
    try:
        with open(CUSTOM_COMMANDS_FILE, "w") as f:
            json.dump(commands, f, indent=2)
    except Exception as e:
        print(f"Error saving custom commands: {e}")

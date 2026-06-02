import json
import base64
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from core import state

router = APIRouter()

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("Incoming connection via WebSocket.")

    try:
        while True:
            data = await websocket.receive_text()
            print(f"Received from phone: {data[:120]}...")

            try:
                msg = json.loads(data)
                if msg.get("type") == "screenshot":
                    b64 = msg.get("data", "")
                    state.latest_screenshot = base64.b64decode(b64)
                    state.screenshot_event.set()
                    print("Screenshot received and stored.")
                elif msg.get("type") == "device_size":
                    state.device_width = msg.get("width")
                    state.device_height = msg.get("height")
                    if msg.get("is_helper"):
                        if state.active_helper_connection is not None and state.active_helper_connection != websocket:
                            try:
                                await state.active_helper_connection.close()
                            except:
                                pass
                        state.active_helper_connection = websocket
                        state.helper_accessibility_active = True
                        state.client_version = msg.get("version", "helper-1.0.0")
                        print(f"Helper connected: {state.device_width}x{state.device_height}, version: {state.client_version}")
                    else:
                        if state.active_connection is not None and state.active_connection != websocket:
                            try:
                                await state.active_connection.close()
                            except:
                                pass
                        state.active_connection = websocket
                        state.main_accessibility_active = bool(msg.get("accessibility_active", False))
                        state.client_version = msg.get("version")
                        print(f"Phone connected: {state.device_width}x{state.device_height}, version: {state.client_version}, active: {state.main_accessibility_active}")
                elif msg.get("type") == "log":
                    log_msg = msg.get("message", "")
                    state.phone_logs.append(log_msg)
                    if len(state.phone_logs) > 150:
                        state.phone_logs.pop(0)
            except Exception as e:
                print(f"Could not parse message: {e}")

    except WebSocketDisconnect:
        print("WebSocket disconnected.")
        if state.active_connection == websocket:
            state.active_connection = None
            state.main_accessibility_active = False
        if state.active_helper_connection == websocket:
            state.active_helper_connection = None
            state.helper_accessibility_active = False
    except Exception as e:
        print(f"WebSocket error: {e}")
        if state.active_connection == websocket:
            state.active_connection = None
            state.main_accessibility_active = False
        if state.active_helper_connection == websocket:
            state.active_helper_connection = None
            state.helper_accessibility_active = False

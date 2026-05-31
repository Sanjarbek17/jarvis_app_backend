import asyncio
import base64
import json
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel
import uvicorn

app = FastAPI()

# Store the active websocket connection to the phone
active_connection: WebSocket = None

# Store the device screen dimensions
device_width: int | None = None
device_height: int | None = None

# Store the client's current app version and the latest available version on server
client_version: str | None = None
latest_version: str = "1.0.0"

# Store the latest screenshot (raw PNG bytes)
latest_screenshot: bytes | None = None
screenshot_event = asyncio.Event()

phone_logs: list[str] = []

class CommandModel(BaseModel):
    action: str
    x: float | None = None
    y: float | None = None
    x2: float | None = None
    y2: float | None = None
    duration: int | None = 300
    label: str | None = None
    text: str | None = None

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global active_connection, latest_screenshot, client_version, phone_logs
    await websocket.accept()
    print("Phone connected via WebSocket.")

    if active_connection is not None:
        try:
            await active_connection.close()
        except:
            pass

    active_connection = websocket

    try:
        while True:
            data = await websocket.receive_text()
            print(f"Received from phone: {data[:120]}...")

            try:
                msg = json.loads(data)
                if msg.get("type") == "screenshot":
                    b64 = msg.get("data", "")
                    latest_screenshot = base64.b64decode(b64)
                    screenshot_event.set()
                    print("Screenshot received and stored.")
                elif msg.get("type") == "device_size":
                    global device_width, device_height
                    device_width = msg.get("width")
                    device_height = msg.get("height")
                    if "version" in msg:
                        client_version = msg.get("version")
                    print(f"Device size updated: {device_width}x{device_height}, version: {client_version}")
                elif msg.get("type") == "log":
                    log_msg = msg.get("message", "")
                    phone_logs.append(log_msg)
                    if len(phone_logs) > 150:
                        phone_logs.pop(0)
            except Exception as e:
                print(f"Could not parse message: {e}")

    except WebSocketDisconnect:
        print("Phone disconnected.")
        if active_connection == websocket:
            active_connection = None
    except Exception as e:
        print(f"WebSocket error: {e}")
        if active_connection == websocket:
            active_connection = None

@app.post("/execute")
async def execute_command(command: CommandModel):
    global active_connection, device_width, device_height
    if active_connection is None:
        raise HTTPException(status_code=503, detail="Phone is not connected to the server.")

    try:
        payload = command.model_dump(exclude_none=True)
        width = device_width or 1080
        height = device_height or 2400

        if command.action == "tap" and command.x is not None and command.y is not None:
            # Use ratio conversion if both x and y are <= 1.0 (indicating ratios)
            if 0.0 <= command.x <= 1.0 and 0.0 <= command.y <= 1.0:
                payload["x"] = float(command.x * width)
                payload["y"] = float(command.y * height)
                print(f"Relaying tap: scaled ratio ({command.x}, {command.y}) to absolute ({payload['x']}, {payload['y']}) using device size {width}x{height}")
            else:
                print(f"Relaying tap: absolute coordinates ({command.x}, {command.y}) used directly")

        elif command.action == "swipe" and command.x is not None and command.y is not None and command.x2 is not None and command.y2 is not None:
            # Use ratio conversion if coordinates are <= 1.0
            if (0.0 <= command.x <= 1.0 and 0.0 <= command.y <= 1.0 and 
                0.0 <= command.x2 <= 1.0 and 0.0 <= command.y2 <= 1.0):
                payload["x"] = float(command.x * width)
                payload["y"] = float(command.y * height)
                payload["x2"] = float(command.x2 * width)
                payload["y2"] = float(command.y2 * height)
                print(f"Relaying swipe: scaled start ({command.x}, {command.y}) and end ({command.x2}, {command.y2}) to absolute using device size {width}x{height}")
            else:
                print(f"Relaying swipe: absolute coordinates used directly")

        await active_connection.send_text(json.dumps(payload))
        return {"status": "success", "message": "Command relayed to phone"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/screenshot")
async def request_screenshot():
    """Ask the phone to take a screenshot and return it as PNG."""
    global active_connection, latest_screenshot
    if active_connection is None:
        raise HTTPException(status_code=503, detail="Phone is not connected to the server.")

    # Clear the previous screenshot and event
    latest_screenshot = None
    screenshot_event.clear()

    # Ask the phone to take a screenshot
    await active_connection.send_text(json.dumps({"action": "screenshot"}))

    # Wait up to 10 seconds for the phone to send back the image
    try:
        await asyncio.wait_for(screenshot_event.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timed out waiting for screenshot from phone.")

    if latest_screenshot is None:
        raise HTTPException(status_code=500, detail="Screenshot data was empty.")

    return Response(content=latest_screenshot, media_type="image/png")

@app.get("/screenshot")
async def get_latest_screenshot():
    """Returns the most recently captured screenshot as PNG."""
    if latest_screenshot is None:
        raise HTTPException(status_code=404, detail="No screenshot available. Call POST /screenshot first.")
    return Response(content=latest_screenshot, media_type="image/png")

@app.get("/phone_logs")
async def get_phone_logs():
    """Returns stored logs from the phone."""
    return {"logs": phone_logs}

@app.get("/status")
async def get_status():
    if active_connection is None:
        return {
            "connected": False,
            "message": "Phone is offline",
            "latest_version": latest_version
        }
    return {
        "connected": True,
        "message": "Phone is online and ready for commands",
        "client_version": client_version,
        "latest_version": latest_version
    }

@app.get("/version")
async def get_version():
    return {"latest_version": latest_version}

@app.post("/version")
async def set_version(version: str):
    global latest_version
    latest_version = version
    return {"status": "success", "latest_version": latest_version}

@app.post("/upload_apk")
async def upload_apk(version: str, file: UploadFile = File(...)):
    global latest_version
    latest_version = version
    apk_path = os.path.join(os.path.dirname(__file__), "app-release.apk")
    with open(apk_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)
    return {"status": "success", "filename": "app-release.apk", "latest_version": latest_version}

@app.get("/apk")
async def get_apk():
    apk_path = os.path.join(os.path.dirname(__file__), "app-release.apk")
    if not os.path.exists(apk_path):
        raise HTTPException(status_code=404, detail="APK file not found on server.")
    return FileResponse(apk_path, media_type="application/vnd.android.package-archive", filename="app-release.apk")

@app.post("/update")
async def trigger_update(request: Request):
    global active_connection, latest_version
    if active_connection is None:
        raise HTTPException(status_code=503, detail="Phone is not connected to the server.")
    
    # Construct download URL dynamically based on Request netloc
    download_url = f"http://{request.base_url.netloc}/apk"
    payload = {
        "action": "update",
        "url": download_url,
        "version": latest_version
    }
    await active_connection.send_text(json.dumps(payload))
    return {"status": "success", "message": "Update command sent to phone", "url": download_url}

if __name__ == "__main__":
    print("Starting Phone Controller Backend on port 10555...")
    uvicorn.run(app, host="0.0.0.0", port=10555)

import asyncio
import base64
import json
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File
from fastapi.responses import Response, FileResponse, HTMLResponse
from pydantic import BaseModel
import uvicorn

app = FastAPI()

CUSTOM_COMMANDS_FILE = os.path.join(os.path.dirname(__file__), "custom_commands.json")

def load_custom_commands():
    if os.path.exists(CUSTOM_COMMANDS_FILE):
        try:
            with open(CUSTOM_COMMANDS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading custom commands: {e}")
    return {}

def save_custom_commands(commands):
    try:
        with open(CUSTOM_COMMANDS_FILE, "w") as f:
            json.dump(commands, f, indent=2)
    except Exception as e:
        print(f"Error saving custom commands: {e}")

# Store the active websocket connections
active_connection: WebSocket = None
active_helper_connection: WebSocket = None
main_accessibility_active: bool = False
helper_accessibility_active: bool = False

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
    send_to_helper: bool | None = False

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global active_connection, active_helper_connection, main_accessibility_active, helper_accessibility_active, latest_screenshot, client_version, phone_logs
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
                    latest_screenshot = base64.b64decode(b64)
                    screenshot_event.set()
                    print("Screenshot received and stored.")
                elif msg.get("type") == "device_size":
                    global device_width, device_height
                    device_width = msg.get("width")
                    device_height = msg.get("height")
                    if msg.get("is_helper"):
                        if active_helper_connection is not None and active_helper_connection != websocket:
                            try:
                                await active_helper_connection.close()
                            except:
                                pass
                        active_helper_connection = websocket
                        helper_accessibility_active = True
                        client_version = msg.get("version", "helper-1.0.0")
                        print(f"Helper connected: {device_width}x{device_height}, version: {client_version}")
                    else:
                        if active_connection is not None and active_connection != websocket:
                            try:
                                await active_connection.close()
                            except:
                                pass
                        active_connection = websocket
                        main_accessibility_active = bool(msg.get("accessibility_active", False))
                        client_version = msg.get("version")
                        print(f"Phone connected: {device_width}x{device_height}, version: {client_version}, active: {main_accessibility_active}")
                elif msg.get("type") == "log":
                    log_msg = msg.get("message", "")
                    phone_logs.append(log_msg)
                    if len(phone_logs) > 150:
                        phone_logs.pop(0)
            except Exception as e:
                print(f"Could not parse message: {e}")

    except WebSocketDisconnect:
        print("WebSocket disconnected.")
        if active_connection == websocket:
            active_connection = None
            main_accessibility_active = False
        if active_helper_connection == websocket:
            active_helper_connection = None
            helper_accessibility_active = False
    except Exception as e:
        print(f"WebSocket error: {e}")
        if active_connection == websocket:
            active_connection = None
            main_accessibility_active = False
        if active_helper_connection == websocket:
            active_helper_connection = None
            helper_accessibility_active = False

@app.post("/execute")
async def execute_command(command: CommandModel):
    global active_connection, active_helper_connection, device_width, device_height
    
    if command.send_to_helper:
        conn = active_helper_connection
        if conn is None:
            raise HTTPException(status_code=503, detail="Helper app is not connected.")
    else:
        conn = active_connection
        if conn is None:
            raise HTTPException(status_code=503, detail="Main controller app is not connected.")

    try:
        payload = command.model_dump(exclude_none=True)
        # remove backend routing field from target payload
        if "send_to_helper" in payload:
            del payload["send_to_helper"]

        width = device_width or 1080
        height = device_height or 2400

        if command.action == "tap" and command.x is not None and command.y is not None:
            if 0.0 <= command.x <= 1.0 and 0.0 <= command.y <= 1.0:
                payload["x"] = float(command.x * width)
                payload["y"] = float(command.y * height)
                print(f"Relaying tap: scaled ratio ({command.x}, {command.y}) to absolute ({payload['x']}, {payload['y']}) using device size {width}x{height}")
            else:
                print(f"Relaying tap: absolute coordinates ({command.x}, {command.y}) used directly")

        elif command.action == "swipe" and command.x is not None and command.y is not None and command.x2 is not None and command.y2 is not None:
            if (0.0 <= command.x <= 1.0 and 0.0 <= command.y <= 1.0 and 
                0.0 <= command.x2 <= 1.0 and 0.0 <= command.y2 <= 1.0):
                payload["x"] = float(command.x * width)
                payload["y"] = float(command.y * height)
                payload["x2"] = float(command.x2 * width)
                payload["y2"] = float(command.y2 * height)
                print(f"Relaying swipe: scaled start ({command.x}, {command.y}) and end ({command.x2}, {command.y2}) to absolute using device size {width}x{height}")
            else:
                print(f"Relaying swipe: absolute coordinates used directly")

        await conn.send_text(json.dumps(payload))
        return {"status": "success", "message": "Command relayed to phone"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/screenshot")
async def request_screenshot(send_to_helper: bool = False):
    """Ask the phone to take a screenshot and return it as PNG."""
    global active_connection, active_helper_connection, latest_screenshot
    
    if send_to_helper:
        conn = active_helper_connection
        if conn is None:
            raise HTTPException(status_code=503, detail="Helper app is not connected.")
    else:
        conn = active_connection
        if conn is None:
            raise HTTPException(status_code=503, detail="Main controller app is not connected.")

    latest_screenshot = None
    screenshot_event.clear()

    await conn.send_text(json.dumps({"action": "screenshot"}))

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
    return {
        "main_connected": active_connection is not None,
        "main_accessibility_active": main_accessibility_active,
        "helper_connected": active_helper_connection is not None,
        "helper_accessibility_active": helper_accessibility_active,
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
async def trigger_update(request: Request, send_to_helper: bool = False):
    global active_connection, active_helper_connection, latest_version
    
    if send_to_helper:
        conn = active_helper_connection
        if conn is None:
            raise HTTPException(status_code=503, detail="Helper app is not connected.")
    else:
        conn = active_connection
        if conn is None:
            raise HTTPException(status_code=503, detail="Main controller app is not connected.")
    
    download_url = f"http://{request.base_url.netloc}/apk"
    payload = {
        "action": "update",
        "url": download_url,
        "version": latest_version
    }
    await conn.send_text(json.dumps(payload))
    return {"status": "success", "message": "Update command sent to phone", "url": download_url}

class CustomCommandModel(BaseModel):
    name: str
    steps: list[dict]

async def relay_step(step_data: dict, send_to_helper: bool = False):
    global active_connection, active_helper_connection, device_width, device_height
    
    if send_to_helper:
        conn = active_helper_connection
    else:
        conn = active_connection
        
    if conn is None:
        raise HTTPException(status_code=503, detail="Target connection is not online.")
    
    payload = step_data.copy()
    width = device_width or 1080
    height = device_height or 2400
    
    action = payload.get("action")
    
    def to_float(val):
        try:
            return float(val) if val is not None else None
        except:
            return None

    x = to_float(payload.get("x"))
    y = to_float(payload.get("y"))
    x2 = to_float(payload.get("x2"))
    y2 = to_float(payload.get("y2"))
    
    if action == "tap" and x is not None and y is not None:
        if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
            payload["x"] = float(x * width)
            payload["y"] = float(y * height)
            print(f"Relaying tap: scaled ratio ({x}, {y}) to absolute ({payload['x']}, {payload['y']})")
        else:
            payload["x"] = x
            payload["y"] = y
            
    elif action == "swipe" and x is not None and y is not None and x2 is not None and y2 is not None:
        if (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and 
            0.0 <= x2 <= 1.0 and 0.0 <= y2 <= 1.0):
            payload["x"] = float(x * width)
            payload["y"] = float(y * height)
            payload["x2"] = float(x2 * width)
            payload["y2"] = float(y2 * height)
            print(f"Relaying swipe: scaled start ({x}, {y}) and end ({x2}, {y2}) to absolute")
        else:
            payload["x"] = x
            payload["y"] = y
            payload["x2"] = x2
            payload["y2"] = y2
            
    await conn.send_text(json.dumps(payload))

@app.get("/custom_commands")
async def get_custom_commands():
    return load_custom_commands()

@app.post("/custom_commands")
async def create_custom_command(cmd: CustomCommandModel):
    commands = load_custom_commands()
    commands[cmd.name] = cmd.steps
    save_custom_commands(commands)
    return {"status": "success", "message": f"Custom command '{cmd.name}' saved."}

@app.delete("/custom_commands/{name}")
async def delete_custom_command(name: str):
    commands = load_custom_commands()
    if name in commands:
        del commands[name]
        save_custom_commands(commands)
        return {"status": "success", "message": f"Custom command '{name}' deleted."}
    raise HTTPException(status_code=404, detail="Custom command not found")

@app.post("/execute_custom/{name}")
async def execute_custom(name: str, send_to_helper: bool = False):
    commands = load_custom_commands()
    if name not in commands:
        raise HTTPException(status_code=404, detail="Custom command not found")
        
    steps = commands[name]
    print(f"Executing custom command '{name}' with {len(steps)} steps...")
    
    for i, step in enumerate(steps):
        action = step.get("action")
        if action == "sleep":
            duration = int(step.get("duration", 1000))
            print(f"Step {i+1}: sleep {duration}ms")
            await asyncio.sleep(duration / 1000.0)
        else:
            print(f"Step {i+1}: action '{action}'")
            await relay_step(step, send_to_helper=send_to_helper)
            # Default sleep between actions
            await asyncio.sleep(1.0)
            
    return {"status": "success", "message": f"Custom command '{name}' completed execution"}

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Phone Controller Dashboard</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg-gradient: radial-gradient(circle at center, #0f1c24 0%, #050a0f 100%);
                --card-bg: rgba(15, 27, 36, 0.6);
                --card-border: rgba(20, 110, 120, 0.15);
                --text-main: #e2e8f0;
                --text-muted: #94a3b8;
                --accent: #00f2fe;
                --accent-hover: #4facfe;
                --accent-glow: rgba(0, 242, 254, 0.3);
                --danger: #ef4444;
                --success: #10b981;
            }
            * {
                box-sizing: border-box;
                margin: 0;
                padding: 0;
            }
            body {
                font-family: 'Outfit', sans-serif;
                background: var(--bg-gradient);
                color: var(--text-main);
                min-height: 100vh;
                display: flex;
                flex-direction: column;
                overflow-x: hidden;
            }
            header {
                backdrop-filter: blur(12px);
                background: rgba(5, 10, 15, 0.8);
                border-bottom: 1px solid var(--card-border);
                padding: 16px 32px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                position: sticky;
                top: 0;
                z-index: 100;
            }
            .logo {
                font-size: 24px;
                font-weight: 800;
                background: linear-gradient(135deg, #00f2fe 0%, #4facfe 100%);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                letter-spacing: 0.5px;
            }
            .status-badge {
                display: flex;
                align-items: center;
                gap: 16px;
                background: rgba(255, 255, 255, 0.05);
                padding: 8px 18px;
                border-radius: 20px;
                font-size: 14px;
                border: 1px solid var(--card-border);
            }
            .status-dot {
                width: 10px;
                height: 10px;
                border-radius: 50%;
                background: var(--danger);
            }
            .status-dot.online {
                background: var(--success);
                box-shadow: 0 0 10px var(--success);
                animation: pulse 2s infinite;
            }
            @keyframes pulse {
                0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.4); }
                70% { box-shadow: 0 0 0 10px rgba(16, 185, 129, 0); }
                100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
            }
            .container {
                max-width: 1400px;
                margin: 24px auto;
                padding: 0 24px;
                display: grid;
                grid-template-columns: 480px 1fr;
                gap: 24px;
                flex-grow: 1;
            }
            .glass-card {
                background: var(--card-bg);
                backdrop-filter: blur(16px);
                border: 1px solid var(--card-border);
                border-radius: 24px;
                padding: 24px;
                box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
                display: flex;
                flex-direction: column;
                gap: 16px;
            }
            .phone-viewer {
                align-items: center;
            }
            .screen-container {
                position: relative;
                width: 320px;
                aspect-ratio: 9/19.5;
                border-radius: 36px;
                border: 12px solid #1e293b;
                box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5);
                overflow: hidden;
                background: #000;
                cursor: crosshair;
                user-select: none;
            }
            .screen-container img {
                width: 100%;
                height: 100%;
                object-fit: fill;
            }
            .canvas-overlay {
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                z-index: 10;
            }
            .screen-loader {
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                background: rgba(0, 0, 0, 0.75);
                color: #fff;
                padding: 12px 24px;
                border-radius: 20px;
                font-size: 14px;
                display: none;
                z-index: 20;
                backdrop-filter: blur(4px);
            }
            .panel-header {
                font-size: 18px;
                font-weight: 600;
                color: var(--text-main);
                border-bottom: 1px solid rgba(255, 255, 255, 0.08);
                padding-bottom: 10px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            .btn {
                background: linear-gradient(135deg, var(--accent) 0%, var(--accent-hover) 100%);
                color: #050a0f;
                border: none;
                padding: 12px 20px;
                border-radius: 12px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s ease;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
            }
            .btn:hover {
                transform: translateY(-2px);
                box-shadow: 0 0 15px var(--accent-glow);
            }
            .btn:active {
                transform: translateY(0);
            }
            .btn-secondary {
                background: rgba(255, 255, 255, 0.05);
                color: var(--text-main);
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
            .btn-secondary:hover {
                background: rgba(255, 255, 255, 0.1);
                box-shadow: none;
            }
            .btn-danger {
                background: var(--danger);
                color: #fff;
            }
            .btn-danger:hover {
                box-shadow: 0 0 15px rgba(239, 68, 68, 0.4);
            }
            .control-row {
                display: flex;
                gap: 12px;
                width: 100%;
            }
            .control-row .btn {
                flex: 1;
            }
            .input-group {
                display: flex;
                flex-direction: column;
                gap: 6px;
                width: 100%;
            }
            .input-group label {
                font-size: 13px;
                color: var(--text-muted);
            }
            .input-group input, .input-group select {
                background: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.1);
                color: var(--text-main);
                padding: 12px;
                border-radius: 12px;
                font-family: inherit;
                font-size: 14px;
                outline: none;
                transition: border 0.2s;
            }
            .input-group input:focus, .input-group select:focus {
                border-color: var(--accent);
            }
            .tab-container {
                display: flex;
                background: rgba(255, 255, 255, 0.03);
                padding: 4px;
                border-radius: 14px;
                border: 1px solid rgba(255, 255, 255, 0.05);
            }
            .tab-btn {
                flex: 1;
                padding: 10px;
                background: none;
                border: none;
                color: var(--text-muted);
                cursor: pointer;
                font-weight: 600;
                font-size: 14px;
                border-radius: 10px;
                transition: all 0.2s;
            }
            .tab-btn.active {
                background: rgba(255, 255, 255, 0.08);
                color: var(--text-main);
            }
            .logs-container {
                height: 200px;
                background: rgba(0, 0, 0, 0.4);
                border-radius: 14px;
                border: 1px solid rgba(255, 255, 255, 0.05);
                padding: 12px;
                overflow-y: auto;
                font-family: monospace;
                font-size: 12px;
                display: flex;
                flex-direction: column;
                gap: 6px;
            }
            .log-line {
                color: var(--text-muted);
                line-height: 1.4;
            }
            .log-line.highlight {
                color: var(--accent);
            }
            .macro-step {
                display: flex;
                justify-content: space-between;
                align-items: center;
                background: rgba(255, 255, 255, 0.03);
                padding: 10px 14px;
                border-radius: 10px;
                border: 1px solid rgba(255, 255, 255, 0.05);
                font-size: 14px;
            }
            .macro-step-list {
                max-height: 300px;
                overflow-y: auto;
                display: flex;
                flex-direction: column;
                gap: 8px;
            }
            .macro-list {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
                gap: 16px;
            }
            .macro-card {
                background: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.05);
                padding: 16px;
                border-radius: 16px;
                display: flex;
                flex-direction: column;
                gap: 12px;
            }
            .macro-card-title {
                font-weight: 600;
                font-size: 16px;
                color: var(--text-main);
            }
            .macro-card-steps {
                font-size: 13px;
                color: var(--text-muted);
            }

            /* Switch Styles */
            .switch input:checked + .slider {
                background-color: var(--accent);
                box-shadow: 0 0 10px var(--accent-glow);
            }
            .switch input:checked + .slider:before {
                transform: translateX(24px);
                background-color: #050a0f;
            }
            .slider:before {
                position: absolute;
                content: "";
                height: 18px;
                width: 18px;
                left: 3px;
                bottom: 3px;
                background-color: var(--text-main);
                transition: .4s;
                border-radius: 50%;
            }
        </style>
    </head>
    <body>
        <header>
            <div class="logo">Phone Controller</div>
            <div class="status-badge">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span style="font-weight: 600; color: var(--text-muted);">Main App:</span>
                    <span id="status-dot-main" class="status-dot"></span>
                    <span id="status-text-main">Checking...</span>
                </div>
                <div style="display: flex; align-items: center; gap: 8px; border-left: 1px solid var(--card-border); padding-left: 16px;">
                    <span style="font-weight: 600; color: var(--text-muted);">Helper App:</span>
                    <span id="status-dot-helper" class="status-dot"></span>
                    <span id="status-text-helper">Checking...</span>
                </div>
            </div>
        </header>

        <div class="container">
            <!-- Left Pane: Live Viewer -->
            <div class="glass-card phone-viewer">
                <div class="panel-header" style="width: 100%;">
                    <span>Live Screen</span>
                    <button class="btn btn-secondary" onclick="refreshScreenshot()" style="padding: 6px 12px; font-size: 13px;">Refresh</button>
                </div>
                <div class="screen-container">
                    <div id="screen-loader" class="screen-loader">Taking screenshot...</div>
                    <img id="screen-img" src="/screenshot" alt="Phone Screen" onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'320\\' height=\\'711\\' viewBox=\\'0 0 320 711\\'><rect width=\\'100%\\' height=\\'100%\\' fill=\\'%23111827\\'/><text x=\\'50%\\' y=\\'50%\\' dominant-baseline=\\'middle\\' text-anchor=\\'middle\\' fill=\\'%2394a3b8\\'>Offline</text></svg>'">
                    <div id="canvas-overlay" class="canvas-overlay"></div>
                </div>
                <div class="control-row">
                    <button class="btn btn-secondary" onclick="sendNav('back')">Back</button>
                    <button class="btn btn-secondary" onclick="sendNav('home')">Home</button>
                    <button class="btn btn-secondary" onclick="sendNav('recents')">Recents</button>
                    <button class="btn btn-secondary" onclick="sendNav('wakeup')">Wake Up</button>
                </div>
            </div>

            <!-- Right Pane: Actions and Custom Commands -->
            <div style="display: flex; flex-direction: column; gap: 24px;">
                <!-- Global Destination Routing Selector -->
                <div class="glass-card" style="padding: 16px 24px; flex-direction: row; align-items: center; justify-content: space-between; gap: 12px;">
                    <span style="font-weight: 600; font-size: 15px;">Route Commands via Helper App</span>
                    <label class="switch" style="position: relative; display: inline-block; width: 50px; height: 26px;">
                        <input type="checkbox" id="send-to-helper-chk" style="opacity: 0; width: 0; height: 0;">
                        <span class="slider" style="position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: rgba(255,255,255,0.1); transition: .4s; border-radius: 34px; border: 1px solid var(--card-border);"></span>
                    </label>
                </div>

                <!-- Tab Controls -->
                <div class="tab-container">
                    <button id="tab-actions-btn" class="tab-btn active" onclick="switchTab('actions')">Manual Controls</button>
                    <button id="tab-macros-btn" class="tab-btn" onclick="switchTab('macros')">Macros & Recording</button>
                </div>

                <!-- Tab content: Manual Controls -->
                <div id="tab-actions" class="glass-card">
                    <div class="panel-header">Quick Action</div>
                    
                    <div class="control-row">
                        <div class="input-group">
                            <label>Text / App Name / Button Label</label>
                            <input type="text" id="action-text" placeholder="e.g. Telegram, click label, or write text">
                        </div>
                    </div>
                    
                    <div class="control-row">
                        <button class="btn" onclick="sendTextAction('open')">Open App</button>
                        <button class="btn" onclick="sendTextAction('click')">Click Label</button>
                        <button class="btn" onclick="sendTextAction('write')">Type Text</button>
                    </div>

                    <div class="panel-header" style="margin-top: 16px;">Console Logs</div>
                    <div id="logs" class="logs-container">
                        <div class="log-line">No logs yet...</div>
                    </div>
                </div>

                <!-- Tab content: Macro & Recording -->
                <div id="tab-macros" class="glass-card" style="display: none;">
                    <div class="panel-header">
                        <span>Macro Recorder</span>
                        <button id="record-btn" class="btn btn-danger" onclick="toggleRecording()">Record Mode: OFF</button>
                    </div>

                    <div id="recording-panel" style="display: none; flex-direction: column; gap: 12px;">
                        <div class="macro-step-list" id="recorded-steps">
                            <!-- Steps added dynamically -->
                        </div>
                        <div class="control-row">
                            <button class="btn btn-secondary" onclick="addSleepStep()">Add Delay (1s)</button>
                            <button class="btn btn-danger" onclick="clearRecordedSteps()">Clear Steps</button>
                        </div>
                        <div class="control-row" style="margin-top: 12px;">
                            <div class="input-group">
                                <label>Macro Name</label>
                                <input type="text" id="macro-name" placeholder="e.g. open_telegram_saved">
                            </div>
                            <button class="btn" onclick="saveMacro()" style="margin-top: 22px;">Save Macro</button>
                        </div>
                    </div>

                    <div class="panel-header" style="margin-top: 16px;">Saved Macros</div>
                    <div class="macro-list" id="saved-macros-list">
                        <!-- Macros populated dynamically -->
                    </div>
                </div>
            </div>
        </div>

        <script>
            // Adjust container aspect-ratio to match screenshot image dimensions
            window.addEventListener('DOMContentLoaded', () => {
                let img = document.getElementById('screen-img');
                const adjustAspect = () => {
                    if (img.naturalWidth && img.naturalHeight) {
                        let container = document.querySelector('.screen-container');
                        container.style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`;
                    }
                };
                img.addEventListener('load', adjustAspect);
                if (img.complete) adjustAspect();
            });

            let isRecording = false;
            let recordedSteps = [];
            let startX = 0, startY = 0;
            let isDragging = false;

            function getSendToHelper() {
                return document.getElementById('send-to-helper-chk').checked;
            }

            // Connect status polling
            async function checkStatus() {
                try {
                    let res = await fetch('/status');
                    let data = await res.json();
                    
                    let dotMain = document.getElementById('status-dot-main');
                    let textMain = document.getElementById('status-text-main');
                    if (data.main_connected) {
                        dotMain.className = 'status-dot online';
                        textMain.innerText = 'Online' + (data.main_accessibility_active ? ' (Access Active)' : ' (No Access)');
                    } else {
                        dotMain.className = 'status-dot';
                        textMain.innerText = 'Offline';
                    }

                    let dotHelper = document.getElementById('status-dot-helper');
                    let textHelper = document.getElementById('status-text-helper');
                    if (data.helper_connected) {
                        dotHelper.className = 'status-dot online';
                        textHelper.innerText = 'Online' + (data.helper_accessibility_active ? ' (Access Active)' : ' (No Access)');
                    } else {
                        dotHelper.className = 'status-dot';
                        textHelper.innerText = 'Offline';
                    }
                } catch(e) {}
            }
            setInterval(checkStatus, 3000);
            checkStatus();

            // Poll Phone Logs
            async function getLogs() {
                try {
                    let res = await fetch('/phone_logs');
                    let data = await res.json();
                    let container = document.getElementById('logs');
                    if (data.logs && data.logs.length > 0) {
                        container.innerHTML = '';
                        data.logs.forEach(log => {
                            let div = document.createElement('div');
                            div.className = 'log-line';
                            if (log.includes('ActionExecutor:')) {
                                div.className = 'log-line highlight';
                            }
                            div.innerText = log;
                            container.appendChild(div);
                        });
                        container.scrollTop = container.scrollHeight;
                    }
                } catch(e) {}
            }
            setInterval(getLogs, 2000);
            getLogs();

            // Load saved macros
            async function loadMacros() {
                try {
                    let res = await fetch('/custom_commands');
                    let data = await res.json();
                    let list = document.getElementById('saved-macros-list');
                    list.innerHTML = '';
                    Object.keys(data).forEach(name => {
                        let steps = data[name];
                        let card = document.createElement('div');
                        card.className = 'macro-card';
                        card.innerHTML = `
                            <div class="macro-card-title">${name}</div>
                            <div class="macro-card-steps">${steps.length} actions</div>
                            <div class="control-row">
                                <button class="btn" onclick="runMacro('${name}')" style="flex: 1;">Run</button>
                                <button class="btn btn-secondary btn-danger" onclick="deleteMacro('${name}')" style="padding: 10px;">Del</button>
                            </div>
                        `;
                        list.appendChild(card);
                    });
                } catch(e) {}
            }
            loadMacros();

            // Refresh Screen Screenshot
            async function refreshScreenshot() {
                let loader = document.getElementById('screen-loader');
                loader.style.display = 'block';
                try {
                    await fetch('/screenshot?send_to_helper=' + getSendToHelper(), { method: 'POST' });
                    document.getElementById('screen-img').src = '/screenshot?' + Date.now();
                } catch(e) {
                    alert('Screenshot failed: ' + e);
                } finally {
                    loader.style.display = 'none';
                }
            }

            // Canvas Interaction
            let overlay = document.getElementById('canvas-overlay');
            overlay.addEventListener('mousedown', (e) => {
                let rect = overlay.getBoundingClientRect();
                startX = (e.clientX - rect.left) / rect.width;
                startY = (e.clientY - rect.top) / rect.height;
                isDragging = true;
            });

            overlay.addEventListener('mouseup', (e) => {
                if (!isDragging) return;
                isDragging = false;
                let rect = overlay.getBoundingClientRect();
                let endX = (e.clientX - rect.left) / rect.width;
                let endY = (e.clientY - rect.top) / rect.height;

                let dist = Math.hypot(endX - startX, endY - startY);
                if (dist < 0.02) {
                    // Tap action
                    handleTap(startX, startY);
                } else {
                    // Swipe action
                    handleSwipe(startX, startY, endX, endY);
                }
            });

            async function handleTap(x, y) {
                let img = document.getElementById('screen-img');
                let sendX = x;
                let sendY = y;
                if (img && img.naturalWidth && img.naturalHeight) {
                    sendX = Math.round(x * img.naturalWidth);
                    sendY = Math.round(y * img.naturalHeight);
                    console.log(`handleTap: using absolute coordinates (${sendX}, ${sendY}) based on image natural size ${img.naturalWidth}x${img.naturalHeight}`);
                } else {
                    sendX = Math.round(x * 1000) / 1000;
                    sendY = Math.round(y * 1000) / 1000;
                }

                if (isRecording) {
                    recordedSteps.push({ action: 'tap', x: sendX, y: sendY });
                    updateStepsUI();
                } else {
                    await fetch('/execute', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ action: 'tap', x: sendX, y: sendY, send_to_helper: getSendToHelper() })
                    });
                    setTimeout(refreshScreenshot, 1200);
                }
            }

            async function handleSwipe(x1, y1, x2, y2) {
                let img = document.getElementById('screen-img');
                let sendX1 = x1, sendY1 = y1, sendX2 = x2, sendY2 = y2;
                if (img && img.naturalWidth && img.naturalHeight) {
                    sendX1 = Math.round(x1 * img.naturalWidth);
                    sendY1 = Math.round(y1 * img.naturalHeight);
                    sendX2 = Math.round(x2 * img.naturalWidth);
                    sendY2 = Math.round(y2 * img.naturalHeight);
                    console.log(`handleSwipe: using absolute start (${sendX1}, ${sendY1}) and end (${sendX2}, ${sendY2})`);
                } else {
                    sendX1 = Math.round(x1 * 1000) / 1000;
                    sendY1 = Math.round(y1 * 1000) / 1000;
                    sendX2 = Math.round(x2 * 1000) / 1000;
                    sendY2 = Math.round(y2 * 1000) / 1000;
                }

                if (isRecording) {
                    recordedSteps.push({ action: 'swipe', x: sendX1, y: sendY1, x2: sendX2, y2: sendY2, duration: 300 });
                    updateStepsUI();
                } else {
                    await fetch('/execute', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ action: 'swipe', x: sendX1, y: sendY1, x2: sendX2, y2: sendY2, duration: 300, send_to_helper: getSendToHelper() })
                    });
                    setTimeout(refreshScreenshot, 1500);
                }
            }

            // Quick Text actions
            async function sendTextAction(action) {
                let input = document.getElementById('action-text');
                let val = input.value.trim();
                if (!val) return;

                let payload = { action };
                if (action === 'open' || action === 'write') {
                    payload.text = val;
                } else if (action === 'click') {
                    payload.label = val;
                }

                if (isRecording) {
                    recordedSteps.push(payload);
                    updateStepsUI();
                    input.value = '';
                } else {
                    await fetch('/execute', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ ...payload, send_to_helper: getSendToHelper() })
                    });
                    input.value = '';
                    setTimeout(refreshScreenshot, 1500);
                }
            }

            async function sendNav(action) {
                if (isRecording) {
                    recordedSteps.push({ action });
                    updateStepsUI();
                } else {
                    await fetch('/execute', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ action, send_to_helper: getSendToHelper() })
                    });
                    setTimeout(refreshScreenshot, 1200);
                }
            }

            // Recording controls
            function toggleRecording() {
                isRecording = !isRecording;
                let btn = document.getElementById('record-btn');
                let panel = document.getElementById('recording-panel');
                if (isRecording) {
                    btn.innerText = 'Record Mode: ON';
                    btn.className = 'btn btn-danger';
                    panel.style.display = 'flex';
                } else {
                    btn.innerText = 'Record Mode: OFF';
                    btn.className = 'btn btn-secondary';
                    panel.style.display = 'none';
                }
            }

            function updateStepsUI() {
                let container = document.getElementById('recorded-steps');
                container.innerHTML = '';
                recordedSteps.forEach((step, idx) => {
                    let div = document.createElement('div');
                    div.className = 'macro-step';
                    let desc = '';
                    if (step.action === 'tap') desc = `Tap (${step.x}, ${step.y})`;
                    else if (step.action === 'swipe') desc = `Swipe (${step.x}, ${step.y}) -> (${step.x2}, ${step.y2})`;
                    else if (step.action === 'open') desc = `Open App "${step.text}"`;
                    else if (step.action === 'click') desc = `Click Button "${step.label}"`;
                    else if (step.action === 'write') desc = `Type text "${step.text}"`;
                    else if (step.action === 'sleep') desc = `Wait ${step.duration}ms`;
                    else desc = step.action.toUpperCase();

                    div.innerHTML = `
                        <span>${idx + 1}. ${desc}</span>
                        <button class="btn btn-secondary" onclick="removeStep(${idx})" style="padding: 4px 8px; font-size: 12px;">X</button>
                    `;
                    container.appendChild(div);
                });
            }

            function addSleepStep() {
                recordedSteps.push({ action: 'sleep', duration: 1000 });
                updateStepsUI();
            }

            function removeStep(idx) {
                recordedSteps.splice(idx, 1);
                updateStepsUI();
            }

            function clearRecordedSteps() {
                recordedSteps = [];
                updateStepsUI();
            }

            async function saveMacro() {
                let name = document.getElementById('macro-name').value.trim();
                if (!name) return alert('Enter macro name');
                if (recordedSteps.length === 0) return alert('No steps to save');

                let res = await fetch('/custom_commands', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, steps: recordedSteps })
                });
                let data = await res.json();
                if (data.status === 'success') {
                    alert('Macro saved successfully');
                    document.getElementById('macro-name').value = '';
                    clearRecordedSteps();
                    loadMacros();
                }
            }

            async function runMacro(name) {
                alert('Running macro ' + name);
                await fetch('/execute_custom/' + name + '?send_to_helper=' + getSendToHelper(), { method: 'POST' });
                setTimeout(refreshScreenshot, 2000);
            }

            async function deleteMacro(name) {
                if (confirm('Delete macro ' + name + '?')) {
                    await fetch('/custom_commands/' + name, { method: 'DELETE' });
                    loadMacros();
                }
            }

            // Tab Switching
            function switchTab(tab) {
                document.getElementById('tab-actions').style.display = tab === 'actions' ? 'flex' : 'none';
                document.getElementById('tab-macros').style.display = tab === 'macros' ? 'flex' : 'none';
                document.getElementById('tab-actions-btn').className = tab === 'actions' ? 'tab-btn active' : 'tab-btn';
                document.getElementById('tab-macros-btn').className = tab === 'macros' ? 'tab-btn active' : 'tab-btn';
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    print("Starting Phone Controller Backend on port 10555...")
    uvicorn.run(app, host="0.0.0.0", port=10555)

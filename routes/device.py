import asyncio
import json
from fastapi import APIRouter, HTTPException, Request, Response, UploadFile, File
from fastapi.responses import FileResponse
from core import state
from core.config import APK_PATH

router = APIRouter()

@router.post("/screenshot")
async def request_screenshot(send_to_helper: bool = False):
    """Ask the phone to take a screenshot and return it as PNG."""
    if send_to_helper:
        conn = state.active_helper_connection
        if conn is None:
            raise HTTPException(status_code=503, detail="Helper app is not connected.")
    else:
        conn = state.active_connection
        if conn is None:
            raise HTTPException(status_code=503, detail="Main controller app is not connected.")

    state.latest_screenshot = None
    state.screenshot_event.clear()

    await conn.send_text(json.dumps({"action": "screenshot"}))

    try:
        await asyncio.wait_for(state.screenshot_event.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timed out waiting for screenshot from phone.")

    if state.latest_screenshot is None:
        raise HTTPException(status_code=500, detail="Screenshot data was empty.")

    return Response(content=state.latest_screenshot, media_type="image/png")

@router.get("/screenshot")
async def get_latest_screenshot():
    """Returns the most recently captured screenshot as PNG."""
    if state.latest_screenshot is None:
        raise HTTPException(status_code=404, detail="No screenshot available. Call POST /screenshot first.")
    return Response(content=state.latest_screenshot, media_type="image/png")

@router.get("/phone_logs")
async def get_phone_logs():
    """Returns stored logs from the phone."""
    return {"logs": state.phone_logs}

@router.get("/status")
async def get_status():
    return {
        "main_connected": state.active_connection is not None,
        "main_accessibility_active": state.main_accessibility_active,
        "helper_connected": state.active_helper_connection is not None,
        "helper_accessibility_active": state.helper_accessibility_active,
        "client_version": state.client_version,
        "latest_version": state.latest_version
    }

@router.get("/version")
async def get_version():
    return {"latest_version": state.latest_version}

@router.post("/version")
async def set_version(version: str):
    state.latest_version = version
    return {"status": "success", "latest_version": state.latest_version}

@router.post("/upload_apk")
async def upload_apk(version: str, file: UploadFile = File(...)):
    state.latest_version = version
    with open(APK_PATH, "wb") as buffer:
        content = await file.read()
        buffer.write(content)
    return {"status": "success", "filename": "app-release.apk", "latest_version": state.latest_version}

@router.get("/apk")
async def get_apk():
    import os
    if not os.path.exists(APK_PATH):
        raise HTTPException(status_code=404, detail="APK file not found on server.")
    return FileResponse(APK_PATH, media_type="application/vnd.android.package-archive", filename="app-release.apk")

@router.post("/update")
async def trigger_update(request: Request, send_to_helper: bool = False):
    if send_to_helper:
        conn = state.active_helper_connection
        if conn is None:
            raise HTTPException(status_code=503, detail="Helper app is not connected.")
    else:
        conn = state.active_connection
        if conn is None:
            raise HTTPException(status_code=503, detail="Main controller app is not connected.")
    
    download_url = f"http://{request.base_url.netloc}/apk"
    payload = {
        "action": "update",
        "url": download_url,
        "version": state.latest_version
    }
    await conn.send_text(json.dumps(payload))
    return {"status": "success", "message": "Update command sent to phone", "url": download_url}

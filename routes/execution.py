import json
import base64
import asyncio
import urllib.request
from fastapi import APIRouter, HTTPException, BackgroundTasks
from core import state
from core.config import OPENROUTER_API_KEY
from models import CommandModel

router = APIRouter()

async def relay_step(step_data: dict, send_to_helper: bool = False):
    if send_to_helper:
        conn = state.active_helper_connection
    else:
        conn = state.active_connection
        
    if conn is None:
        raise HTTPException(status_code=503, detail="Target connection is not online.")
    
    payload = step_data.copy()
    width = state.device_width or 1080
    height = state.device_height or 2400
    
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

@router.post("/execute")
async def execute_command(command: CommandModel):
    if command.send_to_helper:
        conn = state.active_helper_connection
        if conn is None:
            raise HTTPException(status_code=503, detail="Helper app is not connected.")
    else:
        conn = state.active_connection
        if conn is None:
            raise HTTPException(status_code=503, detail="Main controller app is not connected.")

    try:
        payload = command.model_dump(exclude_none=True)
        if "send_to_helper" in payload:
            del payload["send_to_helper"]

        width = state.device_width or 1080
        height = state.device_height or 2400

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

@router.post("/execute_custom/{name}")
async def execute_custom(name: str, send_to_helper: bool = False):
    commands = state.load_custom_commands()
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
            await asyncio.sleep(1.0)
            
    return {"status": "success", "message": f"Custom command '{name}' completed execution"}

@router.post("/reset_accessibility")
async def reset_accessibility():
    conn = state.active_helper_connection
    if conn is None:
        raise HTTPException(status_code=503, detail="Helper app is not connected.")
    try:
        await conn.send_text(json.dumps({"action": "reset_accessibility"}))
        return {"status": "success", "message": "Reset accessibility command sent to helper app"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- AI Assistant Background Loop ---
from services.assistant_service import run_agent_loop

@router.post("/assistant/start")
async def start_assistant(goal: str, background_tasks: BackgroundTasks, max_steps: int = 15):
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="OpenRouter API Key not set in backend.")
    if state.agent_running:
        raise HTTPException(status_code=400, detail="An assistant task is already running.")
        
    state.agent_running = True
    state.agent_goal = goal
    state.agent_task = asyncio.create_task(run_agent_loop(goal, relay_step, max_steps=max_steps))
    return {"status": "success", "message": f"Started assistant for: '{goal}' (limit: {max_steps} steps)"}

@router.get("/assistant/status")
async def get_assistant_status():
    return {
        "running": state.agent_running,
        "goal": state.agent_goal,
        "logs": state.agent_logs
    }

@router.post("/assistant/stop")
async def stop_assistant():
    if not state.agent_running:
        return {"status": "success", "message": "No assistant task was running."}
        
    state.agent_running = False
    if state.agent_task:
        state.agent_task.cancel()
    return {"status": "success", "message": "Assistant task stopped."}

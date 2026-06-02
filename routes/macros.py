from fastapi import APIRouter, HTTPException
from core import state
from models import CustomCommandModel

router = APIRouter()

@router.get("/custom_commands")
async def get_custom_commands():
    return state.load_custom_commands()

@router.post("/custom_commands")
async def create_custom_command(cmd: CustomCommandModel):
    commands = state.load_custom_commands()
    commands[cmd.name] = cmd.steps
    state.save_custom_commands(commands)
    return {"status": "success", "message": f"Custom command '{cmd.name}' saved."}

@router.delete("/custom_commands/{name}")
async def delete_custom_command(name: str):
    commands = state.load_custom_commands()
    if name in commands:
        del commands[name]
        state.save_custom_commands(commands)
        return {"status": "success", "message": f"Custom command '{name}' deleted."}
    raise HTTPException(status_code=404, detail="Custom command not found")

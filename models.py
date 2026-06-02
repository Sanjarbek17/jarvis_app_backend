from pydantic import BaseModel

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

class CustomCommandModel(BaseModel):
    name: str
    steps: list[dict]

import os
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def get_dashboard():
    template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "dashboard.html")
    with open(template_path, "r") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

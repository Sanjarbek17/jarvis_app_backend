import uvicorn
from fastapi import FastAPI
from routes import websocket, device, execution, macros, web
from core.config import HOST, PORT

app = FastAPI()

# Include routers
app.include_router(websocket.router)
app.include_router(device.router)
app.include_router(execution.router)
app.include_router(macros.router)
app.include_router(web.router)

if __name__ == "__main__":
    print(f"Starting Phone Controller Backend on port {PORT}...")
    uvicorn.run("main:app", host=HOST, port=PORT, reload=True)

"""NetTwin — точка входа FastAPI.

Порядок важен: сначала API-роутеры и WebSocket, в конце — статика фронтенда.
"""
import asyncio
import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .routers import (agent, backup, hosts, interfaces, map, measurements,
                      sessions, system, ws)
from .runtime import runtime
from .services.bus import hub_events, hub_ifaces
from .services.oui import load_extra

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-18s %(levelname)-7s %(message)s",
)
logging.getLogger("scapy.runtime").setLevel(logging.ERROR)
log = logging.getLogger("nettwin")

app = FastAPI(title="NetTwin", version=config.VERSION,
              docs_url="/api/docs", openapi_url="/api/openapi.json")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception("Необработанная ошибка на %s: %s", request.url.path, exc)
    return JSONResponse(status_code=500,
                        content={"detail": f"Внутренняя ошибка сервера: {exc}"})


@app.on_event("startup")
async def on_startup():
    db.init()
    if os.path.exists(config.OUI_EXTRA):
        load_extra(config.OUI_EXTRA)
    hub_ifaces.set_loop(asyncio.get_running_loop())
    hub_events.set_loop(asyncio.get_running_loop())
    runtime.start()
    log.info("NetTwin %s запущен: http://localhost:%d (БД: %s)",
             config.VERSION, config.PORT, config.DB_PATH)


@app.on_event("shutdown")
def on_shutdown():
    runtime.stop()
    log.info("NetTwin остановлен")


# --- REST API ---
app.include_router(system.router, prefix="/api", tags=["system"])
app.include_router(interfaces.router, prefix="/api", tags=["interfaces"])
app.include_router(hosts.router, prefix="/api", tags=["hosts"])
app.include_router(map.router, prefix="/api", tags=["map"])
app.include_router(sessions.router, prefix="/api", tags=["sessions"])
app.include_router(measurements.router, prefix="/api", tags=["measurements"])
app.include_router(backup.router, prefix="/api", tags=["backup"])
app.include_router(agent.router, prefix="/api", tags=["agent"])

# --- WebSocket ---
app.include_router(ws.router)

# --- Статика фронтенда (последним, чтобы не перекрывать /api) ---
app.mount("/", StaticFiles(directory=str(config.STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=config.PORT, reload=False)

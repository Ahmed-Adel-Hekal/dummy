"""
app.py — SignalMind SaaS entry point (thin shell)

All logic lives in:
  db.py          — database + CRUD
  auth.py        — JWT + session helpers
  pipelines.py   — background generation + scheduler
  ui.py          — CSS, sidebar, page helpers
  routes/auth.py     generate.py  strategy.py
         calendar.py account.py   insights.py  api.py
"""
import asyncio
import logging
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from db import init_db, OUTPUT_ROOT
import pipelines   # registers _scheduler_pool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("SignalMind")

# ── App ───────────────────────────────────────────────────────
app = FastAPI(title="SignalMind SaaS", version="3.0.0")

# ── Middleware ────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


class PerfHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        import time as _t
        t0 = _t.perf_counter()
        response = await call_next(request)
        ms = round((_t.perf_counter() - t0) * 1000)
        response.headers["X-Response-Time"]        = f"{ms}ms"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"]        = "SAMEORIGIN"
        return response


app.add_middleware(PerfHeadersMiddleware)

# ── Static files ──────────────────────────────────────────────
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=str(OUTPUT_ROOT)), name="outputs")

# ── Routers ───────────────────────────────────────────────────
from routes.auth     import router as auth_router
from routes.generate import router as generate_router
from routes.strategy import router as strategy_router
from routes.calendar import router as calendar_router
from routes.account  import router as account_router
from routes.insights import router as insights_router
from routes.api      import router as api_router

app.include_router(auth_router)
app.include_router(generate_router)
app.include_router(strategy_router)
app.include_router(calendar_router)
app.include_router(account_router)
app.include_router(insights_router)
app.include_router(api_router)


# ── Startup ───────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(pipelines._scheduler_loop())
    logger.info("SignalMind v3 started — scheduler active")

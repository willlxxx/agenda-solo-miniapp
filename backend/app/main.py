"""Punto de entrada FastAPI."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import agenda, auth, availability, blocked, public, services, waitlist
from app.config import get_settings

app = FastAPI(title="Agenda Solo API", version="0.1.0", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (
    auth.router,
    services.router,
    availability.router,
    blocked.router,
    agenda.router,
    waitlist.router,
    public.router,
):
    app.include_router(router, prefix="/api/v1")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"estado": "ok"}

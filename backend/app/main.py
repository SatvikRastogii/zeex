import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, auth, boms, health, org, rfqs, vendor
from app.config import get_settings

settings = get_settings()
logging.basicConfig(
    level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

app = FastAPI(title=settings.app_title)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
for r in (
    health.router,
    auth.router,
    org.router,
    boms.catalog_router,
    boms.router,
    rfqs.router,
    vendor.router,
    admin.router,
):
    app.include_router(r, prefix="/api")

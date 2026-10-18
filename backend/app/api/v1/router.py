from fastapi import APIRouter

from app.api.v1 import data, health, routes

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(routes.router)
api_router.include_router(data.router)

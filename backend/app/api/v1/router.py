from fastapi import APIRouter

from app.api.v1 import data, feedback, health, preferences, routes

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(routes.router)
api_router.include_router(data.router)
api_router.include_router(preferences.router)
api_router.include_router(feedback.router)

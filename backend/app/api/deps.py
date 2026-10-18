"""FastAPI dependencies giving endpoints access to shared resources."""

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine

from app.cache.redis import ResilientRedis
from app.core.config import Settings
from app.integrations.otp.client import OtpClient


@dataclass(frozen=True)
class Resources:
    """Long-lived clients created at startup and closed at shutdown."""

    settings: Settings
    db_engine: AsyncEngine
    redis: ResilientRedis
    otp: OtpClient


def get_resources(request: Request) -> Resources:
    resources: Resources = request.app.state.resources
    return resources

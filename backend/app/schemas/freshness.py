from datetime import datetime

from pydantic import BaseModel

from app.core.freshness import Freshness, SourceKind


class SourceFreshnessItem(BaseModel):
    source: str
    kind: SourceKind
    freshness: Freshness
    # "ok" or "failing": whether the most recent fetch attempt succeeded.
    last_fetch: str
    observed_at: datetime | None
    fetched_at: datetime | None
    valid_until: datetime | None


class DataFreshnessResponse(BaseModel):
    generated_at: datetime
    sources: list[SourceFreshnessItem]

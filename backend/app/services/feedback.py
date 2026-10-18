"""Store anonymous feedback on a route result.

What is stored: the stars, the cleaned comment, and an HMAC of the result
reference (``search_id:route_id``). No locations, client address or time of
travel. Rating the same result again replaces the earlier rating, so a
double submit never creates two rows.

Comments are free text, and people do type contact details into them;
email addresses and phone numbers are masked before storage.
"""

import hashlib
import hmac
import re
import uuid

from sqlalchemy import Boolean, literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.models import RouteFeedback
from app.schemas.feedback import FeedbackRequest, FeedbackResponse

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# 10+ digits with common separators: phone numbers, not times or route numbers.
_PHONE = re.compile(r"\+?\(?(?:\d[\s().-]*){10,}")


def clean_comment(comment: str | None) -> str | None:
    if comment is None:
        return None
    text = _CONTROL.sub(" ", comment)
    text = _EMAIL.sub("[email removed]", text)
    text = _PHONE.sub("[number removed] ", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text or None


class FeedbackStore:
    def __init__(self, engine: AsyncEngine, secret: bytes) -> None:
        self._engine = engine
        self._secret = secret

    def reference(self, search_id: str, route_id: str) -> str:
        return hmac.new(
            self._secret, f"{search_id}:{route_id}".encode(), hashlib.sha256
        ).hexdigest()

    async def record(self, feedback: FeedbackRequest) -> FeedbackResponse:
        values = {
            "feedback_id": uuid.uuid4(),
            "route_result_ref_hash": self.reference(feedback.search_id, feedback.route_id),
            "stars": feedback.stars,
            "comment": clean_comment(feedback.comment),
        }
        insert_stmt = insert(RouteFeedback).values(**values)
        upsert = insert_stmt.on_conflict_do_update(
            index_elements=[RouteFeedback.route_result_ref_hash],
            set_={"stars": insert_stmt.excluded.stars, "comment": insert_stmt.excluded.comment},
        )
        # PostgreSQL sets xmax to 0 on a fresh insert, not on an update.
        inserted = literal_column("xmax = 0", Boolean).label("inserted")
        async with AsyncSession(self._engine) as session, session.begin():
            result = await session.execute(upsert.returning(RouteFeedback.feedback_id, inserted))
            row = result.one()
        return FeedbackResponse(
            feedback_id=str(row.feedback_id), status="recorded" if row.inserted else "updated"
        )

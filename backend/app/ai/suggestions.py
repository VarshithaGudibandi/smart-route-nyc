"""Ask a language model for transfer proposals.

The model sees only the timetable facts for one journey (no rider text, no
locations) and answers in the strict ``ModelProposals`` schema. Its answer
is a list of hypotheses: each is checked against the same facts as rule
proposals, then verified by OTP. Nothing the model writes reaches the rider
except the short ``reason`` of a proposal that passed verification.
"""

import json
from typing import Any

import structlog
from pydantic import ValidationError

from app.ai.guard import GuardedLlm
from app.ai.prompts import SUGGESTIONS_SYSTEM, suggestions_user_message
from app.schemas.suggestions import ModelProposals, TransferProposal
from app.services.suggestions.context import TransferFacts

logger = structlog.get_logger(__name__)

MAX_OUTPUT_TOKENS = 500


class InvalidProposalsError(Exception):
    """The model's reply did not match the proposals schema."""


def facts_document(facts: TransferFacts) -> dict[str, Any]:
    """The compact fact sheet the model sees. IDs here form its allow-list."""

    def lines(options: tuple[Any, ...]) -> list[dict[str, Any]]:
        return [
            {
                "route_id": line.route_id,
                "line": line.route,
                "stops": line.stops,
                "minutes_between_trains": round(line.headway_s / 60, 1) if line.headway_s else None,
                "alerts": list(line.alerts),
            }
            for line in options
        ]

    segment = facts.segment
    return {
        "board": {"station_id": segment.board_station, "name": facts.board_name},
        "alight": {"station_id": segment.alight_station, "name": facts.alight_name},
        "current_route": {
            "route_ids": list(segment.route_ids),
            "transfer_station_ids": list(segment.transfer_stations),
        },
        "direct_lines": lines(facts.direct),
        "candidates": [
            {
                "station_id": option.station_id,
                "name": option.name,
                "to_transfer": lines(option.to_transfer),
                "to_destination": lines(option.to_destination),
            }
            for option in facts.options
        ],
    }


class ModelProposalGenerator:
    def __init__(self, llm: GuardedLlm) -> None:
        self._llm = llm

    @property
    def name(self) -> str:
        return self._llm.provider.name

    async def propose(self, facts: TransferFacts) -> list[TransferProposal]:
        document = json.dumps(facts_document(facts), separators=(",", ":"))
        response = await self._llm.complete_json(
            "suggestions", SUGGESTIONS_SYSTEM, suggestions_user_message(document), MAX_OUTPUT_TOKENS
        )
        try:
            return ModelProposals.model_validate_json(response.text.strip()).proposals
        except ValidationError as exc:
            logger.warning("llm_proposals_invalid", errors=exc.error_count())
            raise InvalidProposalsError("The model's reply did not match the schema") from exc

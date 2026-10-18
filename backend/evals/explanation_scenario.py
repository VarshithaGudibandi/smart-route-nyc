"""The route explanation cases are written against. DEMO DATA for the
elevator status and the alert: the route itself is the recorded OTP plan
from Journal Square to Columbia University (tests/fixtures)."""

import json
from datetime import datetime
from pathlib import Path

from app.integrations.otp.mapper import to_route
from app.services.accessibility.assessor import assess_route
from app.services.accessibility.snapshot import AccessibilitySnapshot, Elevator
from app.services.explanations.facts import Fact, route_facts
from app.services.transit.alerts import ActiveAlert, AlertSnapshot

FIXTURE = Path(__file__).parents[1] / "tests/fixtures/otp_plan_journal_square_to_columbia.json"
NOW = datetime.fromisoformat("2026-10-19T12:20:00+00:00")


def scenario_facts() -> list[Fact]:
    plan = json.loads(FIXTURE.read_text())
    route = to_route(plan["data"]["planConnection"]["edges"][0]["node"])
    snapshot = AccessibilitySnapshot(
        elevators_by_stop={
            "138": [Elevator("EL001", True, True, "Street to uptown platform", None, None)]
        },
        observed_at=NOW,
    )
    alerts = AlertSnapshot.build(
        [
            ActiveAlert(
                alert_id="demo",
                alert_type="Delays",
                header="Northbound [1] trains are running with delays.",
                periods=((0, None),),
                route_ids=frozenset({"MTASBWY:1"}),
                station_ids=frozenset(),
            )
        ]
    )
    route = assess_route(route, snapshot, True, NOW)
    return route_facts(route, True, snapshot, alerts, NOW)

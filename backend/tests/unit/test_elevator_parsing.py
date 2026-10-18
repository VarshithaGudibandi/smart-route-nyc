import json
from datetime import datetime
from zoneinfo import ZoneInfo

from app.ingestion.sources.mta_elevators import parse_current_outages, parse_equipment
from tests.fakes import FIXTURES

NEW_YORK = ZoneInfo("America/New_York")


def load(name: str) -> list[dict[str, object]]:
    data: list[dict[str, object]] = json.loads((FIXTURES / name).read_text())
    return data


def test_equipment_maps_complexes_to_every_gtfs_stop() -> None:
    by_id = {e.equipment_id: e for e in parse_equipment(load("mta_ene_equipments.json"))}
    assert by_id["EL223"].gtfs_stop_ids == ("A31", "L01")
    assert by_id["EL223"].station_name == "14 St"
    assert by_id["ES269"].gtfs_stop_ids == ("630", "F11")


def test_equipment_flags() -> None:
    by_id = {e.equipment_id: e for e in parse_equipment(load("mta_ene_equipments.json"))}
    assert by_id["ES422"].is_active is False
    assert by_id["EL223"].is_active is True
    assert by_id["ES103"].equipment_type == "ES"
    assert all(isinstance(e.ada_accessible, bool) for e in by_id.values())


def test_scheduled_future_outages_are_not_current() -> None:
    outages = parse_current_outages(load("mta_ene_outages.json"))
    assert set(outages) == {"ES456", "ES269"}
    assert "EL382" not in outages


def test_outage_times_are_new_york_local() -> None:
    outage = parse_current_outages(load("mta_ene_outages.json"))["ES456"]
    assert outage.started_at == datetime(2026, 10, 18, 0, 28, tzinfo=NEW_YORK)
    assert outage.reason == "Cleaning"


def test_malformed_times_become_unknown() -> None:
    raw = [{"equipment": "EL1", "isupcomingoutage": "N", "outagedate": "soon", "reason": ""}]
    outage = parse_current_outages(raw)["EL1"]
    assert outage.started_at is None and outage.reason is None

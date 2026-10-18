"""Tests for deriving wheelchair_boarding from MTA's elevator inventory.

Run with: python -m unittest discover -s otp/tests
The GTFS archive is a minimal synthetic one built per test.
"""

import csv
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from gtfs_accessibility import accessible_stop_ids, enrich_wheelchair_boarding

STOPS = """stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station
120,96 St,40.79,-73.97,1,
120N,96 St,40.79,-73.97,,120
120S,96 St,40.79,-73.97,,120
117,116 St-Columbia University,40.80,-73.96,1,
117N,116 St-Columbia University,40.80,-73.96,,117
"""


def elevator(stops: str, ada: str = "Y", active: str = "Y", kind: str = "EL") -> dict[str, str]:
    return {
        "equipmenttype": kind,
        "isactive": active,
        "ADA": ada,
        "elevatorsgtfsstopid": stops,
    }


class AccessibleStopIdsTest(unittest.TestCase):
    def test_only_active_ada_elevators_count(self) -> None:
        equipment = [
            elevator("120"),
            elevator("117", ada="N"),
            elevator("130", active="N"),
            elevator("140", kind="ES"),
        ]
        self.assertEqual(accessible_stop_ids(equipment), {"120"})

    def test_station_complexes_cover_every_stop(self) -> None:
        self.assertEqual(accessible_stop_ids([elevator("132/D19/L02")]), {"132", "D19", "L02"})


class EnrichTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.gtfs = Path(self.tmp.name) / "subway.zip"
        with zipfile.ZipFile(self.gtfs, "w") as archive:
            archive.writestr("stops.txt", STOPS)
            archive.writestr("routes.txt", "route_id\n1\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def stops(self) -> dict[str, str]:
        with zipfile.ZipFile(self.gtfs) as archive:
            rows = csv.DictReader(io.StringIO(archive.read("stops.txt").decode()))
            return {row["stop_id"]: row["wheelchair_boarding"] for row in rows}

    def test_marks_stations_and_their_platforms(self) -> None:
        stats = enrich_wheelchair_boarding(self.gtfs, json.dumps([elevator("120")]).encode())

        self.assertEqual(
            self.stops(),
            {"120": "1", "120N": "1", "120S": "1", "117": "2", "117N": "2"},
        )
        self.assertEqual((stats.stations, stats.accessible_stations), (2, 1))

    def test_other_files_are_unchanged(self) -> None:
        enrich_wheelchair_boarding(self.gtfs, json.dumps([]).encode())
        with zipfile.ZipFile(self.gtfs) as archive:
            self.assertEqual(archive.read("routes.txt"), b"route_id\n1\n")

    def test_reports_equipment_for_unknown_stops(self) -> None:
        stats = enrich_wheelchair_boarding(self.gtfs, json.dumps([elevator("999")]).encode())
        self.assertEqual(stats.unmatched_equipment_stops, ["999"])

    def test_rerunning_is_idempotent(self) -> None:
        payload = json.dumps([elevator("120")]).encode()
        enrich_wheelchair_boarding(self.gtfs, payload)
        first = self.stops()
        enrich_wheelchair_boarding(self.gtfs, payload)
        self.assertEqual(self.stops(), first)


if __name__ == "__main__":
    unittest.main()

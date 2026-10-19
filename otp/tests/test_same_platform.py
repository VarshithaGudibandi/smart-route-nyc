"""Tests for shortening transfers between lines that share a platform.

Run with: python -m unittest discover -s otp/tests
The GTFS archive is a minimal synthetic one built per test.
"""

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from same_platform import STATIONS_FILE, shorten_same_platform_transfers

# Chambers St: the 1 and 2 stop at the same northbound platform. 50 St: only
# the 1. Times Sq: MTA already allows 0 seconds.
STOPS = """stop_id,stop_name,parent_station
137,Chambers St,
137N,Chambers St,137
126,50 St,
126N,50 St,126
127,Times Sq-42 St,
127N,Times Sq-42 St,127
"""
TRIPS = "route_id,service_id,trip_id\n1,W,t1\n2,W,t2\n"
STOP_TIMES = """trip_id,arrival_time,departure_time,stop_id,stop_sequence
t1,08:00:00,08:00:00,137N,1
t1,08:10:00,08:10:00,127N,2
t1,08:12:00,08:12:00,126N,3
t2,08:02:00,08:02:00,137N,1
t2,08:09:00,08:09:00,127N,2
"""
TRANSFERS = """from_stop_id,to_stop_id,transfer_type,min_transfer_time
137,137,2,180
126,126,2,180
127,127,2,0
137,127,2,300
"""


def archive(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("stops.txt", STOPS)
        z.writestr("trips.txt", TRIPS)
        z.writestr("stop_times.txt", STOP_TIMES)
        z.writestr("transfers.txt", TRANSFERS)


class SamePlatformTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.zip = self.dir / "mta-subway-gtfs.zip"
        archive(self.zip)

    def transfers(self) -> list[str]:
        with zipfile.ZipFile(self.zip) as z:
            return z.read("transfers.txt").decode().splitlines()

    def test_shared_platforms_lose_their_minimum_and_others_keep_it(self) -> None:
        stats = shorten_same_platform_transfers(self.zip, self.dir)
        self.assertEqual(stats.shortened, 1)
        rows = self.transfers()
        self.assertIn("137,137,2,0", rows)
        self.assertIn("126,126,2,180", rows)  # one line only
        self.assertIn("137,127,2,300", rows)  # between stations: untouched

    def test_the_station_list_names_only_our_estimates(self) -> None:
        shorten_same_platform_transfers(self.zip, self.dir)
        listed = json.loads((self.dir / STATIONS_FILE).read_text())
        self.assertEqual(listed["change_seconds"], 60)
        # Times Sq already had no minimum in MTA's data; it is not our estimate.
        self.assertEqual(
            listed["stations"],
            {"137": {"name": "Chambers St", "routes": ["1", "2"], "mta_min_transfer_s": 180}},
        )

    def test_a_second_run_changes_nothing(self) -> None:
        shorten_same_platform_transfers(self.zip, self.dir)
        first = (self.dir / STATIONS_FILE).read_text()
        stats = shorten_same_platform_transfers(self.zip, self.dir)
        self.assertEqual(stats.shortened, 0)
        self.assertEqual((self.dir / STATIONS_FILE).read_text(), first)

    def test_other_files_are_unchanged(self) -> None:
        shorten_same_platform_transfers(self.zip, self.dir)
        with zipfile.ZipFile(self.zip) as z:
            self.assertEqual(z.read("stop_times.txt").decode(), STOP_TIMES)
            self.assertEqual(io.BytesIO(z.read("stops.txt")).read().decode(), STOPS)


if __name__ == "__main__":
    unittest.main()

"""Shorten MTA's minimum transfer time where lines share a platform.

MTA's subway GTFS gives most stations a 3-minute minimum transfer to
themselves (``transfers.txt``: ``137,137,2,180``), the same whether the rider
walks between levels or steps across an island platform from the local to
the express. OpenTripPlanner applies it to every change, which hides most
local/express shortcuts.

This step finds stations where two or more lines stop at the same platform
in the same direction (the same GTFS child stop, such as ``137N`` for the 1,
2 and 3 northbound at Chambers St) and removes that station's minimum
self-transfer time. OpenTripPlanner's transfer slack (``transferSlack`` in
``router-config.json``, 1 minute) is then the whole time a change there
takes. That is an estimate, not MTA data: a few stations stack express and
local tracks on different levels under one stop ID, where the change takes
longer. Routes that rely on it are labelled by the API, which reads the
station list this step writes.

The rewritten archive is otherwise byte-for-byte identical to the original.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# OTP's slack is added to this, so a change takes ``CHANGE_SECONDS`` in all.
MIN_TRANSFER_S = 0
CHANGE_SECONDS = 60
STATIONS_FILE = "same-platform-transfers.json"


@dataclass(frozen=True)
class Stats:
    stations: int
    shortened: int


def _rows(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def shared_platforms(stops: bytes, trips: bytes, stop_times: bytes) -> dict[str, set[str]]:
    """Parent station -> routes, for stations where some platform serves 2+ routes."""
    parent = {row["stop_id"]: row.get("parent_station") or "" for row in _rows(stops)}
    route_of = {row["trip_id"]: row["route_id"] for row in _rows(trips)}
    routes_at: dict[str, set[str]] = defaultdict(set)
    for row in csv.DictReader(io.StringIO(stop_times.decode("utf-8-sig"))):
        route = route_of.get(row["trip_id"])
        if route:
            routes_at[row["stop_id"]].add(route)
    stations: dict[str, set[str]] = defaultdict(set)
    for stop_id, routes in routes_at.items():
        station = parent.get(stop_id)
        if station and len(routes) > 1:
            stations[station] |= routes
    return dict(stations)


def shorten_same_platform_transfers(gtfs_zip: Path, out_dir: Path) -> Stats:
    """Rewrite ``transfers.txt`` in ``gtfs_zip`` in place, and write the station list."""
    with zipfile.ZipFile(gtfs_zip) as source:
        entries = [(info, source.read(info.filename)) for info in source.infolist()]
    files = {info.filename: data for info, data in entries}
    stations = shared_platforms(files["stops.txt"], files["trips.txt"], files["stop_times.txt"])
    names = {row["stop_id"]: row["stop_name"] for row in _rows(files["stops.txt"])}

    text = files["transfers.txt"].decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    rows = list(reader)
    shortened: dict[str, int] = {}
    for row in rows:
        station = row["from_stop_id"]
        if station == row["to_stop_id"] and station in stations:
            minimum = int(row.get("min_transfer_time") or 0)
            if minimum > MIN_TRANSFER_S:
                shortened[station] = minimum
                row["min_transfer_time"] = str(MIN_TRANSFER_S)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)

    tmp = gtfs_zip.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w") as target:
        for info, data in entries:
            if info.filename == "transfers.txt":
                data = out.getvalue().encode()
            target.writestr(info, data)
    tmp.replace(gtfs_zip)

    # Only stations whose MTA minimum was longer are an estimate of ours. A
    # rerun on an already rewritten archive finds nothing new and keeps the list.
    path = out_dir / STATIONS_FILE
    if shortened or not path.exists():
        listed = {
            station: {
                "name": names.get(station, station),
                "routes": sorted(stations[station]),
                "mta_min_transfer_s": minimum,
            }
            for station, minimum in sorted(shortened.items())
        }
        path.write_text(
            json.dumps({"change_seconds": CHANGE_SECONDS, "stations": listed}, indent=1) + "\n"
        )
    return Stats(stations=len(stations), shortened=len(shortened))

"""Derive GTFS ``wheelchair_boarding`` for subway stations from MTA's elevator inventory.

MTA's subway GTFS leaves ``wheelchair_boarding`` empty, so OpenTripPlanner's
wheelchair routing cannot tell accessible stations from inaccessible ones and
never steers toward the former. MTA's equipment feed lists every elevator with
the GTFS stop IDs it serves. A station with at least one active ADA-accessible
elevator is marked ``1`` (accessible); every other station is marked ``2``
(not accessible). Platforms inherit their parent station's value.

Only the static inventory is used here. Live outages change by the minute and
are applied when routes are assessed, not baked into the graph.

The rewritten archive is otherwise byte-for-byte identical to the original.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ACCESSIBLE = "1"
NOT_ACCESSIBLE = "2"


@dataclass(frozen=True)
class EnrichmentStats:
    stations: int
    accessible_stations: int
    stops_updated: int
    unmatched_equipment_stops: list[str]


def accessible_stop_ids(equipment: list[dict[str, Any]]) -> set[str]:
    """GTFS stop IDs served by at least one active, ADA-accessible elevator."""
    stop_ids: set[str] = set()
    for item in equipment:
        if (
            item.get("equipmenttype") == "EL"
            and item.get("isactive") == "Y"
            and item.get("ADA") == "Y"
        ):
            # Station complexes list several stops: "132/D19/L02".
            stop_ids.update(s.strip() for s in item.get("elevatorsgtfsstopid", "").split("/"))
    stop_ids.discard("")
    return stop_ids


def enrich_wheelchair_boarding(gtfs_zip: Path, equipment_json: bytes) -> EnrichmentStats:
    """Rewrite ``stops.txt`` in ``gtfs_zip`` in place with derived accessibility."""
    accessible = accessible_stop_ids(json.loads(equipment_json))

    with zipfile.ZipFile(gtfs_zip) as source:
        entries = [(info, source.read(info.filename)) for info in source.infolist()]

    stops_index = next(i for i, (info, _) in enumerate(entries) if info.filename == "stops.txt")
    stops_info, stops_raw = entries[stops_index]
    rows = list(csv.DictReader(io.StringIO(stops_raw.decode("utf-8-sig"))))
    fieldnames = list(rows[0].keys())
    if "wheelchair_boarding" not in fieldnames:
        fieldnames.append("wheelchair_boarding")

    stations = {row["stop_id"] for row in rows if row.get("location_type") == "1"}
    for row in rows:
        station = row.get("parent_station") or row["stop_id"]
        row["wheelchair_boarding"] = ACCESSIBLE if station in accessible else NOT_ACCESSIBLE

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    entries[stops_index] = (stops_info, out.getvalue().encode("utf-8"))

    tmp = gtfs_zip.with_suffix(".enriching")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for info, data in entries:
            target.writestr(info, data)
    tmp.replace(gtfs_zip)

    return EnrichmentStats(
        stations=len(stations),
        accessible_stations=len(stations & accessible),
        stops_updated=len(rows),
        unmatched_equipment_stops=sorted(accessible - stations),
    )

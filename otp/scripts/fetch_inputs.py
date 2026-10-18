"""Download and validate the GTFS and OpenStreetMap inputs for the OTP graph.

Reads ``otp/sources.json``, downloads every source into the graph directory,
verifies published checksums where the provider offers them, derives subway
station accessibility from MTA's elevator inventory (see gtfs_accessibility.py),
and records a manifest (checksum, size, service dates) for every input.

Usage:
    python otp/scripts/fetch_inputs.py [--graph-dir data/otp/graph] [--skip-osm]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import http.client
import io
import json
import random
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# Sibling module; the script's directory is on sys.path when run directly.
from gtfs_accessibility import enrich_wheelchair_boarding

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES_FILE = REPO_ROOT / "otp" / "sources.json"
CONFIG_DIR = REPO_ROOT / "otp" / "config"

MAX_ATTEMPTS = 4
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 30.0
TIMEOUT_S = 120
# Resumed attempts allowed per file, on top of MAX_ATTEMPTS failures in a row.
MAX_RESUMES = 20
PROGRESS_STEP_BYTES = 25 << 20
# Warn when a feed stops covering service within this window.
EXPIRY_WARNING = timedelta(days=21)
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


@dataclass
class SourceResult:
    file: str
    url: str
    ok: bool
    sha256: str | None = None
    size_bytes: int | None = None
    service_start: str | None = None
    service_end: str | None = None
    feed_version: str | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def download(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest`` atomically, retrying transient failures.

    Retries use exponential backoff with full jitter. Client errors other than
    408/429 are not retried because repeating them cannot succeed. A retry
    resumes from the bytes already received when the server supports ranges,
    with ``If-Range`` so a file that changed in between is fetched whole, and
    an attempt that made progress does not use up the retry budget: a slow
    mirror can take many attempts to finish a large extract.
    """
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.unlink(missing_ok=True)
    validator: str | None = None
    failures = 0
    while True:
        have = tmp.stat().st_size if tmp.exists() else 0
        before = have
        request = urllib.request.Request(url)
        if have and validator:
            request.add_header("Range", f"bytes={have}-")
            request.add_header("If-Range", validator)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as resp:
                validator = resp.headers.get("ETag") or resp.headers.get("Last-Modified")
                resumed = resp.status == 206
                total = _expected_size(resp, have if resumed else 0)
                with tmp.open("ab" if resumed else "wb") as out:
                    have = have if resumed else 0
                    reported = have
                    while chunk := resp.read(1 << 20):
                        out.write(chunk)
                        have += len(chunk)
                        if total and have - reported >= PROGRESS_STEP_BYTES:
                            print(f"  {have >> 20} of {total >> 20} MB", file=sys.stderr)
                            reported = have
            if total and have != total:
                raise ConnectionError(f"connection closed at {have} of {total} bytes")
            tmp.replace(dest)
            return
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_STATUS:
                raise
            error: Exception = exc
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.IncompleteRead,
        ) as exc:
            error = exc
        progressed = tmp.exists() and tmp.stat().st_size > before
        failures = 0 if progressed else failures + 1
        if failures >= MAX_ATTEMPTS or (progressed and _resumes(tmp) > MAX_RESUMES):
            raise error
        delay = random.uniform(0, min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** max(failures, 1)))
        done = tmp.stat().st_size >> 20 if tmp.exists() else 0
        print(f"  retrying in {delay:.1f}s ({done} MB received; {error})", file=sys.stderr)
        time.sleep(delay)


def _expected_size(resp: http.client.HTTPResponse, offset: int) -> int | None:
    length = resp.headers.get("Content-Length")
    return offset + int(length) if length and length.isdigit() else None


_resume_counts: dict[Path, int] = {}


def _resumes(tmp: Path) -> int:
    """Count resumed attempts per file, so a server that trickles bytes forever still ends."""
    _resume_counts[tmp] = _resume_counts.get(tmp, 0) + 1
    return _resume_counts[tmp]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_gtfs_table(archive: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    if name not in archive.namelist():
        return []
    with archive.open(name) as raw:
        return list(csv.DictReader(io.TextIOWrapper(raw, "utf-8-sig")))


def inspect_gtfs(path: Path, result: SourceResult) -> None:
    """Fill in service dates and data-quality warnings for a GTFS archive."""
    with zipfile.ZipFile(path) as archive:
        calendar = read_gtfs_table(archive, "calendar.txt")
        exceptions = read_gtfs_table(archive, "calendar_dates.txt")
        feed_info = read_gtfs_table(archive, "feed_info.txt")
        stops = read_gtfs_table(archive, "stops.txt")

    # calendar_dates.txt rows with exception_type 1 add service on that date.
    added = [r["date"] for r in exceptions if r.get("exception_type") == "1"]
    starts = [r["start_date"] for r in calendar] + added
    ends = [r["end_date"] for r in calendar] + added
    if starts:
        result.service_start, result.service_end = min(starts), max(ends)
    if feed_info:
        result.feed_version = feed_info[0].get("feed_version") or None

    if result.service_end:
        end = datetime.strptime(result.service_end, "%Y%m%d").date()
        if end < date.today():
            result.warnings.append(f"service ended on {end}")
        elif end - date.today() < EXPIRY_WARNING:
            result.warnings.append(f"service ends soon, on {end}")

    if stops and not any(s.get("wheelchair_boarding") in {"1", "2"} for s in stops):
        result.warnings.append(
            "no stop declares wheelchair_boarding; accessibility needs another source"
        )


class ChecksumMismatchError(Exception):
    pass


def verify_published_md5(path: Path, published_name: str, checksum_url: str) -> None:
    """Compare ``path`` with the MD5 a provider lists for ``published_name``."""
    with urllib.request.urlopen(checksum_url, timeout=TIMEOUT_S) as resp:
        listing = resp.read().decode()
    expected = next(
        (line.split()[0] for line in listing.splitlines() if line.endswith(published_name)), None
    )
    if expected is None:
        raise ChecksumMismatchError(f"{published_name} is not listed in {checksum_url}")
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise ChecksumMismatchError(f"MD5 mismatch for {path.name}")


def fetch_source(entry: dict[str, str], graph_dir: Path) -> SourceResult:
    result = SourceResult(file=entry["file"], url=entry["url"], ok=False)
    dest = graph_dir / entry["file"]
    print(f"Fetching {entry['file']}")
    try:
        download(entry["url"], dest)
        if "md5_url" in entry:
            published_name = entry["url"].rsplit("/", 1)[-1]
            verify_published_md5(dest, published_name, entry["md5_url"])
        result.sha256 = sha256_of(dest)
        result.size_bytes = dest.stat().st_size
        if dest.suffix == ".zip":
            inspect_gtfs(dest, result)
        result.ok = True
    except (
        urllib.error.URLError,
        TimeoutError,
        ConnectionError,
        http.client.HTTPException,
        zipfile.BadZipFile,
        ChecksumMismatchError,
    ) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def enrich_accessibility(
    config: dict[str, str] | None, graph_dir: Path, results: list[SourceResult]
) -> dict[str, object]:
    """Derive wheelchair_boarding for subway stations; fail the feed if that is impossible.

    Building a graph without it would silently stop wheelchair routing from
    preferring accessible stations, so a failure here fails the whole fetch.
    """
    if config is None:
        return {}
    feed = next((r for r in results if r.file == config["feed_file"]), None)
    if feed is None or not feed.ok:
        return {}
    print("Deriving station accessibility from the MTA elevator inventory")
    equipment_path = graph_dir / "mta-elevator-equipment.json"
    try:
        download(config["equipment_url"], equipment_path)
        stats = enrich_wheelchair_boarding(graph_dir / feed.file, equipment_path.read_bytes())
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError, KeyError) as exc:
        feed.ok = False
        feed.error = f"accessibility enrichment failed: {type(exc).__name__}: {exc}"
        return {}

    feed.sha256 = sha256_of(graph_dir / feed.file)
    feed.size_bytes = (graph_dir / feed.file).stat().st_size
    feed.warnings = [w for w in feed.warnings if "wheelchair_boarding" not in w]
    if stats.unmatched_equipment_stops:
        feed.warnings.append(
            f"elevators reference stops missing from GTFS: {stats.unmatched_equipment_stops}"
        )
    print(f"  {stats.accessible_stations} of {stats.stations} stations have an ADA elevator")
    return {
        "wheelchair_boarding": {
            "feed": feed.file,
            "derived_from": config["equipment_url"],
            "equipment_sha256": sha256_of(equipment_path),
            "stations": stats.stations,
            "accessible_stations": stats.accessible_stations,
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--graph-dir", type=Path, default=REPO_ROOT / "data" / "otp" / "graph")
    parser.add_argument("--skip-osm", action="store_true", help="only fetch GTFS feeds")
    args = parser.parse_args()

    sources = json.loads(SOURCES_FILE.read_text())
    graph_dir: Path = args.graph_dir
    graph_dir.mkdir(parents=True, exist_ok=True)
    for config in ("build-config.json", "router-config.json"):
        shutil.copy(CONFIG_DIR / config, graph_dir / config)

    results = [fetch_source(entry, graph_dir) for entry in sources["gtfs"]]
    derived = enrich_accessibility(sources.get("accessibility"), graph_dir, results)

    if not args.skip_osm:
        results += [fetch_source(entry, graph_dir) for entry in sources["osm"]]

    manifest = {
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sources": [asdict(r) for r in results],
        "derived": derived,
    }
    (graph_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    for r in results:
        status = "OK  " if r.ok else "FAIL"
        dates = f"{r.service_start}..{r.service_end}" if r.service_start else ""
        print(f"{status} {r.file:34} {dates:19} {r.error or ''}")
        for warning in r.warnings:
            print(f"     warning: {warning}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())

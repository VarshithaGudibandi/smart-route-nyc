"""Identify the routing graph currently served by OpenTripPlanner.

OTP's API does not expose a graph version, so the build script writes one
next to the graph. Cache keys include it, which retires every cached route
the moment a rebuilt graph is deployed.
"""

from pathlib import Path

UNVERSIONED = "unversioned"


class GraphVersion:
    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._cached: tuple[int, str] | None = None

    def current(self) -> str:
        if self._path is None:
            return UNVERSIONED
        try:
            mtime = self._path.stat().st_mtime_ns
        except FileNotFoundError:
            return UNVERSIONED
        if self._cached is None or self._cached[0] != mtime:
            self._cached = (mtime, self._path.read_text().strip() or UNVERSIONED)
        return self._cached[1]

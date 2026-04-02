from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


UNCHANGED_READ_MESSAGE = (
    "File unchanged since last read. "
    "The content from the earlier read_file result in this conversation is still current — refer to that instead of re-reading."
)


@dataclass(frozen=True)
class ReadSnapshot:
    path: str
    start_line: int
    end_line: int
    mtime_ns: int
    content_hash: str


class ReadTracker:
    def __init__(self):
        self._snapshots: dict[tuple[str, int, int], ReadSnapshot] = {}

    def is_unchanged(self, path: Path, start_line: int, end_line: int, mtime_ns: int) -> bool:
        snapshot = self._snapshots.get(self._key(path, start_line, end_line))
        return snapshot is not None and snapshot.mtime_ns == mtime_ns

    def remember(
        self,
        path: Path,
        start_line: int,
        end_line: int,
        *,
        mtime_ns: int,
        content: str,
    ) -> ReadSnapshot:
        snapshot = ReadSnapshot(
            path=str(path),
            start_line=start_line,
            end_line=end_line,
            mtime_ns=mtime_ns,
            content_hash=sha256(content.encode("utf-8")).hexdigest(),
        )
        self._snapshots[self._key(path, start_line, end_line)] = snapshot
        return snapshot

    def _key(self, path: Path, start_line: int, end_line: int) -> tuple[str, int, int]:
        return (str(path), int(start_line), int(end_line))

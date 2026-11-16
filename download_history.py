"""Successful downloads, stored as one UTF-8 JSON record per line."""
import json
import os
import tempfile
from pathlib import Path
from contextlib import contextmanager, ExitStack
from urllib.parse import parse_qs, urlparse
from PySide6.QtCore import QLockFile


def video_id(url):
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if host == "youtu.be":
        return parts.path.strip("/").split("/")[0]
    if host == "youtube.com" or host.endswith(".youtube.com"):
        segments = parts.path.strip("/").split("/")
        if segments[0] in ("shorts", "embed", "live") and len(segments) > 1:
            return segments[1]
        return parse_qs(parts.query).get("v", [""])[0]
    return ""


class DownloadHistory:
    def __init__(self, path):
        self.path = Path(path)
        self.records = self.read(self.path)

    @staticmethod
    def read(path):
        if not path.exists():
            return []
        records = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or not isinstance(record.get("video_id"), str) or not record["video_id"]:
                    raise ValueError("missing video_id")
            except (ValueError, TypeError) as exc:
                raise ValueError(f"ไฟล์ประวัติไม่ถูกต้อง บรรทัด {number}: {path}") from exc
            records.append(record)
        return records

    def latest(self, key):
        return next((record for record in reversed(self.records) if record["video_id"] == key), None)

    def append(self, record):
        # Replace atomically so an interrupted write cannot truncate existing history.
        with self._locked(self.path):
            records = self.read(self.path) + [record]
            self._write(self.path, records)
            self.records = records

    @staticmethod
    @contextmanager
    def _locked(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(path) + ".lock")
        if not lock.tryLock(5000):
            raise OSError(f"ไม่สามารถล็อกไฟล์ประวัติได้: {path}")
        try:
            yield
        finally:
            lock.unlock()

    @staticmethod
    def _write(path, records):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=path.name + ".", suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                for record in records:
                    output.write(json.dumps(record, ensure_ascii=False) + "\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def clear(self):
        with self._locked(self.path):
            self._write(self.path, [])
            self.records = []

    def move(self, destination):
        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            return
        with ExitStack() as locks:
            for path in sorted({self.path.resolve(), destination.resolve()}):
                locks.enter_context(self._locked(path))
            records = self.read(destination) + self.read(self.path)
            self._write(destination, records)
            if self.path.exists():
                self.path.unlink()
            self.path = destination
            self.records = records

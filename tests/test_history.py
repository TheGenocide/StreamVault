import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from download_history import DownloadHistory, video_id


class HistoryTests(unittest.TestCase):
    def test_two_instances_keep_each_others_downloads(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.jsonl"
            first, second = DownloadHistory(path), DownloadHistory(path)
            first.append({"video_id": "one"})
            second.append({"video_id": "two"})
            self.assertEqual([record["video_id"] for record in DownloadHistory(path).records], ["one", "two"])

    def test_video_links_share_identity(self):
        for url in ["https://youtu.be/abcdefghijk?t=3", "https://www.youtube.com/watch?v=abcdefghijk&list=test",
                    "https://music.youtube.com/watch?v=abcdefghijk", "https://youtube.com/shorts/abcdefghijk",
                    "https://youtube.com/live/abcdefghijk"]:
            self.assertEqual(video_id(url), "abcdefghijk")
        self.assertEqual(video_id("https://example.com/watch?v=abcdefghijk"), "")

    def test_persist_move_merge_and_clear(self):
        with tempfile.TemporaryDirectory() as folder:
            original, target = [Path(folder) / name for name in ("history.jsonl", "other/history.jsonl")]
            history = DownloadHistory(original)
            history.append({"video_id": "one", "title": "เพลงไทย", "path": "one.mp3"})
            self.assertEqual(DownloadHistory(original).latest("one")["title"], "เพลงไทย")
            other = DownloadHistory(target)
            other.append({"video_id": "two"})
            history.move(target)
            self.assertFalse(original.exists())
            self.assertIsNotNone(history.latest("two"))
            self.assertIsNotNone(DownloadHistory(target).latest("one"))
            history.clear()
            self.assertEqual(DownloadHistory(target).records, [])

    def test_failed_write_preserves_previous_history(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.jsonl"
            history = DownloadHistory(path)
            history.append({"video_id": "one"})
            with patch("download_history.os.replace", side_effect=PermissionError("read-only")):
                with self.assertRaises(PermissionError):
                    history.append({"video_id": "two"})
            self.assertIsNone(history.latest("two"))
            self.assertEqual(len(DownloadHistory(path).records), 1)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_corrupt_destination_does_not_erase_original(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "source.jsonl", Path(folder) / "target.jsonl"
            history = DownloadHistory(source)
            history.append({"video_id": "one"})
            target.write_text("not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                history.move(target)
            self.assertEqual(len(DownloadHistory(source).records), 1)

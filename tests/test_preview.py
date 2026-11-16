import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import PreviewWorker


class PreviewTests(unittest.TestCase):
    def test_uses_checked_selection_instead_of_highest_bitrate_raw_format(self):
        info = {
            "url": "https://example.com/working",
            "http_headers": {"User-Agent": "selected-client"},
            "formats": [{"url": "https://example.com/forbidden", "abr": 999,
                         "acodec": "opus", "vcodec": "none"}],
        }
        with tempfile.TemporaryDirectory() as folder:
            destination = str(Path(folder) / "preview.mp3")
            worker = PreviewWorker("https://www.youtube.com/watch?v=test", destination)
            ready, failure = Mock(), Mock()
            worker.ready.connect(ready)
            worker.failure.connect(failure)

            def convert(args, **kwargs):
                Path(destination).write_bytes(b"audio")
                return Mock(returncode=0)

            with patch("app.bundled_binary", return_value="ffmpeg"), \
                    patch("app.yt_dlp.YoutubeDL") as factory, \
                    patch("subprocess.run", side_effect=convert) as run:
                factory.return_value.__enter__.return_value.extract_info.return_value = info
                worker.run()
            args = run.call_args.args[0]
            self.assertEqual(args[args.index("-i") + 1], info["url"])
            self.assertTrue(factory.call_args.args[0]["check_formats"])
            self.assertIn("User-Agent: selected-client\r\n", args)
            ready.assert_called_once_with(destination)
            failure.assert_not_called()


if __name__ == "__main__":
    unittest.main()

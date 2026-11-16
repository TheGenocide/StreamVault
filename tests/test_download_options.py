import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import ydl_common, DownloadWorker, QueueItem


class DownloadOptionsTests(unittest.TestCase):
    def test_lossless_download_uses_audio_converter_and_correct_extension(self):
        for output_format in ("FLAC", "WAV"):
            with self.subTest(output_format=output_format), tempfile.TemporaryDirectory() as folder:
                item = QueueItem(url="https://youtu.be/abcdefghijk", title="song")
                worker = DownloadWorker([(0, item)], folder, output_format, "Lossless",
                                        add_music_tags=False, save_cover=False)
                completed, failed = Mock(), Mock()
                worker.item_completed.connect(completed)
                worker.item_failed.connect(failed)

                def extract(url, download):
                    if download:
                        Path(folder, "song." + output_format.lower()).write_bytes(b"audio")
                    return {"title": "song"}

                with patch("app.yt_dlp.YoutubeDL") as factory:
                    factory.return_value.__enter__.return_value.extract_info.side_effect = extract
                    worker.run()
                failed.assert_not_called()
                completed.assert_called_once_with(0, str(Path(folder, "song." + output_format.lower())))
                options = factory.call_args.args[0]
                self.assertEqual(options["format"], "bestaudio/best")
                self.assertEqual(options["postprocessors"], [{"key": "FFmpegExtractAudio", "preferredcodec": output_format.lower()}])

    def test_format_probes_use_system_temp_instead_of_app_directory(self):
        self.assertEqual(ydl_common()["paths"]["temp"], tempfile.gettempdir())


if __name__ == "__main__":
    unittest.main()

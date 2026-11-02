"""Offline tests for embedded tags and JPG output."""
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mutagen.id3 import ID3
from PIL import Image
from music_tags import music_metadata, save_cover_jpg, thumbnail_as_jpeg, write_mp3_tags


class MusicTagsTests(unittest.TestCase):
    def test_actual_music_fields_only(self):
        meta = music_metadata({"title": "Official Video", "track": "Actual Song",
                               "artist": "Real Artist", "album": "Actual Album",
                               "release_date": "20240113", "channel": "Video Publisher"})
        self.assertEqual(meta["title"], "Actual Song")
        self.assertEqual(meta["artist"], "Real Artist")
        self.assertEqual(meta["album"], "Actual Album")
        self.assertEqual(meta["year"], "2024")

    def test_do_not_pretend_channel_or_upload_year_is_artist_release_year(self):
        meta = music_metadata({"title": "My Video", "channel": "My YouTube Channel",
                               "upload_date": "20261031"})
        self.assertEqual(meta["artist"], "")
        self.assertEqual(meta["album"], "")
        self.assertEqual(meta["year"], "")
        self.assertEqual(meta["channel"], "My YouTube Channel")

    def test_jpg_from_transparent_png(self):
        img = Image.new("RGBA", (120, 80), (140, 90, 20, 0))
        raw = BytesIO()
        img.save(raw, format="PNG")
        jpeg = thumbnail_as_jpeg(raw.getvalue())
        with Image.open(BytesIO(jpeg)) as converted:
            self.assertEqual(converted.format, "JPEG")
            self.assertEqual(converted.mode, "RGB")
            self.assertEqual(converted.size, (120, 80))

    def test_reject_invalid_thumbnail(self):
        with self.assertRaises(ValueError):
            thumbnail_as_jpeg(b"not an image")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is needed to generate a test MP3")
    def test_id3_cover_title_and_jpg(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ชื่อเพลง.mp3"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "sine=frequency=1000:duration=0.15",
                            "-codec:a", "libmp3lame", str(path)], check=True, timeout=20)
            img = Image.new("RGB", (200, 200), (24, 65, 106))
            buf = BytesIO()
            img.save(buf, format="WEBP")
            jpg = thumbnail_as_jpeg(buf.getvalue())
            tags = music_metadata({"track": "เพลงใหม่", "artist": "ศิลปิน", "album": "อัลบั้ม", "release_year": 2025,
                                   "channel": "ช่องเพลง", "webpage_url": "https://www.youtube.com/watch?v=abc"})
            write_mp3_tags(path, tags, jpg)
            data = ID3(path)
            self.assertEqual(str(data["TIT2"]), "เพลงใหม่")
            self.assertEqual(str(data["TPE1"]), "ศิลปิน")
            self.assertEqual(str(data["TALB"]), "อัลบั้ม")
            self.assertEqual(str(data["TDRC"]), "2025")
            self.assertEqual(data.getall("APIC")[0].mime, "image/jpeg")
            self.assertEqual(data.getall("APIC")[0].data, jpg)
            jpg_path = save_cover_jpg(path, jpg)
            self.assertTrue(jpg_path.is_file())
            self.assertEqual(jpg_path.read_bytes(), jpg)
            with self.assertRaises(FileExistsError):
                save_cover_jpg(path, jpg)


if __name__ == "__main__":
    unittest.main()

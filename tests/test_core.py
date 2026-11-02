"""Dependency-free tests for the app's filesystem and URL helpers.
Run: python -m unittest discover -s tests -v
"""
import ast
import glob
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

source = Path(__file__).resolve().parents[1] / "app.py"
module = ast.parse(source.read_text(encoding="utf-8"))
functions = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
needed = ["youtube_url", "seconds_label", "safe_stem", "pick_file_base"]
tree = ast.Module(body=[functions[name] for name in needed], type_ignores=[])
ns = {"re": re, "glob": glob, "Path": Path, "urlparse": urlparse}
exec(compile(ast.fix_missing_locations(tree), str(source), "exec"), ns)


class CoreTests(unittest.TestCase):
    def test_valid_url(self):
        self.assertTrue(ns["youtube_url"]("https://youtu.be/dQw4w9WgXcQ"))
        self.assertTrue(ns["youtube_url"]("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        self.assertTrue(ns["youtube_url"]("https://music.youtube.com/watch?v=dQw4w9WgXcQ"))

    def test_reject_non_youtube_urls(self):
        self.assertFalse(ns["youtube_url"]("https://youtube.com.evil.test/watch?v=1"))
        self.assertFalse(ns["youtube_url"]("javascript:alert(1)"))
        self.assertFalse(ns["youtube_url"]("https://example.com/hi"))

    def test_safe_filename(self):
        self.assertEqual(ns["safe_stem"]('เพลงไทย: new/song? > *'), "เพลงไทย_ new_song_ _ _")
        self.assertEqual(ns["safe_stem"]("CON"), "_CON")
        self.assertLessEqual(len(ns["safe_stem"]("a" * 400)), 165)

    def test_duration(self):
        self.assertEqual(ns["seconds_label"](63), "01:03")
        self.assertEqual(ns["seconds_label"](3661), "1:01:01")

    def test_collision_avoidance(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / "Foo.mp3").write_bytes(b"demo")
            used = set()
            self.assertEqual(ns["pick_file_base"](folder, "Foo", used), "Foo (2)")
            self.assertEqual(ns["pick_file_base"](folder, "Foo", used), "Foo (3)")


if __name__ == "__main__":
    unittest.main()

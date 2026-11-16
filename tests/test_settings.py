import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from app import MainWindow, QueueItem, default_history_path


class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def tearDown(self):
        for window in self.application.topLevelWidgets():
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_settings_survive_close_and_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "settings.ini")
            with patch("app.QSettings", create=True, side_effect=lambda *args: QSettings(path, QSettings.Format.IniFormat)), \
                    patch.object(MainWindow, "_check_tools"):
                window = MainWindow()
                window.format.setCurrentText("MP4")
                window.quality.setCurrentText("HD · 720p")
                window.folder.setText(str(Path(folder) / "เพลง"))
                window.chk_tags.setChecked(False)
                window.chk_cover.setChecked(False)
                window.volume.setValue(32)
                window.history_path.setText(str(Path(folder) / "history.jsonl"))
                window.close()
                reopened = MainWindow()
                try:
                    self.assertEqual(reopened.format.currentText(), "MP4")
                    self.assertEqual(reopened.quality.currentText(), "HD · 720p")
                    self.assertEqual(reopened.folder.text(), str(Path(folder) / "เพลง"))
                    self.assertFalse(reopened.chk_tags.isChecked())
                    self.assertFalse(reopened.chk_cover.isChecked())
                    self.assertEqual(reopened.volume.value(), 32)
                    self.assertAlmostEqual(reopened.audio.volume(), 0.32, places=5)
                    self.assertEqual(reopened.history_path.text(), str(Path(folder) / "history.jsonl"))
                finally:
                    reopened.close()

    def test_success_is_logged_and_duplicate_is_visible_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "settings.ini")
            with patch("app.QSettings", side_effect=lambda *args: QSettings(path, QSettings.Format.IniFormat)), \
                    patch.object(MainWindow, "_check_tools"):
                settings = QSettings(path, QSettings.Format.IniFormat)
                settings.setValue("history_path", str(Path(folder) / "history.jsonl"))
                settings.sync()
                window = MainWindow()
                window.items.append(QueueItem(url="https://youtu.be/abcdefghijk", title="เพลง <test>"))
                window._add_table_row(0)
                self.assertIn("เพลง &lt;test&gt;", window.table.cellWidget(0, 0).text())
                window.on_file_failed(0, "test failure")
                self.assertEqual(window.history.records, [])
                downloaded = Path(folder) / "song.mp3"
                downloaded.write_bytes(b"test")
                window.on_file_complete(0, str(Path(folder) / "song.mp3"))
                window.close()
                reopened = MainWindow()
                try:
                    reopened.items.append(QueueItem(url="https://www.youtube.com/watch?v=abcdefghijk"))
                    reopened._add_table_row(0)
                    self.assertEqual(reopened.table.item(0, 4).text(), "เคยโหลดแล้ว")
                    self.assertEqual(len(reopened.history.records), 1)
                    self.assertIn("https://www.youtube.com/watch?v=abcdefghijk",
                                  reopened.table.cellWidget(0, 0).text())
                    reopened.folder.setText(folder)
                    with patch("app.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as question, \
                            patch("app.DownloadWorker") as worker:
                        reopened.download_all()
                        question.assert_called_once()
                        worker.assert_not_called()
                    with patch("app.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                        reopened.clear_history()
                    self.assertEqual(reopened.table.item(0, 4).text(), "ยังไม่เคยโหลด")
                    self.assertEqual(reopened.history.records, [])
                    self.assertTrue(downloaded.exists())
                finally:
                    reopened.close()

    def test_default_log_is_beside_exe_not_extraction_folder(self):
        with patch("app.sys.frozen", True, create=True), patch("app.sys.executable", "D:/portable/StreamVault.exe"):
            self.assertEqual(default_history_path(), Path("D:/portable/download_history.jsonl"))

    def test_lossless_controls_and_saved_format(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "settings.ini")
            with patch("app.QSettings", side_effect=lambda *args: QSettings(path, QSettings.Format.IniFormat)), \
                    patch.object(MainWindow, "_check_tools"):
                window = MainWindow()
                for output_format in ("WAV", "FLAC"):
                    window.format.setCurrentText(output_format)
                    self.assertEqual(window.quality.currentText(), "Lossless")
                    self.assertFalse(window.quality.isEnabled())
                    self.assertFalse(window.chk_tags.isEnabled())
                    self.assertTrue(window.chk_cover.isEnabled())
                window.close()
                reopened = MainWindow()
                try:
                    self.assertEqual(reopened.format.currentText(), "FLAC")
                    self.assertFalse(reopened.quality.isEnabled())
                    reopened.format.setCurrentText("MP3")
                    self.assertTrue(reopened.quality.isEnabled())
                    self.assertEqual(reopened.quality.currentText(), "320 kbps")
                finally:
                    reopened.close()

    def test_log_failure_keeps_download_and_warning_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "settings.ini")
            with patch("app.QSettings", side_effect=lambda *args: QSettings(path, QSettings.Format.IniFormat)), \
                    patch.object(MainWindow, "_check_tools"):
                window = MainWindow()
                window.items.append(QueueItem(url="https://youtu.be/abcdefghijk"))
                window._add_table_row(0)
                with patch.object(window.history, "append", side_effect=PermissionError("read-only")), \
                        patch("app.QMessageBox.warning") as warning:
                    window.on_file_complete(0, str(Path(folder) / "song.mp3"))
                warning.assert_called_once()
                self.assertTrue(window.items[0].local_path)
                self.assertIn("บันทึก log ไม่ได้", window.items[0].status)
                window.on_file_warning(0, "cover unavailable")
                self.assertIn("บันทึก log ไม่ได้", window.items[0].status)
                self.assertEqual(window.history.records, [])
                window.close()

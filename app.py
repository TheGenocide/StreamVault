# -*- coding: utf-8 -*-
"""StreamVault: YouTube batch downloader with in-app before/after audio previews."""
from __future__ import annotations

import os
import re
import glob
import shutil
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QThread, Signal, QUrl, QSize, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap, QPainter, QLinearGradient
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QLabel, QPushButton, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QProgressBar, QComboBox, QLineEdit,
    QFileDialog, QSlider, QMessageBox, QSplitter, QGraphicsDropShadowEffect, QCheckBox,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
import yt_dlp
from music_tags import music_metadata, thumbnail_as_jpeg, write_mp3_tags, save_cover_jpg

APP_NAME = "StreamVault"
VERSION = "1.2.0"
SAMPLE_SECONDS = 30
DEFAULT_DIR = Path.home() / "Downloads" / APP_NAME
BG = "#0B111C"
CARD = "#131D2C"
BORDER = "#273449"
TXT = "#E9F1FF"
MUTED = "#90A0B8"
PRIMARY = "#778CFF"
GREEN = "#33D5AE"


def application_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def bundled_binary(name: str) -> str | None:
    executable = f"{name}.exe" if sys.platform == "win32" else name
    places = [application_root() / "tools" / executable,
              Path(__file__).resolve().parent / "tools" / executable]
    for path in places:
        if path.is_file():
            return str(path)
    return shutil.which(name)


def setup_binary_path():
    tools = application_root() / "tools"
    if tools.is_dir():
        os.environ["PATH"] = str(tools) + os.pathsep + os.environ.get("PATH", "")


def ydl_common() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 22,
        "retries": 5,
        "fragment_retries": 5,
        "skip_unavailable_fragments": True,
        "windowsfilenames": True,
    }
    deno = bundled_binary("deno")
    if deno:
        opts["js_runtimes"] = {"deno": {"path": deno}}
    ffmpeg = bundled_binary("ffmpeg")
    if ffmpeg:
        opts["ffmpeg_location"] = str(Path(ffmpeg).parent)
    return opts


def youtube_url(url: str) -> bool:
    try:
        parts = urlparse(url.strip())
        host = (parts.hostname or "").lower()
        return (parts.scheme in ("http", "https") and
                (host == "youtu.be" or host == "youtube.com" or
                 host.endswith(".youtube.com")) and bool(parts.path.strip("/")))
    except ValueError:
        return False


def seconds_label(seconds: float | int | None) -> str:
    if seconds is None:
        return "--:--"
    number = max(0, int(seconds))
    minutes, sec = divmod(number, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:d}:{minutes:02d}:{sec:02d}" if hours else f"{minutes:02d}:{sec:02d}"


def safe_stem(title: str) -> str:
    # Do not trust external video titles as filesystem paths.
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title or "video")
    name = re.sub(r"\s+", " ", name).strip(" .")[:165].rstrip(" .") or "video"
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
    if name.split(".")[0].upper() in reserved:
        name = "_" + name
    return name


def pick_file_base(directory: Path, title: str, used: set[str]) -> str:
    stem = safe_stem(title)
    candidate = stem
    number = 2
    while (candidate.casefold() in used or any(directory.glob(glob.escape(candidate) + ".*"))):
        candidate = f"{stem} ({number})"
        number += 1
    used.add(candidate.casefold())
    return candidate


def get_thumbnail_bytes(info: dict) -> bytes | None:
    """Try official YouTube thumbnails from best to smaller sizes (no external domains)."""
    options = []
    if info.get("thumbnail"):
        options.append(info["thumbnail"])
    choices = sorted(info.get("thumbnails") or [],
                     key=lambda t: (t.get("width") or 0) * (t.get("height") or 0),
                     reverse=True)
    options.extend(t.get("url") for t in choices if t.get("url"))
    for url in dict.fromkeys(options):
        try:
            u = urlparse(url)
            host = (u.hostname or "").lower()
            if u.scheme != "https" or not (host == "ytimg.com" or host.endswith(".ytimg.com") or
                                             host == "ggpht.com" or host.endswith(".ggpht.com")):
                continue
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=7) as response:
                raw = response.read(2_000_001)
                if 0 < len(raw) <= 2_000_000:
                    return raw
        except Exception:
            continue
    return None


@dataclass
class QueueItem:
    url: str
    title: str = "กำลังรออ่านข้อมูล..."
    duration: int = 0
    status: str = "รอตรวจสอบ"
    local_path: str = ""
    thumbnail: bytes | None = None
    vid: str = ""
    song: dict | None = None


class MetadataWorker(QThread):
    found = Signal(int, object)
    failure = Signal(int, str)
    completed = Signal()

    def __init__(self, rows: list[tuple[int, str]], parent=None):
        super().__init__(parent)
        self.rows = rows
        self.cancelled = False

    def run(self):
        for row, url in self.rows:
            if self.cancelled:
                break
            try:
                with yt_dlp.YoutubeDL({**ydl_common(), "skip_download": True}) as ydl:
                    info = ydl.extract_info(url, download=False)
                if not info or info.get("_type") == "playlist":
                    raise ValueError("ลิงก์นี้ไม่ใช่วิดีโอเดี่ยว กรุณาเพิ่มลิงก์วิดีโอแต่ละรายการ")
                data = {
                    "title": info.get("title") or "ไม่ทราบชื่อ",
                    "duration": info.get("duration") or 0,
                    "vid": info.get("id") or "",
                    "thumbnail": get_thumbnail_bytes(info),
                    "song": music_metadata(info),
                }
                self.found.emit(row, data)
            except Exception as exc:
                self.failure.emit(row, str(exc))
        self.completed.emit()


class DownloadCancelled(Exception):
    pass


class DownloadWorker(QThread):
    status_update = Signal(int, str)
    progress_update = Signal(int, int, str)
    item_completed = Signal(int, str)
    item_failed = Signal(int, str)
    item_warning = Signal(int, str)
    completed = Signal()

    def __init__(self, items: list[tuple[int, QueueItem]], folder: str,
                 output_format: str, quality: str,
                 add_music_tags: bool = True, save_cover: bool = True, parent=None):
        super().__init__(parent)
        self.items = items
        self.folder = Path(folder)
        self.output_format = output_format
        self.quality = quality
        self.add_music_tags = add_music_tags
        self.save_cover = save_cover
        self.cancelled = False

    def stop(self):
        self.cancelled = True

    def run(self):
        used = set()
        self.folder.mkdir(parents=True, exist_ok=True)
        for row, item in self.items:
            if self.cancelled:
                self.status_update.emit(row, "ยกเลิก")
                continue
            try:
                self.status_update.emit(row, "กำลังเชื่อมต่อ")
                with yt_dlp.YoutubeDL({**ydl_common(), "skip_download": True}) as inspector:
                    info = inspector.extract_info(item.url, download=False)
                if not info or info.get("_type") == "playlist":
                    raise ValueError("ไม่รองรับ playlist URL ในเวอร์ชันนี้")
                base = pick_file_base(self.folder, info.get("title") or item.title, used)
                destination = str(self.folder / (base.replace("%", "%%") + ".%(ext)s"))
                found_path: list[str] = []

                def progress_hook(d: dict):
                    if self.cancelled:
                        raise DownloadCancelled("ผู้ใช้ยกเลิก")
                    status = d.get("status")
                    if status == "downloading":
                        downloaded = d.get("downloaded_bytes") or 0
                        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                        pct = min(99, int(downloaded * 100 / total)) if total else 0
                        speed = d.get("speed")
                        speed_text = f" · {speed / 1024 / 1024:.1f} MB/s" if speed else ""
                        self.progress_update.emit(row, pct, f"กำลังโหลด {pct}%{speed_text}")
                    elif status == "finished":
                        self.status_update.emit(row, "กำลังรวม/แปลงไฟล์")

                def post_hook(d: dict):
                    if d.get("status") == "finished":
                        path = d.get("info_dict", {}).get("filepath")
                        if path:
                            found_path.append(str(path))

                opts = ydl_common()
                opts.update({
                    "outtmpl": {"default": destination},
                    "progress_hooks": [progress_hook],
                    "postprocessor_hooks": [post_hook],
                    "overwrites": False,
                    "continuedl": True,
                })
                if self.output_format == "MP3":
                    opts["format"] = "bestaudio/best"
                    opts["postprocessors"] = [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": self.quality.replace(" kbps", ""),
                    }]
                else:
                    max_height = {"สูงสุดที่มี": 99999, "4K · 2160p": 2160,
                                  "2K · 1440p": 1440, "Full HD · 1080p": 1080,
                                  "HD · 720p": 720, "480p": 480, "360p": 360}[self.quality]
                    height = f"[height<={max_height}]"
                    opts["format"] = (
                        f"bv*{height}[ext=mp4]+ba[ext=m4a]/"
                        f"bv*{height}+ba/b{height}"
                    )
                    opts["merge_output_format"] = "mp4"
                    opts["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]
                with yt_dlp.YoutubeDL(opts) as ydl:
                    result = ydl.extract_info(item.url, download=True)
                output = self.folder / (base + (".mp3" if self.output_format == "MP3" else ".mp4"))
                if not output.is_file():
                    for candidate in reversed(found_path):
                        if Path(candidate).is_file() and Path(candidate).suffix.lower() == output.suffix:
                            output = Path(candidate)
                            break
                if not output.is_file() and result:
                    alternate = result.get("filepath")
                    if alternate and Path(alternate).is_file():
                        output = Path(alternate)
                if not output.is_file():
                    raise FileNotFoundError("ไม่พบไฟล์หลังแปลง กรุณาตรวจสอบโฟลเดอร์ปลายทาง")

                # Cover/metadata are best-effort extras; a valid downloaded file remains a success.
                warnings = []
                mp3_tags = self.output_format == "MP3" and self.add_music_tags
                if mp3_tags or self.save_cover:
                    cover = None
                    try:
                        raw = item.thumbnail or get_thumbnail_bytes(info)
                        if raw:
                            cover = thumbnail_as_jpeg(raw)
                        else:
                            warnings.append("ไม่พบภาพปกจาก YouTube")
                    except Exception as exc:
                        warnings.append(f"แปลงภาพปกไม่ได้: {exc}")
                    if mp3_tags:
                        try:
                            self.status_update.emit(row, "กำลังใส่ข้อมูลเพลง")
                            write_mp3_tags(output, music_metadata(info), cover)
                        except Exception as exc:
                            warnings.append(f"ฝังข้อมูล MP3 ไม่สำเร็จ: {exc}")
                    if self.save_cover and cover:
                        try:
                            save_cover_jpg(output, cover)
                        except Exception as exc:
                            warnings.append(f"บันทึก JPG ไม่สำเร็จ: {exc}")
                self.progress_update.emit(row, 100, "สำเร็จ")
                self.item_completed.emit(row, str(output))
                if warnings:
                    self.item_warning.emit(row, " | ".join(warnings))
            except Exception as exc:
                if self.cancelled:
                    self.status_update.emit(row, "ยกเลิก")
                else:
                    self.item_failed.emit(row, str(exc))
        self.completed.emit()


class PreviewWorker(QThread):
    ready = Signal(str)
    failure = Signal(str)

    def __init__(self, url: str, destination: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.destination = destination

    def run(self):
        import subprocess
        ffmpeg = bundled_binary("ffmpeg")
        if not ffmpeg:
            self.failure.emit("ไม่พบ FFmpeg กรุณาใช้ชุดโปรแกรมที่มี tools/ffmpeg.exe")
            return
        try:
            opts = {**ydl_common(), "format": "bestaudio/best", "skip_download": True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                formats = info.get("formats") or []
                playable = [f for f in formats if f.get("acodec") not in (None, "none") and
                            f.get("vcodec") == "none" and f.get("url")]
                playable.sort(key=lambda f: (f.get("abr") or 0, f.get("asr") or 0))
                stream = (playable[-1] if playable else info)
                stream_url = stream.get("url")
                if not stream_url:
                    raise ValueError("ไม่พบสตรีมเสียงสำหรับตัวอย่าง")
                headers = dict(info.get("http_headers") or {})
                headers.update(stream.get("http_headers") or {})
            args = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                    "-nostdin", "-rw_timeout", "25000000"]
            if headers:
                lines = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if "\r" not in str(v) and "\n" not in str(v))
                args += ["-headers", lines]
            args += ["-i", stream_url, "-t", str(SAMPLE_SECONDS),
                     "-vn", "-map", "0:a:0", "-codec:a", "libmp3lame", "-q:a", "4",
                     self.destination]
            run_opts = {"capture_output": True, "text": True, "timeout": 130}
            if os.name == "nt":
                run_opts["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(args, **run_opts)
            if result.returncode != 0 or not Path(self.destination).is_file():
                raise RuntimeError((result.stderr or "ไม่สามารถอ่านเสียงจาก YouTube").strip()[-650:])
            self.ready.emit(self.destination)
        except Exception as exc:
            self.failure.emit(str(exc))


def shadow(widget, opacity=40):
    fx = QGraphicsDropShadowEffect(widget)
    fx.setBlurRadius(26)
    fx.setOffset(0, 7)
    fx.setColor(QColor(0, 0, 0, opacity))
    widget.setGraphicsEffect(fx)


def panel_layout() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(13)
    return frame, layout


def label(text: str, *, size=11, bold=False, muted=False) -> QLabel:
    q = QLabel(text)
    q.setWordWrap(True)
    q.setStyleSheet(f"color: {MUTED if muted else TXT}; font-size: {size}px; font-weight: {'750' if bold else '400'};")
    return q


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        setup_binary_path()
        self.items: list[QueueItem] = []
        self.metadata_worker: MetadataWorker | None = None
        self.download_worker: DownloadWorker | None = None
        self.preview_worker: PreviewWorker | None = None
        self.tempdir = tempfile.TemporaryDirectory(prefix="streamvault_preview_")
        self.preview_seq = 0
        self.preview_path = ""
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(0.75)
        self.is_seeking = False
        self.setWindowTitle(f"{APP_NAME} — YouTube MP3 / MP4")
        iconfile = application_root() / "assets" / "streamvault.ico"
        if iconfile.is_file():
            self.setWindowIcon(QIcon(str(iconfile)))
        self.setMinimumSize(930, 750)
        self.resize(1100, 850)
        self.setAcceptDrops(True)
        self._make_ui()
        self._wire_player()
        self.update_counters()
        self._check_tools()

    def _make_ui(self):
        # A single-column, low-noise interface. No sidebars, steps, or advanced panels.
        self.setStyleSheet("""
        QMainWindow, QWidget#central { background:#0E1420; color:#EAF0FB; }
        QWidget { font-family:'Segoe UI', 'Tahoma', 'Leelawadee UI', sans-serif; font-size:12px; }
        QFrame#panel { background:#172131; border:1px solid #2B394A; border-radius:13px; }
        QLabel { background:transparent; }
        QPlainTextEdit, QLineEdit, QComboBox {
            background:#101925; color:#F0F5FF; border:1px solid #344357;
            border-radius:8px; padding:7px 10px; selection-background-color:#526ED1;
        }
        QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus { border:1px solid #8094EE; }
        QComboBox { min-height:23px; }
        QComboBox QAbstractItemView { background:#172131; color:white; selection-background-color:#354C76; }
        QPushButton { background:#263449; border:1px solid #3A4C62; color:#EAF0FB;
            border-radius:8px; padding:8px 12px; font-weight:600; }
        QPushButton:hover { background:#344661; }
        QPushButton:disabled { color:#738198; background:#1D2938; border-color:#2B394A; }
        QPushButton#primary { background:#637DEC; border:1px solid #637DEC; color:#FFFFFF; }
        QPushButton#primary:hover { background:#7990F2; }
        QPushButton#primary:disabled { background:#34436B; border-color:#34436B; color:#8F9BBB; }
        QPushButton#quiet { background:transparent; border:1px solid #344357; color:#B9C7DC; }
        QPushButton#quiet:hover { background:#273448; }
        QPushButton#danger { background:transparent; border:1px solid #5A3E4E; color:#F5A6AE; }
        QTableWidget { background:#111A28; border:1px solid #2C3A4C; border-radius:8px;
            color:#E7EEFF; gridline-color:transparent; outline:none; }
        QCheckBox { background:transparent; color:#B9C7DC; font-size:11px; spacing:7px; }
        QHeaderView::section { background:#202E40; color:#ADBBD0; border:none;
            border-bottom:1px solid #344357; padding:8px; font-weight:600; }
        QTableWidget::item { padding:5px; border-bottom:1px solid #243448; }
        QTableWidget::item:selected { background:#304567; color:white; }
        QProgressBar { background:#27374B; border:none; border-radius:5px;
            height:11px; text-align:center; color:#EAF0FB; font-size:10px; }
        QProgressBar::chunk { background:#7990F2; border-radius:5px; }
        QSlider::groove:horizontal { height:5px; background:#324255; border-radius:2px; }
        QSlider::handle:horizontal { background:#9DACF8; width:14px; height:14px; margin:-5px 0; border-radius:7px; }
        QSlider::sub-page:horizontal { background:#768AEC; border-radius:2px; }
        """)
        root = QWidget()
        root.setObjectName("central")
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(20, 17, 20, 14)
        main.setSpacing(10)

        # Product identity and the most useful file action.
        header = QHBoxLayout()
        identity = QVBoxLayout()
        identity.setSpacing(1)
        brand = QLabel("●  StreamVault")
        brand.setStyleSheet("font-size:22px; font-weight:750; color:#F1F5FF;")
        identity.addWidget(brand)
        identity.addWidget(label("YouTube เป็น MP3 หรือ MP4", size=11, muted=True))
        header.addLayout(identity)
        header.addStretch(1)
        self.btn_open = QPushButton("เปิดโฟลเดอร์")
        self.btn_open.setObjectName("quiet")
        self.btn_open.clicked.connect(self.open_folder)
        header.addWidget(self.btn_open)
        main.addLayout(header)

        # Links: multiple URLs are accepted; inspection starts automatically.
        input_panel, input_layout = panel_layout()
        input_layout.setContentsMargins(15, 13, 15, 13)
        input_layout.setSpacing(8)
        input_head = QHBoxLayout()
        input_head.addWidget(label("เพิ่มลิงก์วิดีโอ", size=14, bold=True))
        input_head.addStretch(1)
        input_head.addWidget(label("วางได้หลายลิงก์ บรรทัดละ 1 ลิงก์", size=11, muted=True))
        input_layout.addLayout(input_head)
        add_row = QHBoxLayout()
        add_row.setSpacing(10)
        self.links = QPlainTextEdit()
        self.links.setPlaceholderText("วางลิงก์ YouTube ที่นี่ เช่น https://youtu.be/...")
        self.links.setFixedHeight(71)
        add_row.addWidget(self.links, 1)
        self.btn_add = QPushButton("+  เพิ่มรายการ")
        self.btn_add.setObjectName("primary")
        self.btn_add.setMinimumWidth(128)
        self.btn_add.setFixedHeight(42)
        self.btn_add.clicked.connect(self.add_links)
        add_row.addWidget(self.btn_add, 0, Qt.AlignmentFlag.AlignVCenter)
        input_layout.addLayout(add_row)
        main.addWidget(input_panel)

        # Queue: one list, one selection action, per-file progress.
        queue_panel, queue_layout = panel_layout()
        queue_layout.setContentsMargins(15, 13, 15, 13)
        queue_layout.setSpacing(9)
        queue_head = QHBoxLayout()
        queue_head.addWidget(label("รายการวิดีโอ", size=14, bold=True))
        self.count_label = label("0 รายการ", size=11, muted=True)
        queue_head.addWidget(self.count_label)
        queue_head.addStretch(1)
        self.stats = label("ดาวน์โหลดแล้ว 0", size=11, muted=True)
        queue_head.addWidget(self.stats)
        self.btn_remove = QPushButton("ลบรายการที่เลือก")
        self.btn_remove.setObjectName("quiet")
        self.btn_remove.clicked.connect(self.remove_selected)
        queue_head.addWidget(self.btn_remove)
        queue_layout.addLayout(queue_head)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ชื่อวิดีโอ", "ความยาว", "สถานะ", "ดาวน์โหลด"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 86)
        self.table.setColumnWidth(2, 138)
        self.table.setColumnWidth(3, 138)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setMinimumHeight(145)
        self.table.itemSelectionChanged.connect(self.show_selected)
        queue_layout.addWidget(self.table, 1)
        main.addWidget(queue_panel, 1)

        # Compact output controls, no separate settings page.
        settings_panel, settings_layout = panel_layout()
        settings_layout.setContentsMargins(15, 12, 15, 12)
        settings_layout.setSpacing(9)
        settings_head = QHBoxLayout()
        settings_head.addWidget(label("ตั้งค่าดาวน์โหลด", size=14, bold=True))
        settings_head.addStretch(1)
        settings_head.addWidget(label("ชื่อไฟล์จะตรงกับชื่อวิดีโอโดยอัตโนมัติ", size=11, muted=True))
        settings_layout.addLayout(settings_head)
        form_row = QHBoxLayout()
        form_row.setSpacing(10)
        form_row.addWidget(label("ชนิดไฟล์", size=11, muted=True))
        self.format = QComboBox()
        self.format.addItems(["MP3", "MP4"])
        self.format.setMinimumWidth(106)
        self.format.currentTextChanged.connect(self.update_qualities)
        form_row.addWidget(self.format, 1)
        form_row.addWidget(label("คุณภาพ", size=11, muted=True))
        self.quality = QComboBox()
        self.quality.setMinimumWidth(148)
        form_row.addWidget(self.quality, 2)
        form_row.addWidget(label("บันทึกที่", size=11, muted=True))
        self.folder = QLineEdit(str(DEFAULT_DIR))
        self.folder.setToolTip("โฟลเดอร์บันทึกไฟล์")
        form_row.addWidget(self.folder, 5)
        choose = QPushButton("เลือกที่เก็บ")
        choose.setObjectName("quiet")
        choose.clicked.connect(self.pick_folder)
        form_row.addWidget(choose)
        settings_layout.addLayout(form_row)
        extras = QHBoxLayout()
        extras.setSpacing(20)
        self.chk_tags = QCheckBox("ฝังหน้าปกและข้อมูลเพลงใน MP3")
        self.chk_tags.setToolTip("ใช้ข้อมูลเพลงที่ YouTube มีจริง ไม่เดาศิลปินหรืออัลบั้มจากชื่อช่อง")
        self.chk_tags.setChecked(True)
        extras.addWidget(self.chk_tags)
        self.chk_cover = QCheckBox("บันทึกหน้าปกเป็น JPG")
        self.chk_cover.setChecked(True)
        extras.addWidget(self.chk_cover)
        extras.addStretch(1)
        settings_layout.addLayout(extras)
        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.btn_stop = QPushButton("หยุดดาวน์โหลด")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.clicked.connect(self.stop_download)
        self.btn_stop.setEnabled(False)
        action_row.addWidget(self.btn_stop)
        self.btn_download = QPushButton("↓  ดาวน์โหลดทั้งหมด")
        self.btn_download.setObjectName("primary")
        self.btn_download.clicked.connect(self.download_all)
        self.btn_download.setMinimumWidth(206)
        action_row.addWidget(self.btn_download)
        settings_layout.addLayout(action_row)
        main.addWidget(settings_panel)

        # Small music player; before/after preview is reachable in one place.
        preview_panel, preview_layout = panel_layout()
        preview_layout.setContentsMargins(15, 11, 15, 11)
        preview_layout.setSpacing(9)
        preview_row = QHBoxLayout()
        preview_row.setSpacing(12)
        self.art = QLabel()
        self.art.setFixedSize(103, 63)
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art.setStyleSheet("background:#222F46; border-radius:7px;")
        self.default_art()
        preview_row.addWidget(self.art)
        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        self.selected_title = QLabel("เลือกวิดีโอเพื่อทดลองฟัง")
        self.selected_title.setWordWrap(False)
        self.selected_title.setStyleSheet("color:#EAF0FB; font-size:13px; font-weight:700;")
        self.selected_title.setMinimumWidth(100)
        info_col.addWidget(self.selected_title)
        self.selected_meta = label("ฟังตัวอย่างก่อนโหลด หรือเล่นไฟล์หลังดาวน์โหลด", size=11, muted=True)
        info_col.addWidget(self.selected_meta)
        self.selected_path = QLabel("เลือกจากรายการด้านบน")
        self.selected_path.setStyleSheet("color:#8293AA; font-size:10px;")
        self.selected_path.setWordWrap(False)
        self.selected_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_col.addWidget(self.selected_path)
        preview_row.addLayout(info_col, 1)
        action_col = QVBoxLayout()
        action_col.setSpacing(6)
        self.btn_preview = QPushButton("ฟังตัวอย่าง 30 วิ")
        self.btn_preview.clicked.connect(self.preview_online)
        action_col.addWidget(self.btn_preview)
        self.btn_local = QPushButton("เล่นไฟล์ที่โหลดแล้ว")
        self.btn_local.setObjectName("quiet")
        self.btn_local.clicked.connect(self.play_downloaded)
        action_col.addWidget(self.btn_local)
        preview_row.addLayout(action_col)
        preview_layout.addLayout(preview_row)
        player_row = QHBoxLayout()
        player_row.setSpacing(11)
        self.btn_play = QPushButton("▶")
        self.btn_play.setToolTip("เล่น / หยุดชั่วคราว")
        self.btn_play.setFixedWidth(40)
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_play.setEnabled(False)
        player_row.addWidget(self.btn_play)
        self.time_now = label("00:00", size=11, muted=True)
        player_row.addWidget(self.time_now)
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 0)
        self.timeline.sliderPressed.connect(lambda: setattr(self, "is_seeking", True))
        self.timeline.sliderReleased.connect(self.seek_finished)
        player_row.addWidget(self.timeline, 1)
        self.time_total = label("00:00", size=11, muted=True)
        player_row.addWidget(self.time_total)
        player_row.addWidget(label("เสียง", size=11, muted=True))
        volume = QSlider(Qt.Orientation.Horizontal)
        volume.setRange(0, 100)
        volume.setValue(75)
        volume.setMaximumWidth(105)
        volume.setMinimumWidth(65)
        volume.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        player_row.addWidget(volume)
        preview_layout.addLayout(player_row)
        self.player_state = label("ยังไม่ได้เล่นเสียง", size=11, muted=True)
        preview_layout.addWidget(self.player_state)
        main.addWidget(preview_panel)
        self.footer = label("พร้อมใช้งาน", size=11, muted=True)
        main.addWidget(self.footer)
        self.update_qualities()
        self._sync_actions()

    def _sync_actions(self):
        """Keep the visible actions consistent with queue/worker state."""
        row = self.table.currentRow()
        selected = 0 <= row < len(self.items)
        busy = self.is_busy()
        preview_busy = bool(self.preview_worker and self.preview_worker.isRunning())
        local_ready = selected and bool(self.items[row].local_path)
        self.btn_remove.setEnabled(selected and not busy)
        self.btn_preview.setEnabled(selected and not preview_busy)
        self.btn_local.setEnabled(bool(local_ready))
        self.btn_download.setEnabled(bool(self.items) and not busy)

    def _metadata_finished(self):
        self._sync_actions()
        # The user may have added more URLs while the previous batch was inspected.
        QTimer.singleShot(0, self.inspect_queue)

    def default_art(self):
        canvas = QPixmap(650, 360)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        gradient = QLinearGradient(0, 0, 650, 360)
        gradient.setColorAt(0, QColor("#243E65"))
        gradient.setColorAt(0.55, QColor("#1F325A"))
        gradient.setColorAt(1, QColor("#4C3C80"))
        painter.fillRect(canvas.rect(), gradient)
        painter.setPen(QColor("#B4C1FF"))
        font = QFont("Segoe UI Symbol", 92)
        painter.setFont(font)
        painter.drawText(canvas.rect(), Qt.AlignmentFlag.AlignCenter, "♫")
        painter.end()
        self.art.setPixmap(canvas)
        self.art.setScaledContents(True)

    def _wire_player(self):
        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(self.on_duration)
        self.player.playbackStateChanged.connect(self.on_playback_state)
        self.player.errorOccurred.connect(self.on_player_error)

    def _check_tools(self):
        missing = [x for x in ("ffmpeg", "ffprobe") if not bundled_binary(x)]
        if missing:
            self.footer.setText("⚠  ไม่พบ " + ", ".join(missing) + " — ต้องมีเพื่อแปลงไฟล์")
        elif not bundled_binary("deno"):
            self.footer.setText("⚠  ไม่พบ Deno — วิดีโอบางรายการอาจถูก YouTube ปฏิเสธ (ควรสร้างด้วย build_windows.bat)")

    def update_qualities(self, _unused=None):
        prior = self.quality.currentText()
        self.quality.clear()
        if self.format.currentText() == "MP3":
            choices = ["320 kbps", "256 kbps", "192 kbps", "128 kbps"]
        else:
            choices = ["สูงสุดที่มี", "4K · 2160p", "2K · 1440p", "Full HD · 1080p", "HD · 720p", "480p", "360p"]
        self.quality.addItems(choices)
        self.quality.setCurrentText(prior if prior in choices else choices[0])
        self.chk_tags.setEnabled(self.format.currentText() == "MP3" and not (
            self.download_worker and self.download_worker.isRunning()))

    def pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, "เลือกโฟลเดอร์บันทึกไฟล์", self.folder.text())
        if path:
            self.folder.setText(path)

    def add_links(self):
        lines = re.split(r"[\s,]+", self.links.toPlainText().strip())
        candidates = [x.strip() for x in lines if x.strip()]
        existing = {x.url for x in self.items}
        added = 0
        invalid = 0
        for url in candidates:
            if not youtube_url(url):
                invalid += 1
                continue
            if url in existing:
                continue
            existing.add(url)
            self.items.append(QueueItem(url=url))
            self._add_table_row(len(self.items) - 1)
            added += 1
        if added:
            self.links.clear()
            self.table.selectRow(len(self.items) - added)
            self.inspect_queue()
        elif invalid:
            QMessageBox.warning(self, "ลิงก์ไม่ถูกต้อง", "โปรดใช้ลิงก์ YouTube หรือ youtu.be ที่สมบูรณ์")
        self.footer.setText(f"เพิ่ม {added} รายการ" + (f" • ลิงก์ไม่ถูกต้อง {invalid}" if invalid else ""))
        self.update_counters()

    def _add_table_row(self, row: int):
        item = self.items[row]
        self.table.insertRow(row)
        self.table.setRowHeight(row, 52)
        for col, val in enumerate([item.title, "--:--", item.status]):
            cell = QTableWidgetItem(val)
            cell.setToolTip(item.url if col == 0 else val)
            if col != 0:
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, col, cell)
        bar = QProgressBar()
        bar.setValue(0)
        bar.setFormat("0%")
        bar.setFixedHeight(16)
        self.table.setCellWidget(row, 3, bar)

    def refresh_row(self, row: int):
        if row < 0 or row >= len(self.items):
            return
        item = self.items[row]
        self.table.item(row, 0).setText(item.title)
        self.table.item(row, 0).setToolTip(item.url)
        self.table.item(row, 1).setText(seconds_label(item.duration))
        self.table.item(row, 2).setText(item.status)
        if self.table.currentRow() == row:
            self.show_selected()
        self.update_counters()

    def update_counters(self):
        total = len(self.items)
        completed = sum(bool(x.local_path) for x in self.items)
        self.count_label.setText(f"{total} รายการ")
        self.stats.setText(f"ดาวน์โหลดแล้ว {completed}")
        if hasattr(self, "btn_download"):
            self._sync_actions()

    def inspect_queue(self):
        if self.metadata_worker and self.metadata_worker.isRunning():
            return
        rows = [(i, x.url) for i, x in enumerate(self.items) if x.status == "รอตรวจสอบ"]
        if not rows:
            return
        self.metadata_worker = MetadataWorker(rows, self)
        self.metadata_worker.found.connect(self.on_metadata)
        self.metadata_worker.failure.connect(self.on_metadata_error)
        self.metadata_worker.completed.connect(lambda: self.footer.setText("ตรวจสอบชื่อวิดีโอเสร็จแล้ว"))
        self.metadata_worker.finished.connect(self._metadata_finished)
        self.metadata_worker.start()

    def on_metadata(self, row: int, meta: dict):
        if row >= len(self.items):
            return
        item = self.items[row]
        item.title = meta["title"]
        item.duration = meta["duration"]
        item.vid = meta["vid"]
        item.thumbnail = meta["thumbnail"]
        item.song = meta["song"]
        item.status = "พร้อมดาวน์โหลด"
        self.refresh_row(row)

    def on_metadata_error(self, row: int, reason: str):
        if row >= len(self.items):
            return
        self.items[row].status = "ตรวจสอบไม่สำเร็จ"
        self.refresh_row(row)
        self.table.item(row, 2).setToolTip(reason)

    def show_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.items):
            self.selected_title.setText("เลือกวิดีโอจากรายการ")
            self.selected_meta.setText("ยังไม่ได้เลือกไฟล์")
            self.selected_path.setText("ไฟล์จะถูกตั้งชื่อตามชื่อวิดีโอโดยอัตโนมัติ")
            self.default_art()
            self._sync_actions()
            return
        item = self.items[row]
        from PySide6.QtGui import QFontMetrics
        self.selected_title.setText(QFontMetrics(self.selected_title.font()).elidedText(item.title, Qt.TextElideMode.ElideRight, max(160, self.selected_title.width())))
        self.selected_title.setToolTip(item.title)
        song = item.song or {}
        parts = [x for x in [song.get("artist"), song.get("album"), song.get("year")] if x]
        parts += [seconds_label(item.duration), item.status]
        details = "  ·  ".join(parts)
        self.selected_meta.setText(details)
        self.selected_meta.setToolTip(details + (f"\nช่อง: {song['channel']}" if song.get("channel") else ""))
        note = Path(item.local_path).name if item.local_path else "ตั้งชื่อไฟล์ตามชื่อวิดีโออัตโนมัติ"
        self.selected_path.setText(QFontMetrics(self.selected_path.font()).elidedText(note, Qt.TextElideMode.ElideRight, max(160, self.selected_path.width())))
        self.selected_path.setToolTip(item.local_path or item.url)
        if item.thumbnail:
            pm = QPixmap()
            if pm.loadFromData(item.thumbnail):
                self.art.setPixmap(pm)
                self.art.setScaledContents(True)
            else:
                self.default_art()
        else:
            self.default_art()
        self._sync_actions()

    def remove_selected(self):
        if self.is_busy():
            QMessageBox.information(self, "กำลังทำงาน", "กรุณารอให้ตรวจสอบ/ดาวน์โหลดเสร็จก่อนลบรายการ")
            return
        row = self.table.currentRow()
        if row < 0:
            return
        self.items.pop(row)
        self.table.removeRow(row)
        if self.items:
            self.table.selectRow(min(row, len(self.items) - 1))
        else:
            self.show_selected()
        self.update_counters()

    def is_busy(self):
        return bool((self.download_worker and self.download_worker.isRunning()) or
                    (self.metadata_worker and self.metadata_worker.isRunning()))

    def download_all(self):
        if self.is_busy() and (self.download_worker and self.download_worker.isRunning()):
            return
        if self.metadata_worker and self.metadata_worker.isRunning():
            QMessageBox.information(self, "กำลังตรวจสอบ", "กรุณารอการอ่านชื่อวิดีโอให้เสร็จก่อน")
            return
        if not self.items:
            QMessageBox.information(self, "ยังไม่มีรายการ", "โปรดเพิ่มลิงก์ YouTube ก่อน")
            return
        if not bundled_binary("ffmpeg") or not bundled_binary("ffprobe"):
            QMessageBox.warning(self, "ไม่มี FFmpeg", "ต้องมี ffmpeg และ ffprobe สำหรับแปลง MP3/รวม MP4\nกรุณารัน build_windows.bat ตาม README")
            return
        directory = Path(self.folder.text().strip())
        if not self.folder.text().strip():
            QMessageBox.warning(self, "ไม่พบปลายทาง", "กรุณาเลือกโฟลเดอร์")
            return
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "ไม่สามารถสร้างโฟลเดอร์", str(exc))
            return
        pending = [(i, x) for i, x in enumerate(self.items) if not x.local_path]
        if not pending:
            QMessageBox.information(self, "เสร็จแล้ว", "ทุกรายการมีไฟล์ที่โหลดแล้ว")
            return
        self.download_worker = DownloadWorker(
            pending, str(directory), self.format.currentText(), self.quality.currentText(),
            add_music_tags=self.chk_tags.isChecked(), save_cover=self.chk_cover.isChecked(), parent=self)
        self.download_worker.status_update.connect(self.set_item_status)
        self.download_worker.progress_update.connect(self.set_item_progress)
        self.download_worker.item_completed.connect(self.on_file_complete)
        self.download_worker.item_failed.connect(self.on_file_failed)
        self.download_worker.item_warning.connect(self.on_file_warning)
        self.download_worker.completed.connect(lambda: self.footer.setText("สิ้นสุดการทำงาน — ตรวจสอบสถานะในตาราง"))
        self.download_worker.finished.connect(self.on_download_finished)
        self.btn_download.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_remove.setEnabled(False)
        self.format.setEnabled(False)
        self.quality.setEnabled(False)
        self.chk_tags.setEnabled(False)
        self.chk_cover.setEnabled(False)
        self.footer.setText(f"กำลังดาวน์โหลด {len(pending)} รายการ...")
        self.download_worker.start()

    def set_item_status(self, row: int, status: str):
        self.items[row].status = status
        self.refresh_row(row)

    def set_item_progress(self, row: int, pct: int, message: str):
        self.table.cellWidget(row, 3).setValue(pct)
        self.items[row].status = "กำลังดาวน์โหลด" if pct < 100 else "เสร็จสิ้น"
        self.table.item(row, 2).setText(self.items[row].status)
        self.table.cellWidget(row, 3).setToolTip(message)

    def on_file_complete(self, row: int, path: str):
        self.items[row].local_path = path
        self.items[row].status = "สำเร็จ"
        self.refresh_row(row)
        self.table.cellWidget(row, 3).setValue(100)

    def on_file_warning(self, row: int, message: str):
        self.items[row].status = "สำเร็จ (ข้อมูลไม่ครบ)"
        self.refresh_row(row)
        self.table.item(row, 2).setToolTip(message)
        self.footer.setText("โหลดไฟล์แล้ว แต่บางข้อมูลไม่ครบ • ดูรายละเอียดที่สถานะ")

    def on_file_failed(self, row: int, error: str):
        self.items[row].status = "ล้มเหลว"
        self.refresh_row(row)
        self.table.item(row, 2).setToolTip(error)
        self.table.cellWidget(row, 3).setToolTip(error)
        self.footer.setText("มีรายการโหลดไม่สำเร็จ • วางเมาส์บนสถานะเพื่อดูสาเหตุ")

    def stop_download(self):
        if self.download_worker:
            self.download_worker.stop()
            self.btn_stop.setEnabled(False)
            self.footer.setText("กำลังยกเลิกการดาวน์โหลด...")

    def on_download_finished(self):
        self.btn_download.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_remove.setEnabled(True)
        self.format.setEnabled(True)
        self.quality.setEnabled(True)
        self.chk_cover.setEnabled(True)
        self.update_qualities()
        self.update_counters()
        self._sync_actions()

    def preview_online(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.items):
            QMessageBox.information(self, "เลือกวิดีโอ", "โปรดเลือกวิดีโอจากตาราง")
            return
        if self.preview_worker and self.preview_worker.isRunning():
            QMessageBox.information(self, "กำลังเตรียมตัวอย่าง", "โปรดรอตัวอย่างเสียงก่อนหน้า")
            return
        if not bundled_binary("ffmpeg"):
            QMessageBox.warning(self, "ต้องใช้ FFmpeg", "ไม่พบ FFmpeg สำหรับทดลองฟัง")
            return
        self.player.stop()
        self.preview_seq += 1
        path = str(Path(self.tempdir.name) / f"preview_{self.preview_seq}.mp3")
        self.preview_worker = PreviewWorker(self.items[row].url, path, self)
        self.preview_worker.ready.connect(self.on_preview_ready)
        self.preview_worker.failure.connect(self.on_preview_error)
        self.preview_worker.finished.connect(self._sync_actions)
        self.btn_preview.setEnabled(False)
        self.player_state.setText("กำลังอ่านสตรีมและเตรียมตัวอย่าง 30 วินาที...")
        self.preview_worker.start()
        self._sync_actions()

    def on_preview_ready(self, path: str):
        self.preview_path = path
        self.play_path(path, "ตัวอย่างจาก YouTube · 30 วินาที")

    def on_preview_error(self, reason: str):
        self.player_state.setText("ไม่สามารถทดลองฟังออนไลน์ได้")
        QMessageBox.warning(self, "Preview ไม่สำเร็จ", reason[:900] + "\n\nลองอัปเดต yt-dlp หรือเปิดฟังจาก YouTube โดยตรง")

    def play_downloaded(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.items) or not self.items[row].local_path:
            QMessageBox.information(self, "ยังไม่มีไฟล์", "ดาวน์โหลดรายการที่เลือกก่อน แล้วจึงฟังไฟล์เต็มได้")
            return
        path = self.items[row].local_path
        if not Path(path).is_file():
            QMessageBox.warning(self, "ไม่พบไฟล์", "ไฟล์ถูกย้ายหรือลบไปแล้ว")
            return
        self.play_path(path, f"เล่นไฟล์ที่บันทึกแล้ว · {Path(path).name}")

    def play_path(self, path: str, title: str):
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        self.player_state.setText(title)
        self.btn_play.setEnabled(True)
        self.player.play()

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def on_playback_state(self, state):
        self.btn_play.setText("Ⅱ" if state == QMediaPlayer.PlaybackState.PlayingState else "▶")

    def on_player_error(self, _error, description: str):
        if description:
            self.player_state.setText("เล่นไฟล์ไม่ได้: " + description[:130])

    def on_position(self, position: int):
        if not self.is_seeking:
            self.timeline.setValue(position)
        self.time_now.setText(seconds_label(position / 1000))

    def on_duration(self, duration: int):
        self.timeline.setRange(0, max(0, duration))
        self.time_total.setText(seconds_label(duration / 1000))

    def seek_finished(self):
        self.is_seeking = False
        self.player.setPosition(self.timeline.value())

    def open_folder(self):
        folder = Path(self.folder.text().strip())
        folder.mkdir(parents=True, exist_ok=True)
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasText():
            current = self.links.toPlainText().strip()
            extra = event.mimeData().text().strip()
            self.links.setPlainText((current + "\n" if current else "") + extra)
            self.add_links()
            event.acceptProposedAction()

    def closeEvent(self, event):
        busy = (self.is_busy() or (self.preview_worker and self.preview_worker.isRunning()))
        if busy:
            if self.download_worker:
                self.download_worker.stop()
            if self.metadata_worker:
                self.metadata_worker.cancelled = True
            QMessageBox.information(self, "กำลังปิดโปรแกรม", "มีการดาวน์โหลดหรือตรวจสอบที่กำลังทำงาน\nให้การทำงานปัจจุบันสิ้นสุดก่อน แล้วจึงปิดโปรแกรมอีกครั้ง")
            event.ignore()
            return
        self.player.stop()
        self.tempdir.cleanup()
        super().closeEvent(event)


def main():
    # Ensure readable Thai text on high-density Windows displays.
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

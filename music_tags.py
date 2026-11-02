# -*- coding: utf-8 -*-
"""Pure Python YouTube music metadata and MP3/JPG cover helpers.

This module deliberately does not guess artist/album from the YouTube channel.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TDRC, TIT2, TPE1, TXXX
from PIL import Image, ImageOps, UnidentifiedImageError


def _field(value) -> str:
    """Normalize yt-dlp's optional text fields without manufacturing values."""
    if isinstance(value, (list, tuple)):
        return ", ".join(s for item in value if (s := _field(item)))
    return str(value).strip() if isinstance(value, (str, int)) else ""


def music_metadata(info: dict) -> dict[str, str]:
    """Extract explicit music tags. Upload date is *not* release year."""
    title = _field(info.get("track")) or _field(info.get("title")) or "Unknown title"
    artist = _field(info.get("artist")) or _field(info.get("artists")) or _field(info.get("creator"))
    album = _field(info.get("album"))
    year = _field(info.get("release_year"))
    if not (len(year) == 4 and year.isdecimal()):
        release_date = _field(info.get("release_date"))
        year = release_date[:4] if len(release_date) >= 8 and release_date[:8].isdecimal() else ""
    return {
        "title": title,
        "artist": artist,
        "album": album,
        "year": year,
        "channel": _field(info.get("channel")) or _field(info.get("uploader")),
        "url": _field(info.get("webpage_url")) or _field(info.get("original_url")),
        "upload_date": _field(info.get("upload_date")),
    }


def thumbnail_as_jpeg(raw: bytes, max_dimension: int = 1200) -> bytes:
    """Convert thumbnail (webp/jpeg/png) to reasonably sized RGB JPEG for ID3."""
    if not raw or len(raw) > 2_000_000:
        raise ValueError("Thumbnail missing or too large")
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.width * source.height > 18_000_000:
                raise ValueError("Thumbnail resolution too large")
            img = ImageOps.exif_transpose(source)
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                rgba = img.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                img = background
            else:
                img = img.convert("RGB")
            img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=90, optimize=True)
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Cannot read thumbnail image") from exc


def write_mp3_tags(file_path: str | Path, info: dict[str, str], cover_jpeg: bytes | None = None) -> None:
    """Embed ID3 title, verified music fields, channel/source tags, and front cover."""
    path = Path(file_path)
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.setall("TIT2", [TIT2(encoding=3, text=info["title"])])
    for frame_id, frame_type, value in (
        ("TPE1", TPE1, info.get("artist")),
        ("TALB", TALB, info.get("album")),
        ("TDRC", TDRC, info.get("year")),
    ):
        if value:
            tags.setall(frame_id, [frame_type(encoding=3, text=value)])
    for description, value in (
        ("YouTube Channel", info.get("channel")),
        ("YouTube URL", info.get("url")),
        ("YouTube Upload Date", info.get("upload_date")),
    ):
        if value:
            tags.delall("TXXX:" + description)
            tags.add(TXXX(encoding=3, desc=description, text=value))
    if cover_jpeg:
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_jpeg))
    tags.save(path, v2_version=3)


def save_cover_jpg(media_file: str | Path, cover_jpeg: bytes) -> Path:
    path = Path(media_file).with_suffix(".jpg")
    with path.open("xb") as destination:
        destination.write(cover_jpeg)
    return path

# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all
import sys
onedir = '--onedir' in sys.argv

datas = [('assets/streamvault.ico', 'assets')]
binaries = [('tools/ffmpeg.exe', 'tools'), ('tools/ffprobe.exe', 'tools'), ('tools/deno.exe', 'tools'), ('tools/ffplay.exe', 'tools')]
hiddenimports = []
hiddenimports += collect_submodules('yt_dlp')
tmp_ret = collect_all('yt_dlp_ejs')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Qt uses Windows ICU; an unrelated ICU on PATH has incompatible exports.
a.binaries = [entry for entry in a.binaries
              if entry[0].lower() not in ('icuuc.dll', 'icudt78.dll')]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    *([] if onedir else [a.binaries, a.datas]),
    [],
    name='StreamVault',
    exclude_binaries=onedir,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/streamvault.ico'],
    version='version_info.txt',
)
if onedir:
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name='StreamVault')

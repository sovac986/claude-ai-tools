# -*- mode: python ; coding: utf-8 -*-
# Build:  python -m PyInstaller hr_diktat.spec --noconfirm
# Rezultat: dist\hr_diktat\hr_diktat.exe (one-folder, headless + tray ikona).
# Whisper model (large-v3-turbo) se NE bundla - cita se iz HF cachea pri startu.
import os, glob
from PyInstaller.utils.hooks import collect_all

SP = r"C:\Users\Franjo\AppData\Local\Programs\Python\Python313\Lib\site-packages"

binaries = []
datas = []
hiddenimports = ["keyboard", "pyperclip", "numpy"]

# Teski paketi s vlastitim DLL-ovima / data fajlovima.
for pkg in ("faster_whisper", "ctranslate2", "onnxruntime",
            "sounddevice", "pystray", "PIL"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# NVIDIA CUDA DLL-ovi -> nvidia\<lib>\bin (da _add_nvidia_dlls() ih nadje u _MEIPASS).
for sub in (r"nvidia\cublas\bin", r"nvidia\cudnn\bin"):
    src = os.path.join(SP, sub)
    for dll in glob.glob(os.path.join(src, "*.dll")):
        binaries.append((dll, sub))

a = Analysis(
    ['hr_diktat.py'],
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
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='hr_diktat',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=r'D:\ClaudeAI\tools\diktat\hr_diktat.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='hr_diktat',
)

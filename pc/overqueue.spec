# PyInstaller build for the Windows app: `pyinstaller pc/overqueue.spec` from the repo root.
# Produces a one-folder build in dist/OverQueue, which installer.iss packs into the setup exe.
# One folder rather than one file: it starts faster and doesn't unpack to %TEMP% on every launch.

import os
import sys

sys.path.insert(0, SPECPATH)
import version  # noqa: E402  (stamped by CI before this runs)

# The real app's icon is orange; OverQueue Dev's is purple, so the two tell apart in the taskbar.
ICON = os.path.join(SPECPATH, "icons", "overqueue-dev.ico" if version.DEV else "overqueue.ico")

# Big files the app never uses: OpenCV's video decoder, Qt's software OpenGL fallback, Qt's translations.
UNUSED = ("opencv_videoio_ffmpeg", "opengl32sw.dll", "Qt6/translations")


def used(entries):
    return [entry for entry in entries if not any(name in entry[0].replace("\\", "/") for name in UNUSED)]


a = Analysis(
    ["app.py"],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OverQueue",
    console=False,
    upx=False,
    icon=ICON,
)
coll = COLLECT(exe, used(a.binaries), used(a.datas), name="OverQueue", upx=False)

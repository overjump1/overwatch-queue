# PyInstaller build for the Windows app: `pyinstaller pc/owqueue.spec` from the repo root.
# Produces a one-folder build in dist/OWQueue, which installer.iss packs into the setup exe.
# One folder rather than one file: it starts faster and doesn't unpack to %TEMP% on every launch.

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
    name="OWQueue",
    console=False,
    upx=False,
)
coll = COLLECT(exe, used(a.binaries), used(a.datas), name="OWQueue", upx=False)

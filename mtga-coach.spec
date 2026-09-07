# PyInstaller build: one file, the interface bundled, no console for the packaged app.
# Build with:  python -m PyInstaller --clean --noconfirm mtga-coach.spec
from PyInstaller.utils.hooks import collect_submodules

analysis = Analysis(
    ["run.py"],
    pathex=["src"],
    datas=[("src/mtga_coach/static", "mtga_coach/static")],
    # The review feature imports anthropic lazily, so PyInstaller cannot see it.
    hiddenimports=collect_submodules("anthropic"),
    excludes=["tkinter", "unittest", "pydoc_data", "test"],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive, analysis.scripts, analysis.binaries, analysis.datas, [],
    name="MTGA Coach",
    console=True,
    upx=False,
    disable_windowed_traceback=False,
)

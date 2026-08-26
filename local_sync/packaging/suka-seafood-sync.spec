# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
    copy_metadata,
)


project_root = Path(SPECPATH).parent
source_root = project_root / "src"
entrypoint = source_root / "sukaseafood_sync" / "__main__.py"

package_datas, package_binaries, package_hiddenimports = collect_all(
    "sukaseafood_sync",
    include_py_files=False,
)
datas = [
    (source, destination)
    for source, destination in package_datas
    if not source.casefold().endswith(".dist-info")
]
datas += collect_data_files("certifi")
for metadata_source, metadata_destination in copy_metadata("sukaseafood-sync"):
    metadata_root = Path(metadata_source)
    for metadata_name in ("METADATA", "WHEEL", "entry_points.txt", "top_level.txt"):
        metadata_file = metadata_root / metadata_name
        if metadata_file.is_file():
            datas.append((str(metadata_file), metadata_destination))
binaries = package_binaries
hiddenimports = package_hiddenimports
hiddenimports += collect_submodules("PIL")
# Importing _tkinter activates PyInstaller's supported Tcl/Tk hook, which
# collects the matching Tcl and Tk resource trees for this Python build.
hiddenimports += collect_submodules("tkinter")
hiddenimports += ["_tkinter", "scipy.fftpack"]

a = Analysis(
    [str(entrypoint)],
    pathex=[str(source_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project_root / "packaging" / "pyi_rth_scipy_runtime.py")],
    excludes=[
        "test",
        "tests",
        "pytest",
        "responses",
        "unittest",
        "scipy._lib._testutils",
        "numpy.testing",
        "numpy._pytesttester",
        "pywt._pytesttester",
        # ImageHash pHash uses scipy.fftpack and the frozen self-test exercises
        # that exact path. This optional SciPy statistics extension is absent
        # from the pinned wheel and is not imported by the local tool.
        "scipy.special._cdflib",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SukaSeafoodTrainingSync",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SukaSeafoodTrainingSync",
)

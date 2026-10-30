from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata


SPEC_BASE = Path(SPECPATH).resolve()
ROOT = next((candidate for candidate in (SPEC_BASE, SPEC_BASE.parent)
             if (candidate / "app" / "desktop.py").is_file()), SPEC_BASE.parent)
ICON = ROOT / "app" / "assets" / "lem-icon.ico"
datas = [(str(ROOT / "app" / "desktop_ui"), "app/desktop_ui"),
         (str(ROOT / "app" / "selectors.json"), "app"),
         (str(ICON), "app/assets")]
binaries = []
hiddenimports = ["clr", "pythonnet"]

for package in ("webview", "clr_loader", "pythonnet"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

hiddenimports += collect_submodules("telethon")
# Mailing: recipient time zones (zoneinfo needs tzdata on Windows) and the mailing package.
datas += collect_data_files("tzdata")
hiddenimports += ["tzdata"] + collect_submodules("app.mailing")
hiddenimports += collect_submodules("selenium")
datas += copy_metadata("pywebview")
datas += copy_metadata("Telethon")
datas += copy_metadata("selenium")

a = Analysis(
    [str(ROOT / "packaging" / "entrypoint.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["fastapi", "uvicorn", "httpx", "pytest", "unittest.mock",
              "hypothesis", "_hypothesis_ftz_detector"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LeadEqualMoney",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=True,
    icon=str(ICON),
    version=str(ROOT / "packaging" / "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="LeadEqualMoney",
)

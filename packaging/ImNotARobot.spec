import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# the update check compares GitHub's tag with this, so the bundle takes it from the package
VERSION = re.search(r'__version__ = "(.+?)"', Path(SPECPATH, "../imnotarobot/__init__.py").read_text()).group(1)

datas, binaries, hiddenimports = [], [], []
for pkg in ("ddddocr", "onnxruntime", "rumps", "WebKit", "ServiceManagement", "UserNotifications", "patchright"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("uvicorn") + ["imnotarobot"]
hiddenimports += collect_submodules("imnotarobot")
# mcp.cli exits on import without its optional CLI extras, so it is left out
hiddenimports += collect_submodules("mcp", filter=lambda name: not name.startswith("mcp.cli"))
datas += collect_data_files("mcp")
datas += collect_data_files("imnotarobot")

datas += [("en.lproj/InfoPlist.strings", "en.lproj"), ("ru.lproj/InfoPlist.strings", "ru.lproj")]
a = Analysis(["app_main.py"], pathex=[".."], datas=datas, binaries=binaries, hiddenimports=hiddenimports,
             excludes=["tkinter", "matplotlib", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ImNotARobot", console=False, argv_emulation=False)
coll = COLLECT(exe, a.binaries, a.datas, name="ImNotARobot")
app = BUNDLE(coll, name="ImNotARobot.app", icon="icon.icns", bundle_identifier="app.captcha-stack",
             info_plist={"LSUIElement": True, "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True}, "CFBundleShortVersionString": VERSION, "CFBundleDisplayName": "ImNotARobot", "CFBundleName": "ImNotARobot", "LSHasLocalizedDisplayName": True,
                         "CFBundleDevelopmentRegion": "en", "CFBundleLocalizations": ["en", "ru"],
                         "NSHumanReadableCopyright": "ziganshin"})

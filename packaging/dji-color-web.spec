# PyInstaller spec：pywebview Web 工作台。
# pywebview 的平台后端由安装环境提供；发布前仍需在目标平台执行一次打包验收。

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
project_root = Path(SPECPATH).parent

a = Analysis(
    [str(project_root / "dji_color_classifier" / "web_app.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "prototype" / "index.html"), "prototype"),
        (str(project_root / "prototype" / "app.js"), "prototype"),
    ],
    # pywebview 按当前平台动态导入 DOM 和渲染器后端；仅收集顶层模块会使
    # 打包后的应用无法注册 DOM drop 监听，从而导致目录拖放失效。
    hiddenimports=collect_submodules("webview"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="dji-color-web",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

# dlog_color_classifier

DJI 视频色彩模式识别与整理工具，用于读取 DJI MP4/MOV 容器中的 `djmd` 私有元数据，识别 `D-Log`、`D-Log2`、普通 `709` 或无法确认，并按需重命名、移动、复制或导出报告。

当前版本的核心读取逻辑已经改为原生 MP4/MOV 解析，不强制依赖 `ffmpeg` 或 `ffprobe`。工具不会分析视频画面，也不会修改视频文件内部元数据。

## 功能

- 原生读取 DJI `djmd` 数据轨第一包。
- 识别 `D-Log`、`D-Log2`、普通 `709`、无法确认。
- 导出 CSV 或 JSON 报告。
- 支持添加前缀、移动到分类目录、复制到分类目录。
- 默认预演，不修改文件。
- 执行时生成 `manifest.json`，支持基于 manifest 撤销。
- 支持同 basename 的 `.srt`、`.lrf`、`.thm`、`.jpg`、`.xml` 伴随文件同步整理。
- 提供 CLI 和 PySide6 GUI 入口。

## 识别规则

- `ColorGammaSxS == 22`：D-Log2
- `ColorGammaSxS == 2`：D-Log
- `ColorGammaSxS` 缺失且 `record_mode == 8`：普通 709
- 其他情况：无法确认

## CLI 使用

扫描并导出 CSV：

```powershell
dji-color scan "D:\Users\Ray\Desktop\新建文件夹" --output .\dji_color_modes.csv
```

预演添加前缀：

```powershell
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode prefix
```

真正添加前缀：

```powershell
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode prefix --apply
```

按色彩模式移动到 `dlog/`、`dlog2/`、`rec709/`、`unknown/`：

```powershell
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode move --apply
```

复制到分类目录，并同步处理伴随文件：

```powershell
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode copy --with-sidecars --apply
```

撤销一次执行：

```powershell
dji-color undo "D:\Users\Ray\Desktop\新建文件夹\.dji-color-classifier\manifests\20260629T231500.json" --apply
```

## 旧脚本兼容

旧入口仍然保留：

```powershell
python .\classify_dji_color_modes.py "D:\Users\Ray\Desktop\新建文件夹" --output .\dji_color_modes.csv
python .\rename_dji_color_modes.py "D:\Users\Ray\Desktop\新建文件夹" --apply
```

旧脚本内部已经调用新的核心库。

## GUI

发布版首选 PySide6：

```powershell
pip install -e ".[gui]"
dji-color-gui
```

开发或验收环境如果已有 PyQt5，也可以运行同一套 GUI；程序会优先使用 PySide6，缺失时尝试 PyQt5。

GUI 支持：

- 选择或拖入目录。
- 扫描视频。
- 表格查看识别结果和证据。
- 生成整理计划。
- 自定义文件名模板和目录模板。
- 执行整理。
- 导出报告。
- 从 manifest 载入撤销计划。

## 开发

推荐安装开发依赖：

```powershell
pip install -e ".[gui,dev]"
pytest
```

## 打包

Windows CLI：

```powershell
pyinstaller --clean --noconfirm .\packaging\dji-color-cli.spec
```

Windows GUI 首选 PySide6：

```powershell
pyinstaller --clean --noconfirm .\packaging\dji-color-gui.spec
```

如果当前 Windows 环境的 PySide6 无法加载 Qt DLL，但 PyQt5 可用，可以使用 fallback spec：

```powershell
pyinstaller --clean --noconfirm .\packaging\dji-color-gui-pyqt5.spec
```

## 当前限制

- 原生 reader 只实现读取 DJI `djmd` 第一包所需的 MP4 子集。
- 检测到 `moof/traf/trun` 等 fragmented MP4 结构时会给出明确错误。
- v1.0 不承诺覆盖所有 DJI 机型和固件。
- macOS 未签名 GUI 包可能需要用户手动允许打开。

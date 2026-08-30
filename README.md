# DJI Color Classifier

DJI 视频色彩模式识别与整理工具。读取 MP4/MOV 元数据，识别 `D-Log`、`D-Log2`、普通 `709` 和 `Rec.2100 HLG（HDR）`，无需解码画面，也不会修改视频内部数据。

当前正式版：`2.0.0`

## 下载

从 [Releases](https://github.com/TzRay/dlog_color_classifier/releases/latest) 下载对应版本：

- Windows x64：`dji-color-web-windows-x64.zip`
- macOS Apple Silicon：`dji-color-web-macos-arm64.zip`

解压后直接运行 `dji-color-web`。官方发布仅提供 Web 桌面版，不再提供 CLI 或 Qt GUI 安装包。

## 功能

- 选择或拖入素材文件夹，扫描 MP4、MOV、M4V。
- 按色彩模式统计、筛选和搜索文件。
- 导出 CSV 或 JSON 报告。
- 添加文件名前缀，或将素材复制、移动到分类目录。
- 可递归扫描子目录，并同步处理 `.srt`、`.lrf`、`.thm`、`.jpg`、`.xml` 等同名伴随文件。
- 支持跳过、标记失败、自动追加序号三种冲突策略。

## 使用

1. 选择或拖入 DJI 素材文件夹。
2. 等待扫描完成，核对识别统计和文件明细。
3. 选择复制、移动或添加前缀。
4. 确认伴随文件、冲突策略和目标模板后执行。
5. 需要留档时导出 CSV 或 JSON 报告。

Web 工作台会直接执行整理，不生成 manifest，也不提供撤销。首次使用建议选择“复制到分类目录”，或提前备份素材。

无法确认、元数据冲突或读取失败的视频不会自动整理。

## 识别依据

识别优先级如下：

1. QuickTime `com.dji.camera.ColorGammaSxS` 明确标签。
2. DJI `djmd` 中的 `ColorGammaSxS`：`22` 为 D-Log2，`2` 为 D-Log。
3. 枚举缺失且 `record_mode == 8` 时，低置信度识别为普通 709。

标签冲突或证据不足时标记为“无法确认”。报告会保留证据来源、置信度和冲突说明。

## 从源码运行

需要 Python 3.10 或更高版本：

```powershell
python -m pip install -e ".[web,dev]"
dji-color-web
```

运行检查：

```powershell
python -m pytest -q tests
python -m ruff check .
```

CLI 和 Qt GUI 源码仍为兼容用途保留，但不属于官方发布产物。

## 限制

- 目前不保证覆盖所有 DJI 机型和固件。
- fragmented MP4（`moof/traf/trun`）暂不支持。
- 普通浏览器无法获得拖入文件夹的完整本地路径，请运行桌面应用。
- macOS 正式包目前仅提供 Apple Silicon 版本。

更多信息见 [更新记录](CHANGELOG.md) 和 [技术说明](docs/technical-notes.md)。

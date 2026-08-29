# Changelog

## 2.0.0-preview.1（2026-08-29）

- 新增 pywebview 原生 Web 工作台入口 `dji-color-web`。
- 新增 Python application service、任务状态/进度/取消协议和结构化 Web DTO。
- 原型页面接入真实扫描、整理计划、执行、报告导出、manifest 撤销和 HLG HDR 统计。
- 新增 Web 服务层端到端验收测试与 Web 版 PyInstaller spec。

## 1.1.0

- 新增结构化 QuickTime `ColorGammaSxS` 标签读取，优先使用文件内明确色彩模式。
- 新增 `Rec.2100 HLG（HDR）` 识别、报告、GUI 筛选和 `hlg/` 分类目录。
- 增加多证据置信度与冲突保护；冲突文件不会被自动整理。
- 优化单页 GUI 的扫描、筛选和整理流程，并补充元数据回归测试。

## 1.0.0-dev

- 重构为核心库、CLI 和 GUI 三层结构。
- 新增原生 MP4/MOV `djmd` 读取器，降低对 `ffmpeg/ffprobe` 的依赖。
- 新增 `dji-color scan`、`organize`、`undo`、`version` 命令。
- 新增前缀、移动、复制、manifest 和 undo 流程。
- 新增 PySide6 优先、PyQt5 兼容的 GUI 工作台。
- 新增分类、MP4 reader、计划、manifest、GUI 冒烟测试。

# Changelog

## 1.0.0-dev

- 重构为核心库、CLI 和 GUI 三层结构。
- 新增原生 MP4/MOV `djmd` 读取器，降低对 `ffmpeg/ffprobe` 的依赖。
- 新增 `dji-color scan`、`organize`、`undo`、`version` 命令。
- 新增前缀、移动、复制、manifest 和 undo 流程。
- 新增 PySide6 优先、PyQt5 兼容的 GUI 工作台。
- 新增分类、MP4 reader、计划、manifest、GUI 冒烟测试。

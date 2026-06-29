# Contributing

感谢帮助改进 DJI Color Classifier。这个项目处理用户原始视频素材，代码变更应优先保证可读性、可测试性和文件操作安全。

## 开发环境

```powershell
python -m pip install -e ".[gui,dev]"
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
pytest -q tests
```

如果本机没有 PySide6，但有 PyQt5，也可以运行 GUI 冒烟测试。发布包仍优先使用 PySide6。

## 代码要求

- 核心逻辑放在 `dji_color_classifier/core/`。
- CLI 和 GUI 不直接实现识别规则。
- 文件修改必须先生成计划，再执行。
- 所有面向用户的日志和错误信息优先使用中文。
- 新增 MP4 结构兼容时必须补测试。

## 样本反馈

请不要直接上传完整大视频。更推荐：

- 说明 DJI 设备型号和固件版本。
- 提供工具输出的错误信息。
- 如果可以，提供只含必要元数据的最小复现样本。

## 提交流程

- 运行测试。
- 确认没有 `__pycache__`、报告、manifest 等运行产物。
- 在 PR 中说明是否涉及真实素材文件操作。

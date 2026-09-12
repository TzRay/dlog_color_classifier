# Contributing

感谢帮助改进 DJI Color Classifier。这个项目处理用户原始视频素材，代码变更应优先保证可读性、可测试性和文件操作安全。

## 开发环境

```powershell
python -m pip install -e ".[web,dev]"
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
pytest -q tests
node --test tests/web_runtime.test.cjs
python -m ruff check dji_color_classifier tests scripts classify_dji_color_modes.py rename_dji_color_modes.py
```

当前维护和发布的入口为 Web 桌面版。运行 JavaScript 行为测试需要 Node.js 22 或以上；
历史 Qt 测试在没有对应依赖时自动跳过，不作为桌面版验收依据。

打包后需要验证实际可执行程序，而不仅是源码测试：

```powershell
pyinstaller --clean --noconfirm packaging/dji-color-web.spec
python scripts/verify_bundle.py dist/dji-color-web.exe
```

macOS 将最后一个路径替换为 `dist/dji-color-web`。自检不显示窗口，仅在临时目录中
验证 Web 资源、平台依赖、元数据识别与复制；不能替代原生拖放和窗口布局的实机检查。

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

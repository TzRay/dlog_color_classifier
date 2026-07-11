# GUI Design QA

- Source visual truth: `C:\Users\Ray\.codex\visualizations\2026\07\11\019f505b-56f7-7d62-9b66-655600787592\dji-color-gui-prototype\implementation-final.png`
- Implementation screenshot: `C:\Users\Ray\.codex\visualizations\2026\07\11\019f505b-56f7-7d62-9b66-655600787592\native-gui-results.png`
- Secondary state screenshot: `C:\Users\Ray\.codex\visualizations\2026\07\11\019f505b-56f7-7d62-9b66-655600787592\native-gui-final.png`
- Viewport: 1360 × 860
- State: 第 2 步“检查识别结果”，8 条代表性扫描结果

## Full-view comparison evidence

源 HTML 原型与原生 Qt 截图在同一次视觉检查中并列比较。原生实现保留了左侧任务摘要、三步流程、识别概览、筛选工具、主结果表格和右下角主操作的整体层级。窗口在 1080 × 700 最小尺寸下仍保留主操作和可滚动表格。

## Focused region comparison evidence

- 步骤条：当前步骤使用红色和加粗文字，非当前步骤保持灰色，语义与原型一致。
- 识别概览：D-Log、D-Log2、普通 709 和无法确认均有独立数量与语义色。
- 结果表格：未知结果使用“需要检查”文字和警示色，不只依赖颜色。
- 主操作：第 2 步只提供“下一步：选择整理方式”，第 3 步根据计划数量生成明确按钮文案。

## Required fidelity surfaces

- Fonts and typography: 使用 Windows 原生 Segoe UI / Microsoft YaHei 字体栈；14–22px 层级清晰，路径和长文件名由表格列截断并可横向查看。
- Spacing and layout rhythm: 272px 固定侧栏、28px 主内容边距、12–16px 区域间距和 38px 控件高度与 HTML 原型接近。
- Colors and visual tokens: 使用白色表面、浅灰工作区、深灰正文和 `#e62d2d` 主操作；分类色与原型一致。
- Image quality and asset fidelity: 界面没有摄影或插画资产；文件夹和刷新操作使用 Qt 标准图标库，没有手工 SVG、CSS 图形或占位图。
- Copy and content: 所有主流程词汇均为中文；`prefix`、`error`、`suffix` 和 `manifest` 不再出现在主界面。
- Accessibility: 控件进入 Windows UI Automation 树，表单控件有可读标签，禁用状态与当前流程状态同步。

## Primary interactions tested

- 真实目录扫描：递归扫描 `metadata_transplant_outputs`，识别 3 个 D-Log2 视频。
- 扫描完成后进入第 3 步，选择整理方式后可直接点击整理。
- 筛选、搜索、整理方式变化、返回结果页和高级设置均有对应状态处理。
- 点击整理后内部自动校验计划，执行完成后旧计划失效，不能重复提交。
- Windows 打包程序 `dist\dji-color-gui.exe --smoke` 正常退出。

## Comparison history

### Iteration 1

- P1: 旧界面把内部枚举、模板、执行和日志平铺在一行，主流程不清楚。
- Fix: 改为“选择视频 → 检查识别结果 → 整理文件”三步式状态界面，高级参数折叠到侧栏入口。
- Post-fix evidence: `native-gui-final.png`。

- P1: 伴随文件插入计划后，表格按两个不同列表的相同行号取值，可能显示错误目标路径。
- Fix: 表格改为统一行模型，计划页直接使用每个 `PlanItem.scan_result`。
- Post-fix evidence: `test_gui_plan_rows_stay_aligned_with_sidecars` 通过。

- P1: 文件复制和移动在 GUI 主线程同步执行。
- Fix: 新增后台执行任务和任务令牌；执行期间禁用重复操作，结束后展示逐项结果。
- Post-fix evidence: Windows 真实流程及测试通过。

- P2: 无法确认的视频仍显示为“已识别”。
- Fix: 未知或错误结果显示“需要检查”并使用警示色。
- Post-fix evidence: 最终原生截图第 7 行。

### Iteration 2

- P1: 第三步仍要求单独生成整理预览，主路径存在重复确认。
- Fix: 删除预览按钮和计划表；点击整理时在内部自动计算安全计划，只保留一次包含方式和数量的最终确认。
- Post-fix evidence: `native-gui-final.png` 中直接显示“开始复制整理”。

- P2: 标题、概览数字、步骤和主按钮的字号层级不够明显。
- Fix: 页面标题提高到 28px、概览数字 32px、当前步骤 17px、主按钮 16px，并增加粗细对比。
- Post-fix evidence: `native-gui-results.png` 与 `native-gui-final.png`。

- P2: 页面切换为瞬时硬切，用户容易忽略当前步骤变化。
- Fix: 使用 240ms OutCubic 淡入，并同步更新步骤条的字号、颜色和操作说明横幅。
- Post-fix evidence: 真实窗口交互检查；动画结束后布局无漂移。

## Findings

没有剩余的 P0、P1 或 P2 问题。

## Follow-up polish

- P3: Qt 原生表格未使用 HTML 原型中的胶囊标签，以保证 PySide6/PyQt5 双绑定可移植性。
- P3: 正式发布时可添加经过授权的产品图标，而不是使用 Qt 标准图标。

final result: passed

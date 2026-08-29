# 用户指南

## 基本流程

1. 先扫描目录，查看色彩模式和无法确认的文件。
2. CLI 仍可先预演再加 `--apply`；GUI 保留其既有确认与撤销流程。
3. Web 工作台扫描完成后可直接执行整理；无法确认、证据冲突和识别失败的文件会跳过。

## CLI

扫描：

```powershell
dji-color scan "D:\Users\Ray\Desktop\新建文件夹" --output .\dji_color_modes.csv
```

预演移动到分类目录：

```powershell
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode move
```

执行移动：

```powershell
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode move --apply
```

撤销：

```powershell
dji-color undo "manifest.json" --apply
```

## GUI

GUI 使用轻量单页流程：选择或拖入目录后立即自动识别，结果概览和整理操作会直接显示在同一页。

- 默认选中“复制到分类文件夹”，不会修改原始文件。
- 文件明细默认折叠，需要核对单个视频时再展开。
- 程序会把 `Rec.2100 HLG（HDR）` 单独显示；前缀模式使用 `hlg_`，移动或复制时默认归入 `hlg/`。
- 明确元数据相互冲突的文件会被标记为“无法确认”，并自动从整理计划中跳过。
- 冲突策略、模板、伴随文件和运行日志位于“设置”。
- “操作记录”用于载入 manifest 并撤销之前的整理。

执行前请重点检查：

- 色彩模式是否符合预期。
- 报告中的 QuickTime 色彩标签、主证据来源和置信度；出现“无法确认”时请先查看冲突说明。
- 最终确认窗口中的整理方式和文件数量是否正确。
- 冲突策略是否合适。
- 是否需要同步处理伴随文件。

## 安全建议

- 第一次整理重要素材时，优先使用 `copy` 模式。
- 不要删除 manifest。
- 大批量整理前先用少量文件试跑。

## Web 工作台

Web 工作台采用本地 pywebview，不启动 localhost 服务，也不会上传视频。安装 Web 可选依赖后运行：

```powershell
python -m pip install -e ".[web]"
dji-color-web
```

页面通过 Python bridge 调用任务式应用服务：扫描返回 `task_id`，前端轮询进度后生成 `scan_id`；点击“执行整理”后直接提交 `execute_organize` 任务。页面不接受低级的单文件操作，路径校验、冲突检查和执行结果汇总均由 Python 服务完成。

在桌面 Web 工作台中，可以把素材文件夹直接拖到窗口；pywebview 会在 Python DOM `drop` 事件中提供完整本地路径，然后自动启动扫描。直接用普通浏览器打开 `prototype/index.html` 时受浏览器安全策略限制，拖拽不能读取本地目录路径，请使用“选择素材文件夹”按钮或启动 `dji-color-web`。

默认冲突策略为自动追加序号，可在页面中切换为跳过或标记冲突。Web 工作台不会自动整理 `无法确认`、元数据冲突或识别失败的视频；它们会保留在原位置，并在识别结果和执行统计中明确显示。

Web 工作台不提供整理预演、操作记录、manifest 或撤销。执行期间可以取消任务；取消后页面仍会显示已经完成、跳过和失败的文件数量。CLI 与原生 GUI 的 manifest/撤销能力不受影响。

如果仅需查看视觉原型，可直接打开 `prototype/index.html`；页面显示“等待本地服务”属于预期状态，不能在普通浏览器中执行本地文件操作。

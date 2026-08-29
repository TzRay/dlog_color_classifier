# 用户指南

## 基本流程

1. 先扫描目录，确认识别结果。
2. 检查色彩模式，重点关注“需要检查”的文件。
3. CLI 先预演再加 `--apply`；GUI 选择整理方式后直接点击整理，并在最终确认窗口核对数量。
4. 保留生成的 manifest，后续可以撤销。

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
- 程序会把 `Rec.2100 HLG（HDR）` 单独显示；移动或复制时默认归入 `hlg/`，不会自动添加文件名前缀。
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

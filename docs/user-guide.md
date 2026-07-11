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

GUI 按“选择视频、检查识别结果、整理文件”三个步骤工作，支持扫描、筛选、直接整理、导出报告和从操作记录撤销。

执行前请重点检查：

- 色彩模式是否符合预期。
- 最终确认窗口中的整理方式和文件数量是否正确。
- 冲突策略是否合适。
- 是否需要同步处理伴随文件。

## 安全建议

- 第一次整理重要素材时，优先使用 `copy` 模式。
- 不要删除 manifest。
- 大批量整理前先用少量文件试跑。

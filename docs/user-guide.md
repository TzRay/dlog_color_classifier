# 用户指南

## 基本流程

1. 先扫描目录，确认识别结果。
2. 生成整理计划，检查目标路径。
3. 确认无误后再加 `--apply` 或在 GUI 中点击执行。
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

GUI 支持选择目录、扫描、生成计划、执行整理、导出报告和载入 manifest 撤销。

执行前请重点检查：

- 色彩模式是否符合预期。
- 目标路径是否正确。
- 冲突策略是否合适。
- 是否需要同步处理伴随文件。

## 安全建议

- 第一次整理重要素材时，优先使用 `copy` 模式。
- 不要删除 manifest。
- 大批量整理前先用少量文件试跑。

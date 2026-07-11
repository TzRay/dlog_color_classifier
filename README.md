# dlog_color_classifier

DJI 视频色彩模式识别与整理工具。它读取 DJI MP4/MOV 容器中的 `djmd` 私有元数据，识别素材是 `D-Log`、`D-Log2`、普通 `709`，或无法确认，并按需要生成报告、添加前缀、移动到分类目录、复制到分类目录。

工具不会分析视频画面，不会解码视频，也不会修改视频文件内部元数据。

## 主要功能

- 识别 DJI 视频的 D-Log / D-Log2 / 普通 709。
- 扫描结果可导出 CSV 或 JSON。
- 支持三种整理方式：添加前缀、移动到分类目录、复制到分类目录。
- 默认只预演，不修改文件。
- 执行整理时会生成 `manifest.json`，可用于撤销。
- 可同步处理同名伴随文件，例如 `.srt`、`.lrf`、`.thm`、`.jpg`、`.xml`。
- 提供命令行 CLI 和图形界面 GUI。

## 识别规则

- `ColorGammaSxS == 22`：D-Log2
- `ColorGammaSxS == 2`：D-Log
- `ColorGammaSxS` 缺失且 `record_mode == 8`：普通 709
- 其他情况：无法确认

## CLI 使用指南

CLI 入口命令是：

```powershell
dji-color
```

### 扫描并导出报告

扫描目录并导出 CSV：

```powershell
dji-color scan "D:\Users\Ray\Desktop\新建文件夹" --output .\dji_color_modes.csv
```

导出 JSON：

```powershell
dji-color scan "D:\Users\Ray\Desktop\新建文件夹" --format json --output .\dji_color_modes.json
```

递归扫描子目录：

```powershell
dji-color scan "D:\Users\Ray\Desktop\新建文件夹" --recursive --output .\dji_color_modes.csv
```

减少终端输出：

```powershell
dji-color scan "D:\Users\Ray\Desktop\新建文件夹" --quiet --output .\dji_color_modes.csv
```

### 整理模式

`organize` 默认只预演，不会修改文件。确认计划无误后，再加 `--apply` 执行。

添加前缀：

```powershell
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode prefix
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode prefix --apply
```

默认前缀：

- D-Log -> `dlog_`
- D-Log2 -> `dlog2_`
- 普通 709 -> 不改名
- 无法确认 -> 不改名

移动到分类目录：

```powershell
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode move
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode move --apply
```

默认目录：

- D-Log -> `dlog/`
- D-Log2 -> `dlog2/`
- 普通 709 -> `rec709/`
- 无法确认 -> `unknown/`

复制到分类目录：

```powershell
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode copy
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode copy --apply
```

同步处理伴随文件：

```powershell
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode move --with-sidecars --apply
```

### 自定义模板

文件名模板：

```powershell
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode prefix --name-template "{mode}_{original}"
```

目录模板：

```powershell
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode move --dir-template "{mode}"
```

可用变量：

- `{original}`：原文件名，例如 `DJI_0001.MP4`
- `{stem}`：不含扩展名的文件名
- `{suffix}`：扩展名
- `{mode}`：`dlog`、`dlog2`、`rec709`、`unknown`
- `{mode_label}`：`D-Log`、`D-Log2`、`普通709`、`无法确认`

### 冲突处理

目标文件已存在时，默认停止处理：

```powershell
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode move --on-conflict error
```

跳过冲突文件：

```powershell
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode move --on-conflict skip
```

自动追加序号：

```powershell
dji-color organize "D:\Users\Ray\Desktop\新建文件夹" --mode move --on-conflict suffix
```

### Manifest 和撤销

执行整理时会写入 manifest：

```text
<扫描目录>\.dji-color-classifier\manifests\<时间>.json
```

撤销预演：

```powershell
dji-color undo "D:\Users\Ray\Desktop\新建文件夹\.dji-color-classifier\manifests\20260629T231500.json"
```

真正撤销：

```powershell
dji-color undo "D:\Users\Ray\Desktop\新建文件夹\.dji-color-classifier\manifests\20260629T231500.json" --apply
```

如果撤销时源文件缺失，可选择跳过：

```powershell
dji-color undo "D:\Users\Ray\Desktop\新建文件夹\.dji-color-classifier\manifests\20260629T231500.json" --on-missing skip
```

## GUI 使用指南

GUI 适合不想使用命令行的用户。界面按“选择视频、检查识别结果、整理文件”三个步骤引导操作。

### 第一步：选择视频

- 路径输入框：显示当前视频目录，也可以把文件夹直接拖到窗口里。
- `选择视频文件夹`：打开目录选择窗口并开始扫描。
- `包含子文件夹`：启用后同时扫描所有下级目录。
- `开始扫描` / `重新扫描`：读取视频元数据并刷新识别结果。

### 第二步：检查识别结果

概览区显示 D-Log、D-Log2、普通 709 和无法确认的数量。筛选框和搜索框可快速定位需要检查的文件。

无法确认的视频会以红色“需要检查”显示，程序不会强行猜测其色彩模式。

### 第三步：整理文件

点击 `下一步：选择整理方式` 后，选择以下一种操作：

- `添加文件名前缀`：只给 D-Log / D-Log2 文件添加前缀。
- `移动到分类文件夹`：把文件移动到对应分类目录。
- `复制到分类文件夹（推荐）`：保留原文件并生成分类副本。

选择后直接点击红色主按钮。程序会在内部检查模板、冲突和目标路径，并在真正执行前显示操作方式及文件数量。确认后立即开始，不再需要单独生成整理预览。

冲突策略、伴随文件和模板位于左侧 `高级设置` 中。

### 结果表格

表格用于检查扫描结果和执行状态。

- `状态`：显示该文件是否就绪、跳过或失败。
- `文件名`：原始文件名。
- `色彩模式`：识别结果，包括 D-Log、D-Log2、普通709、无法确认、识别失败。
- `原路径`：当前文件所在位置。
- `目标路径`：执行整理后文件将被移动、复制或重命名到的位置。
- `证据`：用于判断色彩模式的元数据字段。
- `错误`：读取失败、冲突、跳过原因等信息。

### 日志区

左侧 `查看运行日志` 显示扫描、执行和导出结果。如果出现错误，先看这里的提示。

### 撤销操作

窗口顶部菜单有 `从操作记录撤销…`。

使用方式：

1. 点击 `从操作记录撤销…`。
2. 选择之前执行整理时生成的 `manifest.json`。
3. 确认记录数量后点击 `撤销这次整理`。
4. 在最终确认窗口中确认后立即执行。

### 推荐 GUI 工作流程

1. 点击 `选择视频文件夹`，选择 DJI 视频所在目录。
2. 按需要勾选 `包含子文件夹`。
3. 点击 `开始扫描`。
4. 在表格里确认色彩模式。
5. 点击下一步并选择整理方式，首次使用建议选择 `复制到分类文件夹`。
6. 点击红色整理按钮。
7. 在最终确认窗口核对文件数量并确认执行。
8. 保留生成的操作记录，后续需要时可用于撤销。

## 旧脚本兼容

旧入口仍然保留：

```powershell
python .\classify_dji_color_modes.py "D:\Users\Ray\Desktop\新建文件夹" --output .\dji_color_modes.csv
python .\rename_dji_color_modes.py "D:\Users\Ray\Desktop\新建文件夹" --apply
```

旧脚本内部已经调用新的核心库。

## 当前限制

- 原生 reader 只实现读取 DJI `djmd` 第一包所需的 MP4/MOV 子集。
- 检测到 `moof/traf/trun` 等 fragmented MP4 结构时会给出明确错误。
- 不保证覆盖所有 DJI 机型和所有固件。
- 无法确认的视频不会被强行当作 D-Log 或 D-Log2 处理。

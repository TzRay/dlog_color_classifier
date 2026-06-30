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

GUI 适合不想使用命令行的用户。打开程序后，主窗口从上到下分为目录选择区、整理选项区、结果表格和日志区。

### 目录选择区

顶部第一行用于选择素材目录。

- `目录` 输入框：显示当前要扫描的视频目录。可以手动输入路径，也可以把文件夹拖到窗口里。
- `选择目录` 按钮：打开目录选择窗口。
- `递归` 勾选框：勾选后会扫描当前目录及所有子目录；不勾选时只扫描当前目录第一层。
- `扫描` 按钮：读取目录中的视频文件并识别色彩模式。扫描完成后，结果会显示在表格里。

### 整理选项区

第二行用于设置整理方式。

- `模式` 下拉框：选择整理动作。
  - `prefix`：只给 D-Log / D-Log2 文件添加前缀。
  - `move`：把文件移动到分类目录。
  - `copy`：把文件复制到分类目录，原文件保留。
- `冲突` 下拉框：目标文件已存在时的处理方式。
  - `error`：默认策略，发现冲突就停止对应文件。
  - `skip`：跳过冲突文件。
  - `suffix`：自动追加序号，生成不冲突的新文件名。
- `伴随文件` 勾选框：同时整理同名的 `.srt`、`.lrf`、`.thm`、`.jpg`、`.xml` 等文件。
- `文件名模板` 输入框：用于自定义重命名结果，例如 `{mode}_{original}`。
- `目录模板` 输入框：用于自定义分类目录，例如 `{mode}`。
- `生成计划` 按钮：根据当前扫描结果和整理选项生成目标路径。这个按钮不会修改文件。
- `执行` 按钮：执行当前计划。点击后会弹出确认框，确认后才会修改文件。
- `导出报告` 按钮：把当前扫描结果导出为 CSV 或 JSON。

### 结果表格

表格用于检查扫描结果和整理计划。

- `状态`：显示该文件是否就绪、跳过或失败。
- `文件名`：原始文件名。
- `色彩模式`：识别结果，包括 D-Log、D-Log2、普通709、无法确认、识别失败。
- `原路径`：当前文件所在位置。
- `目标路径`：执行整理后文件将被移动、复制或重命名到的位置。
- `证据`：用于判断色彩模式的元数据字段。
- `错误`：读取失败、冲突、跳过原因等信息。

### 日志区

底部日志区显示扫描、生成计划、执行、导出等操作结果。如果出现错误，先看这里的提示。

### 撤销操作

窗口顶部工具栏有 `打开 manifest 撤销`。

使用方式：

1. 点击 `打开 manifest 撤销`。
2. 选择之前执行整理时生成的 `manifest.json`。
3. 程序会把撤销计划显示到表格中。
4. 检查目标路径无误后，点击 `执行`。

### 推荐 GUI 工作流程

1. 点击 `选择目录`，选择 DJI 视频所在文件夹。
2. 按需要勾选 `递归`。
3. 点击 `扫描`。
4. 在表格里确认色彩模式。
5. 选择 `模式`，例如 `move` 或 `copy`。
6. 设置 `冲突` 策略，首次使用建议选择 `error` 或 `copy` 模式。
7. 点击 `生成计划`，检查 `目标路径`。
8. 确认无误后点击 `执行`。
9. 保留生成的 manifest，后续需要时可用于撤销。

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

# dlog_color_classifier

DJI 视频色彩模式识别与整理工具。它读取 DJI MP4/MOV 容器中的 `djmd` 私有元数据与 QuickTime 标签，识别素材是 `D-Log`、`D-Log2`、普通 `709`、`Rec.2100 HLG（HDR）`，或无法确认，并按需要生成报告、添加前缀、移动到分类目录、复制到分类目录。

工具不会分析视频画面，不会解码视频，也不会修改视频文件内部元数据。

当前版本：`2.0.0-preview.1`。这是基于 pywebview 原生 Web 工作台的预览版，发布包由 GitHub Actions 自动构建。

## 主要功能

- 识别 DJI 视频的 D-Log / D-Log2 / 普通 709 / Rec.2100 HLG（HDR）。
- 扫描结果可导出 CSV 或 JSON。
- 支持三种整理方式：添加前缀、移动到分类目录、复制到分类目录。
- 默认只预演，不修改文件。
- 执行整理时会生成 `manifest.json`，可用于撤销。
- 可同步处理同名伴随文件，例如 `.srt`、`.lrf`、`.thm`、`.jpg`、`.xml`。
- 提供命令行 CLI 和图形界面 GUI。
- 提供基于 pywebview 的原生 Web 工作台，继续复用 Python 核心。

## 识别规则

- QuickTime `com.dji.camera.ColorGammaSxS` 有明确标签时优先使用：`D-Log`、`D-Log2`、`Rec.709`、`Rec.2100 HLG`。
- 没有明确标签时，`djmd` 的 `ColorGammaSxS == 22`：D-Log2；`== 2`：D-Log。
- 枚举缺失且 `record_mode == 8` 时，作为低置信度兼容规则识别为普通 709。
- 明确标签与已映射 `djmd` 枚举冲突、或无可用证据时：无法确认。

报告会保留 QuickTime 标签、主证据来源、置信度和冲突说明。程序按 MP4 box 结构读取元数据，不会在压缩视频数据中搜索关键词。
若明确标签与 `djmd` 枚举冲突，文件会标记为“无法确认”且不会被自动重命名、移动或复制。

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
- Rec.2100 HLG（HDR） -> `hlg_`
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
- Rec.2100 HLG（HDR） -> `hlg/`
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
- `{mode}`：`dlog`、`dlog2`、`rec709`、`rec2100_hlg`、`unknown`
- `{mode_label}`：`D-Log`、`D-Log2`、`普通709`、`Rec.2100 HLG（HDR）`、`无法确认`

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

GUI 适合不想使用命令行的用户。识别速度很快，因此界面采用单页操作：选择文件夹后自动识别，在同一页确认结果并整理文件。

### 选择并识别视频

- 路径输入框：显示当前视频目录，也可以把文件夹直接拖到窗口里。
- `选择文件夹`：打开目录选择窗口，选择后立即开始识别。
- 粘贴路径：把目录粘贴到路径框后按 Enter 即可识别。
- `重新识别`：重新读取当前目录中的视频元数据。
- `设置`：可调整是否包含子文件夹；范围改变后会自动重新识别。

### 查看识别结果

概览区直接显示全部视频、D-Log、D-Log2、普通 709、HLG HDR 和待确认数量。无法确认的视频会显示为“需要检查”，程序不会强行猜测其色彩模式。

日常使用不需要查看表格；需要核对单个文件时，点击 `查看文件明细`，再通过筛选框和搜索框定位文件。点击 `返回整理操作` 即可回到主操作区。

### 整理文件

识别完成后，界面会直接显示三种整理方式：

- `添加文件名前缀`：给 D-Log、D-Log2 与 Rec.2100 HLG（HDR）文件添加前缀。
- `移动到分类文件夹`：把文件移动到对应分类目录。
- `复制到分类文件夹`：保留原文件并生成分类副本，默认选中且推荐首次使用。

选择后直接点击红色主按钮。按钮和说明会显示本次操作及文件数量；程序会在内部检查模板、冲突和目标路径，并在真正执行前再次显示准确的处理与跳过数量。

冲突策略、伴随文件、模板和运行日志位于顶部 `设置` 中。

### 结果表格

文件明细默认折叠，展开后显示：

- `状态`：显示该文件是否就绪、跳过或失败。
- `文件名`：原始文件名。
- `色彩模式`：识别结果，包括 D-Log、D-Log2、普通709、Rec.2100 HLG（HDR）、无法确认、识别失败。
- `所在文件夹`：文件当前所在目录；悬停文件名或目录可查看完整路径。

执行出现失败时，界面会自动切换到文件明细并显示失败说明。

### 日志区

点击 `设置`，再点击 `查看运行日志`，可以查看扫描、执行和导出结果。

### 撤销操作

窗口右上角有 `操作记录`。

使用方式：

1. 点击 `操作记录`。
2. 选择之前执行整理时生成的 `manifest.json`。
3. 确认记录数量后点击 `撤销这次整理`。
4. 在最终确认窗口中确认后立即执行。

### 推荐 GUI 工作流程

1. 点击 `选择视频文件夹`，选择 DJI 视频所在目录。
2. 等待自动识别完成，查看各色彩模式数量。
3. 直接使用默认的 `复制到分类文件夹`，或选择其他整理方式。
4. 点击红色整理按钮，在最终确认窗口核对数量并执行。
5. 保留生成的操作记录，后续需要时可用于撤销。

## Web 工作台（原生 Web 路线）

Web 工作台是当前迁移方案的第一阶段实现：页面采用 `prototype/index.html` 的单页工作台原型，桌面壳使用 pywebview，后端通过任务式 Python bridge 调用现有 `core`。前端不直接访问本地文件系统，目录扫描、冲突检查、文件操作和 manifest 撤销都在 Python 服务层完成。

安装并启动：

```powershell
python -m pip install -e ".[web]"
dji-color-web
```

工作台支持：

- 选择或拖入目录，递归扫描 MP4 / MOV / M4V，并显示 D-Log、D-Log2、普通 709、HLG HDR 和待确认统计。
- 以任务 ID 轮询扫描和执行进度，支持取消扫描；长任务不会阻塞页面。
- 复制、移动、添加前缀三种整理方式，支持伴随文件和冲突策略。
- 执行前二次确认，完成后自动写入 manifest；可载入操作记录并执行撤销。
- 导出 CSV 或 JSON 识别报告。

没有安装 pywebview 时仍可直接打开 `prototype/index.html` 做视觉验收；此时页面会显示等待本地服务，真实文件操作必须从 `dji-color-web` 启动。

## 旧脚本兼容

旧入口仍然保留：

```powershell
python .\classify_dji_color_modes.py "D:\Users\Ray\Desktop\新建文件夹" --output .\dji_color_modes.csv
python .\rename_dji_color_modes.py "D:\Users\Ray\Desktop\新建文件夹" --apply
```

旧脚本内部已经调用新的核心库。

## 当前限制

- 原生 reader 实现 DJI `djmd` 第一包及 QuickTime `mdta` 色彩标签所需的 MP4/MOV 子集。
- 检测到 `moof/traf/trun` 等 fragmented MP4 结构时会给出明确错误。
- 不保证覆盖所有 DJI 机型和所有固件。
- 无法确认的视频不会被强行当作 D-Log 或 D-Log2 处理。

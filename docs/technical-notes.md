# 技术说明

## 原生 MP4 读取器

工具只解析读取 DJI `djmd` 第一包所需的 ISO BMFF/MP4 box 子集：

- `moov/trak/mdia/minf/stbl/stsd`
- `stsz` 或 `stz2`
- `stsc`
- `stco` 或 `co64`

reader 不解码视频，不读取画面内容，也不修改 MP4 文件。

分类流程通过同一个文件句柄和 box 树读取两种证据。读取器先检查 `stsd`
确定 `djmd` 轨，再按需读取首个 sample 的尺寸、chunk 映射和偏移，不创建完整
sample 表。单次元数据读取和 djmd 包限制为 8 MiB，容器最多嵌套 32 层。

除 `djmd` 外，reader 还会在 `moov/meta` 或 `moov/udta/meta` 中解析 QuickTime
`keys/ilst/data` 标签。DJI Osmo Pocket 等机型会将明确的色彩模式写入
`com.dji.camera.ColorGammaSxS`，例如 `D-Log`、`D-Log2`、`Rec.709`、
`Rec.2100 HLG`。解析严格按 box 边界进行，不扫描 `mdat` 压缩码流中的关键词。

## 色彩模式判定

当前判定按证据可靠性从高到低进行：

- 已知 QuickTime `ColorGammaSxS` 文本标签：直接使用对应模式；
- `top2.top2.top3.field1 == 22`：D-Log2；
- `top2.top2.top3.field1 == 2`：D-Log；
- `top2.top2.top3.field1` 缺失且 `top2.top3.field5 == 8`：普通 709 兼容规则；
- QuickTime 标签与已映射的 `djmd` 枚举冲突：无法确认并报告冲突。

当前支持 D-Log、D-Log2、普通 709、Rec.2100 HLG（HDR）。无法匹配或证据冲突时返回
“无法确认”，不会强行重命名为某种日志模式。

## 暂不支持

检测到 `moof/traf/trun` 等 fragmented MP4 结构时，原生 reader 会返回明确错误。

## GUI 绑定

发布首选 PySide6。为了方便本地验收和部分现有 Python 环境，代码也兼容 PyQt5。

## Web 应用服务

`dji_color_classifier/web_service.py` 提供与 UI 无关的 JSON DTO 和任务式服务边界：

- `start_scan` / `get_task_status` / `cancel_task`：后台扫描、整理及进度。
- `execute_organize`：根据 scan ID 和当前整理设置直接执行；任务开始时即时构建计划，不写 manifest。
- `export_report`：导出扫描报告。

Web 服务对同一个扫描根目录加整理互斥，并跳过无法确认、元数据冲突和识别失败的文件。取消整理时会返回取消前已完成的结果，前端必须展示该结果；CLI 和原生 GUI 保留预演、manifest 与撤销能力。

扫描与整理共用包含父子目录关系的任务互斥。整理提交后，对应扫描结果即失效，
需要重新识别后才能再次整理；原扫描仍可用于导出报告。任务结果中分别保留
`completed`、`skipped`、`failed` 状态，`pending_count` 表示取消后尚未完成的计划项。

复制按固定大小的数据块写入目标目录内的临时文件，核对尺寸与源文件状态后以
排他重命名发布。取消或失败会清理临时文件；发布前新出现的同名目标不会被覆盖。
视频与伴随文件在计划阶段共用冲突策略和编号，避免整理后的文件失去同名关系。

`dji_color_classifier/web_app.py` 只负责 pywebview 窗口和系统文件对话框。原型页面使用同一组 DTO，因此后续替换为 Vue 3 + TypeScript 或 Tauri 2 时，不需要重写识别和文件整理核心。

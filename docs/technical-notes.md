# 技术说明

## 原生 MP4 读取器

工具只解析读取 DJI `djmd` 第一包所需的 ISO BMFF/MP4 box 子集：

- `moov/trak/mdia/minf/stbl/stsd`
- `stsz` 或 `stz2`
- `stsc`
- `stco` 或 `co64`

reader 不解码视频，不读取画面内容，也不修改 MP4 文件。

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

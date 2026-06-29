# 技术说明

## 原生 MP4 读取器

工具只解析读取 DJI `djmd` 第一包所需的 ISO BMFF/MP4 box 子集：

- `moov/trak/mdia/minf/stbl/stsd`
- `stsz` 或 `stz2`
- `stsc`
- `stco` 或 `co64`

reader 不解码视频，不读取画面内容，也不修改 MP4 文件。

## 色彩模式判定

当前判定规则来自已知样本字段路径：

- `top2.top2.top3.field1 == 22`：D-Log2
- `top2.top2.top3.field1 == 2`：D-Log
- `top2.top2.top3.field1` 缺失且 `top2.top3.field5 == 8`：普通 709

无法匹配时返回“无法确认”，不会强行重命名为某种日志模式。

## 暂不支持

检测到 `moof/traf/trun` 等 fragmented MP4 结构时，原生 reader 会返回明确错误。

## GUI 绑定

发布首选 PySide6。为了方便本地验收和部分现有 Python 环境，代码也兼容 PyQt5。

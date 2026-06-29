# dlog_color_classifier

用于识别 DJI Osmo Pocket 4P 视频里的 D-Log / D-Log2 / 普通 709 色彩模式，并按需给文件名添加前缀。

脚本只读取 MP4 容器中的 DJI `djmd` 私有元数据轨，不分析视频画面内容。

## 文件说明

- `classify_dji_color_modes.py`：批量识别目录内 MP4 文件，输出 CSV。
- `rename_dji_color_modes.py`：只处理 `DJI_` 开头的视频文件；D-Log 添加 `dlog_` 前缀，D-Log2 添加 `dlog2_` 前缀，普通 709 和无法确认的视频保持原名。

## 使用方式

先预演，不修改文件：

```powershell
python .\rename_dji_color_modes.py "D:\Users\Ray\Desktop\新建文件夹"
```

确认输出无误后执行重命名：

```powershell
python .\rename_dji_color_modes.py "D:\Users\Ray\Desktop\新建文件夹" --apply
```

单独生成识别 CSV：

```powershell
python .\classify_dji_color_modes.py "D:\Users\Ray\Desktop\新建文件夹" --output .\dji_color_modes.csv
```

## 识别规则

- `ColorGammaSxS` 枚举 `22`：D-Log2
- `ColorGammaSxS` 枚举 `2`：D-Log
- 枚举缺失且记录模式字段为 `8`：普通 709
- 无法确认：按普通 709 处理，不重命名

## 环境要求

- Python 3.10+
- `ffmpeg` 和 `ffprobe` 需要在 `PATH` 中可用

"""原生 MP4/MOV 元数据读取器。

本模块只实现读取 DJI ``djmd`` 第一包和 QuickTime ``mdta`` 标签所需的
ISO BMFF 子集，不做视频解码，也不扫描压缩码流。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO


class Mp4ReaderError(RuntimeError):
    """MP4 读取失败。"""


class UnsupportedMp4Error(Mp4ReaderError):
    """当前原生读取器暂不支持的 MP4 结构。"""


@dataclass(frozen=True)
class Box:
    """MP4 box 的基础信息。"""

    type: str
    start: int
    size: int
    header_size: int
    children: list["Box"] = field(default_factory=list)

    @property
    def payload_start(self) -> int:
        """box payload 起始偏移。"""

        return self.start + self.header_size

    @property
    def end(self) -> int:
        """box 结束偏移。"""

        return self.start + self.size


CONTAINER_BOXES = {
    "moov",
    "trak",
    "mdia",
    "minf",
    "stbl",
    "edts",
    "dinf",
    "udta",
    "meta",
}

# 大多数 ISO BMFF ``meta`` 是 FullBox，但部分 DJI 文件遵循 QuickTime 写法，
# 直接以子 box 开始。解析时需根据首个子 box 的合法性判断，不能固定跳过 4 字节。
# DJI 写入 QuickTime 元数据时使用的标准键名。
DJI_COLOR_GAMMA_KEY = "com.dji.camera.ColorGammaSxS"


@dataclass(frozen=True)
class SampleTable:
    """定位第一包 sample 所需的表。"""

    sample_entry_types: tuple[str, ...]
    sample_sizes: tuple[int, ...]
    chunk_offsets: tuple[int, ...]
    first_chunk: int
    samples_per_chunk: int


def read_first_djmd_packet(video_path: Path) -> bytes:
    """从 MP4/MOV 文件中读取 DJI `djmd` 轨第一包数据。"""

    with video_path.open("rb") as handle:
        file_size = video_path.stat().st_size
        top_boxes = _parse_children(handle, 0, file_size)
        if _find_box(top_boxes, ("moof",)) is not None:
            raise UnsupportedMp4Error("暂不支持 fragmented MP4：检测到 moof box")

        moov = _find_box(top_boxes, ("moov",))
        if moov is None:
            raise Mp4ReaderError("未找到 moov box，无法读取 sample table")

        for trak in [box for box in moov.children if box.type == "trak"]:
            table = _read_sample_table(handle, trak)
            if table is None or "djmd" not in table.sample_entry_types:
                continue
            offset, size = _first_sample_location(table)
            if offset < 0 or size <= 0 or offset + size > file_size:
                raise Mp4ReaderError("djmd 第一包偏移或长度无效")
            handle.seek(offset)
            return handle.read(size)

    raise Mp4ReaderError("未找到 DJI djmd 数据轨")


def read_quicktime_metadata(video_path: Path) -> dict[str, str]:
    """读取 QuickTime ``mdta`` 键值对。

    仅解析 ``moov/meta`` 或 ``moov/udta/meta`` 中的 ``keys`` 与 ``ilst``，
    不读取 ``mdat``。没有该元数据时返回空字典；容器或元数据结构损坏时抛出
    :class:`Mp4ReaderError`，让调用方保留诊断信息。
    """

    with video_path.open("rb") as handle:
        file_size = video_path.stat().st_size
        top_boxes = _parse_children(handle, 0, file_size)
        moov = _find_box(top_boxes, ("moov",))
        if moov is None:
            raise Mp4ReaderError("未找到 moov box，无法读取 QuickTime 元数据")

        meta = _find_box([moov], ("moov", "meta")) or _find_box([moov], ("moov", "udta", "meta"))
        if meta is None:
            return {}

        keys_box = _find_direct_child(meta, "keys")
        ilst_box = _find_direct_child(meta, "ilst")
        if keys_box is None or ilst_box is None:
            return {}

        keys = _read_mdta_keys(handle, keys_box)
        if not keys:
            return {}
        return _read_mdta_values(handle, ilst_box, keys)


def read_dji_color_gamma_label(video_path: Path) -> str | None:
    """读取 DJI ``ColorGammaSxS`` 文本标签；标签不存在时返回 ``None``。"""

    return read_quicktime_metadata(video_path).get(DJI_COLOR_GAMMA_KEY)


def _parse_children(handle: BinaryIO, start: int, end: int) -> list[Box]:
    """解析指定范围内的子 box。"""

    boxes: list[Box] = []
    offset = start
    while offset + 8 <= end:
        box = _read_box_header(handle, offset, end)
        if box.size < box.header_size:
            raise Mp4ReaderError(f"无效 box 大小：{box.type} at {box.start}")
        if box.end > end:
            raise Mp4ReaderError(f"box 超出父级范围：{box.type} at {box.start}")

        children: list[Box] = []
        if box.type in CONTAINER_BOXES:
            child_start = _container_children_start(handle, box)
            if child_start > box.end:
                raise Mp4ReaderError(f"容器 box 数据过短：{box.type} at {box.start}")
            children = _parse_children(handle, child_start, box.end)
        boxes.append(Box(box.type, box.start, box.size, box.header_size, children))

        if box.size == 0:
            break
        offset = box.end
    return boxes


def _read_box_header(handle: BinaryIO, offset: int, parent_end: int) -> Box:
    """读取 box header，支持 32 位 size 和 64 位 largesize。"""

    handle.seek(offset)
    header = handle.read(8)
    if len(header) != 8:
        raise Mp4ReaderError("读取 box header 失败")

    size32, box_type_raw = struct.unpack(">I4s", header)
    box_type = box_type_raw.decode("latin1")
    header_size = 8

    if size32 == 1:
        largesize_raw = handle.read(8)
        if len(largesize_raw) != 8:
            raise Mp4ReaderError("读取 largesize 失败")
        size = struct.unpack(">Q", largesize_raw)[0]
        header_size = 16
    elif size32 == 0:
        size = parent_end - offset
    else:
        size = size32

    return Box(box_type, offset, size, header_size)


def _container_children_start(handle: BinaryIO, box: Box) -> int:
    """返回容器子 box 的实际起点，兼容两种 ``meta`` 写法。"""

    if box.type != "meta":
        return box.payload_start

    handle.seek(box.payload_start)
    header = handle.read(8)
    if len(header) < 8:
        raise Mp4ReaderError("meta box 数据过短")
    direct_size = struct.unpack_from(">I", header)[0]
    remaining = box.end - box.payload_start
    if 8 <= direct_size <= remaining:
        return box.payload_start
    return box.payload_start + 4


def _read_mdta_keys(handle: BinaryIO, box: Box) -> dict[int, str]:
    """读取 ``keys`` box，建立一基索引到键名的映射。"""

    handle.seek(box.payload_start)
    payload = handle.read(box.size - box.header_size)
    if len(payload) < 8:
        raise Mp4ReaderError("QuickTime keys 数据过短")

    entry_count = struct.unpack_from(">I", payload, 4)[0]
    offset = 8
    keys: dict[int, str] = {}
    for index in range(1, entry_count + 1):
        if offset + 8 > len(payload):
            raise Mp4ReaderError("QuickTime keys 条目不完整")
        entry_size = struct.unpack_from(">I", payload, offset)[0]
        if entry_size < 8 or offset + entry_size > len(payload):
            raise Mp4ReaderError("QuickTime keys 条目大小无效")

        # 4 字节 namespace 后是 UTF-8 键名；未知 namespace 仍保留键名，方便兼容。
        key_raw = payload[offset + 8 : offset + entry_size]
        try:
            key = key_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise Mp4ReaderError("QuickTime keys 包含非 UTF-8 键名") from exc
        keys[index] = key
        offset += entry_size
    return keys


def _read_mdta_values(handle: BinaryIO, ilst_box: Box, keys: dict[int, str]) -> dict[str, str]:
    """读取 ``ilst`` 内与 ``keys`` 索引对应的文本 ``data`` 值。"""

    values: dict[str, str] = {}
    for item in _parse_children(handle, ilst_box.payload_start, ilst_box.end):
        key_index = int.from_bytes(item.type.encode("latin1"), "big")
        key = keys.get(key_index)
        if key is None:
            continue

        data_box = _find_direct_child_from_range(handle, item.payload_start, item.end, "data")
        if data_box is None:
            continue
        value = _read_mdta_text_value(handle, data_box)
        if value is not None:
            values[key] = value
    return values


def _find_direct_child_from_range(handle: BinaryIO, start: int, end: int, box_type: str) -> Box | None:
    """在未预先展开的 box 范围内查找直接子 box。"""

    for child in _parse_children(handle, start, end):
        if child.type == box_type:
            return child
    return None


def _read_mdta_text_value(handle: BinaryIO, box: Box) -> str | None:
    """读取 QuickTime ``data`` box 的 UTF-8 文本 payload。"""

    handle.seek(box.payload_start)
    payload = handle.read(box.size - box.header_size)
    # data 为 FullBox，后续 4 字节为 locale；两者均不是实际文本。
    if len(payload) < 8:
        raise Mp4ReaderError("QuickTime data 数据过短")
    value_raw = payload[8:].rstrip(b"\x00")
    if not value_raw:
        return ""
    try:
        return value_raw.decode("utf-8")
    except UnicodeDecodeError:
        # 非文本 data（例如封面或整数）与色彩标签无关，跳过即可。
        return None


def _read_sample_table(handle: BinaryIO, trak: Box) -> SampleTable | None:
    """从 trak 中读取定位第一包需要的 sample table。"""

    stbl = _find_box([trak], ("trak", "mdia", "minf", "stbl"))
    if stbl is None:
        return None

    stsd = _find_direct_child(stbl, "stsd")
    stsz = _find_direct_child(stbl, "stsz")
    stz2 = _find_direct_child(stbl, "stz2")
    stsc = _find_direct_child(stbl, "stsc")
    stco = _find_direct_child(stbl, "stco")
    co64 = _find_direct_child(stbl, "co64")

    if stsd is None or stsc is None or (stsz is None and stz2 is None) or (stco is None and co64 is None):
        return None

    sample_entry_types = _read_stsd_sample_entry_types(handle, stsd)
    sample_sizes = _read_stsz(handle, stsz) if stsz is not None else _read_stz2(handle, stz2)  # type: ignore[arg-type]
    first_chunk, samples_per_chunk = _read_first_stsc_entry(handle, stsc)
    chunk_offsets = _read_stco(handle, stco) if stco is not None else _read_co64(handle, co64)  # type: ignore[arg-type]

    return SampleTable(
        sample_entry_types=tuple(sample_entry_types),
        sample_sizes=tuple(sample_sizes),
        chunk_offsets=tuple(chunk_offsets),
        first_chunk=first_chunk,
        samples_per_chunk=samples_per_chunk,
    )


def _read_stsd_sample_entry_types(handle: BinaryIO, box: Box) -> list[str]:
    """读取 stsd 中的 sample entry type，例如 `djmd`。"""

    handle.seek(box.payload_start)
    payload = handle.read(box.size - box.header_size)
    if len(payload) < 8:
        raise Mp4ReaderError("stsd 数据过短")

    entry_count = struct.unpack_from(">I", payload, 4)[0]
    offset = 8
    entry_types: list[str] = []
    for _ in range(entry_count):
        if offset + 8 > len(payload):
            raise Mp4ReaderError("stsd sample entry 不完整")
        entry_size, entry_type_raw = struct.unpack_from(">I4s", payload, offset)
        if entry_size < 8 or offset + entry_size > len(payload):
            raise Mp4ReaderError("stsd sample entry 大小无效")
        entry_types.append(entry_type_raw.decode("latin1"))
        offset += entry_size
    return entry_types


def _read_stsz(handle: BinaryIO, box: Box) -> list[int]:
    """读取 stsz sample size 表。"""

    handle.seek(box.payload_start)
    payload = handle.read(box.size - box.header_size)
    if len(payload) < 12:
        raise Mp4ReaderError("stsz 数据过短")
    sample_size, sample_count = struct.unpack_from(">II", payload, 4)
    if sample_count == 0:
        raise Mp4ReaderError("stsz sample_count 为 0")
    if sample_size != 0:
        return [sample_size] * sample_count
    expected = 12 + sample_count * 4
    if len(payload) < expected:
        raise Mp4ReaderError("stsz sample size 表不完整")
    return list(struct.unpack_from(f">{sample_count}I", payload, 12))


def _read_stz2(handle: BinaryIO, box: Box) -> list[int]:
    """读取 compact sample size 表。"""

    handle.seek(box.payload_start)
    payload = handle.read(box.size - box.header_size)
    if len(payload) < 12:
        raise Mp4ReaderError("stz2 数据过短")
    field_size = payload[7]
    sample_count = struct.unpack_from(">I", payload, 8)[0]
    data = payload[12:]

    if field_size == 4:
        if len(data) * 2 < sample_count:
            raise Mp4ReaderError("stz2 4-bit sample size 表不完整")
        sizes: list[int] = []
        for byte in data:
            sizes.append(byte >> 4)
            if len(sizes) == sample_count:
                break
            sizes.append(byte & 0x0F)
            if len(sizes) == sample_count:
                break
        return sizes
    if field_size == 8:
        if len(data) < sample_count:
            raise Mp4ReaderError("stz2 8-bit sample size 表不完整")
        return list(data[:sample_count])
    if field_size == 16:
        if len(data) < sample_count * 2:
            raise Mp4ReaderError("stz2 16-bit sample size 表不完整")
        return list(struct.unpack_from(f">{sample_count}H", data, 0))
    raise Mp4ReaderError(f"不支持的 stz2 field_size：{field_size}")


def _read_first_stsc_entry(handle: BinaryIO, box: Box) -> tuple[int, int]:
    """读取 stsc 第一条映射。第一包 sample 一定位于第一条映射的 first_chunk。"""

    handle.seek(box.payload_start)
    payload = handle.read(box.size - box.header_size)
    if len(payload) < 20:
        raise Mp4ReaderError("stsc 数据过短")
    entry_count = struct.unpack_from(">I", payload, 4)[0]
    if entry_count == 0:
        raise Mp4ReaderError("stsc entry_count 为 0")
    first_chunk, samples_per_chunk, _sample_description_index = struct.unpack_from(">III", payload, 8)
    return first_chunk, samples_per_chunk


def _read_stco(handle: BinaryIO, box: Box) -> list[int]:
    """读取 32 位 chunk offset 表。"""

    handle.seek(box.payload_start)
    payload = handle.read(box.size - box.header_size)
    if len(payload) < 8:
        raise Mp4ReaderError("stco 数据过短")
    entry_count = struct.unpack_from(">I", payload, 4)[0]
    expected = 8 + entry_count * 4
    if len(payload) < expected:
        raise Mp4ReaderError("stco offset 表不完整")
    return list(struct.unpack_from(f">{entry_count}I", payload, 8))


def _read_co64(handle: BinaryIO, box: Box) -> list[int]:
    """读取 64 位 chunk offset 表。"""

    handle.seek(box.payload_start)
    payload = handle.read(box.size - box.header_size)
    if len(payload) < 8:
        raise Mp4ReaderError("co64 数据过短")
    entry_count = struct.unpack_from(">I", payload, 4)[0]
    expected = 8 + entry_count * 8
    if len(payload) < expected:
        raise Mp4ReaderError("co64 offset 表不完整")
    return list(struct.unpack_from(f">{entry_count}Q", payload, 8))


def _first_sample_location(table: SampleTable) -> tuple[int, int]:
    """计算第一包 sample 的文件偏移和长度。"""

    if not table.sample_sizes:
        raise Mp4ReaderError("sample size 表为空")
    if not table.chunk_offsets:
        raise Mp4ReaderError("chunk offset 表为空")
    if table.first_chunk <= 0 or table.first_chunk > len(table.chunk_offsets):
        raise Mp4ReaderError("stsc first_chunk 超出 chunk offset 表范围")
    if table.samples_per_chunk <= 0:
        raise Mp4ReaderError("stsc samples_per_chunk 无效")
    return table.chunk_offsets[table.first_chunk - 1], table.sample_sizes[0]


def _find_direct_child(box: Box, box_type: str) -> Box | None:
    """查找直接子 box。"""

    for child in box.children:
        if child.type == box_type:
            return child
    return None


def _find_box(boxes: list[Box], path: tuple[str, ...]) -> Box | None:
    """按 box 路径查找节点。"""

    if not path:
        return None
    for box in boxes:
        if box.type != path[0]:
            continue
        if len(path) == 1:
            return box
        return _find_box(box.children, path[1:])
    return None

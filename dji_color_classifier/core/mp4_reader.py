"""原生 MP4/MOV 元数据读取器。

本模块只实现读取 DJI ``djmd`` 第一包和 QuickTime ``mdta`` 标签所需的
ISO BMFF 子集，不做视频解码，也不扫描压缩码流。
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO


class Mp4ReaderError(RuntimeError):
    """MP4 读取失败。"""


class UnsupportedMp4Error(Mp4ReaderError):
    """当前原生读取器暂不支持的 MP4 结构。"""


class MissingDjmdError(Mp4ReaderError):
    """容器可读取，但不含 DJI djmd 数据轨。"""


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
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_BOX_DEPTH = 32
MAX_BOX_COUNT = 100_000


@dataclass(frozen=True)
class DjiMetadata:
    """同一次文件读取获得的色彩证据及独立来源的诊断。"""

    metadata_label: str | None
    packet: bytes | None
    size: int
    warnings: tuple[str, ...] = ()


def read_dji_metadata(video_path: Path) -> DjiMetadata:
    """一次打开并解析容器，分别读取 mdta 和 djmd；一侧失败不丢弃另一侧证据。"""

    with video_path.open("rb") as handle:
        file_size = os.fstat(handle.fileno()).st_size
        top_boxes = _parse_children(handle, 0, file_size)
        warnings: list[str] = []
        label: str | None = None
        packet: bytes | None = None
        try:
            label = _read_quicktime_metadata(handle, top_boxes, wanted_key=DJI_COLOR_GAMMA_KEY).get(DJI_COLOR_GAMMA_KEY)
        except Mp4ReaderError as exc:
            warnings.append(f"QuickTime 元数据读取失败：{exc}")
        try:
            packet = _read_djmd_packet(handle, top_boxes, file_size)
        except MissingDjmdError:
            pass
        except Mp4ReaderError as exc:
            warnings.append(f"djmd 元数据读取失败：{exc}")
        return DjiMetadata(label, packet, file_size, tuple(warnings))


def read_first_djmd_packet(video_path: Path) -> bytes:
    """从 MP4/MOV 文件中读取 DJI `djmd` 轨第一包数据。"""

    with video_path.open("rb") as handle:
        file_size = os.fstat(handle.fileno()).st_size
        top_boxes = _parse_children(handle, 0, file_size)
        return _read_djmd_packet(handle, top_boxes, file_size)


def _read_djmd_packet(handle: BinaryIO, top_boxes: list[Box], file_size: int) -> bytes:
    """从已解析的容器中定位第一包，仅读取目标轨道必要的表项。"""

    if _find_box(top_boxes, ("moof",)) is not None:
        raise UnsupportedMp4Error("暂不支持 fragmented MP4：检测到 moof box")
    moov = _find_box(top_boxes, ("moov",))
    if moov is None:
        raise Mp4ReaderError("未找到 moov box，无法读取 sample table")
    for trak in moov.children:
        if trak.type != "trak":
            continue
        location = _read_first_sample_location(handle, trak)
        if location is None:
            continue
        offset, size = location
        if offset < 0 or size <= 0 or offset + size > file_size:
            raise Mp4ReaderError("djmd 第一包偏移或长度无效")
        if size > MAX_METADATA_BYTES:
            raise Mp4ReaderError("djmd 第一包超过允许大小")
        handle.seek(offset)
        packet = handle.read(size)
        if len(packet) != size:
            raise Mp4ReaderError("djmd 第一包数据不完整")
        return packet
    raise MissingDjmdError("未找到 DJI djmd 数据轨")


def read_quicktime_metadata(video_path: Path) -> dict[str, str]:
    """读取 QuickTime ``mdta`` 键值对。

    仅解析 ``moov/meta`` 或 ``moov/udta/meta`` 中的 ``keys`` 与 ``ilst``，
    不读取 ``mdat``。没有该元数据时返回空字典；容器或元数据结构损坏时抛出
    :class:`Mp4ReaderError`，让调用方保留诊断信息。
    """

    with video_path.open("rb") as handle:
        file_size = os.fstat(handle.fileno()).st_size
        top_boxes = _parse_children(handle, 0, file_size)
        return _read_quicktime_metadata(handle, top_boxes)


def _read_quicktime_metadata(
    handle: BinaryIO, top_boxes: list[Box], *, wanted_key: str | None = None
) -> dict[str, str]:
    """复用已解析的 box 树读取 QuickTime 元数据。"""

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
    if wanted_key is not None:
        # 分类只需要色彩标签，不读取无关封面、描述等大 payload。
        keys = {index: key for index, key in keys.items() if key == wanted_key}
    return _read_mdta_values(handle, ilst_box, keys) if keys else {}


def read_dji_color_gamma_label(video_path: Path) -> str | None:
    """读取 DJI ``ColorGammaSxS`` 文本标签；标签不存在时返回 ``None``。"""

    return read_quicktime_metadata(video_path).get(DJI_COLOR_GAMMA_KEY)


def _parse_children(
    handle: BinaryIO, start: int, end: int, *, depth: int = 0, remaining: list[int] | None = None
) -> list[Box]:
    """解析指定范围内的子 box。"""

    if depth > MAX_BOX_DEPTH:
        raise Mp4ReaderError("MP4 容器嵌套层级超过允许范围")
    if remaining is None:
        # 整棵容器树共用计数，避免每个父节点重新获得完整额度。
        remaining = [MAX_BOX_COUNT]
    boxes: list[Box] = []
    offset = start
    while offset + 8 <= end:
        remaining[0] -= 1
        if remaining[0] < 0:
            raise Mp4ReaderError("MP4 box 数量超过允许范围")
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
            children = _parse_children(handle, child_start, box.end, depth=depth + 1, remaining=remaining)
        boxes.append(Box(box.type, box.start, box.size, box.header_size, children))

        if box.size == 0:
            break
        offset = box.end
    return boxes


def _read_box_bytes(handle: BinaryIO, box: Box, offset: int = 0, size: int | None = None) -> bytes:
    """按 payload 相对位置限量读取，先检查边界，避免跨 box 或按恶意计数分配。"""

    payload_size = box.size - box.header_size
    if size is None:
        size = payload_size - offset
    if offset < 0 or size < 0 or offset + size > payload_size:
        raise Mp4ReaderError(f"{box.type} 数据不完整")
    if size > MAX_METADATA_BYTES:
        raise Mp4ReaderError(f"{box.type} 数据超过允许大小")
    handle.seek(box.payload_start + offset)
    payload = handle.read(size)
    if len(payload) != size:
        raise Mp4ReaderError(f"{box.type} 数据不完整")
    return payload


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

    remaining = box.end - box.payload_start
    if remaining < 8:
        raise Mp4ReaderError("meta box 数据过短")
    header = _read_box_bytes(handle, box, 0, 8)
    direct_size = struct.unpack_from(">I", header)[0]
    if 8 <= direct_size <= remaining:
        return box.payload_start
    return box.payload_start + 4


def _read_mdta_keys(handle: BinaryIO, box: Box) -> dict[int, str]:
    """读取 ``keys`` box，建立一基索引到键名的映射。"""

    payload = _read_box_bytes(handle, box)
    if len(payload) < 8:
        raise Mp4ReaderError("QuickTime keys 数据过短")

    entry_count = struct.unpack_from(">I", payload, 4)[0]
    if entry_count > MAX_BOX_COUNT:
        raise Mp4ReaderError("QuickTime keys 数量超过允许范围")
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
    total_bytes = 0
    for item in _parse_children(handle, ilst_box.payload_start, ilst_box.end):
        key_index = int.from_bytes(item.type.encode("latin1"), "big")
        key = keys.get(key_index)
        if key is None:
            continue

        data_box = _find_direct_child_from_range(handle, item.payload_start, item.end, "data")
        if data_box is None:
            continue
        total_bytes += data_box.size - data_box.header_size
        if total_bytes > MAX_METADATA_BYTES:
            raise Mp4ReaderError("QuickTime 文本数据总量超过允许大小")
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

    payload = _read_box_bytes(handle, box)
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


def _read_first_sample_location(handle: BinaryIO, trak: Box) -> tuple[int, int] | None:
    """先识别 djmd 轨道，再读取第一包所需表项；不展开整段素材的 sample 表。"""

    stbl = _find_box([trak], ("trak", "mdia", "minf", "stbl"))
    if stbl is None:
        return None

    stsd = _find_direct_child(stbl, "stsd")
    if stsd is None:
        return None
    sample_entry_types = _read_stsd_sample_entry_types(handle, stsd)
    if "djmd" not in sample_entry_types:
        return None

    stsz = _find_direct_child(stbl, "stsz")
    stz2 = _find_direct_child(stbl, "stz2")
    stsc = _find_direct_child(stbl, "stsc")
    stco = _find_direct_child(stbl, "stco")
    co64 = _find_direct_child(stbl, "co64")

    if stsc is None or (stsz is None and stz2 is None) or (stco is None and co64 is None):
        raise Mp4ReaderError("djmd 轨缺少必要的 sample table")

    first_chunk, sample_description_index = _read_first_stsc_entry(handle, stsc)
    if not 1 <= sample_description_index <= len(sample_entry_types):
        raise Mp4ReaderError("stsc sample description 索引无效")
    if sample_entry_types[sample_description_index - 1] != "djmd":
        raise UnsupportedMp4Error("djmd 轨第一包使用其他 sample description，暂不支持")
    if stsz is not None:
        size = _read_first_stsz_size(handle, stsz)
    else:
        assert stz2 is not None
        size = _read_first_stz2_size(handle, stz2)
    offset_box = stco if stco is not None else co64
    assert offset_box is not None
    offset = _read_chunk_offset(handle, offset_box, first_chunk)
    return offset, size


def _read_stsd_sample_entry_types(handle: BinaryIO, box: Box) -> list[str]:
    """读取 stsd 中的 sample entry type，例如 `djmd`。"""

    payload = _read_box_bytes(handle, box, 0, 8)
    entry_count = struct.unpack_from(">I", payload, 4)[0]
    if entry_count > MAX_BOX_COUNT:
        raise Mp4ReaderError("stsd sample entry 数量超过允许范围")
    offset = 8
    entry_types: list[str] = []
    for _ in range(entry_count):
        entry = _read_box_bytes(handle, box, offset, 8)
        entry_size, entry_type_raw = struct.unpack(">I4s", entry)
        if entry_size < 8 or offset + entry_size > box.size - box.header_size:
            raise Mp4ReaderError("stsd sample entry 大小无效")
        entry_types.append(entry_type_raw.decode("latin1"))
        offset += entry_size
    return entry_types


def _read_first_stsz_size(handle: BinaryIO, box: Box) -> int:
    """读取 stsz 第一包尺寸，固定尺寸只返回标量，不按 sample_count 展开。"""

    payload = _read_box_bytes(handle, box, 0, 12)
    sample_size, sample_count = struct.unpack_from(">II", payload, 4)
    if sample_count == 0:
        raise Mp4ReaderError("stsz sample_count 为 0")
    if sample_size != 0:
        return sample_size
    expected = 12 + sample_count * 4
    if box.size - box.header_size < expected:
        raise Mp4ReaderError("stsz sample size 表不完整")
    return struct.unpack(">I", _read_box_bytes(handle, box, 12, 4))[0]


def _read_first_stz2_size(handle: BinaryIO, box: Box) -> int:
    """读取 compact sample size 表的第一项，并以 box 长度验证声明计数。"""

    payload = _read_box_bytes(handle, box, 0, 12)
    field_size = payload[7]
    sample_count = struct.unpack_from(">I", payload, 8)[0]
    if sample_count == 0:
        raise Mp4ReaderError("stz2 sample_count 为 0")
    if field_size not in (4, 8, 16):
        raise Mp4ReaderError(f"不支持的 stz2 field_size：{field_size}")
    expected = 12 + (sample_count * field_size + 7) // 8
    if box.size - box.header_size < expected:
        raise Mp4ReaderError(f"stz2 {field_size}-bit sample size 表不完整")
    raw = _read_box_bytes(handle, box, 12, 2 if field_size == 16 else 1)
    return raw[0] >> 4 if field_size == 4 else int.from_bytes(raw, "big")


def _read_first_stsc_entry(handle: BinaryIO, box: Box) -> tuple[int, int]:
    """读取第一条 chunk 映射，保留 sample description 索引以验证第一包类型。"""

    payload = _read_box_bytes(handle, box, 0, 20)
    entry_count = struct.unpack_from(">I", payload, 4)[0]
    if entry_count == 0:
        raise Mp4ReaderError("stsc entry_count 为 0")
    if box.size - box.header_size < 8 + entry_count * 12:
        raise Mp4ReaderError("stsc 映射表不完整")
    first_chunk, samples_per_chunk, sample_description_index = struct.unpack_from(">III", payload, 8)
    if first_chunk != 1 or samples_per_chunk <= 0:
        raise Mp4ReaderError("stsc 第一条 chunk 映射无效")
    return first_chunk, sample_description_index


def _read_chunk_offset(handle: BinaryIO, box: Box, chunk_index: int) -> int:
    """读取指定 chunk 偏移，兼容 stco/co64，不分配完整偏移数组。"""

    payload = _read_box_bytes(handle, box, 0, 8)
    entry_count = struct.unpack_from(">I", payload, 4)[0]
    if not 1 <= chunk_index <= entry_count:
        raise Mp4ReaderError("stsc first_chunk 超出 chunk offset 表范围")
    width = 4 if box.type == "stco" else 8
    if box.size - box.header_size < 8 + entry_count * width:
        raise Mp4ReaderError(f"{box.type} offset 表不完整")
    return int.from_bytes(_read_box_bytes(handle, box, 8 + (chunk_index - 1) * width, width), "big")


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

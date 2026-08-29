"""批量脚本：从 MP4 尾部 QuickTime 元数据中提取 ColorGammaSxS 字符串标签。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

TAIL_SIZE = 8192


def extract_strings(data: bytes) -> list[str]:
    """提取连续 ASCII 可打印片段。"""

    strings: list[str] = []
    current: list[int] = []
    for b in data:
        if 32 <= b < 127:
            current.append(b)
        else:
            if len(current) >= 3:
                strings.append(bytes(current).decode("ascii"))
            current = []
    if len(current) >= 3:
        strings.append(bytes(current).decode("ascii"))
    return strings


def gamma_label(path: Path) -> dict:
    """读取文件尾部，返回 ColorGammaSxS 等键对应的字符串值。"""

    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - TAIL_SIZE))
        tail = handle.read()
    strings = extract_strings(tail)
    if "ilst" not in strings:
        return {"file": path.name, "path": str(path), "gamma_label": None, "has_ilst": False, "values": []}
    index = strings.index("ilst")
    values = [s for s in strings[index + 1 :] if s != "data"]
    # 固定键顺序：CameraModel, CameraSerialNumber, ColorGammaSxS, ExposureIndexAsa, ...
    gamma = values[2] if len(values) > 2 else None
    return {"file": path.name, "path": str(path), "gamma_label": gamma, "has_ilst": True, "values": values}


def main(argv: list[str] | None = None) -> int:
    """命令行入口：批量分析目录或文件并输出 JSON。"""

    parser = argparse.ArgumentParser(description="批量提取 ColorGammaSxS 标签")
    parser.add_argument("paths", nargs="+", type=Path, help="目录或 MP4/MOV 文件")
    parser.add_argument("--recursive", action="store_true", help="递归扫描子目录")
    parser.add_argument("--output", type=Path, help="JSON 输出路径，缺省输出到 stdout")
    args = parser.parse_args(argv)

    files: list[Path] = []
    for path in args.paths:
        if path.is_dir():
            pattern = "**/*.mp4" if args.recursive else "*.mp4"
            files.extend(sorted(p for p in path.glob(pattern) if p.is_file()))
        elif path.is_file():
            files.append(path)

    results = [gamma_label(path) for path in files]
    text = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

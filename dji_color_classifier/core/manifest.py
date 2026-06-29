"""Manifest 读写。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from dji_color_classifier import __version__
from dji_color_classifier.core.models import ColorMode, ExecutionRecord, Manifest, PlanAction


def create_manifest(root: Path, operation: str, records: list[ExecutionRecord]) -> Manifest:
    """创建执行 manifest 对象。"""

    return Manifest(
        version=__version__,
        created_at=datetime.now().isoformat(timespec="seconds"),
        root=root,
        operation=operation,
        records=records,
    )


def default_manifest_path(root: Path, created_at: str) -> Path:
    """生成默认 manifest 路径。"""

    safe_time = created_at.replace(":", "").replace("-", "")
    return root / ".dji-color-classifier" / "manifests" / f"{safe_time}.json"


def write_manifest(manifest: Manifest, path: Path | None = None) -> Path:
    """写入 manifest 并返回路径。"""

    target = path or default_manifest_path(manifest.root, manifest.created_at)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def read_manifest(path: Path) -> Manifest:
    """读取 manifest。"""

    data = json.loads(path.read_text(encoding="utf-8"))
    records = [
        ExecutionRecord(
            source=Path(item["source"]),
            target=Path(item["target"]) if item.get("target") else None,
            action=PlanAction(item["action"]),
            mode=ColorMode(item["mode"]),
            success=bool(item["success"]),
            message=str(item.get("message", "")),
            source_size=item.get("source_size"),
            target_size=item.get("target_size"),
        )
        for item in data.get("records", [])
    ]
    return Manifest(
        version=str(data.get("version", "")),
        created_at=str(data.get("created_at", "")),
        root=Path(data.get("root", ".")),
        operation=str(data.get("operation", "")),
        records=records,
    )

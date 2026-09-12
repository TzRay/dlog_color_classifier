"""发布包自检：在临时目录验证资源、桌面依赖和实际扫描/复制链路。"""

from __future__ import annotations

import importlib
import json
import struct
import sys
import tempfile
import time
import traceback
from pathlib import Path

from dji_color_classifier.web_service import ApplicationService


def run_self_check(report_path: Path, html_path: Path) -> int:
    """执行无窗口自检，并写入机器可读取的中文诊断报告。"""

    report: dict = {"success": False, "checks": [], "error": None}
    try:
        if not html_path.is_file() or not html_path.with_name("app.js").is_file():
            raise RuntimeError("发布包缺少 Web 页面或运行脚本")
        report["checks"].append("Web 页面资源完整")

        # 动态后端是打包时最容易遗漏的依赖；只导入，不创建原生窗口。
        importlib.import_module("webview.dom")
        backend = {"win32": "edgechromium", "darwin": "cocoa"}.get(sys.platform)
        if backend:
            importlib.import_module(f"webview.platforms.{backend}")
        report["checks"].append("桌面桥接及平台依赖可加载")

        with tempfile.TemporaryDirectory(prefix="dji-color-check-") as temporary:
            root = Path(temporary)
            source = root / "样例 HLG.MP4"
            source.write_bytes(_metadata_sample())
            (root / "损坏样例.MP4").write_bytes(b"invalid-container")
            service = ApplicationService(max_workers=1)
            try:
                scan = _wait(service, service.start_scan(str(root)))["result"]
                if scan["summary"]["modes"]["rec2100_hlg"] != 1 or scan["summary"]["modes"]["error"] != 1:
                    raise RuntimeError("最小素材识别或异常隔离校验失败")
                result = _wait(service, service.execute_organize({
                    "scan_id": scan["scan_id"], "mode": "copy"
                }))["result"]
                target = root / "hlg" / source.name
                if result["success_count"] != 1 or result["skipped_count"] != 1:
                    raise RuntimeError("复制执行结果校验失败")
                if not target.is_file() or source.read_bytes() != target.read_bytes():
                    raise RuntimeError("复制后的内容校验失败")
                report["checks"].append("真实服务扫描、异常隔离及复制校验通过")
            finally:
                service.close()
        report["success"] = True
    except Exception as exc:
        report["error"] = f"自检失败：{type(exc).__name__}: {exc}"
        # 无控制台发布包必须把完整堆栈留在报告中，便于定位平台专属依赖问题。
        report["traceback"] = traceback.format_exc()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["success"] else 1


def _wait(service: ApplicationService, handle: dict) -> dict:
    """等待小型自检任务结束，防止发布验收无限等待。"""

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        snapshot = service.get_task_status(handle["task_id"])
        if snapshot["state"] == "completed":
            return snapshot
        if snapshot["state"] in {"failed", "cancelled"}:
            raise RuntimeError(snapshot["error"] or snapshot["message"])
        time.sleep(0.01)
    raise TimeoutError("自检任务未在预期时间内完成")


def _metadata_sample() -> bytes:
    """构造只含 QuickTime 色彩标签的小样例，无需携带用户素材。"""

    def box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I4s", len(payload) + 8, kind) + payload

    key = b"com.dji.camera.ColorGammaSxS"
    keys = box(b"keys", b"\0" * 4 + struct.pack(">I", 1) + box(b"mdta", key))
    value = box(b"data", struct.pack(">II", 1, 0) + b"Rec.2100 HLG")
    items = box(b"ilst", box(struct.pack(">I", 1), value))
    return box(b"ftyp", b"isom\0\0\2\0isom") + box(b"moov", box(b"meta", b"\0" * 4 + keys + items))

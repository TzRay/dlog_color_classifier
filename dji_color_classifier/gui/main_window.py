"""主窗口。"""

from __future__ import annotations

from pathlib import Path

from dji_color_classifier.core.executor import execute_plan
from dji_color_classifier.core.manifest import create_manifest, read_manifest, write_manifest
from dji_color_classifier.core.models import ConflictPolicy, PlanItem, ScanResult
from dji_color_classifier.core.planner import build_plan
from dji_color_classifier.core.report import write_report
from dji_color_classifier.core.scanner import scan_directory, summarize_results
from dji_color_classifier.gui.table_model import ResultTableModelMixin
from dji_color_classifier.gui.qt_compat import (
    QAction,
    QAbstractTableModel,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QModelIndex,
    QObject,
    QPlainTextEdit,
    QPushButton,
    QRunnable,
    QTableView,
    QThreadPool,
    QToolBar,
    QVBoxLayout,
    QWidget,
    Signal,
    Slot,
    display_role,
    horizontal_orientation,
    resize_to_contents,
    yes_button,
)


class WorkerSignals(QObject):
    """后台任务信号。"""

    finished = Signal(object)
    failed = Signal(str)


class ScanWorker(QRunnable):
    """后台扫描任务，避免阻塞 UI。"""

    def __init__(self, directory: Path, recursive: bool) -> None:
        super().__init__()
        self.directory = directory
        self.recursive = recursive
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        """执行扫描。"""

        try:
            self.signals.finished.emit(scan_directory(self.directory, recursive=self.recursive))
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class ResultTableModel(ResultTableModelMixin, QAbstractTableModel):
    """扫描和整理计划表格模型。"""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[ScanResult] = []
        self.plan: list[PlanItem] = []

    def data(self, index: QModelIndex, role: int = 0):  # noqa: ANN001,N802
        """返回单元格内容。"""

        if not index.isValid() or role != display_role():
            return None
        result = self.results[index.row()]
        item = self.plan[index.row()] if index.row() < len(self.plan) else None
        values = [
            "失败" if result.error else ("跳过" if item and item.skipped else "就绪"),
            result.path.name,
            result.mode.label,
            str(result.path),
            str(item.target) if item and item.target else "",
            result.evidence.detail,
            result.error or (item.reason if item and item.skipped else ""),
        ]
        return values[index.column()]

    def headerData(self, section: int, orientation, role: int = 0):  # noqa: ANN001,N802
        """返回表头。"""

        if role == display_role() and orientation == horizontal_orientation():
            return self.headers[section]
        return None


class MainWindow(QMainWindow):
    """DJI Color Classifier 主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DJI Color Classifier")
        self.resize(1180, 760)
        self.thread_pool = QThreadPool.globalInstance()
        self.results: list[ScanResult] = []
        self.plan: list[PlanItem] = []
        self.model = ResultTableModel()
        self._build_ui()

    def _build_ui(self) -> None:
        """构建界面控件。"""

        toolbar = QToolBar("主工具栏")
        self.addToolBar(toolbar)
        open_manifest = QAction("打开 manifest 撤销", self)
        open_manifest.triggered.connect(self.undo_from_manifest)
        toolbar.addAction(open_manifest)

        root = QWidget()
        layout = QVBoxLayout(root)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("选择或拖入 DJI 视频目录")
        browse_button = QPushButton("选择目录")
        browse_button.clicked.connect(self.choose_directory)
        self.recursive_check = QCheckBox("递归")
        scan_button = QPushButton("扫描")
        scan_button.clicked.connect(self.start_scan)
        path_row.addWidget(QLabel("目录"))
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse_button)
        path_row.addWidget(self.recursive_check)
        path_row.addWidget(scan_button)
        layout.addLayout(path_row)

        option_row = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["prefix", "move", "copy"])
        self.conflict_combo = QComboBox()
        self.conflict_combo.addItems(["error", "skip", "suffix"])
        self.sidecar_check = QCheckBox("伴随文件")
        self.name_template_edit = QLineEdit()
        self.name_template_edit.setPlaceholderText("{mode}_{original}")
        self.dir_template_edit = QLineEdit()
        self.dir_template_edit.setPlaceholderText("{mode}")
        preview_button = QPushButton("生成计划")
        preview_button.clicked.connect(self.build_current_plan)
        apply_button = QPushButton("执行")
        apply_button.clicked.connect(self.apply_current_plan)
        export_button = QPushButton("导出报告")
        export_button.clicked.connect(self.export_report)
        option_row.addWidget(QLabel("模式"))
        option_row.addWidget(self.mode_combo)
        option_row.addWidget(QLabel("冲突"))
        option_row.addWidget(self.conflict_combo)
        option_row.addWidget(self.sidecar_check)
        option_row.addWidget(QLabel("文件名模板"))
        option_row.addWidget(self.name_template_edit)
        option_row.addWidget(QLabel("目录模板"))
        option_row.addWidget(self.dir_template_edit)
        option_row.addWidget(preview_button)
        option_row.addWidget(apply_button)
        option_row.addWidget(export_button)
        option_row.addStretch(1)
        layout.addLayout(option_row)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.horizontalHeader().setSectionResizeMode(resize_to_contents())
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(150)
        layout.addWidget(self.log)

        self.setCentralWidget(root)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: ANN001,N802
        """接受目录拖拽。"""

        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: ANN001,N802
        """处理目录拖拽。"""

        urls = event.mimeData().urls()
        if urls:
            self.path_edit.setText(urls[0].toLocalFile())

    def choose_directory(self) -> None:
        """选择扫描目录。"""

        directory = QFileDialog.getExistingDirectory(self, "选择 DJI 视频目录")
        if directory:
            self.path_edit.setText(directory)

    def start_scan(self) -> None:
        """启动后台扫描。"""

        directory = Path(self.path_edit.text()).expanduser()
        if not directory.is_dir():
            QMessageBox.warning(self, "目录无效", f"目录不存在：{directory}")
            return
        self._log(f"开始扫描：{directory}")
        worker = ScanWorker(directory, self.recursive_check.isChecked())
        worker.signals.finished.connect(self.on_scan_finished)
        worker.signals.failed.connect(self.on_task_failed)
        self.thread_pool.start(worker)

    @Slot(object)
    def on_scan_finished(self, results: list[ScanResult]) -> None:
        """扫描完成后刷新表格。"""

        self.results = results
        self.plan = []
        self.model.set_rows(results)
        counts = ", ".join(f"{key}: {value}" for key, value in summarize_results(results).items())
        self._log(f"扫描完成：{len(results)} 个文件；{counts}")

    @Slot(str)
    def on_task_failed(self, message: str) -> None:
        """后台任务失败提示。"""

        self._log(f"任务失败：{message}")
        QMessageBox.critical(self, "任务失败", message)

    def build_current_plan(self) -> None:
        """根据当前扫描结果生成整理计划。"""

        if not self.results:
            QMessageBox.information(self, "没有结果", "请先扫描目录。")
            return
        directory = Path(self.path_edit.text()).resolve()
        self.plan = build_plan(
            self.results,
            root=directory,
            mode=self.mode_combo.currentText(),
            conflict_policy=ConflictPolicy(self.conflict_combo.currentText()),
            name_template=self.name_template_edit.text().strip() or None,
            dir_template=self.dir_template_edit.text().strip() or None,
            with_sidecars=self.sidecar_check.isChecked(),
        )
        self.model.set_rows(self.results, self.plan)
        ready = len([item for item in self.plan if not item.skipped and item.target])
        self._log(f"已生成计划：{ready} 个文件待处理")

    def apply_current_plan(self) -> None:
        """执行当前整理计划。"""

        if not self.plan:
            self.build_current_plan()
            if not self.plan:
                return
        ready = len([item for item in self.plan if not item.skipped and item.target])
        answer = QMessageBox.question(self, "确认执行", f"将修改 {ready} 个文件，是否继续？")
        if answer != yes_button():
            return
        records = execute_plan(self.plan, apply=True)
        manifest = create_manifest(Path(self.path_edit.text()).resolve(), self.mode_combo.currentText(), records)
        path = write_manifest(manifest)
        success = len([record for record in records if record.success])
        self._log(f"执行完成：成功记录 {success} 条；manifest：{path}")

    def export_report(self) -> None:
        """导出扫描报告。"""

        if not self.results:
            QMessageBox.information(self, "没有结果", "请先扫描目录。")
            return
        path, selected = QFileDialog.getSaveFileName(self, "导出报告", "dji_color_modes.csv", "CSV (*.csv);;JSON (*.json)")
        if not path:
            return
        fmt = "json" if selected.startswith("JSON") or path.lower().endswith(".json") else "csv"
        write_report(self.results, Path(path), fmt=fmt)
        self._log(f"已导出报告：{path}")

    def undo_from_manifest(self) -> None:
        """从 manifest 读取并预演撤销。"""

        path, _selected = QFileDialog.getOpenFileName(self, "选择 manifest", "", "JSON (*.json)")
        if not path:
            return
        manifest = read_manifest(Path(path))
        from dji_color_classifier.core.executor import build_undo_plan

        self.plan = build_undo_plan(manifest.records)
        self.results = [item.scan_result for item in self.plan]
        self.model.set_rows(self.results, self.plan)
        self._log(f"已载入撤销计划：{len(self.plan)} 条")

    def _log(self, message: str) -> None:
        """写入 GUI 日志。"""

        self.log.appendPlainText(message)

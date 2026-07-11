"""三步式 DJI 色彩模式识别与整理主窗口。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dji_color_classifier.core.executor import build_undo_plan, execute_plan
from dji_color_classifier.core.manifest import create_manifest, read_manifest, write_manifest
from dji_color_classifier.core.models import ConflictPolicy, ExecutionRecord, PlanItem, ScanResult
from dji_color_classifier.core.planner import build_plan
from dji_color_classifier.core.report import write_report
from dji_color_classifier.core.scanner import scan_directory, summarize_results
from dji_color_classifier.gui.table_model import ResultTableModelMixin
from dji_color_classifier.gui.qt_compat import (
    QApplication,
    QAbstractTableModel,
    QAction,
    QCheckBox,
    QColor,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QModelIndex,
    QObject,
    QPlainTextEdit,
    QProgressBar,
    QPropertyAnimation,
    QPushButton,
    QRunnable,
    QStackedWidget,
    QTableView,
    QThreadPool,
    QVBoxLayout,
    QWidget,
    Signal,
    Slot,
    display_role,
    easing_out_cubic,
    foreground_role,
    horizontal_orientation,
    interactive_resize,
    standard_pixmap,
    yes_button,
)


@dataclass(frozen=True)
class ExecutionOutcome:
    """后台执行结果，包含 manifest 写入状态。"""

    token: int
    records: list[ExecutionRecord]
    manifest_path: Path | None
    manifest_error: str | None = None


class TaskSignals(QObject):
    """后台任务统一信号。"""

    finished = Signal(object)
    failed = Signal(object)


class ScanWorker(QRunnable):
    """后台扫描任务。"""

    def __init__(self, token: int, directory: Path, recursive: bool) -> None:
        super().__init__()
        self.token = token
        self.directory = directory
        self.recursive = recursive
        self.signals = TaskSignals()

    @Slot()
    def run(self) -> None:
        """执行扫描，并将任务令牌随结果返回。"""

        try:
            results = scan_directory(self.directory, recursive=self.recursive)
            self.signals.finished.emit((self.token, self.directory, results))
        except Exception as exc:
            self.signals.failed.emit((self.token, str(exc)))


class ExecutionWorker(QRunnable):
    """后台执行整理或撤销计划，避免复制大文件时阻塞窗口。"""

    def __init__(self, token: int, root: Path, operation: str, plan: list[PlanItem]) -> None:
        super().__init__()
        self.token = token
        self.root = root
        self.operation = operation
        self.plan = list(plan)
        self.signals = TaskSignals()

    @Slot()
    def run(self) -> None:
        """执行计划，并尽最大可能保存可撤销记录。"""

        try:
            records = execute_plan(self.plan, apply=True)
        except Exception as exc:
            self.signals.failed.emit((self.token, str(exc)))
            return

        manifest_path: Path | None = None
        manifest_error: str | None = None
        try:
            manifest = create_manifest(self.root, self.operation, records)
            manifest_path = write_manifest(manifest)
        except Exception as exc:
            # 文件操作可能已经完成，因此必须将执行记录交回界面，不能只报“任务失败”。
            manifest_error = str(exc)
        self.signals.finished.emit(ExecutionOutcome(self.token, records, manifest_path, manifest_error))


class ResultTableModel(ResultTableModelMixin, QAbstractTableModel):
    """统一显示扫描结果、整理计划和执行结果。"""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[ScanResult] = []
        self.plan: list[PlanItem] = []
        self.execution_records: list[ExecutionRecord] = []

    def data(self, index: QModelIndex, role: int = 0):  # noqa: ANN001,N802
        """返回单元格文本或状态颜色。"""

        if not index.isValid() or index.row() >= len(self.rows):
            return None
        result = self.rows[index.row()]
        item = self.plan[index.row()] if index.row() < len(self.plan) else None
        record = self.execution_records[index.row()] if index.row() < len(self.execution_records) else None

        if role == foreground_role():
            if record:
                return QColor("#278a49" if record.success else "#b42318")
            if result.error or result.mode.value in {"unknown", "error"} or (item and item.skipped):
                return QColor("#b46a00" if item and item.skipped else "#b42318")
            return None
        if role != display_role():
            return None

        needs_attention = bool(result.error) or result.mode.value in {"unknown", "error"}
        status = "需要检查" if needs_attention else "已识别"
        note = result.error or ("无法从元数据确认色彩模式" if needs_attention else "")
        if item:
            status = "跳过" if item.skipped else "待执行"
            note = item.reason or note
        if record:
            status = "成功" if record.success else "失败"
            note = record.message
        values = [
            status,
            result.path.name,
            result.mode.label,
            str(result.path),
            str(item.target) if item and item.target else "",
            result.evidence.detail,
            note,
        ]
        return values[index.column()]

    def headerData(self, section: int, orientation, role: int = 0):  # noqa: ANN001,N802
        """返回中文表头。"""

        if role == display_role() and orientation == horizontal_orientation():
            return self.headers[section]
        return None


class MainWindow(QMainWindow):
    """面向普通用户的三步式主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DJI 视频色彩模式识别与整理")
        self.resize(1360, 860)
        self.setMinimumSize(1080, 700)

        self.thread_pool = QThreadPool.globalInstance()
        self.results: list[ScanResult] = []
        self.plan: list[PlanItem] = []
        self._scan_token = 0
        self._execution_token = 0
        self._active_workers: dict[int, QRunnable] = {}
        self._busy = False
        self._undo_mode = False
        self._page_animation: QPropertyAnimation | None = None
        self._build_ui()
        self._apply_style()
        self._set_step(1)

    def _build_ui(self) -> None:
        """构建侧栏、步骤条、结果页和整理页。"""

        history_action = QAction("从操作记录撤销…", self)
        history_action.triggered.connect(self.undo_from_manifest)
        self.menuBar().addAction(history_action)

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_sidebar())

        content = QWidget()
        content.setObjectName("content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 24, 28, 20)
        content_layout.setSpacing(16)
        content_layout.addWidget(self._build_stepper())

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_results_page())
        self.pages.addWidget(self._build_organize_page())
        content_layout.addWidget(self.pages, 1)
        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)
        self.setAcceptDrops(True)

    def _build_sidebar(self) -> QWidget:
        """构建任务摘要侧栏。"""

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(288)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(22, 26, 22, 22)
        layout.setSpacing(10)

        title = QLabel("当前任务")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.folder_label = QLabel("尚未选择视频文件夹")
        self.folder_label.setObjectName("folderName")
        self.folder_label.setWordWrap(True)
        layout.addWidget(self.folder_label)
        self.scan_state_label = QLabel("等待开始")
        self.scan_state_label.setObjectName("mutedText")
        layout.addWidget(self.scan_state_label)
        layout.addSpacing(16)

        info_title = QLabel("扫描信息")
        info_title.setObjectName("sectionTitle")
        layout.addWidget(info_title)
        self.fact_labels: dict[str, QLabel] = {}
        for key, label in (("files", "视频文件"), ("modes", "识别结果"), ("scope", "扫描范围")):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            value = QLabel("—")
            value.setObjectName("factValue")
            row.addStretch(1)
            row.addWidget(value)
            self.fact_labels[key] = value
            layout.addLayout(row)
        layout.addSpacing(14)

        self.choose_button = QPushButton("选择视频文件夹")
        self.choose_button.setIcon(self.style().standardIcon(standard_pixmap("SP_DirOpenIcon")))
        self.choose_button.clicked.connect(self.choose_directory)
        layout.addWidget(self.choose_button)
        self.rescan_button = QPushButton("重新扫描")
        self.rescan_button.setIcon(self.style().standardIcon(standard_pixmap("SP_BrowserReload")))
        self.rescan_button.clicked.connect(self.start_scan)
        layout.addWidget(self.rescan_button)
        layout.addStretch(1)

        self.advanced_button = QPushButton("高级设置")
        self.advanced_button.clicked.connect(self._toggle_advanced)
        layout.addWidget(self.advanced_button)
        self.log_button = QPushButton("查看运行日志")
        self.log_button.clicked.connect(self._toggle_log)
        layout.addWidget(self.log_button)
        return sidebar

    def _build_stepper(self) -> QWidget:
        """构建顶部三步流程指示器。"""

        stepper = QFrame()
        stepper.setObjectName("stepper")
        layout = QHBoxLayout(stepper)
        layout.setContentsMargins(18, 4, 18, 4)
        self.step_labels: list[QLabel] = []
        for index, (title, hint) in enumerate(
            (("选择视频", "选择 DJI 视频文件夹"), ("检查识别结果", "确认识别是否正确"), ("整理文件", "确认后立即执行")),
            start=1,
        ):
            label = QLabel(f"{index}   {title}\n      {hint}")
            label.setObjectName("workflowStep")
            self.step_labels.append(label)
            layout.addWidget(label, 1)
        return stepper

    def _build_results_page(self) -> QWidget:
        """构建扫描与识别结果页。"""

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("选择或拖入 DJI 视频目录")
        self.path_edit.textChanged.connect(self._on_path_changed)
        path_row.addWidget(self.path_edit, 1)
        self.recursive_check = QCheckBox("包含子文件夹")
        self.recursive_check.setChecked(True)
        path_row.addWidget(self.recursive_check)
        self.scan_button = QPushButton("开始扫描")
        self.scan_button.setObjectName("primaryButton")
        self.scan_button.clicked.connect(self.start_scan)
        path_row.addWidget(self.scan_button)
        layout.addLayout(path_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        layout.addWidget(self._build_summary())

        filter_row = QHBoxLayout()
        self.status_filter = QComboBox()
        self.status_filter.addItem("全部状态", "all")
        self.status_filter.addItem("已识别", "ready")
        self.status_filter.addItem("需要检查", "attention")
        self.status_filter.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self.status_filter)
        self.mode_filter = QComboBox()
        self.mode_filter.addItem("全部色彩模式", "all")
        for text, value in (("D-Log", "dlog"), ("D-Log2", "dlog2"), ("普通709", "rec709"), ("无法确认", "unknown")):
            self.mode_filter.addItem(text, value)
        self.mode_filter.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self.mode_filter)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索文件名或路径")
        self.search_edit.textChanged.connect(self._apply_filters)
        filter_row.addWidget(self.search_edit, 1)
        self.export_button = QPushButton("导出识别报告")
        self.export_button.clicked.connect(self.export_report)
        filter_row.addWidget(self.export_button)
        layout.addLayout(filter_row)

        self.model = ResultTableModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows if hasattr(QTableView, "SelectionBehavior") else QTableView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(interactive_resize())
        self.table.setColumnWidth(0, 92)
        self.table.setColumnWidth(1, 260)
        self.table.setColumnWidth(2, 110)
        self.table.setColumnWidth(3, 300)
        self.table.setColumnWidth(4, 300)
        self.table.setColumnWidth(5, 220)
        layout.addWidget(self.table, 1)

        self.empty_label = QLabel("选择一个包含 DJI 视频的文件夹，然后开始扫描。")
        self.empty_label.setObjectName("emptyState")
        self.empty_label.setAlignment(self._align_center())
        layout.addWidget(self.empty_label)

        action_row = QHBoxLayout()
        self.selection_label = QLabel("尚无识别结果")
        action_row.addWidget(self.selection_label)
        action_row.addStretch(1)
        self.next_button = QPushButton("下一步：选择整理方式")
        self.next_button.setObjectName("primaryButton")
        self.next_button.clicked.connect(self.show_organize_page)
        self.next_button.setEnabled(False)
        action_row.addWidget(self.next_button)
        layout.addLayout(action_row)

        self.advanced_group = self._build_advanced_group()
        self.advanced_group.setVisible(False)
        layout.addWidget(self.advanced_group)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(130)
        self.log.setVisible(False)
        layout.addWidget(self.log)
        return page

    def _build_summary(self) -> QWidget:
        """构建识别数量概览。"""

        summary = QFrame()
        summary.setObjectName("summary")
        layout = QGridLayout(summary)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setHorizontalSpacing(12)
        heading = QLabel("识别结果概览")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading, 0, 0, 2, 1)
        self.metric_labels: dict[str, QLabel] = {}
        for column, (key, title, color) in enumerate(
            (("D-Log", "D-Log", "#e62d2d"), ("D-Log2", "D-Log2", "#e66b13"), ("普通709", "普通 709", "#2469d8"), ("无法确认", "无法确认", "#666a70")),
            start=1,
        ):
            title_label = QLabel(title)
            title_label.setStyleSheet(f"color: {color}; font-weight: 700;")
            value_label = QLabel("0")
            value_label.setObjectName("metricValue")
            self.metric_labels[key] = value_label
            layout.addWidget(title_label, 0, column)
            layout.addWidget(value_label, 1, column)
        return summary

    def _build_advanced_group(self) -> QGroupBox:
        """构建不干扰主流程的高级设置。"""

        group = QGroupBox("高级设置")
        layout = QGridLayout(group)
        self.conflict_combo = QComboBox()
        self.conflict_combo.addItem("发现同名文件时停止", "error")
        self.conflict_combo.addItem("跳过同名文件", "skip")
        self.conflict_combo.addItem("自动追加序号", "suffix")
        self.name_template_edit = QLineEdit()
        self.name_template_edit.setPlaceholderText("例如：{mode}_{original}")
        self.dir_template_edit = QLineEdit()
        self.dir_template_edit.setPlaceholderText("例如：{mode}")
        self.sidecar_check = QCheckBox("同时整理同名字幕、缩略图等伴随文件")
        layout.addWidget(QLabel("冲突处理"), 0, 0)
        layout.addWidget(self.conflict_combo, 0, 1)
        layout.addWidget(QLabel("文件名模板"), 1, 0)
        layout.addWidget(self.name_template_edit, 1, 1)
        layout.addWidget(QLabel("目录模板"), 2, 0)
        layout.addWidget(self.dir_template_edit, 2, 1)
        layout.addWidget(self.sidecar_check, 3, 0, 1, 2)
        for control in (self.conflict_combo, self.name_template_edit, self.dir_template_edit, self.sidecar_check):
            signal = control.currentIndexChanged if isinstance(control, QComboBox) else (
                control.textChanged if isinstance(control, QLineEdit) else control.stateChanged
            )
            signal.connect(self._invalidate_plan)
        return group

    def _build_organize_page(self) -> QWidget:
        """构建第三步整理方式与直接执行页。"""

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        title = QLabel("选择整理方式")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        hint = QLabel("建议首次使用“复制到分类文件夹”，它会保留全部原始文件。")
        hint.setObjectName("mutedText")
        layout.addWidget(hint)

        options = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("添加文件名前缀", "prefix")
        self.mode_combo.addItem("移动到分类文件夹", "move")
        self.mode_combo.addItem("复制到分类文件夹（推荐）", "copy")
        self.mode_combo.setCurrentIndex(2)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        options.addWidget(QLabel("整理方式"))
        options.addWidget(self.mode_combo, 1)
        layout.addLayout(options)

        self.action_hint = QLabel()
        self.action_hint.setObjectName("actionBanner")
        self.action_hint.setWordWrap(True)
        layout.addWidget(self.action_hint)
        layout.addStretch(1)

        actions = QHBoxLayout()
        self.back_button = QPushButton("返回检查结果")
        self.back_button.clicked.connect(self.show_results_page)
        actions.addWidget(self.back_button)
        actions.addStretch(1)
        self.apply_button = QPushButton("开始复制整理")
        self.apply_button.setObjectName("primaryButton")
        self.apply_button.clicked.connect(self.apply_current_plan)
        self.apply_button.setEnabled(True)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions)
        return page

    @staticmethod
    def _align_center():  # noqa: ANN205
        """兼容 Qt5/Qt6 返回居中对齐枚举。"""

        from dji_color_classifier.gui.qt_compat import Qt

        return getattr(getattr(Qt, "AlignmentFlag", Qt), "AlignCenter")

    def _apply_style(self) -> None:
        """应用与选定原型一致的浅色桌面工具主题。"""

        self.setStyleSheet(
            """
            QWidget { font-family: "Microsoft YaHei UI"; font-size: 14px; }
            QMainWindow, QWidget#content { background: #f6f7f8; color: #202124; }
            QMenuBar { background: #ffffff; border-bottom: 1px solid #e4e6e9; padding: 4px; }
            QFrame#sidebar { background: #ffffff; border-right: 1px solid #e4e6e9; }
            QLabel#sectionTitle { font-size: 17px; font-weight: 700; }
            QLabel#folderName { font-size: 18px; font-weight: 700; }
            QLabel#mutedText { color: #6f7379; font-size: 14px; }
            QLabel#factValue { font-weight: 600; }
            QLabel#workflowStep { padding: 11px 14px; color: #6f7379; font-size: 15px; line-height: 1.5; }
            QLabel#workflowStep[active="true"] { color: #e62d2d; font-size: 17px; font-weight: 700; }
            QFrame#summary { background: #ffffff; border: 1px solid #e4e6e9; border-radius: 8px; }
            QLabel#metricValue { font-size: 32px; font-weight: 700; }
            QLabel#pageTitle { font-size: 28px; font-weight: 700; }
            QLabel#emptyState { color: #6f7379; font-size: 16px; padding: 22px; }
            QLabel#infoBanner { background: #fff8f1; border: 1px solid #f3d7b9; border-radius: 7px; padding: 12px; }
            QLabel#actionBanner { background: #ffffff; border: 1px solid #e4e6e9; border-left: 4px solid #e62d2d; border-radius: 8px; padding: 20px; font-size: 17px; font-weight: 600; }
            QPushButton, QComboBox, QLineEdit { min-height: 42px; border: 1px solid #d9dce0; border-radius: 7px; padding: 0 13px; background: #ffffff; font-size: 15px; }
            QPushButton:hover { background: #f1f2f4; }
            QPushButton:disabled { color: #9a9da2; background: #eceef0; }
            QPushButton#primaryButton { min-height: 46px; color: #ffffff; background: #e62d2d; border-color: #e62d2d; font-size: 16px; font-weight: 700; }
            QPushButton#primaryButton:hover { background: #c92222; }
            QPushButton#primaryButton:disabled { color: #ffffff; background: #e9a1a1; border-color: #e9a1a1; }
            QTableView { background: #ffffff; alternate-background-color: #fbfbfc; border: 1px solid #e4e6e9; border-radius: 8px; gridline-color: #eceef0; selection-background-color: #fff0f0; selection-color: #202124; }
            QHeaderView::section { background: #fafbfc; border: 0; border-bottom: 1px solid #e4e6e9; padding: 10px; font-weight: 600; }
            QGroupBox { border: 1px solid #e4e6e9; border-radius: 8px; margin-top: 10px; padding-top: 12px; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; font-weight: 700; }
            QProgressBar { max-height: 4px; border: 0; background: #eceef0; }
            QProgressBar::chunk { background: #e62d2d; }
            """
        )

    def _set_step(self, step: int) -> None:
        """更新步骤条视觉状态。"""

        for index, label in enumerate(self.step_labels, start=1):
            label.setProperty("active", index == step)
            label.style().unpolish(label)
            label.style().polish(label)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        """统一切换任务运行状态，避免重复提交。"""

        self._busy = busy
        self.progress.setVisible(busy)
        self.scan_button.setEnabled(not busy)
        self.rescan_button.setEnabled(not busy and bool(self.path_edit.text().strip()))
        self.choose_button.setEnabled(not busy)
        self.apply_button.setEnabled(not busy and bool(self.plan))
        self.back_button.setEnabled(not busy)
        if message:
            self.scan_state_label.setText(message)

    def _toggle_advanced(self) -> None:
        """展开或折叠高级设置。"""

        visible = not self.advanced_group.isVisible()
        self.advanced_group.setVisible(visible)
        self.advanced_button.setText("收起高级设置" if visible else "高级设置")

    def _toggle_log(self) -> None:
        """展开或折叠运行日志。"""

        visible = not self.log.isVisible()
        self.log.setVisible(visible)
        self.log_button.setText("隐藏运行日志" if visible else "查看运行日志")

    def _on_path_changed(self) -> None:
        """目录变化时清除旧结果，避免误用已失效计划。"""

        if self._busy:
            return
        self.rescan_button.setEnabled(bool(self.path_edit.text().strip()))
        if self.results:
            self.results = []
            self.plan = []
            self.model.clear()
            self._update_summary([])
            self.next_button.setEnabled(False)
            self.empty_label.setVisible(True)
            self.selection_label.setText("目录已更改，请重新扫描")

    def _on_mode_changed(self) -> None:
        """整理方式变化时使旧内部计划失效。"""

        self._invalidate_plan()
        mode = self.mode_combo.currentData()
        templates_enabled = mode == "prefix"
        self.name_template_edit.setEnabled(templates_enabled)
        self.dir_template_edit.setEnabled(not templates_enabled)

    def _update_organize_action(self) -> None:
        """根据当前整理方式更新醒目的操作说明和主按钮。"""

        mode = str(self.mode_combo.currentData())
        descriptions = {
            "prefix": ("为 D-Log / D-Log2 文件添加前缀", "开始添加前缀"),
            "move": ("将视频移动到对应色彩模式的分类文件夹", "开始移动整理"),
            "copy": ("复制到分类文件夹，并保留全部原始视频（推荐）", "开始复制整理"),
        }
        description, button_text = descriptions[mode]
        self.action_hint.setText(f"即将执行：{description}\n点击主按钮后会显示最终文件数量，确认后立即开始。")
        self.apply_button.setText(button_text)
        self.apply_button.setEnabled(bool(self.results) and not self._busy)

    def _invalidate_plan(self) -> None:
        """设置变化时丢弃内部计划，执行前会自动重新计算。"""

        self.plan = []
        if hasattr(self, "action_hint"):
            self._update_organize_action()

    def _transition_to(self, page_index: int, step: int) -> None:
        """使用短暂淡入强调页面变化，同时避免拖慢操作。"""

        self.pages.setCurrentIndex(page_index)
        self._set_step(step)
        page = self.pages.currentWidget()
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(240)
        animation.setStartValue(0.25)
        animation.setEndValue(1.0)
        animation.setEasingCurve(easing_out_cubic())
        animation.finished.connect(lambda: self._finish_page_transition(page))
        self._page_animation = animation
        animation.start()

    def _finish_page_transition(self, page: QWidget) -> None:
        """移除临时特效并强制重绘，避免部分 Qt/显卡组合留下黑色缓存。"""

        page.setGraphicsEffect(None)
        page.update()
        if self.centralWidget() is not None:
            self.centralWidget().update()

    def dragEnterEvent(self, event) -> None:  # noqa: ANN001,N802
        """只接受包含本地目录的拖拽。"""

        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if urls and Path(urls[0].toLocalFile()).is_dir():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: ANN001,N802
        """使用拖入的第一个目录并开始扫描。"""

        urls = event.mimeData().urls()
        if urls:
            self.path_edit.setText(urls[0].toLocalFile())
            self.start_scan()

    def choose_directory(self) -> None:
        """选择扫描目录并立即进入扫描流程。"""

        directory = QFileDialog.getExistingDirectory(self, "选择 DJI 视频目录")
        if directory:
            self.path_edit.setText(directory)
            self.start_scan()

    def start_scan(self) -> None:
        """启动带令牌的后台扫描，旧任务结果不会覆盖新状态。"""

        if self._busy:
            return
        directory = Path(self.path_edit.text()).expanduser()
        if not directory.is_dir():
            QMessageBox.warning(self, "目录无效", f"目录不存在：{directory}")
            return
        self._scan_token += 1
        token = self._scan_token
        self._undo_mode = False
        self._set_busy(True, "正在扫描…")
        self._log(f"开始扫描：{directory}")
        worker = ScanWorker(token, directory.resolve(), self.recursive_check.isChecked())
        worker.signals.finished.connect(self.on_scan_finished)
        worker.signals.failed.connect(self.on_task_failed)
        self._active_workers[token] = worker
        self.thread_pool.start(worker)

    @Slot(object)
    def on_scan_finished(self, payload) -> None:  # noqa: ANN001
        """只接受当前令牌对应的扫描结果。"""

        token, directory, results = payload
        self._active_workers.pop(token, None)
        if token != self._scan_token:
            self._log(f"忽略已过期的扫描结果：任务 {token}")
            return
        self.results = list(results)
        self.plan = []
        self.model.set_results(self.results)
        self._update_summary(self.results)
        counts = "，".join(f"{key} {value}" for key, value in summarize_results(self.results).items()) or "没有视频"
        self.folder_label.setText(directory.name or str(directory))
        self.fact_labels["files"].setText(f"{len(self.results)} 个")
        self.fact_labels["modes"].setText(counts)
        self.fact_labels["scope"].setText("包含子文件夹" if self.recursive_check.isChecked() else "仅当前文件夹")
        self.selection_label.setText(f"已识别 {len(self.results)} 个视频")
        self.scan_state_label.setText("扫描完成")
        self.empty_label.setVisible(not self.results)
        self.next_button.setEnabled(bool(self.results))
        self.export_button.setEnabled(bool(self.results))
        self._set_busy(False)
        self._set_step(2 if self.results else 1)
        self._log(f"扫描完成：{len(self.results)} 个文件；{counts}")

    @Slot(object)
    def on_task_failed(self, payload) -> None:  # noqa: ANN001
        """显示后台任务错误；过期任务只记日志。"""

        token, message = payload
        self._active_workers.pop(token, None)
        current = token in {self._scan_token, self._execution_token}
        if not current:
            self._log(f"忽略已过期任务错误：{message}")
            return
        self._set_busy(False, "任务失败")
        self._log(f"任务失败：{message}")
        QMessageBox.critical(self, "任务失败", message)

    def _update_summary(self, results: list[ScanResult]) -> None:
        """更新四类识别结果数量。"""

        counts = summarize_results(results)
        for key, label in self.metric_labels.items():
            label.setText(str(counts.get(key, 0)))

    def _apply_filters(self) -> None:
        """按状态、色彩模式和搜索词刷新扫描结果表格。"""

        if self.pages.currentIndex() != 0 or not self.results:
            return
        mode = self.mode_filter.currentData()
        status = self.status_filter.currentData()
        term = self.search_edit.text().strip().lower()
        filtered: list[ScanResult] = []
        for result in self.results:
            if mode != "all" and result.mode.value != mode:
                continue
            needs_attention = bool(result.error) or result.mode.value in {"unknown", "error"}
            if status == "ready" and needs_attention:
                continue
            if status == "attention" and not needs_attention:
                continue
            if term and term not in f"{result.path.name} {result.path} {result.mode.label}".lower():
                continue
            filtered.append(result)
        self.model.set_results(filtered)
        self.selection_label.setText(f"当前显示 {len(filtered)} / {len(self.results)} 个视频")

    def show_organize_page(self) -> None:
        """进入第三步，选择方式后即可直接整理。"""

        if not self.results:
            QMessageBox.information(self, "没有结果", "请先扫描目录。")
            return
        self._undo_mode = False
        self._transition_to(1, 3)
        self._update_organize_action()

    def show_results_page(self) -> None:
        """返回识别结果页。"""

        self._transition_to(0, 2 if self.results else 1)
        self._apply_filters()

    def build_current_plan(self) -> bool:
        """执行前在内部校验设置并计算文件计划。"""

        if not self.results:
            QMessageBox.information(self, "没有结果", "请先扫描目录。")
            return False
        directory = Path(self.path_edit.text()).resolve()
        try:
            self.plan = build_plan(
                self.results,
                root=directory,
                mode=str(self.mode_combo.currentData()),
                conflict_policy=ConflictPolicy(str(self.conflict_combo.currentData())),
                name_template=self.name_template_edit.text().strip() or None,
                dir_template=self.dir_template_edit.text().strip() or None,
                with_sidecars=self.sidecar_check.isChecked(),
            )
        except (KeyError, ValueError, OSError) as exc:
            self.plan = []
            self.apply_button.setEnabled(False)
            self.action_hint.setText(f"设置有误：{exc}")
            QMessageBox.warning(self, "无法开始整理", str(exc))
            return False
        self.model.set_plan(self.plan)
        ready = sum(not item.skipped and item.target is not None for item in self.plan)
        skipped = len(self.plan) - ready
        self._log(f"执行前检查完成：{ready} 个待处理，{skipped} 个跳过")
        return True

    def apply_current_plan(self) -> None:
        """自动计算计划，最终确认后在后台执行。"""

        if self._busy:
            return
        if not self._undo_mode:
            if not self.build_current_plan():
                return
        elif not self.plan:
            QMessageBox.information(self, "没有可撤销内容", "当前操作记录中没有可撤销的文件。")
            return
        ready = sum(not item.skipped and item.target is not None for item in self.plan)
        verb = "撤销" if self._undo_mode else str(self.mode_combo.currentText()).replace("（推荐）", "")
        answer = QMessageBox.question(
            self,
            "最终确认",
            f"整理方式：{verb}\n将处理 {ready} 个文件。\n\n确认后立即开始，是否继续？",
        )
        if answer != yes_button():
            return
        self._execution_token += 1
        token = self._execution_token
        operation = "undo" if self._undo_mode else str(self.mode_combo.currentData())
        root = Path(self.path_edit.text()).resolve()
        worker = ExecutionWorker(token, root, operation, self.plan)
        worker.signals.finished.connect(self.on_execution_finished)
        worker.signals.failed.connect(self.on_task_failed)
        self._active_workers[token] = worker
        self._set_busy(True, "正在执行文件操作…")
        self.thread_pool.start(worker)

    @Slot(object)
    def on_execution_finished(self, outcome: ExecutionOutcome) -> None:
        """刷新执行结果，并立即使旧计划失效。"""

        self._active_workers.pop(outcome.token, None)
        if outcome.token != self._execution_token:
            self._log(f"忽略已过期的执行结果：任务 {outcome.token}")
            return
        self.model.set_execution_records(outcome.records)
        success = sum(record.success for record in outcome.records)
        failed = len(outcome.records) - success
        self.plan = []
        self.apply_button.setEnabled(False)
        self._set_busy(False, "执行完成")
        manifest_text = str(outcome.manifest_path) if outcome.manifest_path else "未能写入"
        self.action_hint.setText(f"执行完成：成功 {success}，失败 {failed}。\n操作记录：{manifest_text}")
        self.apply_button.setEnabled(False)
        self._log(f"执行完成：成功 {success}，失败 {failed}；操作记录：{manifest_text}")
        if outcome.manifest_error:
            QMessageBox.warning(
                self,
                "操作已完成，但撤销记录写入失败",
                f"文件操作已经执行，请不要重复点击。撤销记录写入失败：{outcome.manifest_error}",
            )
        elif failed:
            QMessageBox.warning(self, "部分文件处理失败", f"成功 {success} 个，失败 {failed} 个。请查看表格说明。")
        else:
            QMessageBox.information(self, "执行完成", f"已成功处理 {success} 个记录。\n撤销记录：{manifest_text}")

    def export_report(self) -> None:
        """导出当前完整扫描报告，而不是只导出筛选结果。"""

        if not self.results:
            QMessageBox.information(self, "没有结果", "请先扫描目录。")
            return
        path, selected = QFileDialog.getSaveFileName(self, "导出识别报告", "dji_color_modes.csv", "CSV (*.csv);;JSON (*.json)")
        if not path:
            return
        fmt = "json" if selected.startswith("JSON") or path.lower().endswith(".json") else "csv"
        try:
            write_report(self.results, Path(path), fmt=fmt)
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self._log(f"已导出报告：{path}")

    def undo_from_manifest(self) -> None:
        """读取操作记录并进入明确的撤销模式。"""

        if self._busy:
            return
        path, _selected = QFileDialog.getOpenFileName(self, "选择操作记录", "", "JSON (*.json)")
        if not path:
            return
        try:
            manifest = read_manifest(Path(path))
            plan = build_undo_plan(manifest.records)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.critical(self, "无法读取操作记录", str(exc))
            return
        self._undo_mode = True
        self.plan = plan
        self.results = [item.scan_result for item in plan]
        self.model.set_plan(plan)
        self._transition_to(1, 3)
        self.action_hint.setText(f"已载入操作记录，共 {len(plan)} 个文件可撤销。点击按钮后确认并立即执行。")
        self.apply_button.setText(f"撤销这次整理（{len(plan)} 个文件）")
        self.apply_button.setEnabled(bool(plan))
        self._log(f"已载入撤销计划：{len(plan)} 条")

    def _log(self, message: str) -> None:
        """写入中文运行日志。"""

        self.log.appendPlainText(message)


def create_window_for_smoke_test() -> MainWindow:
    """创建不进入事件循环的窗口，供 CI 冒烟测试复用。"""

    if QApplication.instance() is None:
        raise RuntimeError("必须先创建 QApplication")
    return MainWindow()

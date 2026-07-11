"""Qt 绑定兼容层。

发布版本首选 PySide6；开发环境仍可使用 PyQt5 运行相同界面。
"""

from __future__ import annotations

import os

QT_BINDING = ""


def _load_pyside6() -> bool:
    """尝试加载 PySide6。"""

    global QT_BINDING
    global QAbstractTableModel, QEasingCurve, QModelIndex, QObject, QPropertyAnimation, QRunnable, Qt, QThreadPool, Signal, Slot
    global QAction, QColor, QFont
    global QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGraphicsOpacityEffect, QGridLayout, QGroupBox
    global QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit
    global QProgressBar, QPushButton, QRadioButton, QSizePolicy, QStackedWidget, QStyle, QTableView
    global QToolButton, QVBoxLayout, QWidget

    from PySide6.QtCore import (
        QAbstractTableModel,
        QEasingCurve,
        QModelIndex,
        QObject,
        QPropertyAnimation,
        QRunnable,
        Qt,
        QThreadPool,
        Signal,
        Slot,
    )
    from PySide6.QtGui import QAction, QColor, QFont
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QFrame,
        QGraphicsOpacityEffect,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QRadioButton,
        QSizePolicy,
        QStackedWidget,
        QStyle,
        QTableView,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )

    QT_BINDING = "PySide6"
    return True


def _load_pyqt5() -> bool:
    """尝试加载 PyQt5。"""

    global QT_BINDING
    global QAbstractTableModel, QEasingCurve, QModelIndex, QObject, QPropertyAnimation, QRunnable, Qt, QThreadPool, Signal, Slot
    global QAction, QColor, QFont
    global QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGraphicsOpacityEffect, QGridLayout, QGroupBox
    global QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit
    global QProgressBar, QPushButton, QRadioButton, QSizePolicy, QStackedWidget, QStyle, QTableView
    global QToolButton, QVBoxLayout, QWidget

    from PyQt5.QtCore import (
        QAbstractTableModel,
        QEasingCurve,
        QModelIndex,
        QObject,
        QPropertyAnimation,
        QRunnable,
        Qt,
        QThreadPool,
        pyqtSignal,
        pyqtSlot,
    )
    from PyQt5.QtGui import QColor, QFont
    from PyQt5.QtWidgets import (
        QAction,
        QApplication,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QFrame,
        QGraphicsOpacityEffect,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QRadioButton,
        QSizePolicy,
        QStackedWidget,
        QStyle,
        QTableView,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )

    Signal = pyqtSignal
    Slot = pyqtSlot
    QT_BINDING = "PyQt5"
    return True


def _load_qt_binding() -> None:
    """按用户偏好依次加载 Qt 绑定。"""

    preferred = os.environ.get("DJI_COLOR_QT_BINDING", "PySide6").lower()
    loaders = [_load_pyside6, _load_pyqt5] if preferred != "pyqt5" else [_load_pyqt5, _load_pyside6]
    last_error: Exception | None = None
    for loader in loaders:
        try:
            loader()
            return
        except (ImportError, OSError) as exc:
            last_error = exc
    raise RuntimeError("未安装可用的 PySide6 或 PyQt5，无法启动 GUI。请安装 GUI 依赖或使用 CLI。") from last_error


_load_qt_binding()


def _enum(container, group: str, name: str):  # noqa: ANN001
    """兼容 Qt5 与 Qt6 的分组枚举。"""

    return getattr(getattr(container, group, container), name)


def display_role() -> int:
    """返回显示角色。"""

    return _enum(Qt, "ItemDataRole", "DisplayRole")


def foreground_role() -> int:
    """返回前景色角色。"""

    return _enum(Qt, "ItemDataRole", "ForegroundRole")


def horizontal_orientation() -> int:
    """返回水平方向枚举。"""

    return _enum(Qt, "Orientation", "Horizontal")


def resize_to_contents() -> int:
    """返回按内容调整列宽的枚举。"""

    return _enum(QHeaderView, "ResizeMode", "ResizeToContents")


def interactive_resize() -> int:
    """返回用户可调整列宽的枚举。"""

    return _enum(QHeaderView, "ResizeMode", "Interactive")


def yes_button() -> int:
    """返回确认按钮枚举。"""

    return _enum(QMessageBox, "StandardButton", "Yes")


def standard_pixmap(name: str):  # noqa: ANN201
    """按名称返回 Qt 标准图标枚举。"""

    return _enum(QStyle, "StandardPixmap", name)


def easing_out_cubic():  # noqa: ANN201
    """返回适合页面淡入的缓出曲线。"""

    return _enum(QEasingCurve, "Type", "OutCubic")

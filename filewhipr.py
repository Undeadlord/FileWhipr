#!/usr/bin/env python3
"""act_on_files.py — FileWhipr: recursive copy/move of files by extension, PySide6 GUI.

Launched from a Windows Explorer folder context-menu entry.
The selected folder path is passed as the first command-line argument.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
_LOG_PATH = Path(__file__).resolve().parent / "filewhipr_debug.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (tid=%(thread)d) %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_PATH, mode="w", encoding="utf-8"),
    ],
)
log = logging.getLogger("filewhipr")
log.info("=== FileWhipr starting; debug log at %s ===", _LOG_PATH)


__version__ = "0.2.0"
__release_date__ = "06/22/2026"
_GITHUB_URL = "https://github.com/Undeadlord/FileWhipr"


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
_SETTINGS_PATH = Path(__file__).resolve().parent / "filewhipr_settings.json"

_DEFAULT_EXTENSIONS = [
    ".csv", ".docx", ".jpg", ".mp3", ".mp4",
    ".pdf", ".png", ".stl", ".txt", ".xlsx",
    ".zip",
]

_DEFAULT_SETTINGS: dict = {
    "extensions": _DEFAULT_EXTENSIONS,
    "default_action": "copy",
    "default_recursive": True,
    "close_on_any_result": False,
    "theme": "system",
}


def load_settings() -> dict:
    if _SETTINGS_PATH.exists():
        try:
            with open(_SETTINGS_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
            merged = dict(_DEFAULT_SETTINGS)
            merged.update(data)
            return merged
        except Exception as exc:
            log.warning("Could not load settings: %s", exc)
    return dict(_DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
    except Exception as exc:
        log.warning("Could not save settings: %s", exc)


# --------------------------------------------------------------------------- #
# Core file logic (no Qt dependency)
# --------------------------------------------------------------------------- #
def _human_bytes(n: int) -> str:
    step = 1024.0
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < step or unit == "TB":
            if unit in ("B", "KB"):
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= step
    return f"{n} B"


def unique_destination(dest_dir: Path, name: str) -> Path:
    """Return a non-colliding path under dest_dir, auto-renaming if needed."""
    candidate = dest_dir / name
    if not candidate.exists():
        return candidate
    stem = Path(name).stem
    suffix = Path(name).suffix
    counter = 1
    while True:
        candidate = dest_dir / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


# --------------------------------------------------------------------------- #
# Scan worker — finds matching files off the GUI thread
# --------------------------------------------------------------------------- #
class ScanWorker(QObject):
    """Walks source folders and emits live scan progress, respecting a cancel token.

    Signals:
        scanning(str, int): (current filename, matches so far) throttled to ~20 fps.
        finished(object): emitted with the final sorted list[Path] when done or cancelled.
    """

    scanning = Signal(str, int)  # (current filename, matches found so far)
    finished = Signal(object)    # list[Path]  (may be partial if cancelled)

    _EMIT_INTERVAL = 0.05  # seconds between scanning signals (~20 fps)

    def __init__(
        self,
        sources: list[Path],
        ext_label: str,
        recursive: bool,
        cancel: threading.Event,
    ) -> None:
        super().__init__()
        self._sources = sources
        self._ext_label = ext_label
        self._recursive = recursive
        self._cancel = cancel

    def run(self) -> None:
        no_ext = self._ext_label == "(no extension)"
        target = self._ext_label.lower()
        seen: set[Path] = set()
        matches: list[Path] = []
        last_emit = 0.0

        for source in self._sources:
            if self._cancel.is_set():
                break
            iterator = source.rglob("*") if self._recursive else source.glob("*")
            for path in iterator:
                if self._cancel.is_set():
                    break
                try:
                    if not path.is_file():
                        continue
                    resolved = path.resolve()
                    if resolved in seen:
                        continue
                    suffix = path.suffix.lower()
                    if (no_ext and not suffix) or (not no_ext and suffix == target):
                        seen.add(resolved)
                        matches.append(path)
                    now = time.monotonic()
                    if now - last_emit >= self._EMIT_INTERVAL:
                        self.scanning.emit(path.name, len(matches))
                        last_emit = now
                except OSError as exc:
                    log.warning("Skipping unreadable path %s: %s", path, exc)

        self.finished.emit(sorted(matches))


# --------------------------------------------------------------------------- #
# File worker — runs the copy/move on a background thread
# --------------------------------------------------------------------------- #
class FileWorker(QObject):
    """Performs the copy/move operation off the GUI thread, respecting a cancel token.

    Signals:
        file_started(str, int, int): (filename, 1-based index, total) before each file.
        progress(int, int): (bytes_done, bytes_total) after each chunk.
        finished(int, int, int): (succeeded, failed, bytes_transferred) when done/cancelled.
        error(str): emitted on a fatal, run-ending error.
    """

    file_started = Signal(str, int, int)   # (filename, idx_1based, total_files)
    progress = Signal(int, int)            # (bytes_done, bytes_total)
    finished = Signal(int, int, int)       # (succeeded, failed, bytes_transferred)
    error = Signal(str)

    _CHUNK = 1024 * 1024  # 1 MiB read/write block

    def __init__(
        self,
        files: list[Path],
        dest_dir: Path,
        move: bool,
        cancel: threading.Event,
    ) -> None:
        super().__init__()
        self._files = files
        self._dest_dir = dest_dir
        self._move = move
        self._cancel = cancel

    def run(self) -> None:
        succeeded = 0
        failed = 0
        total_files = len(self._files)

        try:
            self._dest_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.error.emit(f"Cannot create destination folder:\n{exc}")
            return

        sizes: dict[Path, int] = {}
        for src in self._files:
            try:
                sizes[src] = src.stat().st_size
            except OSError:
                sizes[src] = 0
        total_bytes = sum(sizes.values())
        bytes_done = 0

        if total_bytes == 0:
            self.progress.emit(0, 0)

        for i, src in enumerate(self._files):
            if self._cancel.is_set():
                break
            self.file_started.emit(src.name, i + 1, total_files)
            try:
                dest = unique_destination(self._dest_dir, src.name)
                with open(src, "rb") as fsrc, open(dest, "wb") as fdst:
                    while True:
                        block = fsrc.read(self._CHUNK)
                        if not block:
                            break
                        fdst.write(block)
                        bytes_done += len(block)
                        self.progress.emit(bytes_done, total_bytes)
                shutil.copystat(src, dest)
                if self._move:
                    src.unlink()
                succeeded += 1
            except (OSError, shutil.Error) as exc:
                failed += 1
                log.error("Failed on %s: %s", src, exc)
                bytes_done += sizes.get(src, 0)
                self.progress.emit(bytes_done, total_bytes)

        if not self._cancel.is_set():
            self.progress.emit(total_bytes, total_bytes)
        self.finished.emit(succeeded, failed, bytes_done)


# --------------------------------------------------------------------------- #
# Settings dialog
# --------------------------------------------------------------------------- #
class SettingsDialog(QDialog):
    """Editable settings: dropdown extension list, default action, default recursion."""

    def __init__(self, settings: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(380)
        self._settings = dict(settings)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)

        root.addWidget(QLabel("Extensions shown in dropdown:"))

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        for ext in self._settings.get("extensions", []):
            self._list.addItem(ext)
        root.addWidget(self._list)

        edit_row = QHBoxLayout()
        self._ext_input = QLineEdit()
        self._ext_input.setPlaceholderText("e.g. .stl")
        self._ext_input.returnPressed.connect(self._add_ext)
        edit_row.addWidget(self._ext_input, stretch=1)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_ext)
        edit_row.addWidget(add_btn)

        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_ext)
        edit_row.addWidget(remove_btn)

        up_btn = QPushButton("▲")
        up_btn.setFixedWidth(32)
        up_btn.clicked.connect(self._move_up)
        edit_row.addWidget(up_btn)

        down_btn = QPushButton("▼")
        down_btn.setFixedWidth(32)
        down_btn.clicked.connect(self._move_down)
        edit_row.addWidget(down_btn)

        root.addLayout(edit_row)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("divider")
        root.addWidget(line)

        root.addWidget(QLabel("Default action:"))
        action_row = QHBoxLayout()
        self._copy_radio = QRadioButton("Copy")
        self._move_radio = QRadioButton("Move")
        grp = QButtonGroup(self)
        grp.addButton(self._copy_radio)
        grp.addButton(self._move_radio)
        if self._settings.get("default_action") == "move":
            self._move_radio.setChecked(True)
        else:
            self._copy_radio.setChecked(True)
        action_row.addWidget(self._copy_radio)
        action_row.addWidget(self._move_radio)
        action_row.addStretch(1)
        root.addLayout(action_row)

        self._recursive_check = QCheckBox("Include subfolders by default")
        self._recursive_check.setChecked(self._settings.get("default_recursive", True))
        root.addWidget(self._recursive_check)

        self._close_on_any_check = QCheckBox("Close window on all outcomes (not just success)")
        self._close_on_any_check.setChecked(self._settings.get("close_on_any_result", False))
        root.addWidget(self._close_on_any_check)

        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)
        line2.setObjectName("divider")
        root.addWidget(line2)

        root.addWidget(QLabel("Theme:"))
        theme_row = QHBoxLayout()
        self._theme_system = QRadioButton("System")
        self._theme_light = QRadioButton("Light")
        self._theme_dark = QRadioButton("Dark")
        theme_grp = QButtonGroup(self)
        theme_grp.addButton(self._theme_system)
        theme_grp.addButton(self._theme_light)
        theme_grp.addButton(self._theme_dark)
        _theme_map = {"system": self._theme_system, "light": self._theme_light, "dark": self._theme_dark}
        _theme_map.get(self._settings.get("theme", "system"), self._theme_system).setChecked(True)
        theme_row.addWidget(self._theme_system)
        theme_row.addWidget(self._theme_light)
        theme_row.addWidget(self._theme_dark)
        theme_row.addStretch(1)
        root.addLayout(theme_row)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save")
        save_btn.setObjectName("primary")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        root.addLayout(btn_row)

    def _add_ext(self) -> None:
        text = self._ext_input.text().strip().lower()
        if not text:
            return
        if not text.startswith("."):
            text = "." + text
        existing = [self._list.item(i).text() for i in range(self._list.count())]
        if text not in existing:
            self._list.addItem(text)
        self._ext_input.clear()

    def _remove_ext(self) -> None:
        row = self._list.currentRow()
        if row >= 0:
            self._list.takeItem(row)

    def _move_up(self) -> None:
        row = self._list.currentRow()
        if row > 0:
            item = self._list.takeItem(row)
            self._list.insertItem(row - 1, item)
            self._list.setCurrentRow(row - 1)

    def _move_down(self) -> None:
        row = self._list.currentRow()
        if 0 <= row < self._list.count() - 1:
            item = self._list.takeItem(row)
            self._list.insertItem(row + 1, item)
            self._list.setCurrentRow(row + 1)

    def _save(self) -> None:
        exts = [self._list.item(i).text() for i in range(self._list.count())]
        self._settings["extensions"] = exts
        self._settings["default_action"] = "move" if self._move_radio.isChecked() else "copy"
        self._settings["default_recursive"] = self._recursive_check.isChecked()
        self._settings["close_on_any_result"] = self._close_on_any_check.isChecked()
        if self._theme_dark.isChecked():
            self._settings["theme"] = "dark"
        elif self._theme_light.isChecked():
            self._settings["theme"] = "light"
        else:
            self._settings["theme"] = "system"
        self.accept()

    def result_settings(self) -> dict:
        return self._settings


# --------------------------------------------------------------------------- #
# About dialog
# --------------------------------------------------------------------------- #
class AboutDialog(QDialog):
    """Modal 'About' window: version, description, project link, and license.

    Purely informational — performs no network calls or update checks. The
    GitHub link opens in the user's default browser via the label's
    open-external-links behavior.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About FileWhipr")
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)

        # Title + tagline grouped tightly so they read as one unit.
        head_box = QVBoxLayout()
        head_box.setSpacing(2)
        title = QLabel(f"FileWhipr  v{__version__}  ({__release_date__})")
        title.setObjectName("aboutTitle")
        head_box.addWidget(title)

        tagline = QLabel("Whipping files into shape …")
        tagline.setObjectName("aboutTagline")
        head_box.addWidget(tagline)
        root.addLayout(head_box)

        link_color = "#fb923c" if _resolve_dark() else "#c2410c"
        link = QLabel(
            f'<a href="{_GITHUB_URL}" style="color: {link_color}; '
            f'text-decoration: none;">{_GITHUB_URL}</a>'
        )
        link.setObjectName("aboutBody")
        link.setOpenExternalLinks(True)
        link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        root.addWidget(link)

        issues_url = f"{_GITHUB_URL}/issues"
        issues = QLabel(
            f'<a href="{issues_url}" style="color: {link_color}; '
            f'text-decoration: none;">Report an issue</a>'
        )
        issues.setObjectName("aboutBody")
        issues.setOpenExternalLinks(True)
        issues.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        root.addWidget(issues)

        license_lbl = QLabel(
            "Released under the MIT License.\n"
            "Uses PySide6, licensed under the LGPL v3."
        )
        license_lbl.setObjectName("aboutMuted")
        license_lbl.setWordWrap(True)
        root.addWidget(license_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("primary")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class FileWhiprWindow(QWidget):
    """Compact window for choosing an extension, an action, and a destination."""

    _CUSTOM_KEY = "__custom__"

    def __init__(self, sources: list[Path]) -> None:
        super().__init__()
        self._sources = sources
        self._settings = load_settings()
        self._thread: QThread | None = None
        self._worker: FileWorker | None = None
        self._scan_thread: QThread | None = None
        self._scan_worker: ScanWorker | None = None
        self._dest_dir_pending: Path | None = None
        self._cancel_event = threading.Event()
        self._op_start_time: float = 0.0

        self.setWindowTitle("FileWhipr")
        self.setMinimumWidth(500)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        _ico = Path(__file__).resolve().parent / "FileWhipr.ico"
        if _ico.exists():
            self.setWindowIcon(QIcon(str(_ico)))
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        # Header: source label + gear button
        header_row = QHBoxLayout()
        if len(self._sources) == 1:
            src_text = f"Source:  {self._sources[0]}"
        else:
            src_text = f"Sources ({len(self._sources)}):\n" + "\n".join(f"  {s}" for s in self._sources)
        src_lbl = QLabel(src_text)
        src_lbl.setObjectName("sourceLabel")
        src_lbl.setWordWrap(True)
        header_row.addWidget(src_lbl, stretch=1)

        self.about_btn = QPushButton("ⓘ")
        self.about_btn.setObjectName("gearBtn")
        self.about_btn.setFixedSize(28, 28)
        self.about_btn.setToolTip("About FileWhipr")
        self.about_btn.clicked.connect(self._show_about)
        header_row.addWidget(self.about_btn, alignment=Qt.AlignmentFlag.AlignTop)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("gearBtn")
        self.settings_btn.setFixedSize(28, 28)
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.clicked.connect(self._open_settings)
        header_row.addWidget(self.settings_btn, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header_row)

        # Extension row: dropdown + custom text box
        ext_row = QHBoxLayout()
        ext_row.addWidget(QLabel("Extension"))

        self.ext_combo = QComboBox()
        self.ext_combo.setMinimumWidth(150)
        ext_row.addWidget(self.ext_combo)

        self.custom_ext_edit = QLineEdit()
        self.custom_ext_edit.setPlaceholderText("e.g. .stl")
        self.custom_ext_edit.setFixedWidth(90)
        ext_row.addWidget(self.custom_ext_edit)
        ext_row.addStretch(1)
        root.addLayout(ext_row)

        # Action row
        action_row = QHBoxLayout()
        action_row.addWidget(QLabel("Action"))
        self.copy_radio = QRadioButton("Copy")
        self.move_radio = QRadioButton("Move")
        self._action_group = QButtonGroup(self)
        self._action_group.addButton(self.copy_radio)
        self._action_group.addButton(self.move_radio)
        if self._settings.get("default_action") == "move":
            self.move_radio.setChecked(True)
        else:
            self.copy_radio.setChecked(True)
        action_row.addWidget(self.copy_radio)
        action_row.addWidget(self.move_radio)
        action_row.addStretch(1)
        root.addLayout(action_row)

        # Recursive checkbox
        self.recursive_check = QCheckBox("Include subfolders (recursive)")
        self.recursive_check.setChecked(self._settings.get("default_recursive", True))
        root.addWidget(self.recursive_check)

        # Destination row
        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel("Destination"))
        self.dest_edit = QLineEdit()
        self.dest_edit.setPlaceholderText("Choose a folder…")
        dest_row.addWidget(self.dest_edit, stretch=1)
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.clicked.connect(self._browse)
        dest_row.addWidget(self.browse_btn)
        root.addLayout(dest_row)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("divider")
        root.addWidget(line)

        # scan_file_lbl: fast-changing current file during scan; cleared after scan
        self.scan_file_lbl = QLabel("")
        self.scan_file_lbl.setObjectName("scanFileLabel")
        root.addWidget(self.scan_file_lbl)

        # status_lbl: match count during scan / file+count during copy / result
        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("statusLabel")
        root.addWidget(self.status_lbl)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        # Button row — cancel_btn label flips between "Close" and "Cancel"
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.cancel_btn = QPushButton("Close")
        self.cancel_btn.clicked.connect(self._on_cancel_or_close)
        self.ok_btn = QPushButton("Scan and Copy")
        self.ok_btn.setObjectName("primary")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.ok_btn)
        root.addLayout(btn_row)

        self._rebuild_ext_combo()
        self.ext_combo.currentIndexChanged.connect(self._on_ext_changed)
        self.copy_radio.toggled.connect(self._update_ok_label)
        self.move_radio.toggled.connect(self._update_ok_label)
        self._on_ext_changed()
        self._update_ok_label()

    def _rebuild_ext_combo(self, preserve_selection: str | None = None) -> None:
        self.ext_combo.blockSignals(True)
        self.ext_combo.clear()
        self.ext_combo.addItem("Custom", userData=self._CUSTOM_KEY)
        for ext in self._settings.get("extensions", []):
            self.ext_combo.addItem(ext, userData=ext)
        idx = 0
        if preserve_selection and preserve_selection != self._CUSTOM_KEY:
            for i in range(self.ext_combo.count()):
                if self.ext_combo.itemData(i) == preserve_selection:
                    idx = i
                    break
        self.ext_combo.setCurrentIndex(idx)
        self.ext_combo.blockSignals(False)

    def _on_ext_changed(self) -> None:
        is_custom = self.ext_combo.currentData() == self._CUSTOM_KEY
        self.custom_ext_edit.setEnabled(is_custom)

    def _update_ok_label(self) -> None:
        verb = "Move" if self.move_radio.isChecked() else "Copy"
        self.ok_btn.setText(f"Scan and {verb}")

    def _current_ext(self) -> str | None:
        data = self.ext_combo.currentData()
        if data == self._CUSTOM_KEY:
            text = self.custom_ext_edit.text().strip().lower()
            if not text:
                return None
            return text if text.startswith(".") else "." + text
        return data if isinstance(data, str) else None

    def _open_settings(self) -> None:
        prev = self.ext_combo.currentData()
        dlg = SettingsDialog(self._settings, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._settings = dlg.result_settings()
            save_settings(self._settings)
            _apply_theme(self._settings)
            self._rebuild_ext_combo(preserve_selection=prev)
            self._on_ext_changed()

    def _show_about(self) -> None:
        AboutDialog(parent=self).exec()

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select destination folder")
        if chosen:
            self.dest_edit.setText(chosen)

    def _on_ok(self) -> None:
        ext = self._current_ext()
        if ext is None:
            if self.ext_combo.currentData() == self._CUSTOM_KEY:
                self._warn("Enter a custom extension in the text box (e.g. .stl).")
            else:
                self._warn("Pick an extension first.")
            return

        dest_text = self.dest_edit.text().strip()
        if not dest_text:
            self._warn("Choose a destination folder.")
            return
        dest_dir = Path(dest_text)

        if self.move_radio.isChecked():
            try:
                dest_resolved = dest_dir.resolve()
                if any(dest_resolved == s.resolve() for s in self._sources):
                    self._warn("Destination matches a source folder. Pick a different folder.")
                    return
            except OSError:
                pass

        self._start_scan(ext, dest_dir)

    # ----- Cancel / close ---------------------------------------------------- #

    def _on_cancel_or_close(self) -> None:
        if self._scan_thread is not None or self._thread is not None:
            self._on_cancel()
        else:
            self.close()

    def _on_cancel(self) -> None:
        if self._cancel_event.is_set():
            return  # already cancelling — ignore double-click
        self._cancel_event.set()
        self.cancel_btn.setEnabled(False)
        self.scan_file_lbl.setText("")
        self.status_lbl.setText("Cancelling…")

    # ----- Scan phase -------------------------------------------------------- #

    def _start_scan(self, ext: str, dest_dir: Path) -> None:
        self._dest_dir_pending = dest_dir
        self._cancel_event.clear()
        self._set_running(True)
        self.scan_file_lbl.setText("")
        self.status_lbl.setText("Scanning…")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)

        self._scan_thread = QThread(self)
        self._scan_worker = ScanWorker(
            self._sources, ext, self.recursive_check.isChecked(), self._cancel_event
        )
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.scanning.connect(self._on_scan_progress)
        self._scan_worker.finished.connect(
            self._on_scan_finished, Qt.ConnectionType.QueuedConnection
        )
        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_thread.finished.connect(self._scan_worker.deleteLater)
        self._scan_thread.finished.connect(self._scan_thread.deleteLater)
        self._scan_thread.finished.connect(self._clear_scan_refs)
        self._scan_thread.start()

    def _on_scan_progress(self, filename: str, count: int) -> None:
        self.scan_file_lbl.setText(f"Scanning: {filename}")
        if count > 0:
            self.status_lbl.setText(f"{count} found")

    def _on_scan_finished(self, files: list) -> None:
        self.scan_file_lbl.setText("")

        if self._cancel_event.is_set():
            self._reset_progress()
            self._set_running(False)
            self.status_lbl.setText("")
            QMessageBox.information(self, "FileWhipr", "Scan cancelled.")
            if self._settings.get("close_on_any_result", False):
                self.close()
            return

        if not files:
            self._reset_progress()
            self._set_running(False)
            self.status_lbl.setText("")
            self._warn("No matching files found.")
            if self._settings.get("close_on_any_result", False):
                self.close()
            return

        verb = "Moving" if self.move_radio.isChecked() else "Copying"
        self.status_lbl.setText(f"Found {len(files)} file(s). Starting {verb}…")
        self.progress.setRange(0, 0)  # stays indeterminate until first byte arrives

        self._op_start_time = time.monotonic()
        assert self._dest_dir_pending is not None
        self._thread = QThread(self)
        self._worker = FileWorker(
            files, self._dest_dir_pending, move=self.move_radio.isChecked(),
            cancel=self._cancel_event,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.file_started.connect(self._on_file_started)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._thread.start()

    def _clear_scan_refs(self) -> None:
        self._scan_thread = None
        self._scan_worker = None

    # ----- Copy/move phase --------------------------------------------------- #

    def _on_file_started(self, filename: str, idx: int, total: int) -> None:
        verb = "Moving" if self.move_radio.isChecked() else "Copying"
        self.status_lbl.setText(f"{verb}: {filename}  ·  {idx} of {total}")

    def _on_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress.setRange(0, 1)
            self.progress.setValue(1)
            self.progress.setTextVisible(True)
            self.progress.setFormat("done")
            return
        if self.progress.maximum() != total:
            self.progress.setRange(0, total)
            self.progress.setTextVisible(True)
        self.progress.setValue(done)
        self.progress.setFormat(f"{_human_bytes(done)} / {_human_bytes(total)}")

    def _on_finished(self, succeeded: int, failed: int, bytes_done: int) -> None:
        self._teardown_thread()
        elapsed = time.monotonic() - self._op_start_time
        verb_past = "moved" if self.move_radio.isChecked() else "copied"

        if self._cancel_event.is_set():
            self._reset_progress()
            self._set_running(False)
            lines = ["Operation cancelled."]
            if succeeded:
                lines.append(f"{succeeded} file(s) {verb_past} before stopping.")
            if bytes_done > 0:
                lines.append(f"{_human_bytes(bytes_done)} transferred.")
            self.status_lbl.setText("Cancelled")
            QMessageBox.information(self, "FileWhipr", "\n".join(lines))
            if self._settings.get("close_on_any_result", False):
                self.close()
            return

        self._set_running(False)
        lines = [f"{succeeded} file(s) {verb_past}"]
        if bytes_done > 0:
            lines.append(f"{_human_bytes(bytes_done)} transferred")
        lines.append(f"Completed in {elapsed:.1f}s")
        if failed:
            lines.append(f"\n{failed} file(s) failed — see log for details.")
        summary = "\n".join(lines)
        self.scan_file_lbl.setText("")
        self.status_lbl.setText(f"{succeeded} file(s) {verb_past}")
        QMessageBox.information(self, "FileWhipr — Done", summary)
        self.close()

    def _on_error(self, message: str) -> None:
        self._teardown_thread()
        self._reset_progress()
        self._set_running(False)
        self.scan_file_lbl.setText("")
        self.status_lbl.setText("")
        self._warn(message)

    # ----- Thread cleanup ---------------------------------------------------- #

    def _teardown_scan_thread(self) -> None:
        if self._scan_thread is not None:
            self._scan_thread.quit()
            self._scan_thread.wait()
            self._scan_thread = None
            self._scan_worker = None

    def _teardown_thread(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
            self._thread = None
            self._worker = None

    def _reset_progress(self) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)

    def _set_running(self, running: bool) -> None:
        for w in (
            self.ext_combo,
            self.copy_radio,
            self.move_radio,
            self.recursive_check,
            self.dest_edit,
            self.browse_btn,
            self.ok_btn,
            self.settings_btn,
            self.about_btn,
        ):
            w.setEnabled(not running)
        self.custom_ext_edit.setEnabled(
            not running and self.ext_combo.currentData() == self._CUSTOM_KEY
        )
        self.cancel_btn.setText("Cancel" if running else "Close")
        self.cancel_btn.setEnabled(True)  # always accessible

    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, "FileWhipr", message)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._cancel_event.set()  # stop workers quickly on window close
        self._teardown_scan_thread()
        self._teardown_thread()
        super().closeEvent(event)


# --------------------------------------------------------------------------- #
# Styling
# --------------------------------------------------------------------------- #
def _is_system_dark() -> bool:
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return value == 0
    except Exception:
        return False


def _make_stylesheet(dark: bool) -> str:
    if dark:
        bg           = "#1c1c20"
        surface      = "#28282e"
        border       = "#3d3d45"
        text         = "#e9e9ef"
        muted        = "#9898a6"
        hover_bg     = "#323238"
        dis_bg       = "#28282e"
        dis_text     = "#55555f"
        dis_bdr      = "#3d3d45"
        list_alt     = "#242428"
        progress_bg  = "#3d3d45"
    else:
        bg           = "#f7f7f9"
        surface      = "#ffffff"
        border       = "#d6d8e0"
        text         = "#1c1d22"
        muted        = "#52545e"
        hover_bg     = "#eef0f6"
        dis_bg       = "#ececf0"
        dis_text     = "#a7a9b4"
        dis_bdr      = "#e0e1e8"
        list_alt     = "#f7f7f9"
        progress_bg  = "#ececf0"

    accent    = "#fb923c"
    accent_hv = "#f97316"

    return f"""
* {{
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 14px;
    color: {text};
}}
QWidget {{ background: {bg}; }}
#sourceLabel {{ color: {muted}; font-size: 13px; }}
#scanFileLabel {{ color: {muted}; font-size: 13px; min-height: 16px; }}
#statusLabel   {{ color: {muted}; font-size: 13px; min-height: 16px; }}
#aboutTitle {{ font-size: 18px; font-weight: 600; color: {text}; }}
#aboutTagline {{ color: {muted}; font-size: 13px; font-style: italic; }}
#aboutBody  {{ color: {text}; font-size: 13px; }}
#aboutMuted {{ color: {text}; font-size: 12px; }}
QComboBox, QLineEdit {{
    background: {surface};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 6px 9px;
    color: {text};
}}
QComboBox:focus, QLineEdit:focus {{ border: 1px solid {accent}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QPushButton {{
    background: {surface};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 7px 16px;
    color: {text};
}}
QPushButton:hover {{ background: {hover_bg}; }}
QPushButton#primary {{
    background: {accent};
    border: 1px solid {accent};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: {accent_hv}; border-color: {accent_hv}; }}
QPushButton:disabled {{ background: {dis_bg}; color: {dis_text}; border-color: {dis_bdr}; }}
QPushButton#gearBtn {{
    background: transparent;
    border: none;
    border-radius: 6px;
    font-size: 17px;
    padding: 0px;
    color: {muted};
}}
QPushButton#gearBtn:hover {{ background: {hover_bg}; color: {text}; }}
QPushButton#gearBtn:disabled {{ background: transparent; color: {dis_text}; }}
QRadioButton, QCheckBox {{ spacing: 7px; }}
QRadioButton::indicator, QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {border};
    background: {surface};
}}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator:hover, QCheckBox::indicator:hover {{ border: 1px solid {accent}; }}
QRadioButton::indicator:checked {{
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fp:0.5, fp:0.5,
        stop:0 #ffffff, stop:0.40 #ffffff,
        stop:0.45 {accent}, stop:1 {accent});
    border: 1px solid {accent};
}}
QCheckBox::indicator:checked {{ background: {accent}; border: 1px solid {accent}; image: url(none); }}
QProgressBar {{
    background: {progress_bg};
    border: none;
    border-radius: 7px;
    height: 14px;
    text-align: center;
    font-size: 12px;
    color: {text};
}}
QProgressBar::chunk {{ background: {accent}; border-radius: 7px; }}
#divider {{ color: {border}; max-height: 1px; }}
QListWidget {{
    background: {surface};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 4px;
}}
QListWidget::item {{ padding: 4px 6px; border-radius: 4px; color: {text}; }}
QListWidget::item:selected {{ background: {accent}; color: #ffffff; }}
QListWidget::item:alternate {{ background: {list_alt}; }}
"""


def _resolve_dark(settings: dict | None = None) -> bool:
    """Return True if the effective theme is dark, resolving 'system' if needed."""
    if settings is None:
        settings = load_settings()
    theme = settings.get("theme", "system")
    return _is_system_dark() if theme == "system" else theme == "dark"


def _apply_theme(settings: dict) -> None:
    dark = _resolve_dark(settings)
    app = QApplication.instance()
    if isinstance(app, QApplication):
        app.setStyleSheet(_make_stylesheet(dark))


def main() -> int:
    """Entry point. Folder may come from argv[1]; otherwise prompt for one."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)
    _apply_theme(load_settings())

    if len(sys.argv) >= 2:
        sources = [Path(a) for a in sys.argv[1:]]
    else:
        chosen = QFileDialog.getExistingDirectory(None, "Select a folder to act on")
        if not chosen:
            return 0
        sources = [Path(chosen)]

    bad = [s for s in sources if not s.is_dir()]
    if bad:
        QMessageBox.critical(None, "FileWhipr", "Not a folder:\n" + "\n".join(str(s) for s in bad))
        return 1

    window = FileWhiprWindow(sources)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

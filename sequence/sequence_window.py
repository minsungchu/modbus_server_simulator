"""시퀀스 에디터와 실행 컨트롤을 담은 전용 창."""

from __future__ import annotations

import json
import logging
import os
from typing import Callable

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from sequence.sequence_editor import SequenceEditor
from sequence.sequence_engine import SequenceEngine
from sequence.sequence_model import Node, NodeType, Sequence

logger = logging.getLogger("ModbusServerSim")

DEFAULT_FILE = "modbus_sequence.json"


class SequenceWindow(QMainWindow):
    """노드 그래프 편집 + 실행을 제공하는 창."""

    def __init__(
        self,
        read_fn: Callable[[str, int], int],
        write_fn: Callable[[str, int, int], None],
        parent: QWidget | None = None,
    ) -> None:
        """창을 초기화한다.

        Args:
            read_fn: (reg_type, addr) -> int 레지스터 읽기 콜백(메인 윈도우 제공).
            write_fn: (reg_type, addr, value) -> None 쓰기 콜백(메인 윈도우 제공).
            parent: 부모 위젯.
        """
        super().__init__(parent)
        self.setWindowTitle("시퀀스 시뮬레이션")
        self.resize(1000, 640)
        self._read = read_fn
        self._write = write_fn
        self._engine: SequenceEngine | None = None
        self._path = DEFAULT_FILE

        self.sequence = self._load_or_default()
        self.editor = SequenceEditor(self.sequence)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setFixedHeight(140)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(self._build_control_bar())
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.log_view)
        self.setCentralWidget(central)
        self._build_toolbar()
        self._set_running_ui(False)

    def _build_toolbar(self) -> None:
        """파일 조작용 툴바(New/Open/Save)."""
        bar = QToolBar()
        self.addToolBar(bar)
        bar.addAction("New", self._new)
        bar.addAction("Open", self._open)
        bar.addAction("Save", self._save)
        bar.addAction("Save As", self._save_as)

    def _build_control_bar(self) -> QHBoxLayout:
        """실행 제어 버튼(▶ 실행 / ⏭ 스텝 / ■ 정지) 바를 만든다."""
        bar = QHBoxLayout()
        self.run_button = QPushButton("▶ 실행")
        self.run_button.setObjectName("seq_run_button")
        self.run_button.clicked.connect(self._run)
        self.step_button = QPushButton("⏭ 스텝")
        self.step_button.setObjectName("seq_step_button")
        self.step_button.clicked.connect(self._step)
        self.stop_button = QPushButton("■ 정지")
        self.stop_button.setObjectName("seq_stop_button")
        self.stop_button.clicked.connect(self._stop)
        self.run_status = QLabel("정지됨")
        self.run_status.setStyleSheet("font-weight: bold; padding-left: 8px;")
        for btn in (self.run_button, self.step_button, self.stop_button):
            btn.setMinimumWidth(90)
            bar.addWidget(btn)
        bar.addWidget(self.run_status)
        bar.addStretch(1)
        return bar

    def _set_running_ui(self, running: bool) -> None:
        """실행 상태에 따라 버튼 활성화/상태 라벨을 갱신한다."""
        self.run_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.run_status.setText("실행 중…" if running else "정지됨")
        self.run_status.setStyleSheet(
            "font-weight: bold; padding-left: 8px; color: %s;" % ("#22c55e" if running else "#94a3b8")
        )

    def _load_or_default(self) -> Sequence:
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    return Sequence.from_dict(json.load(f))
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                logger.error(f"시퀀스 로드 실패, 기본값 사용: {exc}")
        return Sequence(nodes=[Node(id="n1", type=NodeType.START, x=40, y=60)], edges=[])

    def _reload_editor(self) -> None:
        self.editor.scene.sequence = self.sequence
        self.editor.sequence = self.sequence
        self.editor.scene.rebuild()

    def _new(self) -> None:
        self.sequence = Sequence(nodes=[Node(id="n1", type=NodeType.START, x=40, y=60)], edges=[])
        self._reload_editor()

    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "시퀀스 열기", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.sequence = Sequence.from_dict(json.load(f))
            self._path = path
            self._reload_editor()
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "열기 실패", str(exc))

    def _save(self) -> None:
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self.sequence.to_dict(), f, indent=2, ensure_ascii=False)
            self.log(f"저장됨: {self._path}")
        except OSError as exc:
            QMessageBox.warning(self, "저장 실패", str(exc))

    def _save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "다른 이름으로 저장", DEFAULT_FILE, "JSON (*.json)")
        if path:
            self._path = path
            self._save()

    def _run(self) -> None:
        errors = self.sequence.validate()
        if errors:
            QMessageBox.warning(self, "검증 실패", "\n".join(errors))
            return
        self._stop()
        self._engine = SequenceEngine(self.sequence, self._read, self._write)
        self._engine.node_activated.connect(self.editor.highlight)
        self._engine.step_logged.connect(self.log)
        self._engine.finished.connect(self._on_finished)
        self.log("=== 실행 시작 ===")
        if self._engine.start():
            self._set_running_ui(True)

    def _step(self) -> None:
        if self._engine is None or not self._engine.running:
            self._run()
        else:
            self._engine.step()

    def _stop(self) -> None:
        if self._engine is not None and self._engine.running:
            self._engine.stop()

    def _on_finished(self, reason: str) -> None:
        self.log(f"=== 종료: {reason} ===")
        self._set_running_ui(False)

    def log(self, text: str) -> None:
        """로그뷰에 한 줄 추가한다."""
        self.log_view.appendPlainText(text)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 시그니처)
        self._stop()
        super().closeEvent(event)

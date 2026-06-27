"""시퀀스 에디터와 실행 컨트롤을 담은 전용 창."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from sequence.sequence_editor import SequenceEditor
from sequence.sequence_engine import SequenceEngine
from sequence.sequence_model import Node, NodeType, Sequence

logger = logging.getLogger("ModbusServerSim")

DEFAULT_FILE = "modbus_sequence.json"
SETS_DIR = "sequences"        # 시퀀스 세트(.json) 들을 모아두는 폴더
SETS_CONFIG = "_config.json"  # 기본 세트 등 설정 보관 파일
# 프로그램에 기본 탑재되는(배포에 포함) 다이어그램. 신규 설치 시 사용자 폴더로 시드된다.
# 이 이름의 다이어그램은 이름 변경/삭제가 불가능하다(복사·내보내기는 허용).
BUNDLED_DEFAULT_NAME = "DEFAULT"
BUNDLED_DEFAULT_FILE = "default_sequence.json"  # resources/ 안의 파일명


def bundled_default_path() -> str:
    """배포에 포함된 기본 시퀀스 파일 경로(소스 실행/PyInstaller 모두 지원)."""
    base = getattr(sys, "_MEIPASS",
                   os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))
    return os.path.join(base, "resources", BUNDLED_DEFAULT_FILE)


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
        self.resize(1000, 990)  # 메인 창 높이(약 990)와 비슷하게 맞춘다
        self._read = read_fn
        self._write = write_fn
        self._engine: SequenceEngine | None = None
        self._path = DEFAULT_FILE

        self.sequence = self._load_or_default()
        self.editor = SequenceEditor(self.sequence)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setObjectName("seq_log_view")

        # 에디터와 로그를 탭으로 분리(하단 로그 영역 제거 → 로그는 별도 탭에서 확인).
        self.tabs = QTabWidget()
        self.tabs.setObjectName("seq_tabs")
        self.tabs.addTab(self.editor, "에디터")
        self._log_tab_index = self.tabs.addTab(self.log_view, "로그")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(self._build_control_bar())
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        # Ctrl+S 로도 현재 다이어그램 저장(창 어디에 포커스가 있어도 동작).
        save_sc = QShortcut(QKeySequence.StandardKey.Save, self)
        save_sc.activated.connect(self._save)
        self._set_running_ui(False)

    def showEvent(self, event) -> None:
        """창이 뜰 때 전체 다이어그램이 보이도록 자동으로 화면에 맞춘다(Fit).

        뷰포트 크기가 확정된 뒤 맞춰야 하므로 레이아웃이 끝나는 다음 이벤트
        루프로 미뤄 실행한다.
        """
        super().showEvent(event)
        QTimer.singleShot(0, self.editor.view.fit_contents)

    def _build_control_bar(self) -> QHBoxLayout:
        """상단 바: 실행 제어(▶/⏭/■) + 우측 끝 '시퀀스 다이어그램 관리'."""
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
        # 우측 상단: 저장(컴팩트) + 여러 다이어그램 관리.
        # 저장 완료 안내(저장 버튼 왼쪽). 저장 시 잠깐 떴다가 사라진다.
        self.saved_label = QLabel("")
        self.saved_label.setObjectName("seq_saved_label")
        self.saved_label.setStyleSheet("color:#22c55e; font-weight:bold; padding-right:8px;")
        bar.addWidget(self.saved_label)
        self._saved_timer = QTimer(self)
        self._saved_timer.setSingleShot(True)
        self._saved_timer.setInterval(3000)  # 3초 후 자동 숨김
        self._saved_timer.timeout.connect(lambda: self.saved_label.setText(""))

        self.save_button = QPushButton("💾 저장")
        self.save_button.setObjectName("seq_save_button")
        self.save_button.setToolTip("현재 다이어그램을 저장합니다")
        self.save_button.clicked.connect(self._save)
        bar.addWidget(self.save_button)
        self.manage_button = QPushButton("🗂  시퀀스 다이어그램 관리")
        self.manage_button.setObjectName("seq_manage_button")
        self.manage_button.setToolTip("여러 시퀀스 다이어그램을 저장·불러오기·관리합니다")
        self.manage_button.clicked.connect(self._open_set_manager)
        bar.addWidget(self.manage_button)
        return bar

    def _set_running_ui(self, running: bool) -> None:
        """실행 상태에 따라 버튼 활성화/상태 라벨을 갱신한다."""
        self.run_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.run_status.setText("실행 중…" if running else "정지됨")
        self.run_status.setStyleSheet(
            "font-weight: bold; padding-left: 8px; color: %s;" % ("#22c55e" if running else "#94a3b8")
        )

    # --- 시퀀스 세트(여러 다이어그램) 관리 ---
    def _sets_dir(self) -> str:
        os.makedirs(SETS_DIR, exist_ok=True)
        return SETS_DIR

    def _set_path(self, name: str) -> str:
        return os.path.join(self._sets_dir(), f"{name}.json")

    def _config_path(self) -> str:
        return os.path.join(self._sets_dir(), SETS_CONFIG)

    def list_sets(self) -> list[str]:
        """저장된 세트 이름 목록(가나다 순)."""
        d = self._sets_dir()
        return sorted(os.path.splitext(f)[0] for f in os.listdir(d)
                      if f.endswith(".json") and f != SETS_CONFIG)

    def default_set_name(self) -> str | None:
        try:
            with open(self._config_path(), "r", encoding="utf-8") as f:
                return json.load(f).get("default")
        except (OSError, ValueError):
            return None

    def set_default_set(self, name: str | None) -> None:
        try:
            with open(self._config_path(), "w", encoding="utf-8") as f:
                json.dump({"default": name}, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.error(f"기본 세트 저장 실패: {exc}")

    def write_set(self, name: str, seq: Sequence) -> bool:
        try:
            with open(self._set_path(name), "w", encoding="utf-8") as f:
                json.dump(seq.to_dict(), f, indent=2, ensure_ascii=False)
            return True
        except OSError as exc:
            QMessageBox.warning(self, "저장 실패", str(exc))
            return False

    def load_set(self, name: str) -> bool:
        """이름으로 세트를 불러와 에디터에 적용한다."""
        path = self._set_path(name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.sequence = Sequence.from_dict(json.load(f))
            self._path = path
            self._reload_editor()
            self.log(f"세트 불러옴: {name}")
            return True
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "불러오기 실패", str(exc))
            return False

    def current_set_name(self) -> str | None:
        """현재 시뮬레이션에 로드된 다이어그램(세트) 이름. 세트가 아니면 None."""
        try:
            in_sets = (os.path.dirname(os.path.abspath(self._path))
                       == os.path.abspath(self._sets_dir()))
        except OSError:
            return None
        return os.path.splitext(os.path.basename(self._path))[0] if in_sets else None

    def has_unsaved_changes(self) -> bool:
        """현재 편집 중인 다이어그램이 디스크의 파일과 다른지(저장 안 된 변경) 판정한다."""
        current = json.dumps(self.sequence.to_dict(), ensure_ascii=False, sort_keys=True)
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                saved = json.dumps(json.load(f), ensure_ascii=False, sort_keys=True)
        except (OSError, ValueError):
            return True  # 파일이 없거나 손상 → 변경된 것으로 간주
        return current != saved

    def _open_set_manager(self) -> None:
        SequenceSetDialog(self).exec()

    def _load_or_default(self) -> Sequence:
        # 1) 기본 세트가 지정되어 있으면 그걸 우선 로드
        name = self.default_set_name()
        if name and os.path.exists(self._set_path(name)):
            try:
                with open(self._set_path(name), "r", encoding="utf-8") as f:
                    self._path = self._set_path(name)
                    return Sequence.from_dict(json.load(f))
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                logger.error(f"기본 세트 로드 실패, 기본값 사용: {exc}")
        # 2) 신규 설치(사용자 세트 없음): 배포에 포함된 기본 다이어그램을 시드해 로드
        if not self.list_sets():
            seeded = self._seed_bundled_default()
            if seeded is not None:
                return seeded
        # 3) 구버전 단일 파일 호환
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    return Sequence.from_dict(json.load(f))
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                logger.error(f"시퀀스 로드 실패, 기본값 사용: {exc}")
        return Sequence(nodes=[Node(id="n1", type=NodeType.START, x=40, y=60)], edges=[])

    def _seed_bundled_default(self) -> Sequence | None:
        """배포에 포함된 기본 다이어그램을 사용자 세트 폴더로 시드하고 반환한다.

        신규 설치(사용자가 만든 세트가 하나도 없을 때)에만 호출된다. 시드한
        세트를 기본으로 지정해, 다음 실행부터는 사용자 데이터로 이어진다. 파일을
        쓰지 못해도(읽기전용 위치 등) 로드한 시퀀스는 그대로 반환한다.
        """
        src = bundled_default_path()
        if not os.path.exists(src):
            return None
        try:
            with open(src, "r", encoding="utf-8") as f:
                seq = Sequence.from_dict(json.load(f))
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            logger.error(f"기본 시퀀스 탑재 로드 실패: {exc}")
            return None
        self._path = self._set_path(BUNDLED_DEFAULT_NAME)
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(seq.to_dict(), f, indent=2, ensure_ascii=False)
            self.set_default_set(BUNDLED_DEFAULT_NAME)
            logger.info(f"기본 시퀀스 다이어그램 탑재: {BUNDLED_DEFAULT_NAME}")
        except OSError as exc:
            logger.error(f"기본 시퀀스 세트 생성 실패(로드만 진행): {exc}")
        return seq

    def _reload_editor(self) -> None:
        self.editor.scene.sequence = self.sequence
        self.editor.sequence = self.sequence
        self.editor.scene.rebuild()
        if hasattr(self.editor, "reset_history"):
            self.editor.reset_history()

    def _save(self) -> None:
        """현재 편집 중인 다이어그램을 현재 파일(또는 기본 파일)에 저장한다."""
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self.sequence.to_dict(), f, indent=2, ensure_ascii=False)
            self.log(f"저장됨: {self._path}")
            self.saved_label.setText("저장되었습니다")
            self._saved_timer.start()  # 매 저장마다 재시작 → 마지막 저장 3초 뒤 사라짐
        except OSError as exc:
            QMessageBox.warning(self, "저장 실패", str(exc))

    def _make_engine(self) -> bool:
        """검증 후 새 엔진을 만들고 시그널을 연결한다(시작은 호출측이 담당)."""
        errors = self.sequence.validate()
        if errors:
            QMessageBox.warning(self, "검증 실패", "\n".join(errors))
            return False
        self._stop()
        self._engine = SequenceEngine(self.sequence, self._read, self._write)
        self._engine.active_changed.connect(self.editor.set_active)
        self._engine.step_logged.connect(self.log)
        self._engine.finished.connect(self._on_finished)
        return True

    def _run(self) -> None:
        """▶ 실행: 자동 타이머로 끝까지 진행한다."""
        if not self._make_engine():
            return
        self.log("=== 실행 시작 ===")
        if self._engine.start():
            self._set_running_ui(True)

    def _step(self) -> None:
        """⏭ 스텝: 한 번에 노드 한 단계씩만 진행한다(자동 진행 없음)."""
        if self._engine is not None and self._engine.running:
            self._engine.step()
            return
        # 새 스텝 세션 시작 — START 노드에 진입만 하고 멈춰 있다가, 누를 때마다 한 단계씩.
        if not self._make_engine():
            return
        self.log("=== 스텝 실행 시작 ===")
        if self._engine.start(manual=True):
            self._set_running_ui(True)

    def _stop(self) -> None:
        if self._engine is not None and self._engine.running:
            self._engine.stop()

    def _on_finished(self, reason: str) -> None:
        self.log(f"=== 종료: {reason} ===")
        # 여러 흐름이 동시에 돌 수 있으므로, 엔진이 완전히 멈췄을 때만 UI 를 정지로.
        if self._engine is None or not self._engine.running:
            self._set_running_ui(False)

    def log(self, text: str) -> None:
        """로그뷰에 한 줄 추가한다(로그 탭이 아닐 때는 새 로그 표시)."""
        self.log_view.appendPlainText(text)
        if self.tabs.currentIndex() != self._log_tab_index:
            self.tabs.setTabText(self._log_tab_index, "로그 ●")

    def _on_tab_changed(self, index: int) -> None:
        """로그 탭으로 전환하면 새 로그 표시(●)를 지운다."""
        if index == self._log_tab_index:
            self.tabs.setTabText(self._log_tab_index, "로그")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 시그니처)
        self._stop()
        super().closeEvent(event)


class SequenceSetDialog(QDialog):
    """여러 시퀀스 다이어그램을 목록으로 관리하는 창(불러오기/기본 지정/내보내기/가져오기)."""

    def __init__(self, win: "SequenceWindow") -> None:
        super().__init__(win)
        self.win = win
        self.setObjectName("seq_set_dialog")
        self.setWindowTitle("시퀀스 다이어그램 관리")
        self.resize(620, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QLabel("시퀀스 다이어그램 관리")
        title.setObjectName("seq_set_title")
        root.addWidget(title)
        subtitle = QLabel("저장된 다이어그램을 불러오거나 관리합니다.  ★ 표시는 시작 시 기본으로 열립니다.")
        subtitle.setObjectName("seq_set_subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # 기본/현재 다이어그램 이름 표시
        info = QHBoxLayout()
        info.setSpacing(10)
        self.default_info = QLabel()
        self.default_info.setObjectName("seq_set_info")
        self.current_info = QLabel()
        self.current_info.setObjectName("seq_set_info")
        info.addWidget(self.default_info)
        info.addWidget(self.current_info)
        info.addStretch(1)
        root.addLayout(info)

        body = QHBoxLayout()
        body.setSpacing(14)
        self.list = QListWidget()
        self.list.setObjectName("seq_set_list")
        self.list.itemDoubleClicked.connect(lambda _i: self._select())
        body.addWidget(self.list, 1)
        body.addLayout(self._build_action_panel())
        root.addLayout(body, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        close = QPushButton("닫기")
        close.setObjectName("seq_set_close")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        root.addLayout(footer)
        self._refresh()

    def _build_action_panel(self) -> QVBoxLayout:
        """리스트 우측의 동작 버튼 패널(섹션별 그룹)."""
        col = QVBoxLayout()
        col.setSpacing(8)

        def add_btn(text: str, slot, obj: str = "seq_set_btn") -> QPushButton:
            b = QPushButton(text)
            b.setObjectName(obj)
            b.setMinimumWidth(168)
            b.setMinimumHeight(38)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(slot)
            col.addWidget(b)
            return b

        def section(text: str) -> None:
            lbl = QLabel(text)
            lbl.setObjectName("seq_set_section")
            col.addWidget(lbl)

        section("열기")
        add_btn("✔  선택", self._select, "seq_set_primary")
        add_btn("★  기본으로 지정", self._set_default)
        section("편집")
        add_btn("＋  새로 만들기", self._new)
        add_btn("⧉  복사", self._duplicate)
        self.rename_btn = add_btn("✎  이름 변경", self._rename)
        self.delete_btn = add_btn("🗑  삭제", self._delete, "seq_set_danger")
        self.list.currentItemChanged.connect(lambda *_: self._update_buttons())
        section("파일")
        add_btn("⤓  내보내기", self._export)
        add_btn("⤒  가져오기", self._import)
        col.addStretch(1)
        return col

    def _refresh(self) -> None:
        self.list.clear()
        default = self.win.default_set_name()
        for name in self.win.list_sets():
            label = f"★ {name}" if name == default else name
            if name == BUNDLED_DEFAULT_NAME:
                label += "  🔒"  # 배포 기본 탑재 — 삭제 불가
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.list.addItem(item)
        self.default_info.setText(f"★ 기본 다이어그램:  {default or '없음'}")
        current = self.win.current_set_name()
        self.current_info.setText(f"● 현재 다이어그램:  {current or '(저장 안 된 다이어그램)'}")
        self._update_buttons()

    def _update_buttons(self) -> None:
        """선택 항목에 따라 버튼 상태를 갱신한다.

        기본 탑재 다이어그램은 이름 변경/삭제가 불가능하다(복사·내보내기는 허용).
        """
        protected = self._selected() == BUNDLED_DEFAULT_NAME
        self.delete_btn.setEnabled(not protected)
        self.rename_btn.setEnabled(not protected)

    def _selected(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _ask_name(self, title: str, initial: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, "세트 이름:", text=initial)
        name = name.strip()
        if not ok or not name:
            return None
        if any(c in name for c in '\\/:*?"<>|'):
            QMessageBox.warning(self, "이름 오류", "파일명에 사용할 수 없는 문자가 있습니다.")
            return None
        return name

    def _select(self) -> None:
        """선택한 다이어그램을 시뮬레이션으로 로딩한다.

        현재 편집 중인 다이어그램에 저장하지 않은 변경사항이 있으면, 먼저
        저장할지 물어본다(저장/저장 안 함/취소).
        """
        name = self._selected()
        if not name:
            return
        if self.win.has_unsaved_changes():
            ans = QMessageBox.question(
                self, "변경사항 저장",
                "현재 다이어그램에 저장하지 않은 변경사항이 있습니다.\n"
                "다른 다이어그램을 열기 전에 저장하시겠습니까?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if ans == QMessageBox.StandardButton.Cancel:
                return
            if ans == QMessageBox.StandardButton.Save:
                self.win._save()
        if self.win.load_set(name):
            self.accept()

    def _duplicate(self) -> None:
        """선택한 다이어그램을 내용 그대로, 다른 이름으로 목록에 추가한다."""
        name = self._selected()
        if not name:
            return
        new = self._ask_name("복사", f"{name} 사본")
        if not new:
            return
        if new in self.win.list_sets():
            QMessageBox.warning(self, "복사 실패", "같은 이름의 다이어그램이 이미 있습니다.")
            return
        try:
            shutil.copyfile(self.win._set_path(name), self.win._set_path(new))
            self._refresh()
            self._select_by_name(new)
        except OSError as exc:
            QMessageBox.warning(self, "복사 실패", str(exc))

    def _set_default(self) -> None:
        name = self._selected()
        if name:
            self.win.set_default_set(name)
            self._refresh()

    def _new(self) -> None:
        name = self._ask_name("새 세트")
        if name:
            seq = Sequence(nodes=[Node(id="n1", type=NodeType.START, x=40, y=60)], edges=[])
            if self.win.write_set(name, seq):
                self._refresh()

    def _select_by_name(self, name: str) -> None:
        """목록에서 지정한 이름의 항목을 선택 상태로 만든다."""
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == name:
                self.list.setCurrentRow(i)
                return

    def _rename(self) -> None:
        name = self._selected()
        if not name:
            return
        if name == BUNDLED_DEFAULT_NAME:
            QMessageBox.information(
                self, "이름 변경 불가",
                f"'{name}' 은(는) 프로그램에 기본 탑재된 다이어그램이라 이름을 변경할 수 없습니다.")
            return
        new = self._ask_name("이름 변경", name)
        if new and new != name:
            try:
                os.rename(self.win._set_path(name), self.win._set_path(new))
                if self.win.default_set_name() == name:
                    self.win.set_default_set(new)
                self._refresh()
            except OSError as exc:
                QMessageBox.warning(self, "이름 변경 실패", str(exc))

    def _delete(self) -> None:
        name = self._selected()
        if not name:
            return
        if name == BUNDLED_DEFAULT_NAME:
            QMessageBox.information(
                self, "삭제 불가",
                f"'{name}' 은(는) 프로그램에 기본 탑재된 다이어그램이라 삭제할 수 없습니다.")
            return
        if QMessageBox.question(self, "삭제", f"'{name}' 세트를 삭제할까요?") != QMessageBox.StandardButton.Yes:
            return
        try:
            os.remove(self.win._set_path(name))
            if self.win.default_set_name() == name:
                self.win.set_default_set(None)
            self._refresh()
        except OSError as exc:
            QMessageBox.warning(self, "삭제 실패", str(exc))

    def _export(self) -> None:
        name = self._selected()
        if not name:
            return
        path, _ = QFileDialog.getSaveFileName(self, "세트 내보내기", f"{name}.json", "JSON (*.json)")
        if path:
            try:
                shutil.copyfile(self.win._set_path(name), path)
                QMessageBox.information(self, "내보내기", f"내보냈습니다:\n{path}")
            except OSError as exc:
                QMessageBox.warning(self, "내보내기 실패", str(exc))

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "세트 가져오기", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                Sequence.from_dict(json.load(f))  # 형식 검증
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "가져오기 실패", f"올바른 시퀀스 파일이 아닙니다:\n{exc}")
            return
        base = os.path.splitext(os.path.basename(path))[0]
        name = self._ask_name("가져올 세트 이름", base)
        if name:
            try:
                shutil.copyfile(path, self.win._set_path(name))
                self._refresh()
            except OSError as exc:
                QMessageBox.warning(self, "가져오기 실패", str(exc))

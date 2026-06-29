"""런타임 쓰기 파일(시퀀스 세트)이 임시폴더가 아니라 영구 데이터 폴더에
저장되는지 검증하는 회귀 테스트.

배경: 예전에는 `__main__` 에서 `os.chdir(sys._MEIPASS)` 로 작업 디렉터리를
PyInstaller 임시 추출폴더(_MEIxxxx)로 되돌려, 사용자가 만든 시퀀스가 앱 종료 시
삭제되는 데이터 손실 버그가 있었다. 이 테스트들은 그 회귀를 막는다.
"""

import os
import sys
from pathlib import Path

import pytest

from sequence.sequence_model import Node, NodeType, Sequence
from sequence.sequence_window import SequenceSetDialog, SequenceWindow

SERVER_MODULE = Path(__file__).resolve().parent.parent / "modbus_tcp_server.py"


def _make_window() -> SequenceWindow:
    return SequenceWindow(lambda t, a: 0, lambda t, a, v: None)


def test_new_set_persists_across_restart(qapp, tmp_path, monkeypatch) -> None:
    """'새로 만들기'로 만든 다이어그램이 CWD 기준 영구 파일로 저장되고,
    창을 새로 띄워도(=재시작) 그대로 남아 불러와진다."""
    monkeypatch.chdir(tmp_path)  # 테스트 종료 시 원래 CWD 로 자동 복원

    win = _make_window()
    seq = Sequence(nodes=[Node(id="n1", type=NodeType.START, x=0, y=0)], edges=[])
    assert win.write_set("MyDiagram", seq) is True
    # 영구 파일이 작업 디렉터리 아래 sequences/ 에 생성되어야 한다.
    assert (tmp_path / "sequences" / "MyDiagram.json").exists()
    win.close()

    # 재시작 모사: 새 창 인스턴스가 동일 CWD 에서 세트를 발견·로드해야 한다.
    win2 = _make_window()
    assert "MyDiagram" in win2.list_sets()
    assert win2.load_set("MyDiagram") is True
    win2.close()


def test_duplicate_set_persists_across_restart(qapp, tmp_path, monkeypatch) -> None:
    """관리 창의 '복사(_duplicate)'로 만든 다이어그램도 영구 저장된다."""
    monkeypatch.chdir(tmp_path)

    win = _make_window()
    base = Sequence(nodes=[Node(id="n1", type=NodeType.START, x=0, y=0)], edges=[])
    assert win.write_set("Base", base) is True

    dlg = SequenceSetDialog(win)
    # 이름 입력/선택 다이얼로그를 비대화형으로 대체하고 실제 복사 로직 실행.
    monkeypatch.setattr(dlg, "_selected", lambda: "Base")
    monkeypatch.setattr(dlg, "_ask_name", lambda *a, **k: "Base_copy")
    dlg._duplicate()
    assert (tmp_path / "sequences" / "Base_copy.json").exists()
    dlg.close()
    win.close()

    win2 = _make_window()
    assert "Base_copy" in win2.list_sets()
    win2.close()


def test_server_module_has_no_bundle_chdir() -> None:
    """`os.chdir` 는 _ensure_writable_data_dir() 의 사용자 데이터 폴더 이동
    한 곳에만 있어야 한다. _MEIPASS/번들 폴더로의 chdir 재도입을 차단한다."""
    src = SERVER_MODULE.read_text(encoding="utf-8")
    assert "os.chdir(bundle_dir)" not in src, "번들 폴더로의 chdir 이 재도입됨(데이터 손실 회귀)"
    # 실제 호출은 정확히 하나(_ensure_writable_data_dir 내부)여야 한다.
    # 주석 라인(앞이 '#')은 제외해, 설명 주석에 적힌 문자열이 오탐되지 않게 한다.
    code_chdirs = sum(
        line.split("#", 1)[0].count("os.chdir(") for line in src.splitlines()
    )
    assert code_chdirs == 1


def test_ensure_writable_data_dir_relocates_when_frozen(tmp_path, monkeypatch) -> None:
    """frozen 실행 시 CWD 를 사용자 데이터 폴더(<root>/ModbusTcpServer)로 옮긴다."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))       # Windows 분기
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))      # Linux 분기
    monkeypatch.chdir(tmp_path)

    import modbus_tcp_server as server  # frozen=False 상태로 임포트(이동 없음)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    server._ensure_writable_data_dir()

    assert Path(os.getcwd()) == tmp_path / "ModbusTcpServer"

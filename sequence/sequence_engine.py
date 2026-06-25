"""시퀀스 실행 상태머신.

GUI 스레드의 QTimer 로 일정 간격(틱)마다 현재 노드를 평가하여 신호를 전송하고
대기 조건을 판정하며 분기한다. 테스트를 위해 클록과 레지스터 읽기/쓰기 콜백을
주입받는다.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from sequence.sequence_model import Condition, Node, NodeType, Sequence

logger = logging.getLogger("ModbusServerSim")

_OPS: dict[str, Callable[[int, int], bool]] = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
}


class SequenceEngine(QObject):
    """시퀀스를 노드 단위로 실행하는 상태머신."""

    node_activated = Signal(str)  # 현재 활성 노드 id
    step_logged = Signal(str)     # 실행 로그 한 줄
    finished = Signal(str)        # 종료 사유(END result / "stopped" / "timeout" / "dangling:*")

    def __init__(
        self,
        sequence: Sequence,
        read_fn: Callable[[str, int], int],
        write_fn: Callable[[str, int, int], None],
        *,
        tick_ms: int = 50,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        """엔진을 초기화한다.

        Args:
            sequence: 실행할 시퀀스.
            read_fn: (reg_type, addr) -> int 레지스터 읽기 콜백.
            write_fn: (reg_type, addr, value) -> None 레지스터 쓰기 콜백.
            tick_ms: QTimer 틱 간격(ms).
            now_fn: 단조 시간(초) 반환 함수. 테스트에서 가짜 클록 주입용.
        """
        super().__init__()
        self.sequence = sequence
        self._read = read_fn
        self._write = write_fn
        self._now = now_fn
        self._tick_ms = tick_ms
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._edges = {(e.from_node, e.from_port): e.to_node for e in sequence.edges}
        self.current_id: str | None = None
        self.running = False
        self._node_entered = 0.0

    def start(self) -> bool:
        """검증 후 START 노드부터 실행을 시작한다.

        Returns:
            시작 성공 여부. 검증 실패 시 False 를 반환하고 finished 를 emit 한다.
        """
        errors = self.sequence.validate()
        if errors:
            self.step_logged.emit("검증 실패: " + "; ".join(errors))
            self.finished.emit("invalid")
            return False
        start = self.sequence.start_node()
        assert start is not None  # validate 통과 시 보장됨
        self._enter(start.id)
        self.running = True
        self._timer.start(self._tick_ms)
        return True

    def stop(self) -> None:
        """실행을 중단한다."""
        if not self.running:
            return
        self.running = False
        self._timer.stop()
        self.step_logged.emit("중단됨")
        self.finished.emit("stopped")

    def step(self) -> None:
        """타이머와 무관하게 한 틱만 수동 진행한다(디버깅용)."""
        self._tick()

    def _enter(self, node_id: str) -> None:
        """노드 진입 시각을 기록하고 활성화 신호를 보낸다."""
        self.current_id = node_id
        self._node_entered = self._now()
        node = self.sequence.node_by_id(node_id)
        label = f"{node.type.value}" + (f" ({node.label})" if node and node.label else "")
        self.node_activated.emit(node_id)
        self.step_logged.emit(f"진입: {label}")

    def _finish(self, reason: str) -> None:
        """실행을 종료하고 사유를 보고한다."""
        self.running = False
        self._timer.stop()
        self.finished.emit(reason)

    def _elapsed_ms(self) -> float:
        """현재 노드 진입 후 경과 시간(ms)."""
        return (self._now() - self._node_entered) * 1000.0

    def _eval(self, cond: Condition) -> bool:
        """조건을 현재 레지스터 값으로 평가한다."""
        actual = self._read(cond.reg_type, cond.addr)
        return _OPS[cond.op](actual, cond.value)

    def _transition(self, port: str) -> None:
        """주어진 출력 포트의 엣지를 따라 다음 노드로 이동한다."""
        target = self._edges.get((self.current_id, port))
        if target is None:
            self.step_logged.emit(f"미연결 포트 '{port}' 에서 종료")
            self._finish(f"dangling:{port}")
            return
        self._enter(target)

    def _tick(self) -> None:
        """현재 노드를 한 번 평가한다(노드 타입별 핸들러로 위임)."""
        if not self.running or self.current_id is None:
            return
        node = self.sequence.node_by_id(self.current_id)
        if node is None:
            self._finish("missing_node")
            return
        handler = {
            NodeType.START: self._handle_passthrough,
            NodeType.SEND: self._handle_send,
            NodeType.WAIT: self._handle_wait,
            NodeType.BRANCH: self._handle_branch,
            NodeType.DELAY: self._handle_delay,
            NodeType.END: self._handle_end,
        }[node.type]
        handler(node)

    def _handle_passthrough(self, node: Node) -> None:
        """START: 즉시 next 로 전진."""
        self._transition("next")

    def _handle_send(self, node: Node) -> None:
        """SEND: 모든 쓰기 동작을 수행하고 next 로 전진."""
        for action in node.writes:
            self._write(action.reg_type, action.addr, action.value)
            self.step_logged.emit(f"전송: {action.reg_type}[{action.addr}] = {action.value}")
        self._transition("next")

    def _handle_wait(self, node: Node) -> None:
        """WAIT: 첫 충족 조건으로 분기, 없으면 타임아웃 검사 후 대기 유지."""
        for i, cond in enumerate(node.conditions):
            if self._eval(cond):
                self.step_logged.emit(f"조건 충족: {cond.reg_type}[{cond.addr}] {cond.op} {cond.value}")
                self._transition(f"cond_{i}")
                return
        if node.timeout_ms is not None and self._elapsed_ms() >= node.timeout_ms:
            self.step_logged.emit("타임아웃")
            if (self.current_id, "timeout") in self._edges:
                self._transition("timeout")
            else:
                self._finish("timeout")

    def _handle_branch(self, node: Node) -> None:
        """BRANCH: 레지스터 값을 읽어 일치하는 case 로, 없으면 else 로 분기."""
        actual = self._read(node.branch_reg_type, node.branch_addr)
        for i, case_value in enumerate(node.cases):
            if actual == case_value:
                self._transition(f"case_{i}")
                return
        self._transition("else")

    def _handle_delay(self, node: Node) -> None:
        """DELAY: 지정 시간 경과 시 next 로 전진."""
        if self._elapsed_ms() >= node.delay_ms:
            self._transition("next")

    def _handle_end(self, node: Node) -> None:
        """END: 결과 라벨을 사유로 종료."""
        self.step_logged.emit(f"종료: {node.result or 'end'}")
        self._finish(node.result or "end")

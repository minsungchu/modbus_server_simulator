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

from sequence.sequence_model import Condition, Node, NodeType, Sequence, clamp_value

logger = logging.getLogger("ModbusServerSim")

_OPS: dict[str, Callable[[int, int], bool]] = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
}


class _Cursor:
    """동시에 진행되는 한 실행 흐름.

    dead=True 면 '죽은 경로' 토큰이다. 조건 분기(BRANCH/WAIT)에서 선택되지 않은
    포트로 흘려보내는 토큰으로, 노드를 실행하지 않고 통과만 하며 하류 조인의
    도착 수만 채운다(조건부 합류가 영구 대기에 빠지지 않게 한다).
    """

    __slots__ = ("node_id", "entered", "dead")

    def __init__(self, node_id: str, entered: float, dead: bool = False) -> None:
        self.node_id = node_id
        self.entered = entered
        self.dead = dead


class SequenceEngine(QObject):
    """여러 START 를 동시에 진행하는 다중 커서 상태머신."""

    node_activated = Signal(str)  # 한 흐름이 진입한 노드 id(로그/호환용)
    active_changed = Signal(object)  # 현재 활성 노드 id 집합(set) — 다중 하이라이트
    step_logged = Signal(str)     # 실행 로그 한 줄
    finished = Signal(str)        # 한 흐름의 종료 사유(END result/timeout/dangling/stopped/invalid)

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
        # 한 포트의 연결들. 첫 연결은 그룹 탈출/미연결 판정에 쓰고(_edges),
        # 같은 포트에 연결이 여러 개면 포크(동시 분기)한다(_edges_multi).
        self._edges: dict[tuple[str, str], str] = {}
        self._edges_multi: dict[tuple[str, str], list[str]] = {}
        for e in sequence.edges:
            self._edges.setdefault((e.from_node, e.from_port), e.to_node)
            self._edges_multi.setdefault((e.from_node, e.from_port), []).append(e.to_node)
        # 노드에서 나가는 모든 연결(포트 무관). 죽은 토큰을 모든 하류로 흘릴 때 사용.
        self._out_targets: dict[str, list[str]] = {}
        for e in sequence.edges:
            self._out_targets.setdefault(e.from_node, []).append(e.to_node)
        # 입력 차수(노드로 들어오는 연결 수). 2 이상이면 조인(합류) 노드 →
        # 모든 입력이 도착해야 실행한다.
        self._indeg: dict[str, int] = {}
        for e in sequence.edges:
            self._indeg[e.to_node] = self._indeg.get(e.to_node, 0) + 1
        self.current_id: str | None = None      # 현재 처리 중인 커서의 노드(핸들러용)
        self.running = False
        self._cursors: list[_Cursor] = []        # 동시에 진행되는 흐름들
        self._cur: _Cursor | None = None         # 현재 틱에서 처리 중인 커서
        self._group_done: dict[str, int] = {}   # 반복 그룹별 완료 횟수
        self._join_count: dict[str, int] = {}   # 조인 노드별 도착한 토큰 수(산것+죽은것)
        self._join_live: dict[str, int] = {}    # 그 중 살아있는 토큰 수

    def start(self, *, manual: bool = False) -> bool:
        """검증 후 START 노드부터 실행을 시작한다.

        Args:
            manual: True 면 자동 타이머를 켜지 않는다(스텝 실행). 이 경우
                step() 을 호출할 때마다 한 단계씩 진행한다.

        Returns:
            시작 성공 여부. 검증 실패 시 False 를 반환하고 finished 를 emit 한다.
        """
        errors = self.sequence.validate()
        if errors:
            self.step_logged.emit("검증 실패: " + "; ".join(errors))
            self.finished.emit("invalid")
            return False
        starts = self._top_level_starts()
        assert starts  # validate 통과 시 보장됨
        now = self._now()
        self._group_done = {}  # 반복 카운터 초기화
        self._join_count = {}  # 조인 도착 카운터 초기화
        self._join_live = {}
        self._cursors = []
        for s in starts:
            self._cursors.append(_Cursor(s.id, now))
            label = f"{s.type.value}" + (f" ({s.label})" if s.label else "")
            self.step_logged.emit(f"진입: {label}")
        self.running = True
        self._emit_active()
        if not manual:
            self._timer.start(self._tick_ms)
        return True

    def stop(self) -> None:
        """실행을 중단한다."""
        if not self.running:
            return
        self.running = False
        self._timer.stop()
        self._cursors = []
        self.step_logged.emit("중단됨")
        self._emit_active()
        self.finished.emit("stopped")

    def step(self) -> None:
        """타이머와 무관하게 한 틱만 수동 진행한다(디버깅용)."""
        self._tick()

    def _enter(self, node_id: str) -> None:
        """현재 커서를 노드로 이동시키고 진입 시각/로그를 갱신한다."""
        if self._cur is None:
            return
        self._cur.node_id = node_id
        self._cur.entered = self._now()
        self.current_id = node_id
        node = self.sequence.node_by_id(node_id)
        label = f"{node.type.value}" + (f" ({node.label})" if node and node.label else "")
        self.node_activated.emit(node_id)
        self.step_logged.emit(f"진입: {label}")

    def _finish(self, reason: str) -> None:
        """현재 흐름(커서)을 종료한다. 모든 흐름이 끝나면 엔진을 멈춘다.

        finished 핸들러가 self.running 으로 완전 종료 여부를 판단하므로, emit 전에
        running/timer 상태를 먼저 확정한다(그래야 UI 가 종료를 즉시 인지한다).
        """
        if self._cur in self._cursors:
            self._cursors.remove(self._cur)
        if not self._cursors:
            self.running = False
            self._timer.stop()
        self.finished.emit(reason)

    def _elapsed_ms(self) -> float:
        """현재 커서의 노드 진입 후 경과 시간(ms)."""
        if self._cur is None:
            return 0.0
        return (self._now() - self._cur.entered) * 1000.0

    def _active_ids(self) -> set:
        # 죽은 토큰은 하이라이트에서 제외(실제 실행 흐름만 표시).
        return {c.node_id for c in self._cursors if not c.dead}

    def _emit_active(self) -> None:
        self.active_changed.emit(self._active_ids())

    def _eval(self, cond: Condition) -> bool:
        """조건을 현재 레지스터 값으로 평가한다."""
        actual = self._read(cond.reg_type, cond.addr)
        return _OPS[cond.op](actual, cond.value)

    def _transition(self, port: str) -> None:
        """출력 포트의 엣지를 따라 다음 노드로 이동한다.

        현재 노드가 반복 그룹 안에 있고 다음 노드가 그 그룹 밖이라면(또는 연결이
        없다면) 이는 본문 한 바퀴의 끝이다. 명시적 END 없이도 이 지점에서 반복
        횟수를 채울 때까지 본문 진입점으로 되돌아가고, 다 채우면 밖으로 나간다.
        """
        cur_node = self.sequence.node_by_id(self.current_id)
        targets = self._edges_multi.get((self.current_id, port), [])
        target = targets[0] if targets else None  # 그룹 탈출/미연결 판정용(첫 연결)
        target_node = self.sequence.node_by_id(target) if target else None
        g_cur = self._container_of(cur_node) if cur_node else None
        leaving = g_cur is not None and (target_node is None
                                         or not self._in_group(target_node, g_cur))
        if leaving:
            # 그룹 탈출 경로는 분기하지 않고 첫 연결만 따른다(반복 종료 후 진행).
            self._loop_or_exit(g_cur, port, target)
            return
        if not targets:
            self.step_logged.emit(f"미연결 포트 '{port}' 에서 종료")
            self._finish(f"dangling:{port}")
            return
        # 포크: 같은 포트에 연결이 여러 개면 동시에 갈라진다. 첫 연결은 현재
        # 커서가 따르고, 나머지는 새 커서(분기)를 만들어 병렬 진행한다.
        for extra in targets[1:]:
            self._spawn_branch(cur_node, extra)
        self._goto(cur_node, target)

    def _goto(self, cur_node: Node | None, target_id: str) -> None:
        """target 으로 진입하되, 조인/반복그룹 진입을 함께 처리한다.

        target 의 입력 연결이 2개 이상(조인)이면, 모든 토큰(산것+죽은것)이 도착할
        때까지 현재 토큰을 흡수하고 기다린다. 모두 도착하면 진입하되, 산 토큰이
        하나라도 있었으면 실행(live), 전부 죽었으면 죽은 채로 통과시킨다.
        """
        if not self._join_ready(target_id):
            return  # 아직 다른 토큰을 기다리는 중(현재 토큰은 흡수됨)
        target_node = self.sequence.node_by_id(target_id)
        g_tgt = self._container_of(target_node) if target_node else None
        if (g_tgt is not None and not self._cur.dead
                and (cur_node is None or not self._in_group(cur_node, g_tgt))):
            self._begin_iteration(g_tgt)  # 그룹에 처음 들어가는 순간(첫 반복 시작)
        self._enter(target_id)

    def _join_ready(self, target_id: str) -> bool:
        """조인 노드면 도착 토큰 수를 세고, 모두 도착했는지 반환한다.

        입력 차수가 2 이상인 노드만 조인이다. 마지막 토큰 도착이면 카운터를
        리셋하고 True 를(이때 현재 커서의 dead 를 '전부 죽었는가'로 갱신), 아직
        부족하면 현재 토큰을 흡수(대기)하고 False 를 반환한다. 일반(입력 1개)
        노드는 항상 True.
        """
        need = self._indeg.get(target_id, 0)
        if need < 2:
            return True
        cnt = self._join_count.get(target_id, 0) + 1
        live = self._join_live.get(target_id, 0) + (0 if self._cur.dead else 1)
        node = self.sequence.node_by_id(target_id)
        label = (node.label or node.type.value) if node else target_id
        if cnt < need:
            self._join_count[target_id] = cnt
            self._join_live[target_id] = live
            self.step_logged.emit(f"조인 대기 [{label}]: {cnt}/{need} 도착")
            self._absorb_cur()
            return False
        self._join_count[target_id] = 0  # 다음 합류(반복 등) 위해 초기화
        self._join_live[target_id] = 0
        self._cur.dead = live == 0  # 산 토큰이 하나도 없으면 죽은 채로 통과
        state = "실행" if not self._cur.dead else "죽은경로"
        self.step_logged.emit(f"조인 완료 [{label}]: {need}/{need} 도착 → {state}")
        return True

    def _absorb_cur(self) -> None:
        """현재 커서를 흐름 종료(finished) 없이 제거한다(조인 대기/죽은경로 소멸)."""
        if self._cur in self._cursors:
            self._cursors.remove(self._cur)

    def _spawn_branch(self, cur_node: Node | None, target_id: str,
                      dead: bool = False) -> None:
        """새 커서를 만들어 target 으로 진입시킨다(포크 분기 또는 죽은 경로).

        잠시 현재 커서를 새 분기로 바꿔 _goto 의 조인/그룹 로직을 그대로 태운 뒤
        원래 커서로 되돌린다. 새 분기가 조인 대기로 흡수되면 커서는 제거된다.
        """
        saved_cur, saved_id = self._cur, self.current_id
        branch = _Cursor(saved_id or target_id, self._now(), dead=dead)
        self._cursors.append(branch)
        self._cur = branch
        self._goto(cur_node, target_id)
        self._cur, self.current_id = saved_cur, saved_id

    def _handle_dead(self, node: Node) -> None:
        """죽은 토큰: 노드를 실행하지 않고 모든 하류 연결로 흘려보낸다.

        조건 분기에서 선택되지 않은 경로를 따라가며 하류 조인의 도착 수만 채운다.
        END/미연결에 닿으면 조용히 소멸한다(흐름 종료로 치지 않음).
        """
        if node.type is NodeType.END:
            self._absorb_cur()
            return
        targets = self._out_targets.get(node.id, [])
        if not targets:
            self._absorb_cur()  # 죽은 경로가 막다른 곳에서 소멸
            return
        for extra in targets[1:]:
            self._spawn_branch(node, extra, dead=True)
        self._goto(node, targets[0])

    def _dead_unchosen(self, node: Node, chosen_port: str) -> None:
        """조건 분기(BRANCH/WAIT)에서 선택되지 않은 포트로 죽은 토큰을 흘려보낸다."""
        for (from_id, port), targets in self._edges_multi.items():
            if from_id != node.id or port == chosen_port:
                continue
            for t in targets:
                self._spawn_branch(node, t, dead=True)

    def _tick(self) -> None:
        """모든 활성 커서를 한 번씩 평가한다(각자 자기 노드의 핸들러로 위임)."""
        if not self.running:
            return
        handlers = {
            NodeType.START: self._handle_passthrough,
            NodeType.SEND: self._handle_send,
            NodeType.WAIT: self._handle_wait,
            NodeType.BRANCH: self._handle_branch,
            NodeType.DELAY: self._handle_delay,
            NodeType.END: self._handle_end,
        }
        for cur in list(self._cursors):
            if cur not in self._cursors:
                continue  # 이전 커서 처리 중 제거되었을 수 있음
            self._cur = cur
            self.current_id = cur.node_id
            node = self.sequence.node_by_id(cur.node_id)
            if node is None:
                self._finish("missing_node")
                continue
            if cur.dead:
                self._handle_dead(node)  # 죽은 토큰: 실행 없이 통과만
            else:
                handlers[node.type](node)
        self._cur = None
        # 살아있는 흐름이 모두 끝나고 죽은 토큰까지 모두 소진되면 엔진을 멈춘다.
        if self.running and not self._cursors:
            self.running = False
            self._timer.stop()
        self._emit_active()

    # --- 반복 그룹(컨테이너) 지원 ---
    @staticmethod
    def _in_group(node: Node, group) -> bool:
        """노드 좌표가 그룹 영역 안에 있는지(기하학적 포함)."""
        return (group.x <= node.x <= group.x + group.w
                and group.y <= node.y <= group.y + group.h)

    def _container_of(self, node: Node):
        """노드를 포함하는 가장 안쪽의 반복(loop/iter) 그룹을 찾는다(없으면 None)."""
        best, best_area = None, None
        for g in self.sequence.groups:
            if g.mode in ("loop", "iter") and g.w > 0 and g.h > 0 and self._in_group(node, g):
                area = g.w * g.h
                if best is None or area < best_area:
                    best, best_area = g, area
        return best

    def _body_entry(self, group):
        """반복 그룹 본문의 진입 노드를 추론한다(명시적 START 불필요).

        우선순위: ① 그룹 밖에서 그룹 안으로 들어오는 엣지의 도착 노드,
        ② 그룹 내부에서 들어오는 엣지가 없는 노드(본문 시작), ③ 첫 멤버.
        """
        members = [n for n in self.sequence.nodes if self._in_group(n, group)]
        if not members:
            return None
        member_ids = {n.id for n in members}
        for e in self.sequence.edges:  # ① 외부 → 내부 진입 엣지
            if e.to_node in member_ids and e.from_node not in member_ids:
                return self.sequence.node_by_id(e.to_node)
        internal_targets = {e.to_node for e in self.sequence.edges
                            if e.from_node in member_ids and e.to_node in member_ids}
        for n in members:  # ② 내부 입력이 없는 노드
            if n.id not in internal_targets:
                return n
        return members[0]  # ③ 폴백

    def _begin_iteration(self, group) -> None:
        """반복 1회 시작 시점 처리(iter 모드면 현재 인덱스의 배열값을 레지스터에 쓴다)."""
        if group.mode == "iter" and group.iter_values \
                and group.iter_reg_type is not None and group.iter_addr is not None:
            i = self._group_done.get(group.id, 0) % len(group.iter_values)
            value = clamp_value(group.iter_reg_type, group.iter_values[i])
            self._write(group.iter_reg_type, group.iter_addr, value)
            self.step_logged.emit(
                f"[{group.label or '그룹'}] 배열[{i}]: {group.iter_reg_type}[{group.iter_addr}] = {value}")

    def _loop_or_exit(self, group, port: str, target: str | None) -> None:
        """본문 한 바퀴가 끝났을 때: 남은 반복이 있으면 진입점으로 되돌리고,
        다 채웠으면 그룹 밖(target)으로 나간다."""
        total = group.loop_count if group.mode == "loop" else len(group.iter_values)
        done = self._group_done.get(group.id, 0) + 1
        if total > 0 and done < total:
            self._group_done[group.id] = done
            self.step_logged.emit(f"[{group.label or '그룹'}] 반복 {done + 1}/{total}")
            entry = self._body_entry(group)
            if entry is None:
                self._finish("no_body")
                return
            self._begin_iteration(group)  # 다음 인덱스 값 쓰기(iter)
            self._enter(entry.id)
        else:
            self._group_done[group.id] = 0  # 다음 실행 위해 초기화
            self.step_logged.emit(f"[{group.label or '그룹'}] 반복 완료")
            if target is None:
                self.step_logged.emit(f"미연결 포트 '{port}' 에서 종료")
                self._finish(f"dangling:{port}")
            else:
                cur_node = self.sequence.node_by_id(self.current_id)
                self._goto(cur_node, target)

    def _top_level_starts(self) -> list[Node]:
        """반복 그룹에 속하지 않은 활성 최상위 START 들을 반환한다(여러 개 = 동시 실행).

        비활성(enabled=False) START 는 실행에서 제외해, 지우지 않고도 특정 흐름을
        꺼둘 수 있게 한다.
        """
        return [n for n in self.sequence.nodes
                if n.type is NodeType.START and n.enabled
                and self._container_of(n) is None]

    def _handle_passthrough(self, node: Node) -> None:
        """START: 즉시 next 로 전진(반복 그룹 진입/배열값 쓰기는 전이 단계에서 처리)."""
        self._transition("next")

    def _handle_send(self, node: Node) -> None:
        """SEND: 모든 쓰기 동작을 수행하고 next 로 전진.

        op 가 add/sub 면 현재 값을 읽어 누적/감산한 결과를 쓴다(루프에서 증가 전송).
        """
        for action in node.writes:
            if action.op in ("add", "sub"):
                cur = self._read(action.reg_type, action.addr)
                delta = action.value if action.op == "add" else -action.value
                new = clamp_value(action.reg_type, cur + delta)
                symbol = "+=" if action.op == "add" else "-="
                self._write(action.reg_type, action.addr, new)
                self.step_logged.emit(
                    f"전송: {action.reg_type}[{action.addr}] {symbol} {action.value} → {new}")
            else:
                self._write(action.reg_type, action.addr, action.value)
                self.step_logged.emit(f"전송: {action.reg_type}[{action.addr}] = {action.value}")
        self._transition("next")

    def _handle_wait(self, node: Node) -> None:
        """WAIT: 첫 충족 조건으로 분기, 없으면 타임아웃 검사 후 대기 유지."""
        for i, cond in enumerate(node.conditions):
            if self._eval(cond):
                self.step_logged.emit(f"조건 충족: {cond.reg_type}[{cond.addr}] {cond.op} {cond.value}")
                self._dead_unchosen(node, f"cond_{i}")
                self._transition(f"cond_{i}")
                return
        if node.timeout_ms is not None and self._elapsed_ms() >= node.timeout_ms:
            self.step_logged.emit("타임아웃")
            if (self.current_id, "timeout") in self._edges:
                self._dead_unchosen(node, "timeout")
                self._transition("timeout")
            else:
                self._finish("timeout")

    def _handle_branch(self, node: Node) -> None:
        """BRANCH: 레지스터 값을 읽어 일치하는 case 로, 없으면 else 로 분기.

        선택된 포트로만 산 토큰을 보내고, 나머지 포트로는 죽은 토큰을 흘려보내
        하류 조인이 '안 온 입력'을 영구히 기다리지 않게 한다.
        """
        actual = self._read(node.branch_reg_type, node.branch_addr)
        chosen = "else"
        for i, case_value in enumerate(node.cases):
            if actual == case_value:
                chosen = f"case_{i}"
                break
        self._dead_unchosen(node, chosen)
        self._transition(chosen)

    def _handle_delay(self, node: Node) -> None:
        """DELAY: 지정 시간 경과 시 next 로 전진."""
        if self._elapsed_ms() >= node.delay_ms:
            self._transition("next")

    def _handle_end(self, node: Node) -> None:
        """END: 최상위면 시퀀스 종료. 반복 그룹 안의 END(선택적/레거시)는 한 반복의
        경계로 보고 재실행/탈출을 처리한다(명시적 END 없이도 동작하지만, 두어도 됨)."""
        g = self._container_of(node)
        if g is None:
            self.step_logged.emit(f"종료: {node.result or 'end'}")
            self._finish(node.result or "end")
            return
        self._loop_or_exit(g, "next", self._edges.get((node.id, "next")))

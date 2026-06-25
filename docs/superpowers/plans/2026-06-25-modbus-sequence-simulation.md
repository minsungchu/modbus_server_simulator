# Modbus 시퀀스 시뮬레이션 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modbus TCP 서버 시뮬레이터에 단계별 신호 전송/대기/분기를 비주얼 노드 그래프로 구축·실행·편집하는 시퀀스 시뮬레이션 기능을 추가한다.

**Architecture:** 순수 로직(모델·실행엔진)을 별도 `sequence/` 패키지로 분리하여 단위 테스트(TDD)로 검증하고, GUI(노드 그래프 에디터·전용 창)는 PySide6 `QGraphicsView`로 구현한다. 기존 `modbus_tcp_server.py`에는 "시퀀스 시뮬레이션" 버튼과 store 접근 어댑터 메서드(`read_register`/`engine_write`)만 추가한다.

**Tech Stack:** Python 3.11+, PySide6 6.9.1, pymodbus 3.9.2, pytest (신규 추가). 패키지 관리는 uv.

**참고 스펙:** `docs/superpowers/specs/2026-06-25-modbus-sequence-simulation-design.md`

---

## File Structure

| 파일 | 책임 |
|---|---|
| `sequence/__init__.py` | 패키지 진입, 공개 심볼 export |
| `sequence/sequence_model.py` | `NodeType`, `Condition`, `WriteAction`, `Node`, `Edge`, `Sequence` 데이터모델 + JSON 직렬화 + `validate()` |
| `sequence/sequence_engine.py` | `SequenceEngine(QObject)` 실행 상태머신 (QTimer 구동, 클록/콜백 주입) |
| `sequence/sequence_editor.py` | `NodeItem`, `EdgeItem`, `SequenceScene`, `SequenceEditor(QWidget)` 노드 그래프 에디터 + 속성 패널 |
| `sequence/sequence_window.py` | `SequenceWindow(QMainWindow)` 툴바(New/Open/Save/Run/Stop/Step) + 엔진 배선 |
| `tests/test_sequence_model.py` | 모델 직렬화/검증 테스트 |
| `tests/test_sequence_engine.py` | 엔진 노드별 동작 테스트 |
| `modbus_tcp_server.py` (수정) | 버튼 추가 + `read_register`/`engine_write` 어댑터 + `open_sequence_window` |
| `pyproject.toml` (수정) | dev 의존성에 pytest 추가 |

---

## Task 1: 테스트 도구 및 패키지 스켈레톤

**Files:**
- Modify: `pyproject.toml:14-18`
- Create: `sequence/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: pytest 의존성 추가**

Run:
```bash
uv add --dev pytest
```
Expected: `pyproject.toml` 의 `[dependency-groups] dev` 에 `pytest` 추가, `.venv` 에 설치됨.

- [ ] **Step 2: 패키지 디렉터리 생성**

Create `sequence/__init__.py`:
```python
"""Modbus 시퀀스 시뮬레이션 패키지.

순수 로직(모델/엔진)과 GUI(에디터/창)를 함께 제공한다.
GUI 임포트는 PySide6 가 필요하므로 지연 임포트하지 않고 직접 사용한다.
"""
```

Create `tests/__init__.py`:
```python
```

- [ ] **Step 3: pytest용 QApplication 픽스처 작성**

Create `tests/conftest.py`:
```python
"""pytest 공용 픽스처.

QObject 시그널/슬롯이 동작하려면 QCoreApplication 인스턴스가 하나 필요하다.
GUI(QWidget) 없이도 엔진(QObject) 테스트가 가능하도록 코어 앱만 띄운다.
"""

import pytest
from PySide6.QtCore import QCoreApplication


@pytest.fixture(scope="session")
def qapp() -> QCoreApplication:
    """세션 전체에서 공유하는 QCoreApplication 을 반환한다."""
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app
```

- [ ] **Step 4: 빈 테스트로 도구 동작 확인**

Run:
```bash
uv run pytest -q
```
Expected: `no tests ran` (수집 0개, 에러 없음). pytest 가 정상 설치·실행됨을 확인.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock sequence/__init__.py tests/__init__.py tests/conftest.py
git commit -m "chore: pytest 추가 및 시퀀스 패키지 스켈레톤 생성"
```

---

## Task 2: 데이터 모델 (`sequence_model.py`)

**Files:**
- Create: `sequence/sequence_model.py`
- Test: `tests/test_sequence_model.py`

- [ ] **Step 1: 모델 테스트 작성**

Create `tests/test_sequence_model.py`:
```python
"""sequence_model 직렬화/검증 테스트."""

from sequence.sequence_model import (
    Condition,
    Edge,
    Node,
    NodeType,
    Sequence,
    WriteAction,
)


def test_node_roundtrip_send() -> None:
    node = Node(
        id="n2",
        type=NodeType.SEND,
        x=10.0,
        y=20.0,
        label="start",
        writes=[WriteAction(reg_type="coils", addr=0, value=1)],
    )
    restored = Node.from_dict(node.to_dict())
    assert restored == node


def test_node_roundtrip_wait() -> None:
    node = Node(
        id="n3",
        type=NodeType.WAIT,
        x=0.0,
        y=0.0,
        conditions=[Condition(reg_type="discrete_inputs", addr=1, op="==", value=1)],
        timeout_ms=5000,
    )
    restored = Node.from_dict(node.to_dict())
    assert restored == node


def test_sequence_roundtrip() -> None:
    seq = Sequence(
        nodes=[
            Node(id="n1", type=NodeType.START, x=0.0, y=0.0),
            Node(id="n2", type=NodeType.END, x=100.0, y=0.0, result="success"),
        ],
        edges=[Edge(from_node="n1", from_port="next", to_node="n2")],
    )
    restored = Sequence.from_dict(seq.to_dict())
    assert restored == seq


def test_validate_requires_single_start() -> None:
    seq = Sequence(nodes=[Node(id="n2", type=NodeType.END, x=0.0, y=0.0)], edges=[])
    errors = seq.validate()
    assert any("START" in e for e in errors)


def test_validate_detects_bad_edge() -> None:
    seq = Sequence(
        nodes=[Node(id="n1", type=NodeType.START, x=0.0, y=0.0)],
        edges=[Edge(from_node="n1", from_port="next", to_node="ghost")],
    )
    errors = seq.validate()
    assert any("ghost" in e for e in errors)


def test_value_clamping() -> None:
    action = WriteAction(reg_type="holding_registers", addr=0, value=70000)
    assert action.value == 65535
    action2 = WriteAction(reg_type="coils", addr=0, value=5)
    assert action2.value == 1  # 비트 타입은 0/1로 정규화
```

- [ ] **Step 2: 테스트 실패 확인**

Run:
```bash
uv run pytest tests/test_sequence_model.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'sequence.sequence_model'`

- [ ] **Step 3: 모델 구현**

Create `sequence/sequence_model.py`:
```python
"""시퀀스 노드/연결 데이터모델과 JSON 직렬화.

신호(signal)는 Modbus 레지스터 값으로 정의된다. 노드는 START/SEND/WAIT/
BRANCH/DELAY/END 6종이며, 출력 포트는 다음 규약을 따른다.

- START / SEND / DELAY: 단일 출력 포트 ``"next"``
- WAIT: 조건 i 마다 ``"cond_{i}"``, 타임아웃 시 ``"timeout"``
- BRANCH: case i 마다 ``"case_{i}"``, 그 외 ``"else"``
- END: 출력 포트 없음
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("ModbusServerSim")

BIT_TYPES = ("coils", "discrete_inputs")
REGISTER_TYPES = ("coils", "discrete_inputs", "holding_registers", "input_registers")
OPERATORS = ("==", "!=", ">", "<", ">=", "<=")


def clamp_value(reg_type: str, value: int) -> int:
    """레지스터 타입에 맞게 값을 정규화한다.

    Args:
        reg_type: 레지스터 종류.
        value: 원본 정수 값.

    Returns:
        비트 타입이면 0/1, 그 외에는 0~65535 로 제한한 값.
    """
    value = int(value)
    if reg_type in BIT_TYPES:
        return 1 if value else 0
    return max(0, min(value, 65535))


class NodeType(str, Enum):
    """시퀀스 노드 종류."""

    START = "START"
    SEND = "SEND"
    WAIT = "WAIT"
    BRANCH = "BRANCH"
    DELAY = "DELAY"
    END = "END"


@dataclass
class Condition:
    """대기/분기에 사용하는 단일 비교 조건."""

    reg_type: str
    addr: int
    op: str
    value: int

    def __post_init__(self) -> None:
        if self.op not in OPERATORS:
            raise ValueError(f"지원하지 않는 연산자: {self.op}")
        self.addr = int(self.addr)
        self.value = clamp_value(self.reg_type, self.value)

    def to_dict(self) -> dict:
        """딕셔너리로 직렬화한다."""
        return {"reg_type": self.reg_type, "addr": self.addr, "op": self.op, "value": self.value}

    @classmethod
    def from_dict(cls, data: dict) -> "Condition":
        """딕셔너리에서 역직렬화한다."""
        return cls(reg_type=data["reg_type"], addr=int(data["addr"]), op=data["op"], value=int(data["value"]))


@dataclass
class WriteAction:
    """SEND 노드가 수행하는 단일 쓰기 동작."""

    reg_type: str
    addr: int
    value: int

    def __post_init__(self) -> None:
        self.addr = int(self.addr)
        self.value = clamp_value(self.reg_type, self.value)

    def to_dict(self) -> dict:
        """딕셔너리로 직렬화한다."""
        return {"reg_type": self.reg_type, "addr": self.addr, "value": self.value}

    @classmethod
    def from_dict(cls, data: dict) -> "WriteAction":
        """딕셔너리에서 역직렬화한다."""
        return cls(reg_type=data["reg_type"], addr=int(data["addr"]), value=int(data["value"]))


@dataclass
class Node:
    """시퀀스의 단일 단계(노드)."""

    id: str
    type: NodeType
    x: float
    y: float
    label: str = ""
    writes: list[WriteAction] = field(default_factory=list)       # SEND
    conditions: list[Condition] = field(default_factory=list)     # WAIT
    timeout_ms: int | None = None                                 # WAIT (None=무한대기)
    branch_reg_type: str | None = None                            # BRANCH 대상 레지스터
    branch_addr: int | None = None                                # BRANCH 대상 주소
    cases: list[int] = field(default_factory=list)                # BRANCH case 값 목록
    delay_ms: int = 0                                             # DELAY
    result: str = ""                                             # END 결과 라벨

    def to_dict(self) -> dict:
        """딕셔너리로 직렬화한다(사용되는 필드만 포함)."""
        data: dict = {"id": self.id, "type": self.type.value, "x": self.x, "y": self.y, "label": self.label}
        if self.type is NodeType.SEND:
            data["writes"] = [w.to_dict() for w in self.writes]
        elif self.type is NodeType.WAIT:
            data["conditions"] = [c.to_dict() for c in self.conditions]
            data["timeout_ms"] = self.timeout_ms
        elif self.type is NodeType.BRANCH:
            data["branch_reg_type"] = self.branch_reg_type
            data["branch_addr"] = self.branch_addr
            data["cases"] = list(self.cases)
        elif self.type is NodeType.DELAY:
            data["delay_ms"] = self.delay_ms
        elif self.type is NodeType.END:
            data["result"] = self.result
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Node":
        """딕셔너리에서 역직렬화한다."""
        return cls(
            id=data["id"],
            type=NodeType(data["type"]),
            x=float(data.get("x", 0.0)),
            y=float(data.get("y", 0.0)),
            label=data.get("label", ""),
            writes=[WriteAction.from_dict(w) for w in data.get("writes", [])],
            conditions=[Condition.from_dict(c) for c in data.get("conditions", [])],
            timeout_ms=data.get("timeout_ms"),
            branch_reg_type=data.get("branch_reg_type"),
            branch_addr=data.get("branch_addr"),
            cases=[int(v) for v in data.get("cases", [])],
            delay_ms=int(data.get("delay_ms", 0)),
            result=data.get("result", ""),
        )


@dataclass
class Edge:
    """노드 출력 포트 → 대상 노드 연결."""

    from_node: str
    from_port: str
    to_node: str

    def to_dict(self) -> dict:
        """딕셔너리로 직렬화한다."""
        return {"from_node": self.from_node, "from_port": self.from_port, "to_node": self.to_node}

    @classmethod
    def from_dict(cls, data: dict) -> "Edge":
        """딕셔너리에서 역직렬화한다."""
        return cls(from_node=data["from_node"], from_port=data["from_port"], to_node=data["to_node"])


@dataclass
class Sequence:
    """노드와 연결로 구성된 전체 시퀀스."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    version: int = 1

    def node_by_id(self, node_id: str) -> Node | None:
        """id 로 노드를 찾는다(없으면 None)."""
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def start_node(self) -> Node | None:
        """첫 번째 START 노드를 반환한다(없으면 None)."""
        for node in self.nodes:
            if node.type is NodeType.START:
                return node
        return None

    def validate(self) -> list[str]:
        """실행 전 정합성을 검사하고 오류 메시지 목록을 반환한다(빈 목록=정상)."""
        errors: list[str] = []
        starts = [n for n in self.nodes if n.type is NodeType.START]
        if len(starts) == 0:
            errors.append("START 노드가 없습니다.")
        elif len(starts) > 1:
            errors.append("START 노드가 2개 이상입니다. 하나만 허용됩니다.")

        ids = {n.id for n in self.nodes}
        for edge in self.edges:
            if edge.from_node not in ids:
                errors.append(f"연결의 출발 노드를 찾을 수 없습니다: {edge.from_node}")
            if edge.to_node not in ids:
                errors.append(f"연결의 도착 노드를 찾을 수 없습니다: {edge.to_node}")
        return errors

    def to_dict(self) -> dict:
        """딕셔너리로 직렬화한다."""
        return {
            "version": self.version,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Sequence":
        """딕셔너리에서 역직렬화한다."""
        return cls(
            version=int(data.get("version", 1)),
            nodes=[Node.from_dict(n) for n in data.get("nodes", [])],
            edges=[Edge.from_dict(e) for e in data.get("edges", [])],
        )
```

- [ ] **Step 4: 테스트 통과 확인**

Run:
```bash
uv run pytest tests/test_sequence_model.py -q
```
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add sequence/sequence_model.py tests/test_sequence_model.py
git commit -m "feat: 시퀀스 노드/연결 데이터모델 및 직렬화 추가"
```

---

## Task 3: 실행 엔진 (`sequence_engine.py`)

엔진은 `QObject` + `QTimer` 로 구동되지만, 테스트 가능성을 위해 클록 함수(`now_fn`)와 레지스터 읽기/쓰기 콜백(`read_fn`/`write_fn`)을 주입받고, 한 틱 처리는 `_tick()` 를 직접 호출해 검증한다.

**Files:**
- Create: `sequence/sequence_engine.py`
- Test: `tests/test_sequence_engine.py`

- [ ] **Step 1: 엔진 테스트 작성**

Create `tests/test_sequence_engine.py`:
```python
"""SequenceEngine 노드별 동작 테스트.

QTimer 를 돌리지 않고 engine._tick() 를 직접 호출해 결정적으로 검증한다.
시간 의존(DELAY/timeout)은 가짜 클록(FakeClock)으로 제어한다.
"""

import pytest

from sequence.sequence_engine import SequenceEngine
from sequence.sequence_model import (
    Condition,
    Edge,
    Node,
    NodeType,
    Sequence,
    WriteAction,
)


class FakeClock:
    """테스트용 단조 클록."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeStore:
    """테스트용 레지스터 저장소."""

    def __init__(self) -> None:
        self.data: dict[tuple[str, int], int] = {}

    def read(self, reg_type: str, addr: int) -> int:
        return self.data.get((reg_type, addr), 0)

    def write(self, reg_type: str, addr: int, value: int) -> None:
        self.data[(reg_type, addr)] = value


def make_engine(seq: Sequence, store: FakeStore, clock: FakeClock) -> SequenceEngine:
    return SequenceEngine(seq, read_fn=store.read, write_fn=store.write, now_fn=clock)


def collect_finished(engine: SequenceEngine) -> list[str]:
    reasons: list[str] = []
    engine.finished.connect(reasons.append)
    return reasons


def test_send_writes_and_advances(qapp) -> None:
    store, clock = FakeStore(), FakeClock()
    seq = Sequence(
        nodes=[
            Node(id="n1", type=NodeType.START, x=0, y=0),
            Node(id="n2", type=NodeType.SEND, x=0, y=0,
                 writes=[WriteAction("coils", 0, 1), WriteAction("holding_registers", 5, 1234)]),
            Node(id="n3", type=NodeType.END, x=0, y=0, result="success"),
        ],
        edges=[
            Edge("n1", "next", "n2"),
            Edge("n2", "next", "n3"),
        ],
    )
    engine = make_engine(seq, store, clock)
    reasons = collect_finished(engine)
    engine.start()
    engine._tick()  # START -> n2
    engine._tick()  # SEND writes -> n3
    engine._tick()  # END
    assert store.read("coils", 0) == 1
    assert store.read("holding_registers", 5) == 1234
    assert reasons == ["success"]


def test_wait_first_condition_wins(qapp) -> None:
    store, clock = FakeStore(), FakeClock()
    seq = Sequence(
        nodes=[
            Node(id="n1", type=NodeType.START, x=0, y=0),
            Node(id="w", type=NodeType.WAIT, x=0, y=0,
                 conditions=[
                     Condition("discrete_inputs", 0, "==", 1),
                     Condition("discrete_inputs", 1, "==", 1),
                 ], timeout_ms=10000),
            Node(id="a", type=NodeType.END, x=0, y=0, result="path_a"),
            Node(id="b", type=NodeType.END, x=0, y=0, result="path_b"),
        ],
        edges=[
            Edge("n1", "next", "w"),
            Edge("w", "cond_0", "a"),
            Edge("w", "cond_1", "b"),
        ],
    )
    engine = make_engine(seq, store, clock)
    reasons = collect_finished(engine)
    engine.start()
    engine._tick()  # START -> w
    engine._tick()  # no condition met yet -> stays
    assert reasons == []
    store.write("discrete_inputs", 1, 1)  # 두 번째 조건 충족
    engine._tick()  # -> cond_1 -> b
    engine._tick()  # END path_b
    assert reasons == ["path_b"]


def test_wait_timeout_branch(qapp) -> None:
    store, clock = FakeStore(), FakeClock()
    seq = Sequence(
        nodes=[
            Node(id="n1", type=NodeType.START, x=0, y=0),
            Node(id="w", type=NodeType.WAIT, x=0, y=0,
                 conditions=[Condition("coils", 0, "==", 1)], timeout_ms=1000),
            Node(id="t", type=NodeType.END, x=0, y=0, result="timed_out"),
        ],
        edges=[
            Edge("n1", "next", "w"),
            Edge("w", "timeout", "t"),
        ],
    )
    engine = make_engine(seq, store, clock)
    reasons = collect_finished(engine)
    engine.start()
    engine._tick()  # START -> w
    engine._tick()  # not met, not yet timed out
    assert reasons == []
    clock.advance(1.5)  # 1.5s 경과 > 1000ms
    engine._tick()  # -> timeout -> t
    engine._tick()  # END
    assert reasons == ["timed_out"]


def test_branch_value_based(qapp) -> None:
    store, clock = FakeStore(), FakeClock()
    store.write("holding_registers", 10, 2)
    seq = Sequence(
        nodes=[
            Node(id="n1", type=NodeType.START, x=0, y=0),
            Node(id="br", type=NodeType.BRANCH, x=0, y=0,
                 branch_reg_type="holding_registers", branch_addr=10, cases=[1, 2]),
            Node(id="c0", type=NodeType.END, x=0, y=0, result="case1"),
            Node(id="c1", type=NodeType.END, x=0, y=0, result="case2"),
            Node(id="el", type=NodeType.END, x=0, y=0, result="else"),
        ],
        edges=[
            Edge("n1", "next", "br"),
            Edge("br", "case_0", "c0"),
            Edge("br", "case_1", "c1"),
            Edge("br", "else", "el"),
        ],
    )
    engine = make_engine(seq, store, clock)
    reasons = collect_finished(engine)
    engine.start()
    engine._tick()  # START -> br
    engine._tick()  # value 2 == cases[1] -> case_1 -> c1
    engine._tick()  # END
    assert reasons == ["case2"]


def test_delay_elapses(qapp) -> None:
    store, clock = FakeStore(), FakeClock()
    seq = Sequence(
        nodes=[
            Node(id="n1", type=NodeType.START, x=0, y=0),
            Node(id="d", type=NodeType.DELAY, x=0, y=0, delay_ms=500),
            Node(id="e", type=NodeType.END, x=0, y=0, result="done"),
        ],
        edges=[Edge("n1", "next", "d"), Edge("d", "next", "e")],
    )
    engine = make_engine(seq, store, clock)
    reasons = collect_finished(engine)
    engine.start()
    engine._tick()  # START -> d
    engine._tick()  # delay not elapsed
    assert reasons == []
    clock.advance(0.6)
    engine._tick()  # -> next -> e
    engine._tick()  # END
    assert reasons == ["done"]


def test_dangling_port_finishes(qapp) -> None:
    store, clock = FakeStore(), FakeClock()
    seq = Sequence(
        nodes=[
            Node(id="n1", type=NodeType.START, x=0, y=0),
            Node(id="s", type=NodeType.SEND, x=0, y=0, writes=[]),
        ],
        edges=[Edge("n1", "next", "s")],  # s 의 "next" 미연결
    )
    engine = make_engine(seq, store, clock)
    reasons = collect_finished(engine)
    engine.start()
    engine._tick()  # START -> s
    engine._tick()  # SEND -> next 미연결 -> finished
    assert len(reasons) == 1 and reasons[0].startswith("dangling")
```

- [ ] **Step 2: 테스트 실패 확인**

Run:
```bash
uv run pytest tests/test_sequence_engine.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'sequence.sequence_engine'`

- [ ] **Step 3: 엔진 구현**

Create `sequence/sequence_engine.py`:
```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run:
```bash
uv run pytest tests/test_sequence_engine.py -q
```
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add sequence/sequence_engine.py tests/test_sequence_engine.py
git commit -m "feat: 시퀀스 실행 엔진(상태머신) 추가"
```

---

## Task 4: 노드 그래프 에디터 (`sequence_editor.py`)

GUI 컴포넌트는 자동 단위 테스트가 어렵다. 임포트 가능성 스모크 테스트만 자동화하고, 실제 동작은 Task 8에서 수동 검증한다.

**Files:**
- Create: `sequence/sequence_editor.py`
- Test: `tests/test_sequence_gui_smoke.py`

- [ ] **Step 1: 포트 규약 헬퍼 + 노드 색상 정의 작성**

Create `sequence/sequence_editor.py` (1부 — 상단 임포트와 헬퍼):
```python
"""비주얼 노드 그래프 에디터.

QGraphicsScene/QGraphicsView 로 노드와 연결을 그린다. 좌측 팔레트로 노드를
추가하고, 출력 포트에서 입력 포트로 드래그해 연결하며, 노드를 선택하면 우측
속성 패널에서 config 를 편집한다.
"""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QlistWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from sequence.sequence_model import (
    REGISTER_TYPES,
    Condition,
    Edge,
    Node,
    NodeType,
    Sequence,
    WriteAction,
)

logger = logging.getLogger("ModbusServerSim")

NODE_COLORS: dict[NodeType, QColor] = {
    NodeType.START: QColor(40, 140, 40),
    NodeType.SEND: QColor(47, 108, 146),
    NodeType.WAIT: QColor(210, 140, 20),
    NodeType.BRANCH: QColor(140, 40, 140),
    NodeType.DELAY: QColor(90, 90, 120),
    NodeType.END: QColor(160, 40, 40),
}

NODE_W = 140.0
NODE_H = 70.0
PORT_R = 6.0


def output_ports(node: Node) -> list[str]:
    """노드 타입에 따른 출력 포트 키 목록을 반환한다."""
    if node.type in (NodeType.START, NodeType.SEND, NodeType.DELAY):
        return ["next"]
    if node.type is NodeType.WAIT:
        ports = [f"cond_{i}" for i in range(len(node.conditions))]
        ports.append("timeout")
        return ports
    if node.type is NodeType.BRANCH:
        ports = [f"case_{i}" for i in range(len(node.cases))]
        ports.append("else")
        return ports
    return []  # END
```

- [ ] **Step 2: NodeItem / EdgeItem 그래픽 아이템 작성**

Append to `sequence/sequence_editor.py` (2부):
```python
class EdgeItem(QGraphicsPathItem):
    """두 노드 포트를 잇는 베지어 곡선."""

    def __init__(self, edge: Edge) -> None:
        super().__init__()
        self.edge = edge
        self.setPen(QPen(QColor(200, 200, 200), 2))
        self.setZValue(-1)

    def update_path(self, p1: QPointF, p2: QPointF) -> None:
        """시작점 p1 과 끝점 p2 사이 베지어 경로를 갱신한다."""
        path = QPainterPath(p1)
        dx = max(40.0, abs(p2.x() - p1.x()) * 0.5)
        path.cubicTo(p1.x() + dx, p1.y(), p2.x() - dx, p2.y(), p2.x(), p2.y())
        self.setPath(path)


class NodeItem(QGraphicsItem):
    """하나의 노드를 표현하는 그래픽 아이템(이동 가능)."""

    def __init__(self, node: Node) -> None:
        super().__init__()
        self.node = node
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setPos(node.x, node.y)
        self._highlight = False

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, NODE_W, NODE_H + 10)

    def set_highlight(self, on: bool) -> None:
        """실행 중 현재 노드 강조 표시를 토글한다."""
        self._highlight = on
        self.update()

    def input_point(self) -> QPointF:
        """입력 포트(좌측 중앙)의 씬 좌표."""
        return self.mapToScene(QPointF(0, NODE_H / 2))

    def output_point(self, port: str) -> QPointF:
        """주어진 출력 포트의 씬 좌표(우측, 포트별 세로 분배)."""
        ports = output_ports(self.node)
        if port not in ports:
            return self.mapToScene(QPointF(NODE_W, NODE_H / 2))
        idx = ports.index(port)
        gap = NODE_H / (len(ports) + 1)
        return self.mapToScene(QPointF(NODE_W, gap * (idx + 1)))

    def paint(self, painter, option, widget=None) -> None:
        color = NODE_COLORS[self.node.type]
        painter.setBrush(QBrush(color))
        pen = QPen(QColor(255, 215, 0), 3) if self._highlight else QPen(QColor(30, 30, 40), 1)
        painter.setPen(pen)
        painter.drawRoundedRect(QRectF(0, 0, NODE_W, NODE_H), 8, 8)
        painter.setPen(QPen(QColor(255, 255, 255)))
        title = self.node.type.value
        painter.drawText(QRectF(4, 4, NODE_W - 8, 20), Qt.AlignmentFlag.AlignLeft, title)
        if self.node.label:
            painter.drawText(QRectF(4, 26, NODE_W - 8, 40),
                             Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, self.node.label)
        # 입력 포트(좌)
        painter.setBrush(QBrush(QColor(220, 220, 220)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(0, NODE_H / 2), PORT_R, PORT_R)
        # 출력 포트(우)
        ports = output_ports(self.node)
        gap = NODE_H / (len(ports) + 1) if ports else NODE_H
        for i, port in enumerate(ports):
            y = gap * (i + 1)
            painter.drawEllipse(QPointF(NODE_W, y), PORT_R, PORT_R)
            painter.setPen(QPen(QColor(230, 230, 230)))
            painter.drawText(QRectF(NODE_W - 56, y - 8, 50, 16),
                             Qt.AlignmentFlag.AlignRight, port)
            painter.setPen(Qt.PenStyle.NoPen)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.node.x = self.pos().x()
            self.node.y = self.pos().y()
            if self.scene() is not None:
                self.scene().refresh_edges()
        return super().itemChange(change, value)
```

- [ ] **Step 3: SequenceScene (노드/엣지 관리 + 포트 드래그 연결) 작성**

Append to `sequence/sequence_editor.py` (3부):
```python
class SequenceScene(QGraphicsScene):
    """노드/엣지 아이템을 보관하고 포트 드래그 연결을 처리한다."""

    selection_changed = Signal(object)  # 선택된 NodeItem 또는 None

    def __init__(self, sequence: Sequence) -> None:
        super().__init__()
        self.sequence = sequence
        self.node_items: dict[str, NodeItem] = {}
        self.edge_items: list[EdgeItem] = []
        self._drag_from: tuple[str, str] | None = None  # (node_id, port)
        self._temp_edge: EdgeItem | None = None
        self.rebuild()
        self.selectionChanged.connect(self._on_selection)

    def rebuild(self) -> None:
        """모델로부터 모든 아이템을 다시 생성한다."""
        self.clear()
        self.node_items.clear()
        self.edge_items.clear()
        for node in self.sequence.nodes:
            item = NodeItem(node)
            self.addItem(item)
            self.node_items[node.id] = item
        for edge in self.sequence.edges:
            self._add_edge_item(edge)
        self.refresh_edges()

    def _add_edge_item(self, edge: Edge) -> None:
        item = EdgeItem(edge)
        self.addItem(item)
        self.edge_items.append(item)

    def refresh_edges(self) -> None:
        """모든 엣지 경로를 양 끝 노드 위치에 맞춰 갱신한다."""
        for item in self.edge_items:
            src = self.node_items.get(item.edge.from_node)
            dst = self.node_items.get(item.edge.to_node)
            if src and dst:
                item.update_path(src.output_point(item.edge.from_port), dst.input_point())

    def _port_at(self, scene_pos: QPointF) -> tuple[str, str] | None:
        """주어진 씬 좌표 근처의 출력 포트 (node_id, port) 를 찾는다."""
        for node_id, item in self.node_items.items():
            for port in output_ports(item.node):
                if (item.output_point(port) - scene_pos).manhattanLength() <= PORT_R * 2:
                    return (node_id, port)
        return None

    def _input_at(self, scene_pos: QPointF) -> str | None:
        """주어진 씬 좌표 근처의 입력 포트를 가진 node_id 를 찾는다."""
        for node_id, item in self.node_items.items():
            if (item.input_point() - scene_pos).manhattanLength() <= PORT_R * 2:
                return node_id
        return None

    def mousePressEvent(self, event) -> None:
        hit = self._port_at(event.scenePos())
        if hit is not None:
            self._drag_from = hit
            self._temp_edge = EdgeItem(Edge(hit[0], hit[1], ""))
            self.addItem(self._temp_edge)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._temp_edge is not None and self._drag_from is not None:
            src = self.node_items[self._drag_from[0]]
            self._temp_edge.update_path(src.output_point(self._drag_from[1]), event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._temp_edge is not None and self._drag_from is not None:
            target = self._input_at(event.scenePos())
            self.removeItem(self._temp_edge)
            self._temp_edge = None
            if target is not None and target != self._drag_from[0]:
                self.connect_ports(self._drag_from[0], self._drag_from[1], target)
            self._drag_from = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def connect_ports(self, from_node: str, from_port: str, to_node: str) -> None:
        """포트 연결을 만든다(같은 출력 포트의 기존 연결은 교체)."""
        self.sequence.edges = [
            e for e in self.sequence.edges
            if not (e.from_node == from_node and e.from_port == from_port)
        ]
        self.edge_items = [
            i for i in self.edge_items
            if not (i.edge.from_node == from_node and i.edge.from_port == from_port)
            or (self.removeItem(i) or False)
        ]
        edge = Edge(from_node, from_port, to_node)
        self.sequence.edges.append(edge)
        self._add_edge_item(edge)
        self.refresh_edges()

    def _on_selection(self) -> None:
        items = [i for i in self.selectedItems() if isinstance(i, NodeItem)]
        self.selection_changed.emit(items[0] if items else None)

    def add_node(self, node: Node) -> None:
        """새 노드를 모델과 씬에 추가한다."""
        self.sequence.nodes.append(node)
        item = NodeItem(node)
        self.addItem(item)
        self.node_items[node.id] = item

    def delete_selected(self) -> None:
        """선택된 노드와 그에 연결된 엣지를 삭제한다."""
        for item in list(self.selectedItems()):
            if isinstance(item, NodeItem):
                nid = item.node.id
                self.sequence.nodes = [n for n in self.sequence.nodes if n.id != nid]
                self.sequence.edges = [
                    e for e in self.sequence.edges if e.from_node != nid and e.to_node != nid
                ]
        self.rebuild()
```

- [ ] **Step 4: 속성 패널 + SequenceEditor 위젯 작성**

Append to `sequence/sequence_editor.py` (4부):
```python
class PropertyPanel(QWidget):
    """선택된 노드의 config 를 편집하는 폼."""

    changed = Signal()  # 편집 발생 시(엣지/그래프 갱신 트리거)

    def __init__(self) -> None:
        super().__init__()
        self._node: Node | None = None
        self._layout = QVBoxLayout(self)
        self._layout.addWidget(QLabel("속성"))
        self.setMaximumWidth(280)

    def _clear(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(1)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def set_node(self, item: NodeItem | None) -> None:
        """선택 노드를 표시한다(None 이면 비움)."""
        self._clear()
        self._node = item.node if item else None
        if self._node is None:
            return
        node = self._node
        label_edit = QLineEdit(node.label)
        label_edit.textChanged.connect(self._on_label_changed)
        self._layout.addWidget(QLabel("라벨"))
        self._layout.addWidget(label_edit)

        if node.type is NodeType.SEND:
            self._build_send(node)
        elif node.type is NodeType.WAIT:
            self._build_wait(node)
        elif node.type is NodeType.BRANCH:
            self._build_branch(node)
        elif node.type is NodeType.DELAY:
            self._build_delay(node)
        elif node.type is NodeType.END:
            self._build_end(node)
        self._layout.addStretch(1)

    def _on_label_changed(self, text: str) -> None:
        if self._node is not None:
            self._node.label = text
            self.changed.emit()

    def _reg_combo(self, current: str | None) -> QComboBox:
        combo = QComboBox()
        combo.addItems(list(REGISTER_TYPES))
        if current in REGISTER_TYPES:
            combo.setCurrentText(current)
        return combo

    def _build_send(self, node: Node) -> None:
        self._layout.addWidget(QLabel("쓰기 동작 (한 줄=하나)"))
        edit = QLineEdit(";".join(f"{w.reg_type},{w.addr},{w.value}" for w in node.writes))
        edit.setPlaceholderText("coils,0,1;holding_registers,5,1234")

        def apply() -> None:
            actions: list[WriteAction] = []
            for chunk in edit.text().split(";"):
                parts = [p.strip() for p in chunk.split(",")]
                if len(parts) == 3 and parts[0] in REGISTER_TYPES:
                    actions.append(WriteAction(parts[0], int(parts[1]), int(parts[2])))
            node.writes = actions
            self.changed.emit()

        edit.editingFinished.connect(apply)
        self._layout.addWidget(edit)

    def _build_wait(self, node: Node) -> None:
        self._layout.addWidget(QLabel("조건 (한 줄=하나, 순서=포트)"))
        edit = QLineEdit(";".join(f"{c.reg_type},{c.addr},{c.op},{c.value}" for c in node.conditions))
        edit.setPlaceholderText("discrete_inputs,0,==,1;coils,1,==,1")

        def apply() -> None:
            conds: list[Condition] = []
            for chunk in edit.text().split(";"):
                parts = [p.strip() for p in chunk.split(",")]
                if len(parts) == 4 and parts[0] in REGISTER_TYPES:
                    conds.append(Condition(parts[0], int(parts[1]), parts[2], int(parts[3])))
            node.conditions = conds
            self.changed.emit()

        edit.editingFinished.connect(apply)
        self._layout.addWidget(edit)
        self._layout.addWidget(QLabel("타임아웃(ms, 0=무한)"))
        spin = QSpinBox()
        spin.setRange(0, 3_600_000)
        spin.setValue(node.timeout_ms or 0)
        spin.valueChanged.connect(lambda v: (setattr(node, "timeout_ms", v or None), self.changed.emit()))
        self._layout.addWidget(spin)

    def _build_branch(self, node: Node) -> None:
        self._layout.addWidget(QLabel("대상 레지스터"))
        combo = self._reg_combo(node.branch_reg_type)
        combo.currentTextChanged.connect(lambda t: (setattr(node, "branch_reg_type", t), self.changed.emit()))
        node.branch_reg_type = node.branch_reg_type or combo.currentText()
        self._layout.addWidget(combo)
        self._layout.addWidget(QLabel("주소"))
        addr = QSpinBox()
        addr.setRange(0, 65535)
        addr.setValue(node.branch_addr or 0)
        addr.valueChanged.connect(lambda v: (setattr(node, "branch_addr", v), self.changed.emit()))
        self._layout.addWidget(addr)
        self._layout.addWidget(QLabel("case 값 (쉼표 구분, 순서=포트)"))
        edit = QLineEdit(",".join(str(v) for v in node.cases))

        def apply() -> None:
            node.cases = [int(p) for p in edit.text().split(",") if p.strip().lstrip("-").isdigit()]
            self.changed.emit()

        edit.editingFinished.connect(apply)
        self._layout.addWidget(edit)

    def _build_delay(self, node: Node) -> None:
        self._layout.addWidget(QLabel("지연(ms)"))
        spin = QSpinBox()
        spin.setRange(0, 3_600_000)
        spin.setValue(node.delay_ms)
        spin.valueChanged.connect(lambda v: (setattr(node, "delay_ms", v), self.changed.emit()))
        self._layout.addWidget(spin)

    def _build_end(self, node: Node) -> None:
        self._layout.addWidget(QLabel("결과 라벨"))
        edit = QLineEdit(node.result)
        edit.textChanged.connect(lambda t: (setattr(node, "result", t), self.changed.emit()))
        self._layout.addWidget(edit)


class SequenceEditor(QWidget):
    """팔레트 + 그래프 뷰 + 속성 패널을 묶은 에디터 위젯."""

    def __init__(self, sequence: Sequence) -> None:
        super().__init__()
        self.sequence = sequence
        self.scene = SequenceScene(sequence)
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHints(self.view.renderHints())
        self.panel = PropertyPanel()

        palette = QVBoxLayout()
        palette.addWidget(QLabel("노드 추가"))
        for ntype in NodeType:
            btn = QPushButton(ntype.value)
            btn.clicked.connect(lambda _=False, t=ntype: self._add(t))
            palette.addWidget(btn)
        del_btn = QPushButton("선택 삭제")
        del_btn.clicked.connect(self.scene.delete_selected)
        palette.addWidget(del_btn)
        palette.addStretch(1)

        root = QHBoxLayout(self)
        left = QWidget()
        left.setLayout(palette)
        left.setMaximumWidth(150)
        root.addWidget(left)
        root.addWidget(self.view, 1)
        root.addWidget(self.panel)

        self.scene.selection_changed.connect(self.panel.set_node)
        self.panel.changed.connect(self._on_panel_changed)
        self._counter = len(sequence.nodes)

    def _add(self, ntype: NodeType) -> None:
        self._counter += 1
        node = Node(id=f"n{self._counter}", type=ntype, x=40.0, y=40.0 + 20 * self._counter)
        self.scene.add_node(node)

    def _on_panel_changed(self) -> None:
        # WAIT/BRANCH 는 포트 수가 바뀔 수 있어 노드 아이템을 다시 그린다.
        sel = [i for i in self.scene.selectedItems() if isinstance(i, NodeItem)]
        for item in sel:
            item.update()
        self.scene.refresh_edges()

    def highlight(self, node_id: str) -> None:
        """실행 중 활성 노드를 강조한다."""
        for nid, item in self.scene.node_items.items():
            item.set_highlight(nid == node_id)
```

- [ ] **Step 5: GUI 스모크 테스트 작성**

Create `tests/test_sequence_gui_smoke.py`:
```python
"""GUI 컴포넌트가 임포트/생성되는지만 확인하는 스모크 테스트.

QWidget 생성에는 QApplication 이 필요하므로 직접 만든다. 디스플레이가 없는
CI 에서는 offscreen 플랫폼이 필요할 수 있다(QT_QPA_PLATFORM=offscreen).
"""

import pytest

from sequence.sequence_model import Edge, Node, NodeType, Sequence


@pytest.fixture(scope="module")
def gui_app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    return app


def test_editor_constructs(gui_app) -> None:
    from sequence.sequence_editor import SequenceEditor

    seq = Sequence(
        nodes=[Node(id="n1", type=NodeType.START, x=0, y=0),
               Node(id="n2", type=NodeType.END, x=200, y=0, result="ok")],
        edges=[Edge("n1", "next", "n2")],
    )
    editor = SequenceEditor(seq)
    assert len(editor.scene.node_items) == 2
    assert len(editor.scene.edge_items) == 1
```

- [ ] **Step 6: 스모크 테스트 실행**

Run:
```bash
QT_QPA_PLATFORM=offscreen uv run pytest tests/test_sequence_gui_smoke.py -q
```
Expected: PASS (1 passed). (Windows PowerShell 에서는 `$env:QT_QPA_PLATFORM='offscreen'; uv run pytest tests/test_sequence_gui_smoke.py -q`)

- [ ] **Step 7: Commit**

```bash
git add sequence/sequence_editor.py tests/test_sequence_gui_smoke.py
git commit -m "feat: 시퀀스 노드 그래프 에디터 위젯 추가"
```

---

## Task 5: 전용 창 (`sequence_window.py`)

**Files:**
- Create: `sequence/sequence_window.py`

- [ ] **Step 1: SequenceWindow 구현**

Create `sequence/sequence_window.py`:
```python
"""시퀀스 에디터와 실행 컨트롤을 담은 전용 창."""

from __future__ import annotations

import json
import logging
import os
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QToolBar,
    QWidget,
    QVBoxLayout,
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
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.log_view)
        self.setCentralWidget(central)
        self._build_toolbar()

    def _build_toolbar(self) -> None:
        bar = QToolBar()
        self.addToolBar(bar)
        bar.addAction("New", self._new)
        bar.addAction("Open", self._open)
        bar.addAction("Save", self._save)
        bar.addAction("Save As", self._save_as)
        bar.addSeparator()
        bar.addAction("Run", self._run)
        bar.addAction("Step", self._step)
        bar.addAction("Stop", self._stop)

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
        self._engine.start()

    def _step(self) -> None:
        if self._engine is None or not self._engine.running:
            self._run()
        elif self._engine is not None:
            self._engine.step()

    def _stop(self) -> None:
        if self._engine is not None and self._engine.running:
            self._engine.stop()

    def _on_finished(self, reason: str) -> None:
        self.log(f"=== 종료: {reason} ===")

    def log(self, text: str) -> None:
        """로그뷰에 한 줄 추가한다."""
        self.log_view.appendPlainText(text)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 시그니처)
        self._stop()
        super().closeEvent(event)
```

- [ ] **Step 2: 창 단독 실행 확인**

Run (PowerShell):
```powershell
$env:QT_QPA_PLATFORM='offscreen'; uv run python -c "from PySide6.QtWidgets import QApplication; from sequence.sequence_window import SequenceWindow; app=QApplication([]); w=SequenceWindow(lambda t,a:0, lambda t,a,v:None); print('window ok', w.windowTitle())"
```
Expected: `window ok 시퀀스 시뮬레이션`

- [ ] **Step 3: Commit**

```bash
git add sequence/sequence_window.py
git commit -m "feat: 시퀀스 전용 창(에디터+실행 컨트롤) 추가"
```

---

## Task 6: 메인 앱 통합 (`modbus_tcp_server.py`)

**Files:**
- Modify: `modbus_tcp_server.py` (어댑터 메서드 + 버튼 + 핸들러 추가)

- [ ] **Step 1: store 접근 어댑터 메서드 추가**

`ModbusServerSimulator` 클래스에 메서드 추가 (예: `on_register_value_changed` 메서드 바로 아래, 약 line 1508 이후):
```python
    # --- 시퀀스 엔진 연동 어댑터 ---
    def read_register(self, reg_type: str, addr: int) -> int:
        """시퀀스 엔진용: 레지스터 타입/주소의 현재 값을 store 에서 읽는다.

        Args:
            reg_type: "coils"|"discrete_inputs"|"holding_registers"|"input_registers".
            addr: 레지스터 주소.

        Returns:
            정수 값(읽기 실패 시 0).
        """
        fc = {"coils": 1, "discrete_inputs": 2, "holding_registers": 3, "input_registers": 4}.get(reg_type)
        if fc is None:
            logger.error(f"read_register: 알 수 없는 레지스터 타입 {reg_type}")
            return 0
        try:
            return int(self.store.getValues(fc, addr, 1)[0])
        except (IndexError, ValueError, TypeError) as exc:
            logger.error(f"read_register 실패 ({reg_type}[{addr}]): {exc}")
            return 0

    def engine_write(self, reg_type: str, addr: int, value: int) -> None:
        """시퀀스 엔진용: 레지스터에 값을 쓴다(UI/저장과 동기화).

        on_register_value_changed 로 위임하여 외부 클라이언트 쓰기로 오탐되지
        않게 하고, 해당 위젯 표시도 즉시 갱신한다.
        """
        self.on_register_value_changed(reg_type, addr, value)
        widget = getattr(self, f"{reg_type}_widget", None)
        if widget is not None:
            try:
                widget.update_value(addr, value)
            except (KeyError, AttributeError) as exc:
                logger.debug(f"engine_write 위젯 갱신 생략 ({reg_type}[{addr}]): {exc}")

    @Slot()
    def open_sequence_window(self) -> None:
        """시퀀스 시뮬레이션 창을 연다(단일 인스턴스 재사용)."""
        from sequence.sequence_window import SequenceWindow

        if getattr(self, "_sequence_window", None) is None:
            self._sequence_window = SequenceWindow(self.read_register, self.engine_write, parent=self)
        self._sequence_window.show()
        self._sequence_window.raise_()
        self._sequence_window.activateWindow()
```

- [ ] **Step 2: "시퀀스 시뮬레이션" 버튼 추가**

`init_ui` 의 옵션 레이아웃(약 line 951~965, `options_layout` 구성부) 끝의 `main_layout.addLayout(options_layout)` 직전에 버튼을 추가:
```python
        # 시퀀스 시뮬레이션 창 열기 버튼
        self.sequence_button = QPushButton("시퀀스 시뮬레이션")
        self.sequence_button.setObjectName("sequence_button")
        self.sequence_button.setToolTip("노드 그래프로 신호 전송/대기/분기 시퀀스를 편집·실행합니다")
        self.sequence_button.clicked.connect(self.open_sequence_window)
        options_layout.addWidget(self.sequence_button)
```
그리고 `__init__` 에서 단일 인스턴스 보관용 속성 초기화를 `self.server_thread = None` 근처(약 line 571)에 추가:
```python
        self._sequence_window = None
```

- [ ] **Step 3: 임포트 가능 및 어댑터 동작 확인 (수동 통합 테스트)**

Run (PowerShell):
```powershell
$env:QT_QPA_PLATFORM='offscreen'; uv run python -c "import modbus_tcp_server as m; from PySide6.QtWidgets import QApplication; app=QApplication([]); w=m.ModbusServerSimulator(); w.engine_write('holding_registers', 5, 4660); print('read back', w.read_register('holding_registers', 5))"
```
Expected: `read back 4660` (0x1234). 콘솔/로그에 예외 없음.

- [ ] **Step 4: Commit**

```bash
git add modbus_tcp_server.py
git commit -m "feat: 메인 앱에 시퀀스 시뮬레이션 창 연동(어댑터+버튼)"
```

---

## Task 7: 전체 회귀 테스트

**Files:** (없음 — 검증만)

- [ ] **Step 1: 전체 테스트 실행**

Run (PowerShell):
```powershell
$env:QT_QPA_PLATFORM='offscreen'; uv run pytest -q
```
Expected: 모든 테스트 PASS (model 6 + engine 6 + smoke 1 = 13 passed).

- [ ] **Step 2: 기존 앱 정상 임포트 확인(회귀)**

Run (PowerShell):
```powershell
$env:QT_QPA_PLATFORM='offscreen'; uv run python -c "import modbus_tcp_server; print('import ok')"
```
Expected: `import ok` (기존 기능 임포트 회귀 없음).

---

## Task 8: 수동 엔드투엔드 검증

자동화하기 어려운 GUI 상호작용을 사람이 직접 확인한다.

- [ ] **Step 1: 앱 실행**

Run:
```bash
uv run python modbus_tcp_server.py
```

- [ ] **Step 2: 시퀀스 시나리오 작성·실행 (체크리스트)**

다음을 GUI에서 직접 수행하여 확인한다:
1. "시퀀스 시뮬레이션" 버튼 → 창이 열린다.
2. 팔레트로 SEND, WAIT, BRANCH, DELAY, END 노드를 추가한다.
3. 출력 포트에서 입력 포트로 드래그하여 노드를 연결한다(베지어 곡선 표시).
4. 노드 클릭 → 우측 속성 패널에서 config 편집(예: SEND `coils,0,1`; WAIT `discrete_inputs,0,==,1` timeout 5000).
5. Save → `modbus_sequence.json` 생성 확인. 창을 닫았다 다시 열면 복원됨.
6. 메인 창에서 Connect(서버 시작) 후 Run → 현재 노드가 노란 테두리로 하이라이트되고 로그가 쌓인다.
7. WAIT 단계에서 메인 창의 디스크릿 입력 체크박스를 켜거나 외부 Modbus 클라이언트로 값을 쓰면 해당 cond 포트로 분기한다.
8. 타임아웃/Stop 동작 확인.

- [ ] **Step 3: 최종 커밋(필요 시 문서/스크린샷 정리)**

```bash
git add -A
git commit -m "docs: 시퀀스 시뮬레이션 수동 검증 완료"
```

---

## Self-Review 결과

- **Spec 커버리지:** 신호=레지스터값(read/engine_write 어댑터, Task 6), 능동/자가 둘 다(폴링 기반 WAIT, Task 3), 다중조건 선착순(WAIT cond_i, Task 3), 값 기반(BRANCH, Task 3), 타임아웃(WAIT timeout, Task 3), 노드 그래프 편집(Task 4·5), 별도 JSON 저장(Task 5) — 모두 태스크로 매핑됨.
- **Placeholder:** 없음. 각 코드 스텝에 실제 코드 포함.
- **타입 일관성:** `Node`/`Edge`/`Condition`/`WriteAction` 필드명, `output_ports()` 포트 키 규약(`next`/`cond_i`/`timeout`/`case_i`/`else`), 엔진 시그널명(`node_activated`/`step_logged`/`finished`), 어댑터명(`read_register`/`engine_write`)이 Task 2~6 전반에서 일치.
- **주의:** `SequenceScene.connect_ports` 의 리스트 컴프리헨션에 부작용(removeItem) 사용은 가독성이 낮으므로 구현 시 명시적 루프로 바꿔도 무방(동작 동일 유지).

"""SequenceEngine 노드별 동작 테스트.

QTimer 를 돌리지 않고 engine._tick() 를 직접 호출해 결정적으로 검증한다.
시간 의존(DELAY/timeout)은 가짜 클록(FakeClock)으로 제어한다.
"""

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

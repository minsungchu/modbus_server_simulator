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

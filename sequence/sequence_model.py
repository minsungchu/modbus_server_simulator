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

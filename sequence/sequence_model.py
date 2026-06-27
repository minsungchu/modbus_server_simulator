"""시퀀스 노드/연결 데이터모델과 JSON 직렬화.

신호(signal)는 Modbus 레지스터 값으로 정의된다. 노드는 START/SEND/WAIT/
BRANCH/DELAY/END 6종이며, 출력 포트는 다음 규약을 따른다.

- START / SEND / DELAY: 단일 출력 포트 ``"next"``
- WAIT: 조건 i 마다 ``"cond_{i}"``, 타임아웃 시 ``"timeout"``
- BRANCH: case i 마다 ``"case_{i}"``, 그 외 ``"else"``
- END: 컨테이너(반복 그룹) 안에서는 ``"next"`` 로 이어짐, 최상위 END 는 종료

반복은 그룹(Group)의 mode 속성으로 표현한다. mode 가 loop/iter 인 그룹은 그 영역
안의 서브 시퀀스(자체 START~END)를 반복 실행하는 컨테이너가 되고, none 이면 순수한
시각적 그룹이다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("ModbusServerSim")

BIT_TYPES = ("coils", "discrete_inputs")
REGISTER_TYPES = ("coils", "discrete_inputs", "holding_registers", "input_registers")
OPERATORS = ("==", "!=", ">", "<", ">=", "<=")

# SEND 쓰기 연산: 지정(set, =) / 더하기(add, +=) / 빼기(sub, -=)
WRITE_OPS = ("set", "add", "sub")
WRITE_OP_SYMBOLS = {"set": "=", "add": "+=", "sub": "-="}


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
    """SEND 노드가 수행하는 단일 쓰기 동작.

    op 에 따라 동작이 달라진다.
    - "set": 해당 번지에 value 를 그대로 쓴다(=).
    - "add": 현재 값에 value 를 더해서 쓴다(+=). 루프와 함께 쓰면 누적된다.
    - "sub": 현재 값에서 value 를 빼서 쓴다(-=).
    """

    reg_type: str
    addr: int
    value: int
    op: str = "set"

    def __post_init__(self) -> None:
        if self.op not in WRITE_OPS:
            raise ValueError(f"지원하지 않는 쓰기 연산: {self.op}")
        self.addr = int(self.addr)
        self.value = clamp_value(self.reg_type, self.value)

    def to_dict(self) -> dict:
        """딕셔너리로 직렬화한다."""
        return {"reg_type": self.reg_type, "addr": self.addr, "value": self.value, "op": self.op}

    @classmethod
    def from_dict(cls, data: dict) -> "WriteAction":
        """딕셔너리에서 역직렬화한다."""
        return cls(reg_type=data["reg_type"], addr=int(data["addr"]),
                   value=int(data["value"]), op=data.get("op", "set"))


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
    enabled: bool = True                                          # False 면 실행 제외(주로 START)

    def to_dict(self) -> dict:
        """딕셔너리로 직렬화한다(사용되는 필드만 포함)."""
        data: dict = {"id": self.id, "type": self.type.value, "x": self.x, "y": self.y, "label": self.label}
        if not self.enabled:  # 활성 노드가 기본 → 비활성일 때만 저장(기존 파일 호환)
            data["enabled"] = False
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
            enabled=bool(data.get("enabled", True)),
        )


@dataclass
class Edge:
    """노드 출력 포트 → 대상 노드 연결."""

    from_node: str
    from_port: str
    to_node: str
    # 연결점(변)을 명시 지정할 때 사용. None 이면 가장 가까운 변으로 자동 선택.
    from_side: str | None = None   # 출력 변: "bottom" / "right"
    to_side: str | None = None     # 입력 변: "top" / "left"

    def to_dict(self) -> dict:
        """딕셔너리로 직렬화한다(명시 지정된 변만 포함)."""
        data = {"from_node": self.from_node, "from_port": self.from_port, "to_node": self.to_node}
        if self.from_side is not None:
            data["from_side"] = self.from_side
        if self.to_side is not None:
            data["to_side"] = self.to_side
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Edge":
        """딕셔너리에서 역직렬화한다."""
        return cls(from_node=data["from_node"], from_port=data["from_port"], to_node=data["to_node"],
                   from_side=data.get("from_side"), to_side=data.get("to_side"))


GROUP_MODES = ("none", "loop", "iter")  # 없음 / 반복(N회) / 배열 순회


@dataclass
class Group:
    """여러 노드를 묶는 자유 크기 영역(프레임) 겸 반복 컨테이너.

    영역은 (x, y, w, h) 사각형으로 저장되며 사용자가 자유롭게 위치/크기를
    조절할 수 있다. 어떤 노드가 그룹에 속하는지는 노드 중심이 이 영역 안에
    있는지로 판정한다.

    mode 에 따라 동작이 달라진다.
    - "none": 순수 시각적 그룹(실행에 영향 없음).
    - "loop": 영역 안 서브 시퀀스(자체 START~END)를 loop_count 회 반복.
    - "iter": iter_values 길이만큼 반복하며, 매 반복마다 그 값을 대상 번지에 씀.
    """

    id: str
    label: str = ""
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    color: str = "#3b82f6"
    mode: str = "none"
    loop_count: int = 1
    iter_values: list[int] = field(default_factory=list)
    iter_reg_type: str | None = None
    iter_addr: int | None = None
    members: list[str] = field(default_factory=list)  # 구버전 호환(영역 미지정 시 계산용)

    def to_dict(self) -> dict:
        """딕셔너리로 직렬화한다."""
        return {
            "id": self.id, "label": self.label,
            "x": self.x, "y": self.y, "w": self.w, "h": self.h,
            "color": self.color, "mode": self.mode,
            "loop_count": self.loop_count,
            "iter_values": list(self.iter_values),
            "iter_reg_type": self.iter_reg_type, "iter_addr": self.iter_addr,
            "members": list(self.members),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Group":
        """딕셔너리에서 역직렬화한다."""
        return cls(
            id=data["id"],
            label=data.get("label", ""),
            x=float(data.get("x", 0.0)),
            y=float(data.get("y", 0.0)),
            w=float(data.get("w", 0.0)),
            h=float(data.get("h", 0.0)),
            color=data.get("color", "#3b82f6"),
            mode=data.get("mode", "none"),
            loop_count=int(data.get("loop_count", 1)),
            iter_values=[int(v) for v in data.get("iter_values", [])],
            iter_reg_type=data.get("iter_reg_type"),
            iter_addr=data.get("iter_addr"),
            members=[str(m) for m in data.get("members", [])],
        )


@dataclass
class TextNote:
    """시퀀스 실행과 무관한 자유 텍스트 메모(주석/설명)."""

    id: str
    text: str = ""
    x: float = 0.0
    y: float = 0.0
    w: float = 220.0
    h: float = 80.0

    def to_dict(self) -> dict:
        """딕셔너리로 직렬화한다."""
        return {"id": self.id, "text": self.text,
                "x": self.x, "y": self.y, "w": self.w, "h": self.h}

    @classmethod
    def from_dict(cls, data: dict) -> "TextNote":
        """딕셔너리에서 역직렬화한다."""
        return cls(
            id=data["id"],
            text=data.get("text", ""),
            x=float(data.get("x", 0.0)),
            y=float(data.get("y", 0.0)),
            w=float(data.get("w", 220.0)),
            h=float(data.get("h", 80.0)),
        )


@dataclass
class Sequence:
    """노드와 연결로 구성된 전체 시퀀스."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    notes: list[TextNote] = field(default_factory=list)
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

    def _loop_group_of(self, node: Node) -> "Group | None":
        """노드를 포함하는 가장 안쪽 반복(loop/iter) 그룹(없으면 None)."""
        best, best_area = None, None
        for g in self.groups:
            if g.mode in ("loop", "iter") and g.w > 0 and g.h > 0:
                if g.x <= node.x <= g.x + g.w and g.y <= node.y <= g.y + g.h:
                    area = g.w * g.h
                    if best is None or area < best_area:
                        best, best_area = g, area
        return best

    def validate(self) -> list[str]:
        """실행 전 정합성을 검사하고 오류 메시지 목록을 반환한다(빈 목록=정상).

        반복 그룹은 명시적 START/END 노드 없이 동작한다. 영역 안에 본문 노드가
        하나라도 있으면 그 노드를 진입점으로 삼아 지정 횟수만큼 반복한다. 따라서
        최상위(그룹 밖) START 가 하나 이상 있는지와, 각 반복 그룹이 비어 있지
        않은지만 확인한다.
        """
        errors: list[str] = []
        starts = [n for n in self.nodes if n.type is NodeType.START]
        top_starts = [n for n in starts if self._loop_group_of(n) is None]
        active_top = [n for n in top_starts if n.enabled]
        if len(top_starts) == 0:
            errors.append("최상위 START 노드가 없습니다.")
        elif len(active_top) == 0:
            errors.append("활성화된 최상위 START 노드가 없습니다(모두 비활성).")
        # 최상위 START 가 여러 개면 각 흐름을 동시에 실행한다(허용).
        for g in self.groups:
            if g.mode in ("loop", "iter"):
                inner = [n for n in self.nodes if self._loop_group_of(n) is g]
                if not inner:
                    errors.append(f"반복 그룹 '{g.label or g.id}' 안에 노드가 없습니다.")

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
            "groups": [g.to_dict() for g in self.groups],
            "notes": [t.to_dict() for t in self.notes],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Sequence":
        """딕셔너리에서 역직렬화한다.

        지원하지 않는 노드 타입(예: 구버전 LOOP/ITER)은 전체 로드를 실패시키지 않고
        건너뛴다. 해당 노드를 가리키는 연결도 함께 제거한다.
        """
        nodes: list[Node] = []
        for n in data.get("nodes", []):
            try:
                nodes.append(Node.from_dict(n))
            except (ValueError, KeyError) as exc:
                logger.warning(f"알 수 없는 노드 건너뜀({n.get('type')!r}): {exc}")
        valid_ids = {n.id for n in nodes}
        edges = [Edge.from_dict(e) for e in data.get("edges", [])
                 if e.get("from_node") in valid_ids and e.get("to_node") in valid_ids]
        return cls(
            version=int(data.get("version", 1)),
            nodes=nodes,
            edges=edges,
            groups=[Group.from_dict(g) for g in data.get("groups", [])],
            notes=[TextNote.from_dict(t) for t in data.get("notes", [])],
        )

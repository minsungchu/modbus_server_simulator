"""비주얼 노드 그래프 에디터.

QGraphicsScene/QGraphicsView 로 노드와 연결을 그린다. 좌측 팔레트로 노드를
추가하고, 출력 포트에서 입력 포트로 드래그해 연결하며, 노드를 선택하면 우측
속성 패널에서 config 를 편집한다.
"""

from __future__ import annotations

import logging

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
        painter.drawText(QRectF(4, 4, NODE_W - 8, 20), Qt.AlignmentFlag.AlignLeft, self.node.type.value)
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
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(220, 220, 220)))
            painter.drawEllipse(QPointF(NODE_W, y), PORT_R, PORT_R)
            painter.setPen(QPen(QColor(230, 230, 230)))
            painter.drawText(QRectF(NODE_W - 60, y - 8, 54, 16),
                             Qt.AlignmentFlag.AlignRight, port)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.node.x = self.pos().x()
            self.node.y = self.pos().y()
            scene = self.scene()
            if isinstance(scene, SequenceScene):
                scene.refresh_edges()
        return super().itemChange(change, value)


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
        # 같은 출력 포트의 기존 엣지를 모델/씬에서 제거
        self.sequence.edges = [
            e for e in self.sequence.edges
            if not (e.from_node == from_node and e.from_port == from_port)
        ]
        remaining: list[EdgeItem] = []
        for item in self.edge_items:
            if item.edge.from_node == from_node and item.edge.from_port == from_port:
                self.removeItem(item)
            else:
                remaining.append(item)
        self.edge_items = remaining

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
        self._layout.addWidget(QLabel(f"노드: {node.type.value} ({node.id})"))
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
        self._layout.addWidget(QLabel("쓰기 동작 (세미콜론 구분)"))
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
        self._layout.addWidget(QLabel("조건 (세미콜론 구분, 순서=포트)"))
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
        node.branch_reg_type = node.branch_reg_type or combo.currentText()
        combo.currentTextChanged.connect(lambda t: (setattr(node, "branch_reg_type", t), self.changed.emit()))
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
        for item in self.scene.node_items.values():
            item.update()
        self.scene.refresh_edges()

    def highlight(self, node_id: str) -> None:
        """실행 중 활성 노드를 강조한다."""
        for nid, item in self.scene.node_items.items():
            item.set_highlight(nid == node_id)

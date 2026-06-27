"""노드 배치 관련 동작 테스트: 겹침 판정, 자석 정렬(스냅), 다중 정렬/간격.

애니메이션은 QVariantAnimation 기반이라, 테스트에서는 currentTime 을 끝으로
보내 보간을 즉시 마무리(t=1)한 뒤 결과 좌표를 검증한다.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF

from sequence.sequence_editor import NODE_W, EdgeItem, SequenceScene, TextNoteItem
from sequence.sequence_model import Edge, Group, Node, NodeType, Sequence, TextNote


def _seq(*coords: tuple[float, float]) -> SequenceScene:
    """주어진 좌표마다 START 노드 하나씩 가진 시퀀스의 씬을 만든다."""
    nodes = [Node(id=f"n{i}", type=NodeType.START, x=x, y=y)
             for i, (x, y) in enumerate(coords)]
    return SequenceScene(Sequence(nodes=nodes))


def _finish_anim(scene: SequenceScene) -> None:
    """진행 중인 트윈 애니메이션을 끝(t=1)으로 보내 결과를 확정한다."""
    anim = scene._node_anim
    assert anim is not None, "애니메이션이 시작되지 않았습니다"
    anim.setCurrentTime(anim.duration())


def test_node_overlap_detected(qapp) -> None:
    scene = _seq((0, 0), (50, 0))  # x 범위가 겹침
    b = scene.node_items["n1"]
    assert scene._node_has_conflict(b) is True
    b.setPos(QPointF(600, 600))    # 멀리 떨어뜨리면 겹침 없음
    assert scene._node_has_conflict(b) is False


def test_drag_snaps_to_left_edge(qapp) -> None:
    scene = _seq((0, 0), (100, 300))
    b = scene.node_items["n1"]
    b.setSelected(True)            # 단일 선택 → 스냅 활성
    scene.begin_interactive_drag(kind="node")
    snapped = scene.snap_position(b, QPointF(3.0, 300.0))  # n0 좌변(0)에 근접
    assert abs(snapped.x() - 0.0) < 1e-6   # 좌-좌 정렬로 스냅
    assert abs(snapped.y() - 300.0) < 1e-6  # y 는 정렬 대상 없음 → 유지


def test_snap_guides_only_after_drag_threshold(qapp) -> None:
    scene = _seq((0, 0), (0, 300))   # 두 노드의 좌변이 이미 정렬됨(x=0)
    b = scene.node_items["n1"]
    b.setSelected(True)
    scene.begin_interactive_drag(kind="node")
    scene.snap_position(b, QPointF(0.0, 300.0))   # 움직이지 않은 클릭
    assert scene._snap_guides == []               # 정렬돼 있어도 가이드 없음
    scene.snap_position(b, QPointF(2.0, 300.0))   # 임계값 미만 미세 이동
    assert scene._snap_guides == []
    scene.snap_position(b, QPointF(0.0, 312.0))   # 충분히 끌면 + 정렬되면
    assert scene._snap_guides != []               # 가이드 표시
    scene.end_interactive_drag()
    assert scene._snap_guides == []               # 드래그 종료 시 제거


def test_no_snap_when_far(qapp) -> None:
    scene = _seq((0, 0), (100, 300))
    b = scene.node_items["n1"]
    b.setSelected(True)
    scene.begin_interactive_drag(kind="node")
    snapped = scene.snap_position(b, QPointF(40.0, 300.0))  # 임계(7px) 밖
    assert abs(snapped.x() - 40.0) < 1e-6   # 스냅 안 됨


def test_align_left(qapp) -> None:
    scene = _seq((0, 0), (50, 0), (200, 0))
    for it in scene.node_items.values():
        it.setSelected(True)
    scene.align_selected("left")
    _finish_anim(scene)
    xs = [round(it.pos().x()) for it in scene.node_items.values()]
    assert xs == [0, 0, 0]


def test_distribute_horizontal_equal_gaps(qapp) -> None:
    scene = _seq((0, 0), (250, 0), (600, 0))
    for it in scene.node_items.values():
        it.setSelected(True)
    scene.distribute_selected("h")
    _finish_anim(scene)
    xs = sorted(round(it.pos().x()) for it in scene.node_items.values())
    # span=(600+NODE_W)-0, gap=(span-3*NODE_W)/2 → 가장자리 간격 균등
    gap = ((600 + NODE_W) - 3 * NODE_W) / 2
    assert xs == [0, round(NODE_W + gap), round(2 * (NODE_W + gap))]


def _seq_with_group() -> SequenceScene:
    """그룹 영역(200,100,184,220) 안에 멤버 노드 1개를 둔 씬."""
    member = Node(id="m0", type=NodeType.START, x=230.0, y=150.0)
    group = Group(id="g0", label="G", x=200.0, y=100.0, w=184.0, h=220.0,
                  members=["m0"])
    return SequenceScene(Sequence(nodes=[member], groups=[group]))


def test_node_drag_moves_contents_only_not_group(qapp) -> None:
    member = Node(id="m0", type=NodeType.START, x=230.0, y=150.0)
    member2 = Node(id="m1", type=NodeType.START, x=230.0, y=240.0)
    group = Group(id="g0", label="G", x=200.0, y=100.0, w=184.0, h=260.0,
                  members=["m0", "m1"])
    scene = SequenceScene(Sequence(nodes=[member, member2], groups=[group]))
    for it in scene.node_items.values():
        it.setSelected(True)
    scene.begin_interactive_drag(kind="node")
    gi = scene.group_items[0]
    start_x, start_y = gi._rect.x(), gi._rect.y()
    for it in scene.node_items.values():           # 노드 드래그(Qt 다중 이동) 모사
        it.setPos(it.pos() + QPointF(40.0, 25.0))
    # 노드 드래그는 내용물만 옮기고, 그룹 프레임은 그대로 둔다.
    assert abs(gi._rect.x() - start_x) < 1e-6
    assert abs(gi._rect.y() - start_y) < 1e-6


def test_align_inside_single_selected_group(qapp) -> None:
    # 한 그룹 안에 좌변이 어긋난 노드 2개 → 그룹만 선택하고 좌측 정렬
    m0 = Node(id="m0", type=NodeType.START, x=220.0, y=130.0)
    m1 = Node(id="m1", type=NodeType.START, x=260.0, y=230.0)
    group = Group(id="g0", label="G", x=200.0, y=100.0, w=200.0, h=260.0,
                  members=["m0", "m1"])
    scene = SequenceScene(Sequence(nodes=[m0, m1], groups=[group]))
    gi = scene.group_items[0]
    grect = QRectF(gi._rect)
    gi.setSelected(True)                            # 그룹 하나만 선택
    assert scene.alignment_unit_count() == 2        # 내부 노드 수로 환산
    scene.align_selected("left")
    _finish_anim(scene)
    xs = [round(scene.node_items[i].pos().x()) for i in ("m0", "m1")]
    assert xs == [220, 220]                         # 내부 노드끼리 좌측 정렬
    assert gi._rect == grect                         # 그룹 테두리는 그대로


def test_align_group_as_unit_keeps_member_offset(qapp) -> None:
    scene = _seq_with_group()
    scene.add_node(Node(id="ext", type=NodeType.START, x=50.0, y=400.0))
    gi = scene.group_items[0]
    member = scene.node_items["m0"]
    offset = member.pos().x() - gi._rect.x()        # 그룹 대비 멤버 상대 위치(=30)
    gi.setSelected(True)
    scene.node_items["ext"].setSelected(True)
    scene.align_selected("left")                    # 단위: 그룹 박스 + 외부 노드
    _finish_anim(scene)
    assert abs(gi._rect.x() - 50.0) < 1e-6          # 그룹 좌변이 외부 노드(50)에 정렬
    assert abs(scene.node_items["ext"].pos().x() - 50.0) < 1e-6
    assert abs((member.pos().x() - gi._rect.x()) - offset) < 1e-6  # 상대 위치 유지


def test_text_note_roundtrip_in_sequence(qapp) -> None:
    seq = Sequence(notes=[TextNote(id="t1", text="메모", x=10.0, y=20.0)])
    restored = Sequence.from_dict(seq.to_dict())
    assert len(restored.notes) == 1
    assert restored.notes[0].text == "메모"
    assert restored.notes[0].x == 10.0


def test_scene_add_and_delete_note(qapp) -> None:
    scene = SequenceScene(Sequence())
    item = scene.add_note(TextNote(id="t1", text="hi", x=0.0, y=0.0))
    assert scene.note_items and len(scene.sequence.notes) == 1
    item.setSelected(True)
    scene.delete_selected()
    assert scene.note_items == [] and scene.sequence.notes == []


def test_text_note_edit_updates_model(qapp) -> None:
    scene = SequenceScene(Sequence(notes=[TextNote(id="t1", text="", x=0.0, y=0.0)]))
    item: TextNoteItem = scene.note_items[0]
    item._text.setPlainText("새 내용")     # 편집 시 contentsChanged → 모델 반영
    assert item.note.text == "새 내용"
    assert scene.sequence.notes[0].text == "새 내용"


def test_edge_side_roundtrip(qapp) -> None:
    e = Edge("a", "next", "b", from_side="right", to_side="left")
    r = Edge.from_dict(e.to_dict())
    assert r.from_side == "right" and r.to_side == "left"


def test_port_hit_side(qapp) -> None:
    scene = SequenceScene(Sequence(nodes=[Node(id="n0", type=NodeType.START, x=0, y=0)]))
    it = scene.node_items["n0"]
    assert it.in_hit_side(it.mapToScene(QPointF(NODE_W / 2, 0))) == "top"
    assert it.in_hit_side(it.mapToScene(QPointF(0, it._height / 2))) == "left"
    assert it.out_hit_side(it.mapToScene(QPointF(NODE_W / 2, it._height))) == ("next", "bottom")
    assert it.out_hit_side(it.mapToScene(QPointF(NODE_W, it._height / 2))) == ("next", "right")


def test_explicit_to_side_used_in_path(qapp) -> None:
    scene = SequenceScene(Sequence(
        nodes=[Node(id="n0", type=NodeType.START, x=0, y=0),
               Node(id="n1", type=NodeType.START, x=0, y=300)],
        edges=[Edge("n0", "next", "n1")]))
    item = scene.edge_items[0]
    n1 = scene.node_items["n1"]
    scene.sequence.edges[0].to_side = "left"
    scene.refresh_edges()
    assert (item.p2 - n1.mapToScene(QPointF(0, n1._height / 2))).manhattanLength() < 1e-6
    scene.sequence.edges[0].to_side = "top"
    scene.refresh_edges()
    assert (item.p2 - n1.mapToScene(QPointF(NODE_W / 2, 0))).manhattanLength() < 1e-6


def test_connect_ports_stores_sides(qapp) -> None:
    scene = SequenceScene(Sequence(nodes=[
        Node(id="n0", type=NodeType.START, x=0, y=0),
        Node(id="n1", type=NodeType.START, x=0, y=300)]))
    scene.connect_ports("n0", "next", "n1", from_side="right", to_side="left")
    e = scene.sequence.edges[0]
    assert e.from_side == "right" and e.to_side == "left"


def test_reroute_in_end_to_other_side(qapp) -> None:
    scene = SequenceScene(Sequence(
        nodes=[Node(id="n0", type=NodeType.START, x=0, y=0),
               Node(id="n1", type=NodeType.START, x=0, y=300)],
        edges=[Edge("n0", "next", "n1", to_side="top")]))
    item = scene.edge_items[0]
    scene._reroute = (item, "in")
    scene._temp_edge = EdgeItem(Edge("", "", ""), temp=True)
    scene.addItem(scene._temp_edge)
    n1 = scene.node_items["n1"]
    scene._finish_reroute(n1.mapToScene(QPointF(0, n1._height / 2)))  # 좌측 IN 포트로 드롭
    assert item.edge.to_node == "n1" and item.edge.to_side == "left"


def test_reroute_out_end_to_other_node(qapp) -> None:
    scene = SequenceScene(Sequence(
        nodes=[Node(id="n0", type=NodeType.START, x=0, y=0),
               Node(id="n1", type=NodeType.START, x=400, y=0),
               Node(id="n2", type=NodeType.START, x=0, y=300)],
        edges=[Edge("n0", "next", "n2")]))
    item = scene.edge_items[0]
    scene._reroute = (item, "out")
    scene._temp_edge = EdgeItem(Edge("", "", ""), temp=True)
    scene.addItem(scene._temp_edge)
    n1 = scene.node_items["n1"]
    scene._finish_reroute(n1.mapToScene(QPointF(NODE_W / 2, n1._height)))  # n1 하단 OUT 으로 드롭
    assert item.edge.from_node == "n1" and item.edge.from_port == "next"
    assert item.edge.from_side == "bottom"
    assert scene._edges_by_node.get("n1")  # 인덱스 재구성 확인


def test_align_noop_with_single_selection(qapp) -> None:
    scene = _seq((10, 20), (300, 400))
    scene.node_items["n0"].setSelected(True)  # 1개만 선택
    scene.align_selected("left")
    assert scene._node_anim is None           # 정렬 동작 없음
    assert scene.node_items["n0"].pos().x() == 10

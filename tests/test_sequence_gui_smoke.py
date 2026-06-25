"""GUI 컴포넌트가 임포트/생성되는지만 확인하는 스모크 테스트.

QWidget 생성에는 QApplication 이 필요하다. conftest 의 세션 공용 qapp 픽스처
(offscreen QApplication)를 사용한다.
"""

from sequence.sequence_model import Edge, Node, NodeType, Sequence


def test_editor_constructs(qapp) -> None:
    from sequence.sequence_editor import SequenceEditor

    seq = Sequence(
        nodes=[Node(id="n1", type=NodeType.START, x=0, y=0),
               Node(id="n2", type=NodeType.END, x=200, y=0, result="ok")],
        edges=[Edge("n1", "next", "n2")],
    )
    editor = SequenceEditor(seq)
    assert len(editor.scene.node_items) == 2
    assert len(editor.scene.edge_items) == 1

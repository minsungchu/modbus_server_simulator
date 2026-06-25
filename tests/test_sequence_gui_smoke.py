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

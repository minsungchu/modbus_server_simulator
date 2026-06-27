"""비주얼 노드 그래프 에디터.

QGraphicsScene/QGraphicsView 로 노드와 연결을 그린다. 좌측 팔레트로 노드를
추가하고, 출력 포트에서 입력 포트로 드래그해 연결하며, 노드를 선택하면 우측
속성 패널에서 config 를 편집한다.
"""

from __future__ import annotations

import heapq
import json
import logging
import math
from collections import defaultdict

from PySide6.QtCore import (
    QEasingCurve,
    QMimeData,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDrag,
    QFont,
    QFontMetrics,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from sequence.sequence_model import (
    GROUP_MODES,
    OPERATORS,
    REGISTER_TYPES,
    WRITE_OP_SYMBOLS,
    WRITE_OPS,
    Condition,
    Edge,
    Group,
    Node,
    NodeType,
    Sequence,
    TextNote,
    WriteAction,
    clamp_value,
)

logger = logging.getLogger("ModbusServerSim")

NODE_COLORS: dict[NodeType, QColor] = {
    NodeType.START: QColor("#22c55e"),   # emerald
    NodeType.SEND: QColor("#3b82f6"),    # blue
    NodeType.WAIT: QColor("#f59e0b"),    # amber
    NodeType.BRANCH: QColor("#a855f7"),  # purple
    NodeType.DELAY: QColor("#64748b"),   # slate
    NodeType.END: QColor("#ef4444"),     # red
}

# 노드 본체/캔버스 색상(QSS 다크 테마와 동일 계열)
NODE_BODY = QColor("#1e293b")
NODE_BODY_SELECTED = QColor("#27364d")
NODE_BORDER = QColor("#0b1220")
NODE_TITLE = QColor("#f8fafc")
NODE_LABEL = QColor("#cbd5e1")
PORT_FILL = QColor("#e2e8f0")
PORT_RING = QColor("#0f172a")
PORT_LABEL = QColor("#94a3b8")
PORT_IN_FILL = QColor("#ef4444")   # In(입력) 연결점 — 빨강
PORT_OUT_FILL = QColor("#3b82f6")  # Out(출력) 연결점 — 파랑
CANVAS_BG = QColor("#0f172a")
CANVAS_GRID = QColor("#1b2740")
EDGE_COLOR = QColor("#64748b")
HIGHLIGHT = QColor("#fbbf24")

# 팔레트 → 캔버스 드래그앤드롭 시 노드 타입을 실어 나르는 MIME 키
NODE_MIME = "application/x-modbus-node-type"
# 팔레트 → 캔버스 드래그앤드롭으로 텍스트 메모를 놓을 때 쓰는 MIME 키
NOTE_MIME = "application/x-modbus-note"

NODE_W = 184.0
NODE_H = 76.0          # 기본/최소 높이(실제 높이는 내용에 따라 가변)
HEADER_H = 28.0
RADIUS = 11.0
PORT_R = 6.0
PORT_M = PORT_R + 3.0  # boundingRect 상하 여백(포트가 본체 밖으로 그려짐)
LINE_H = 20.0          # 본문 요약 한 줄(칩) 슬롯 높이
BODY_TOP = 6.0         # 헤더 아래 본문 시작 여백
SUMMARY_COLOR = QColor("#9fb6d4")
LABEL_MAX = 10         # 노드 라벨(이름) 최대 글자 수(헤더에 함께 표시)

# 본문 정보 칩(둥근 배경 + 좌측 액센트 바) 스타일
CHIP_BG = QColor(148, 163, 184, 30)    # 아주 옅은 슬레이트 배경
CHIP_TEXT = QColor("#e8eef7")          # 값 텍스트(밝게)
CHIP_MUTED = QColor("#64748b")         # 빈 설정 placeholder
CHIP_INSET = 10.0      # 칩 좌우 여백
CHIP_GAP = 3.0         # 칩 사이 간격
CHIP_RADIUS = 5.0
# 본문 값 표시용 모노스페이스 폰트(레지스터/주소/값 자릿수 정렬 → 가독성)
BODY_FONT_FAMILY = "Consolas"
BODY_FONT_PT = 9.0

# 레지스터 종류 → 노드 안에 표시할 짧은 약어
REG_ABBR = {
    "coils": "COIL",
    "discrete_inputs": "DI",
    "holding_registers": "HR",
    "input_registers": "IR",
}


def _reg_abbr(reg_type: str | None) -> str:
    """레지스터 종류를 노드 표시용 짧은 약어로 바꾼다."""
    if not reg_type:
        return "?"
    return REG_ABBR.get(reg_type, reg_type[:3].upper())


def output_ports(node: Node) -> list[str]:
    """노드 타입에 따른 출력 포트 키 목록을 반환한다."""
    if node.type in (NodeType.START, NodeType.SEND, NodeType.DELAY, NodeType.END):
        # END 는 반복 그룹 안에서 컨테이너 밖으로 이어질 때만 쓰인다(최상위는 종료).
        return ["next"]
    if node.type is NodeType.WAIT:
        ports = [f"cond_{i}" for i in range(len(node.conditions))]
        ports.append("timeout")
        return ports
    if node.type is NodeType.BRANCH:
        ports = [f"case_{i}" for i in range(len(node.cases))]
        ports.append("else")
        return ports
    return []


class EdgeItem(QGraphicsPathItem):
    """두 노드 포트를 잇는 베지어 곡선."""

    def __init__(self, edge: Edge, temp: bool = False) -> None:
        super().__init__()
        self.edge = edge
        self._temp = temp
        self.p1: QPointF | None = None   # 출력(OUT) 끝점 씬 좌표
        self.p2: QPointF | None = None   # 입력(IN) 끝점 씬 좌표
        pen = QPen(QColor("#93c5fd") if temp else EDGE_COLOR, 2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        if temp:
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setZValue(-1)
        if not temp:
            # 연결선 클릭 선택 → Delete 로 제거 가능
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    def shape(self) -> QPainterPath:
        # 얇은 선도 쉽게 클릭되도록 히트 영역을 넓힌다.
        stroker = QPainterPathStroker()
        stroker.setWidth(12.0)
        return stroker.createStroke(self.path())

    def paint(self, painter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(self.pen())
        if self.isSelected():
            pen.setColor(QColor("#93c5fd"))
            pen.setWidthF(3.4)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self.path())

    def update_path(self, p1: QPointF, p2: QPointF,
                    exit_side: str = "bottom", entry_side: str = "top",
                    obstacles: list[QRectF] | None = None,
                    endpoints: list[QRectF] | None = None,
                    channel: float | None = None) -> None:
        """출력점 p1(exit_side: bottom/right) → 입력점 p2(entry_side: top/left) 사이를
        직각 꺾은선 + 둥근 모서리로 잇는다.

        다른 노드와 겹치거나 경로가 되접혀(fold-back) 뾰족해질 때는 A* 직교
        라우터로 노드를 피해 부드럽게 다시 긋는다. endpoints(양 끝 노드 사각형)는
        A* 에서만 장애물로 써서, 선이 자기 출발/도착 노드 위를 가로지르지 않게 한다.

        Args:
            p1: 출력 포트의 씬 좌표.
            p2: 입력 포트의 씬 좌표.
            exit_side: 출력 변("bottom" 또는 "right").
            entry_side: 입력 변("top" 또는 "left").
            obstacles: 피해야 할 다른 노드 사각형 목록.
            endpoints: 출발/도착 노드 사각형(작은 여백) — A* 전용 장애물.
            channel: 꺾어 나가는 공통 좌표(세로 경로면 y, 가로 경로면 x). 같은
                포트에서 여러 노드로 팬아웃할 때 한 높이에서 갈라지도록 정렬하는
                용도. None 이면 두 스텁의 중점을 쓴다.
        """
        self.p1, self.p2 = p1, p2  # 끝점 좌표 저장(재연결 드래그 히트테스트용)
        r = 16.0
        exit_v = exit_side == "bottom"
        entry_v = entry_side == "top"
        # 스텁(직진) 길이: 노드가 가까우면 거리에 비례해 짧게, 멀면 상한으로 고정.
        # 고정 길이면 가까운 노드끼리 선이 불필요하게 길게 직진해 보인다.
        span = abs(p2.y() - p1.y()) if (exit_v or entry_v) else abs(p2.x() - p1.x())
        ext = max(8.0, min(22.0, span * 0.32))
        obstacles = obstacles or []
        a = (QPointF(p1.x(), p1.y() + ext) if exit_v
             else QPointF(p1.x() + ext, p1.y()))
        b = (QPointF(p2.x(), p2.y() - ext) if entry_v
             else QPointF(p2.x() - ext, p2.y()))

        def _split(lo: float, hi: float, default: float) -> float:
            """갈라지는 좌표를 정한다. channel 이 있으면 스텁 사이로 클램프."""
            if channel is None:
                return default
            return min(max(channel, min(lo, hi)), max(lo, hi))

        # 1) 기본(최단) 경로
        if exit_v and entry_v:
            mid = _split(a.y(), b.y(), (a.y() + b.y()) / 2.0)
            base = [p1, a, QPointF(a.x(), mid), QPointF(b.x(), mid), b, p2]
        elif not exit_v and not entry_v:
            mid = _split(a.x(), b.x(), (a.x() + b.x()) / 2.0)
            base = [p1, a, QPointF(mid, a.y()), QPointF(mid, b.y()), b, p2]
        elif exit_v and not entry_v:
            base = [p1, a, QPointF(a.x(), b.y()), b, p2]
        else:
            base = [p1, a, QPointF(b.x(), a.y()), b, p2]

        # 2) 재라우팅 필요: 다른 노드와 겹치거나, 경로가 되접혀(스파이크) 뾰족할 때
        reroute = (obstacles and not _route_clear(base, obstacles)) or _has_foldback(base)
        if reroute:
            astar_obs = obstacles + (endpoints or [])
            route = _astar_route(a, b, astar_obs)
            if route is not None:
                pts = _dedup_points([p1, *route, p2])
            else:
                pts = _dedup_points(self._detour(p1, p2, a, b, obstacles) or base)
        else:
            pts = _dedup_points(base)
        self.setPath(_rounded_path(pts, r))

    def _detour(self, p1, p2, a, b, obstacles):
        """기본 경로가 노드와 겹칠 때 우회 경로를 찾는다.

        1순위로 격자 기반 A* 직교 라우터를 사용해 노드 사이 통로를 따라
        최소 굴절 경로를 찾고, 실패하면 단순 좌/우 세로 채널로 폴백한다.
        """
        route = _astar_route(a, b, obstacles)
        if route is not None:
            return _dedup_points([p1, *route, p2])

        # 폴백: 좌/우 세로 채널
        rights = [r.right() for r in obstacles]
        lefts = [r.left() for r in obstacles]
        margin = 40.0
        right_x = max([p1.x(), p2.x()] + rights) + margin
        left_x = min([p1.x(), p2.x()] + lefts) - margin
        for ch in (right_x, left_x):
            cand = [p1, a, QPointF(ch, a.y()), QPointF(ch, b.y()), b, p2]
            if _route_clear(cand, obstacles):
                return cand
        return None


def _seg_hits_rect(p: QPointF, q: QPointF, rect: QRectF) -> bool:
    """축 정렬 선분 p-q 가 사각형 rect 내부를 지나는지(경계 살짝 접촉은 무시)."""
    if abs(p.y() - q.y()) < 0.5:  # 수평 선분
        y = p.y()
        if not (rect.top() < y < rect.bottom()):
            return False
        x0, x1 = sorted((p.x(), q.x()))
        return x0 < rect.right() and x1 > rect.left()
    if abs(p.x() - q.x()) < 0.5:  # 수직 선분
        x = p.x()
        if not (rect.left() < x < rect.right()):
            return False
        y0, y1 = sorted((p.y(), q.y()))
        return y0 < rect.bottom() and y1 > rect.top()
    return False


def _has_foldback(pts: list[QPointF]) -> bool:
    """경로에 진행 방향이 역전되는(되접히는) 구간이 있으면 True.

    출력 변이 목적지와 반대일 때 stub 이 반대로 꺾여 뾰족한 스파이크가 생기는데,
    인접 두 선분의 내적이 음수면 그 지점이다.
    """
    for i in range(len(pts) - 2):
        v1x, v1y = pts[i + 1].x() - pts[i].x(), pts[i + 1].y() - pts[i].y()
        v2x, v2y = pts[i + 2].x() - pts[i + 1].x(), pts[i + 2].y() - pts[i + 1].y()
        if v1x * v2x + v1y * v2y < -1e-6:
            return True
    return False


def _route_clear(pts: list[QPointF], obstacles: list[QRectF]) -> bool:
    """경로의 모든 선분이 어떤 장애물 사각형과도 겹치지 않으면 True."""
    for i in range(len(pts) - 1):
        for r in obstacles:
            if _seg_hits_rect(pts[i], pts[i + 1], r):
                return False
    return True


def _dedup_points(pts: list[QPointF]) -> list[QPointF]:
    """연속 중복점과 한 직선 위의 중간 꼭짓점(공선점)을 제거해 경로를 단순화한다."""
    out: list[QPointF] = []
    for p in pts:
        if out and (p - out[-1]).manhattanLength() < 0.5:
            continue
        out.append(p)
    i = 1
    while i < len(out) - 1:
        a, b, c = out[i - 1], out[i], out[i + 1]
        # 세 점이 같은 수평/수직선 위면 가운데 점 제거
        if (abs(a.x() - b.x()) < 0.5 and abs(b.x() - c.x()) < 0.5) or \
           (abs(a.y() - b.y()) < 0.5 and abs(b.y() - c.y()) < 0.5):
            del out[i]
        else:
            i += 1
    return out


def _astar_route(a: QPointF, b: QPointF, obstacles: list[QRectF],
                 gap: float = 10.0) -> list[QPointF] | None:
    """Hanan 격자 위에서 A* 로 a→b 직교 우회 경로를 찾는다.

    장애물 사각형들의 변(±gap)과 시작/끝 좌표로 후보 격자선을 만들고, 인접
    격자점을 장애물을 통과하지 않는 수평/수직 선분으로 연결한 그래프에서
    이동 거리 + 굴절 패널티를 최소화하는 경로를 탐색한다. 경로가 없으면 None.

    Args:
        a: 시작점(출력 스텁 끝).
        b: 끝점(입력 스텁 끝).
        obstacles: 피해야 할 사각형 목록.
        gap: 격자선을 장애물 변에서 띄울 여백.

    Returns:
        a 와 b 를 포함한 꼭짓점 목록, 또는 경로를 못 찾으면 None.
    """
    xs = {a.x(), b.x()}
    ys = {a.y(), b.y()}
    for r in obstacles:
        xs.update((r.left() - gap, r.right() + gap))
        ys.update((r.top() - gap, r.bottom() + gap))
    xs = sorted(xs)
    ys = sorted(ys)
    if len(xs) * len(ys) > 4000:  # 과도한 격자 방지(성능 안전장치)
        return None
    xi = {x: i for i, x in enumerate(xs)}
    yi = {y: i for i, y in enumerate(ys)}

    def clear(p: QPointF, q: QPointF) -> bool:
        return all(not _seg_hits_rect(p, q, r) for r in obstacles)

    start = (xi[a.x()], yi[a.y()])
    goal = (xi[b.x()], yi[b.y()])

    def h(c: tuple[int, int]) -> float:
        return abs(xs[c[0]] - xs[goal[0]]) + abs(ys[c[1]] - ys[goal[1]])

    turn_penalty = 18.0
    # 상태: (격자ix, 격자iy, 진입방향) — 방향 0=가로,1=세로,-1=시작
    start_state = (start[0], start[1], -1)
    best: dict[tuple[int, int, int], float] = {start_state: 0.0}
    came: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    pq: list[tuple[float, tuple[int, int, int]]] = [(h(start), start_state)]
    found: tuple[int, int, int] | None = None
    while pq:
        _, state = heapq.heappop(pq)
        cx, cy, cdir = state
        if (cx, cy) == goal:
            found = state
            break
        g = best[state]
        for dx, dy, ndir in ((1, 0, 0), (-1, 0, 0), (0, 1, 1), (0, -1, 1)):
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < len(xs) and 0 <= ny < len(ys)):
                continue
            p = QPointF(xs[cx], ys[cy])
            q = QPointF(xs[nx], ys[ny])
            if not clear(p, q):
                continue
            step = abs(q.x() - p.x()) + abs(q.y() - p.y())
            cost = g + step + (turn_penalty if cdir != -1 and cdir != ndir else 0.0)
            nstate = (nx, ny, ndir)
            if cost < best.get(nstate, float("inf")):
                best[nstate] = cost
                came[nstate] = state
                heapq.heappush(pq, (cost + h((nx, ny)), nstate))
    if found is None:
        return None

    path: list[QPointF] = []
    s = found
    while True:
        path.append(QPointF(xs[s[0]], ys[s[1]]))
        if s not in came:
            break
        s = came[s]
    path.reverse()
    return path


def _rounded_path(points: list[QPointF], radius: float) -> QPainterPath:
    """꺾은선(직교 경로)을 그리되 각 모서리를 둥글게(quad arc) 처리한다."""
    path = QPainterPath(points[0])
    for i in range(1, len(points) - 1):
        prev, cur, nxt = points[i - 1], points[i], points[i + 1]
        d1x, d1y = cur.x() - prev.x(), cur.y() - prev.y()
        d2x, d2y = nxt.x() - cur.x(), nxt.y() - cur.y()
        l1 = math.hypot(d1x, d1y)
        l2 = math.hypot(d2x, d2y)
        if l1 < 1e-6 or l2 < 1e-6:
            continue
        r = min(radius, l1 / 2, l2 / 2)
        before = QPointF(cur.x() - d1x / l1 * r, cur.y() - d1y / l1 * r)
        after = QPointF(cur.x() + d2x / l2 * r, cur.y() + d2y / l2 * r)
        path.lineTo(before)
        path.quadTo(cur, after)  # 모서리를 둥글게
    path.lineTo(points[-1])
    return path


def _top_rounded_path(rect: QRectF, r: float) -> QPainterPath:
    """상단 모서리만 둥근(하단은 직각) 사각형 경로를 만든다(헤더 밴드용)."""
    path = QPainterPath()
    path.moveTo(rect.left(), rect.bottom())
    path.lineTo(rect.left(), rect.top() + r)
    path.quadTo(rect.left(), rect.top(), rect.left() + r, rect.top())
    path.lineTo(rect.right() - r, rect.top())
    path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + r)
    path.lineTo(rect.right(), rect.bottom())
    path.closeSubpath()
    return path


ICON_BAR = QColor("#e2e8f0")    # 정렬 아이콘의 노드 막대(밝은 회색)
ICON_AXIS = QColor("#60a5fa")   # 정렬 기준선(파랑) — 무엇을 기준으로 맞추는지 강조


def _align_icon(kind: str) -> QIcon:
    """정렬/간격 의미가 한눈에 보이도록 직접 그린 아이콘을 만든다.

    기준선(파랑) + 그 선에 정렬된 노드 막대(회색)로 표현한다. 간격(distribute)은
    기준선 없이 같은 크기의 막대를 균등 간격으로 그려 "고른 간격"을 나타낸다.

    Args:
        kind: left/right/cx/top/bottom/cy/dist_h/dist_v.
    """
    s = 36
    pm = QPixmap(s, s)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    m = 5.0
    thick = 5.0
    lengths = (22.0, 13.0, 18.0)  # 막대 길이(서로 달라 정렬이 더 잘 보임)
    slots = (8.0, 15.5, 23.0)     # 막대 3개의 가로/세로 위치

    def bar(x: float, y: float, w: float, h: float) -> None:
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, w, h), 2.0, 2.0)
        p.fillPath(path, ICON_BAR)

    def axis_v(x: float) -> None:
        p.fillRect(QRectF(x - 1.0, m - 1.0, 2.0, s - 2 * m + 2.0), ICON_AXIS)

    def axis_h(y: float) -> None:
        p.fillRect(QRectF(m - 1.0, y - 1.0, s - 2 * m + 2.0, 2.0), ICON_AXIS)

    if kind == "left":
        axis_v(m)
        for w, y in zip(lengths, slots):
            bar(m, y, w, thick)
    elif kind == "right":
        axis_v(s - m)
        for w, y in zip(lengths, slots):
            bar(s - m - w, y, w, thick)
    elif kind == "cx":
        axis_v(s / 2)
        for w, y in zip(lengths, slots):
            bar(s / 2 - w / 2, y, w, thick)
    elif kind == "top":
        axis_h(m)
        for h, x in zip(lengths, slots):
            bar(x, m, thick, h)
    elif kind == "bottom":
        axis_h(s - m)
        for h, x in zip(lengths, slots):
            bar(x, s - m - h, thick, h)
    elif kind == "cy":
        axis_h(s / 2)
        for h, x in zip(lengths, slots):
            bar(x, s / 2 - h / 2, thick, h)
    elif kind == "dist_h":          # 세로 막대 3개, 가로 간격 균등
        for x in (7.0, 15.5, 24.0):
            bar(x, 8.0, thick, 20.0)
    elif kind == "dist_v":          # 가로 막대 3개, 세로 간격 균등
        for y in (7.0, 15.5, 24.0):
            bar(8.0, y, 20.0, thick)
    p.end()
    return QIcon(pm)


class NodeItem(QGraphicsObject):
    """하나의 노드를 표현하는 그래픽 아이템(이동 가능).

    QGraphicsObject 를 상속해 ``pos`` 프로퍼티 애니메이션(겹침 시 되돌리기)을
    QPropertyAnimation 으로 처리할 수 있게 한다.
    """

    def __init__(self, node: Node) -> None:
        super().__init__()
        self.node = node
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        # 본체를 오프스크린 픽스맵에 캐싱 → 드래그 시 리페인트 없이 블릿(부드러운 이동).
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.setPos(node.x, node.y)
        self._highlight = False
        self._height = self._compute_height()

    def boundingRect(self) -> QRectF:
        # 포트가 네 변(상/좌=In, 하/우=Out)으로 본체 밖에 그려지므로 사방 여백 포함.
        return QRectF(-PORT_M, -PORT_M, NODE_W + 2 * PORT_M, self._height + 2 * PORT_M)

    # --- 노드 안에 표시할 설정 요약 ---
    def _summary_lines(self) -> list[str]:
        """노드 타입별로 설정된 값을 한눈에 보이도록 짧은 문자열 목록으로 만든다."""
        n = self.node
        if n.type is NodeType.SEND:
            return [f"{_reg_abbr(w.reg_type)}[{w.addr}] {WRITE_OP_SYMBOLS.get(w.op, '=')} {w.value}"
                    for w in n.writes] or ["(쓰기 없음)"]
        if n.type is NodeType.WAIT:
            out = [f"{_reg_abbr(c.reg_type)}[{c.addr}] {c.op} {c.value}" for c in n.conditions]
            if n.timeout_ms:
                out.append(f"timeout: {n.timeout_ms}ms")
            return out or ["(조건 없음)"]
        if n.type is NodeType.BRANCH:
            head = f"{_reg_abbr(n.branch_reg_type)}[{n.branch_addr or 0}]"
            if n.cases:
                return [head, "cases: " + ", ".join(str(c) for c in n.cases)]
            return [head]
        if n.type is NodeType.DELAY:
            return [f"{n.delay_ms} ms"]
        if n.type is NodeType.END:
            return [f"→ {n.result}"] if n.result else []
        return []  # START

    def _content_lines(self) -> list[str]:
        """본문에 그릴 줄(라벨은 헤더에 표시하므로 설정 요약만)."""
        return self._summary_lines()

    def _compute_height(self) -> float:
        """헤더 + 본문 줄 수 + 하단 포트 라벨 영역을 더한 높이."""
        n_lines = max(1, len(self._content_lines()))
        port_strip = 18.0 if output_ports(self.node) else 8.0
        return max(NODE_H, HEADER_H + BODY_TOP + n_lines * LINE_H + port_strip)

    def refresh_geometry(self) -> None:
        """설정 변경 후 높이를 다시 계산한다(지오메트리 변경 통지 포함)."""
        new_h = self._compute_height()
        if abs(new_h - self._height) > 0.01:
            self.prepareGeometryChange()
            self._height = new_h
        self.update()

    def set_highlight(self, on: bool) -> None:
        """실행 중 현재 노드 강조 표시를 토글한다."""
        self._highlight = on
        self.update()

    def center_scene(self) -> QPointF:
        return self.mapToScene(QPointF(NODE_W / 2, self._height / 2))

    def body_scene_rect(self, margin: float = 14.0) -> QRectF:
        """본체의 씬 좌표 사각형(연결선 우회용 장애물). margin 만큼 부풀린다."""
        r = self.mapToScene(QRectF(0, 0, NODE_W, self._height)).boundingRect()
        return r.adjusted(-margin, -margin, margin, margin)

    def _in_local(self) -> list[tuple[QPointF, str]]:
        """In(입력) 연결점: 상단 중앙 + 좌측 중앙."""
        return [(QPointF(NODE_W / 2, 0), "top"), (QPointF(0, self._height / 2), "left")]

    def _out_local(self) -> dict[str, list[tuple[QPointF, str]]]:
        """Out(출력) 연결점: 포트를 하단·우측에 분배. 단일 출력은 하단+우측 둘 다."""
        h = self._height
        ports = output_ports(self.node)
        res: dict[str, list[tuple[QPointF, str]]] = {}
        if len(ports) == 1:
            res[ports[0]] = [(QPointF(NODE_W / 2, h), "bottom"), (QPointF(NODE_W, h / 2), "right")]
        elif ports:
            nb = (len(ports) + 1) // 2
            bottoms, rights = ports[:nb], ports[nb:]
            for i, p in enumerate(bottoms):
                res[p] = [(QPointF(NODE_W * (i + 1) / (len(bottoms) + 1), h), "bottom")]
            for j, p in enumerate(rights):
                res[p] = [(QPointF(NODE_W, h * (j + 1) / (len(rights) + 1)), "right")]
        return res

    def input_point(self, toward: QPointF | None = None,
                    side: str | None = None) -> tuple[QPointF, str]:
        """입력 연결점(씬 좌표, 변)을 반환한다.

        side 가 지정되면 그 변을 우선 사용하고, 없으면 toward 에 가장 가까운 변,
        toward 도 없으면 첫 번째 변을 쓴다.
        """
        cands = [(self.mapToScene(p), s) for p, s in self._in_local()]
        if side is not None:
            for c in cands:
                if c[1] == side:
                    return c
        if toward is None:
            return cands[0]
        return min(cands, key=lambda c: (c[0] - toward).manhattanLength())

    def output_point(self, port: str, toward: QPointF | None = None,
                     side: str | None = None) -> tuple[QPointF, str]:
        """출력 연결점(씬 좌표, 변)을 반환한다. side 지정 시 그 변을 우선 사용."""
        local = self._out_local().get(port)
        if not local:
            return (self.mapToScene(QPointF(NODE_W / 2, self._height)), "bottom")
        cands = [(self.mapToScene(p), s) for p, s in local]
        if side is not None:
            for c in cands:
                if c[1] == side:
                    return c
        if toward is None:
            return cands[0]
        return min(cands, key=lambda c: (c[0] - toward).manhattanLength())

    def out_hit(self, scene_pos: QPointF) -> str | None:
        """씬 좌표 근처의 출력 포트 키를 찾는다(없으면 None)."""
        hit = self.out_hit_side(scene_pos)
        return hit[0] if hit else None

    def out_hit_side(self, scene_pos: QPointF) -> tuple[str, str] | None:
        """씬 좌표 근처의 출력 (포트, 변) 을 찾는다(없으면 None)."""
        for port, local in self._out_local().items():
            for p, s in local:
                if (self.mapToScene(p) - scene_pos).manhattanLength() <= PORT_R * 2:
                    return (port, s)
        return None

    def in_hit(self, scene_pos: QPointF) -> bool:
        """씬 좌표가 입력 연결점 근처인지."""
        return self.in_hit_side(scene_pos) is not None

    def in_hit_side(self, scene_pos: QPointF) -> str | None:
        """씬 좌표 근처의 입력 변("top"/"left")을 찾는다(없으면 None)."""
        for p, s in self._in_local():
            if (self.mapToScene(p) - scene_pos).manhattanLength() <= PORT_R * 2:
                return s
        return None

    def paint(self, painter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        h = self._height
        accent = NODE_COLORS[self.node.type]
        body_rect = QRectF(0, 0, NODE_W, h)

        # 본체(라운드) — 살짝 어두운 슬레이트 면
        body_path = QPainterPath()
        body_path.addRoundedRect(body_rect, RADIUS, RADIUS)
        painter.fillPath(body_path, QBrush(NODE_BODY_SELECTED if self.isSelected() else NODE_BODY))

        # 헤더 밴드 — 노드 타입 색 그라데이션(상단만 라운드)
        header_rect = QRectF(0, 0, NODE_W, HEADER_H)
        grad = QLinearGradient(0, 0, 0, HEADER_H)
        grad.setColorAt(0.0, accent.lighter(118))
        grad.setColorAt(1.0, accent)
        painter.fillPath(_top_rounded_path(header_rect, RADIUS), QBrush(grad))

        # 테두리 — 평소 1px, 선택 시 액센트, 실행 강조 시 골드 글로우
        if self._highlight:
            painter.setPen(QPen(HIGHLIGHT, 2.5))
        elif self.isSelected():
            painter.setPen(QPen(QColor("#3b82f6"), 2))
        else:
            painter.setPen(QPen(NODE_BORDER, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(body_rect, RADIUS, RADIUS)

        # 헤더 텍스트: 타입 + (라벨). 라벨은 최대 LABEL_MAX 자.
        painter.setPen(QPen(NODE_TITLE))
        title_font = QFont(painter.font())
        title_font.setBold(True)
        title_font.setPointSizeF(10.0)
        painter.setFont(title_font)
        header_text = self.node.type.value
        if self.node.label:
            header_text += f"  ·  {self.node.label[:LABEL_MAX]}"
        fm_title = QFontMetrics(title_font)
        painter.drawText(QRectF(12, 0, NODE_W - 24, HEADER_H),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         fm_title.elidedText(header_text, Qt.TextElideMode.ElideRight, int(NODE_W - 24)))

        # 본문: 설정 요약을 칩(둥근 배경 + 좌측 액센트 바)으로 표시.
        # 값은 모노스페이스로 그려 자릿수가 정렬되어 읽기 쉽다.
        chip_h = LINE_H - CHIP_GAP
        chip_w = NODE_W - CHIP_INSET * 2
        y = HEADER_H + BODY_TOP
        mono = QFont(BODY_FONT_FAMILY)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSizeF(BODY_FONT_PT)
        ui = QFont(painter.font())  # 한글 placeholder 용 기본 폰트
        ui.setPointSizeF(8.5)
        ui.setItalic(True)
        fm_mono = QFontMetrics(mono)
        fm_ui = QFontMetrics(ui)
        for line in self._summary_lines():
            placeholder = line.startswith("(")  # "(쓰기 없음)" 등
            chip = QRectF(CHIP_INSET, y, chip_w, chip_h)
            chip_path = QPainterPath()
            chip_path.addRoundedRect(chip, CHIP_RADIUS, CHIP_RADIUS)
            painter.fillPath(chip_path, QBrush(CHIP_BG))
            if not placeholder:
                bar = QRectF(CHIP_INSET + 2, y + 3, 3, chip_h - 6)
                bar_path = QPainterPath()
                bar_path.addRoundedRect(bar, 1.5, 1.5)
                painter.fillPath(bar_path, QBrush(accent))
            tx = CHIP_INSET + (12 if not placeholder else 9)
            tw = chip_w - (16 if not placeholder else 12)
            painter.setFont(ui if placeholder else mono)
            painter.setPen(QPen(CHIP_MUTED if placeholder else CHIP_TEXT))
            fm = fm_ui if placeholder else fm_mono
            painter.drawText(QRectF(tx, y, tw, chip_h),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             fm.elidedText(line, Qt.TextElideMode.ElideRight, int(tw)))
            y += LINE_H

        # In 포트(상단/좌측) — 빨강
        for p, _s in self._in_local():
            self._draw_port(painter, p, PORT_IN_FILL)

        # Out 포트(하단/우측) — 파랑 + 포트명(다중일 때만)
        out_local = self._out_local()
        multi = len(out_local) > 1
        port_font = QFont(painter.font())
        port_font.setPointSizeF(8.0)
        painter.setFont(port_font)
        for port, local in out_local.items():
            for p, side in local:
                if multi:
                    painter.setPen(QPen(PORT_LABEL))
                    if side == "bottom":
                        painter.drawText(QRectF(p.x() - 34, p.y() - 17, 68, 13),
                                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, port)
                    else:
                        painter.drawText(QRectF(p.x() - 66, p.y() - 8, 58, 16),
                                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, port)
                self._draw_port(painter, p, PORT_OUT_FILL)

        # 비활성(실행 제외) 노드: 반투명 오버레이로 흐리게 + 우측 상단 OFF 배지.
        if not self.node.enabled:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(15, 23, 42, 160)))
            painter.drawRoundedRect(body_rect, RADIUS, RADIUS)
            badge = QRectF(NODE_W - 50, 6, 42, 18)
            painter.setBrush(QBrush(QColor(100, 116, 139)))
            painter.drawRoundedRect(badge, 9, 9)
            badge_font = QFont(painter.font())
            badge_font.setBold(True)
            badge_font.setPointSizeF(8.0)
            painter.setFont(badge_font)
            painter.setPen(QPen(QColor("#f1f5f9")))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, "OFF")

    def _draw_port(self, painter, center: QPointF, fill: QColor) -> None:
        """포트(색 점 + 어두운 링)를 그린다(빨강=In, 파랑=Out)."""
        painter.setPen(QPen(PORT_RING, 1.5))
        painter.setBrush(QBrush(fill))
        painter.drawEllipse(center, PORT_R, PORT_R)

    def mousePressEvent(self, event) -> None:
        # 드래그 시작을 씬에 알려, 이동 중에는 가벼운(A* 없는) 경로 갱신만 하게 한다.
        super().mousePressEvent(event)
        scene = self.scene()
        if isinstance(scene, SequenceScene):
            scene.begin_interactive_drag()

    def mouseReleaseEvent(self, event) -> None:
        # 드래그 종료 → 씬이 한 번만 전체(정밀 라우팅) 경로 갱신을 수행한다.
        super().mouseReleaseEvent(event)
        scene = self.scene()
        if isinstance(scene, SequenceScene):
            scene.end_interactive_drag()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            # 드래그 중 자석 정렬: 적용 전에 위치를 보정한다.
            scene = self.scene()
            if isinstance(scene, SequenceScene):
                value = scene.snap_position(self, value)
            return super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.node.x = self.pos().x()
            self.node.y = self.pos().y()
            scene = self.scene()
            if isinstance(scene, SequenceScene):
                scene.node_moved(self)
        return super().itemChange(change, value)


class GroupItem(QGraphicsItem):
    """여러 노드를 감싸는 그룹 영역. 영역을 드래그하면 소속 노드가 함께 이동한다."""

    PAD = 22.0
    TITLE_H = 24.0
    HANDLE = 10.0
    MIN_W = 70.0
    MIN_H = 60.0

    _CURSORS = {
        "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
        "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
        "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
        "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
    }

    def __init__(self, group: Group, scene_ref: "SequenceScene") -> None:
        super().__init__()
        self.group = group
        self._scene_ref = scene_ref
        self._rect = QRectF()
        self._mode: str | None = None          # None | "move" | 핸들이름
        self._start_scene = QPointF()
        self._start_rect = QRectF()
        self._move_starts: dict[str, QPointF] = {}
        self.setZValue(-2)  # 노드/엣지보다 뒤에 그린다
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setAcceptHoverEvents(True)
        self.recompute()

    def recompute(self) -> None:
        """모델의 사각형을 반영한다(영역 미지정 시 소속 노드로부터 1회 계산)."""
        g = self.group
        if g.w <= 0 or g.h <= 0:
            rect = None
            for nid in g.members:
                it = self._scene_ref.node_items.get(nid)
                if it is not None:
                    br = it.mapToScene(it.boundingRect()).boundingRect()
                    rect = br if rect is None else rect.united(br)
            if rect is not None:
                rect = rect.adjusted(-self.PAD, -self.PAD - self.TITLE_H, self.PAD, self.PAD)
                g.x, g.y, g.w, g.h = rect.x(), rect.y(), rect.width(), rect.height()
        self.prepareGeometryChange()
        self._rect = QRectF(g.x, g.y, g.w, g.h)
        self.update()

    def boundingRect(self) -> QRectF:
        m = self.HANDLE
        return self._rect.adjusted(-m, -m, m, m)

    GRAB_BAND = 18.0  # 프레임 가장자리 잡기/선택 영역 두께(내부는 통과)

    def shape(self) -> QPainterPath:
        """마우스/선택 히트 영역을 '테두리 밴드 + 제목'으로만 한정한다.

        내부 빈 영역은 통과시켜, 그룹 안에서 러버밴드로 노드만 박스 선택하거나
        클릭이 노드로 가도록 한다(그룹은 테두리/제목/핸들로 잡는다).
        """
        r = self._rect
        outer = QPainterPath()
        outer.addRoundedRect(r.adjusted(-self.HANDLE, -self.HANDLE,
                                        self.HANDLE, self.HANDLE), 14, 14)
        if r.width() > 2 * self.GRAB_BAND and r.height() > 2 * self.GRAB_BAND:
            inner = QPainterPath()
            inner.addRect(r.adjusted(self.GRAB_BAND, self.GRAB_BAND,
                                     -self.GRAB_BAND, -self.GRAB_BAND))
            band = outer.subtracted(inner)
        else:
            band = outer  # 너무 작으면 전체를 잡기 영역으로
        title = QPainterPath()
        title.addRect(QRectF(r.left(), r.top(), r.width(), self.TITLE_H))
        return band.united(title)

    def _handle_rects(self) -> dict[str, QRectF]:
        r, hs = self._rect, self.HANDLE
        cx, cy = r.center().x(), r.center().y()
        pts = {
            "nw": (r.left(), r.top()), "n": (cx, r.top()), "ne": (r.right(), r.top()),
            "e": (r.right(), cy), "se": (r.right(), r.bottom()), "s": (cx, r.bottom()),
            "sw": (r.left(), r.bottom()), "w": (r.left(), cy),
        }
        return {k: QRectF(x - hs / 2, y - hs / 2, hs, hs) for k, (x, y) in pts.items()}

    def _handle_at(self, pos: QPointF) -> str | None:
        for name, hr in self._handle_rects().items():
            if hr.contains(pos):
                return name
        return None

    def contained_node_items(self) -> list["NodeItem"]:
        """중심이 그룹 영역 안에 있는 노드 아이템들(기하적 소속)."""
        out = []
        for it in self._scene_ref.node_items.values():
            center = it.mapToScene(it.boundingRect().center())
            if self._rect.contains(center):
                out.append(it)
        return out

    def paint(self, painter, option, widget=None) -> None:
        if self._rect.isEmpty():
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        sel = self.isSelected()
        color = QColor(self.group.color)
        fill = QColor(color)
        fill.setAlpha(38 if sel else 26)
        painter.setBrush(QBrush(fill))
        pen = QPen(color, 2.4 if sel else 1.6)
        pen.setStyle(Qt.PenStyle.SolidLine if sel else Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRoundedRect(self._rect, 14, 14)
        # 제목 칩
        title = self.group.label or "그룹"
        if self.group.mode == "loop":
            title += f"  ×{self.group.loop_count}"
        elif self.group.mode == "iter":
            title += f"  [{len(self.group.iter_values)}]"
        fm = QFontMetrics(painter.font())
        chip_w = min(self._rect.width(), fm.horizontalAdvance(title) + 24)
        chip = QRectF(self._rect.left(), self._rect.top(), chip_w, self.TITLE_H)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawRoundedRect(chip, 9, 9)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.drawText(chip.adjusted(12, 0, -8, 0),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)
        # 선택 시 크기 조절 핸들
        if sel:
            painter.setPen(QPen(color, 1))
            painter.setBrush(QBrush(QColor("#0f172a")))
            for hr in self._handle_rects().values():
                painter.drawRect(hr)

    def _set_rect(self, rect: QRectF) -> None:
        rect = rect.normalized()
        self.prepareGeometryChange()
        self._rect = rect
        g = self.group
        g.x, g.y, g.w, g.h = rect.x(), rect.y(), rect.width(), rect.height()
        self.update()

    def hoverMoveEvent(self, event) -> None:
        name = self._handle_at(event.pos()) if self.isSelected() else None
        if name:
            self.setCursor(self._CURSORS[name])
        else:
            self.unsetCursor()
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)  # 그룹 선택(→ 속성 패널에 그룹 표시)
        self._start_scene = event.scenePos()
        self._start_rect = QRectF(self._rect)
        handle = self._handle_at(event.pos())
        if handle:
            self._mode = handle
        else:
            self._mode = "move"
            self._move_starts = {it.node.id: it.pos() for it in self.contained_node_items()}
        # 영역 이동 시 소속 노드가 함께 움직이므로 드래그 상태로 표시(가벼운 경로 갱신).
        self._scene_ref.begin_interactive_drag(kind="group")
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._mode is None:
            return
        delta = event.scenePos() - self._start_scene
        if self._mode == "move":
            self._set_rect(self._start_rect.translated(delta))
            for it in self.contained_node_items_at_press():
                sp = self._move_starts.get(it.node.id)
                if sp is not None:
                    it.setPos(sp + delta)
        else:
            self._resize(self._mode, delta)
        event.accept()

    def contained_node_items_at_press(self) -> list["NodeItem"]:
        """드래그 시작 시점에 영역에 있던 노드들(이동 대상)."""
        return [it for it in self._scene_ref.node_items.values()
                if it.node.id in self._move_starts]

    def _resize(self, handle: str, delta: QPointF) -> None:
        r = QRectF(self._start_rect)
        if "w" in handle:
            r.setLeft(min(r.left() + delta.x(), r.right() - self.MIN_W))
        if "e" in handle:
            r.setRight(max(r.right() + delta.x(), r.left() + self.MIN_W))
        if "n" in handle:
            r.setTop(min(r.top() + delta.y(), r.bottom() - self.MIN_H))
        if "s" in handle:
            r.setBottom(max(r.bottom() + delta.y(), r.top() + self.MIN_H))
        self._set_rect(r)

    def mouseReleaseEvent(self, event) -> None:
        self._mode = None
        self._move_starts = {}
        self._scene_ref.end_interactive_drag()  # 정밀 라우팅으로 한 번 마무리
        event.accept()

    def nudge(self, dx: float, dy: float) -> list[str]:
        """그룹 영역과 소속 노드를 (dx, dy) 만큼 옮긴다(방향키 미세 이동).

        Returns:
            함께 이동한 소속 노드 id 목록(상위에서 중복 이동을 막는 데 사용).
        """
        members = self.contained_node_items()  # 이동 전 영역 기준으로 소속 판정
        self._set_rect(self._rect.translated(dx, dy))
        delta = QPointF(dx, dy)
        for it in members:
            it.setPos(it.pos() + delta)
        return [it.node.id for it in members]


NOTE_BG = QColor("#fef3c7")        # 메모 배경(연한 앰버 — 다크 캔버스에서 눈에 띔)
NOTE_BG_SEL = QColor("#fde68a")    # 선택 시 배경(한 단계 진한 앰버 — 또렷하게)
NOTE_BORDER = QColor("#f59e0b")    # 메모 테두리
NOTE_BORDER_SEL = QColor("#2563eb")  # 선택 시 테두리(진한 파랑)
NOTE_GLOW = QColor("#3b82f6")      # 선택 시 외곽 글로우 색
NOTE_TEXT = QColor("#1f2937")      # 본문 텍스트(어둡게)
NOTE_PLACEHOLDER = QColor("#a8997a")  # 빈 메모 안내 문구


class _NoteText(QGraphicsTextItem):
    """메모 내부 편집용 텍스트. 포커스를 잃으면 부모에 편집 종료를 알린다."""

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        parent = self.parentItem()
        if isinstance(parent, TextNoteItem):
            parent.end_edit()


class TextNoteItem(QGraphicsObject):
    """시퀀스 실행과 무관한 자유 텍스트 메모(이동/선택/더블클릭 편집)."""

    PAD = 9.0
    MIN_H = 40.0
    SEL_GLOW = 7.0  # 선택 글로우가 카드 밖으로 번질 여백

    def __init__(self, note: TextNote) -> None:
        super().__init__()
        self.note = note
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.setZValue(-1.5)  # 노드보다 뒤, 그룹보다 앞(주석 레이어)
        self.setPos(note.x, note.y)
        self._editing = False
        self._text = _NoteText(self)
        self._text.setPos(self.PAD, self.PAD)
        self._text.setTextWidth(note.w - 2 * self.PAD)
        self._text.setDefaultTextColor(NOTE_TEXT)
        font = QFont(self._text.font())
        font.setPointSizeF(10.0)
        self._text.setFont(font)
        self._text.setPlainText(note.text)
        self._text.document().contentsChanged.connect(self._on_contents_changed)
        self._sync_height()

    def boundingRect(self) -> QRectF:
        # 선택 글로우가 카드 밖에 그려지므로 그만큼 여백을 포함한다.
        m = self.SEL_GLOW
        return QRectF(-m, -m, self.note.w + 2 * m, self.note.h + 2 * m)

    def shape(self) -> QPainterPath:
        # 클릭/선택 히트 영역은 글로우 여백을 빼고 카드 본체로만 한정한다.
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self.note.w, self.note.h), 8.0, 8.0)
        return path

    def _sync_height(self) -> None:
        """텍스트 양에 맞춰 메모 높이를 조정한다."""
        h = max(self.MIN_H, self._text.boundingRect().height() + 2 * self.PAD)
        if abs(h - self.note.h) > 0.5:
            self.prepareGeometryChange()
            self.note.h = h
            self.update()

    def _on_contents_changed(self) -> None:
        self.note.text = self._text.toPlainText()
        self._sync_height()

    def paint(self, painter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0, 0, self.note.w, self.note.h)
        sel = self.isSelected()
        # 선택: 외곽 글로우(반투명 파랑 동심 링) — 밝은 메모에서도 또렷하게 보이도록.
        if sel:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for width, alpha in ((7.0, 45), (4.5, 90), (2.5, 160)):
                glow = QColor(NOTE_GLOW)
                glow.setAlpha(alpha)
                painter.setPen(QPen(glow, width))
                pad = width / 2.0
                painter.drawRoundedRect(rect.adjusted(-pad, -pad, pad, pad), 10.0, 10.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 8.0, 8.0)
        painter.fillPath(path, QBrush(NOTE_BG_SEL if sel else NOTE_BG))
        painter.setPen(QPen(NOTE_BORDER_SEL if sel else NOTE_BORDER, 2.6 if sel else 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 8.0, 8.0)
        if not self.note.text and not self._editing:
            painter.setPen(QPen(NOTE_PLACEHOLDER))
            painter.drawText(rect.adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD),
                             Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                             "더블클릭하여 편집")

    def mouseDoubleClickEvent(self, event) -> None:
        self.begin_edit()
        event.accept()

    def begin_edit(self) -> None:
        """편집 모드로 전환한다(텍스트 입력 가능)."""
        self._editing = True
        self._text.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self._text.setFocus(Qt.FocusReason.MouseFocusReason)
        self.update()

    def end_edit(self) -> None:
        """편집을 끝내고 모델에 반영한다."""
        self._editing = False
        self._text.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.note.text = self._text.toPlainText()
        self.update()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.note.x = self.pos().x()
            self.note.y = self.pos().y()
        return super().itemChange(change, value)


class SequenceScene(QGraphicsScene):
    """노드/엣지 아이템을 보관하고 포트 드래그 연결을 처리한다."""

    selection_changed = Signal(object)  # 선택된 NodeItem 또는 None

    def __init__(self, sequence: Sequence) -> None:
        super().__init__()
        self.sequence = sequence
        self.node_items: dict[str, NodeItem] = {}
        self.edge_items: list[EdgeItem] = []
        # 노드 id → 그 노드에 연결된 엣지 아이템들. 드래그 시 움직인 노드의
        # 엣지만 골라 갱신하기 위한 인덱스(전체 순회 회피).
        self._edges_by_node: dict[str, list[EdgeItem]] = {}
        self.group_items: list[GroupItem] = []
        self.note_items: list[TextNoteItem] = []
        self._group_counter = len(sequence.groups)
        self._drag_from: tuple[str, str] | None = None  # 새 엣지 시작 (node_id, port)
        self._drag_from_side: str | None = None         # 새 엣지 출력 변
        self._reroute: tuple[EdgeItem, str] | None = None  # 끝점 재연결 (엣지, "in"|"out")
        self._temp_edge: EdgeItem | None = None
        # 노드/그룹 이동 드래그 진행 상태. 드래그 중에는 가벼운 경로(A* 생략)만
        # 그리고, 놓는 순간 정밀 경로로 한 번만 다시 그린다.
        self._drag_active = False
        self._drag_moved = False
        self._drag_kind: str | None = None       # "node" | "group"
        self._drag_single = False                 # 단일 노드 드래그(스냅 정렬 적용 대상)
        self._drag_start_pos: dict[str, QPointF] = {}  # 드래그 시작 시 노드 위치 스냅샷
        self._drag_start_grect: dict[str, QRectF] = {}  # 드래그 시작 시 그룹 사각형 스냅샷
        self._drag_group_members: dict[str, list[str]] = {}  # 그룹별 소속 노드(시작 시점)
        self._following = False                    # 그룹 추종 이동 중 재진입 방지
        # 겹침 시 시작 위치로 되돌리거나, 버튼 정렬로 이동할 때 쓰는 애니메이션.
        self._animating = False
        self._node_anim: QVariantAnimation | None = None
        # 드래그 중 자석 정렬 안내선: [("v"|"h", 씬좌표), ...]
        self._snap_guides: list[tuple[str, float]] = []
        self.rebuild()
        self.selectionChanged.connect(self._on_selection)

    def drawBackground(self, painter, rect) -> None:
        """캔버스에 도트 그리드 배경을 그려 평면적인 느낌을 줄인다."""
        painter.fillRect(rect, CANVAS_BG)
        step = 26
        left = int(rect.left()) - (int(rect.left()) % step)
        top = int(rect.top()) - (int(rect.top()) % step)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(CANVAS_GRID))
        y = top
        while y < rect.bottom():
            x = left
            while x < rect.right():
                painter.drawEllipse(QPointF(x, y), 1.1, 1.1)
                x += step
            y += step

    def rebuild(self) -> None:
        """모델로부터 모든 아이템을 다시 생성한다."""
        self.clear()
        self.node_items.clear()
        self.edge_items.clear()
        self._edges_by_node.clear()
        self.group_items.clear()
        self.note_items.clear()
        for node in self.sequence.nodes:
            item = NodeItem(node)
            self.addItem(item)
            self.node_items[node.id] = item
        for edge in self.sequence.edges:
            self._add_edge_item(edge)
        for group in self.sequence.groups:
            self._add_group_item(group)
        for note in self.sequence.notes:
            self._add_note_item(note)
        self.refresh_edges()

    def _add_note_item(self, note: TextNote) -> "TextNoteItem":
        item = TextNoteItem(note)
        self.addItem(item)
        self.note_items.append(item)
        return item

    def add_note(self, note: TextNote) -> "TextNoteItem":
        """새 텍스트 메모를 모델과 씬에 추가한다."""
        self.sequence.notes.append(note)
        return self._add_note_item(note)

    def _add_edge_item(self, edge: Edge) -> None:
        item = EdgeItem(edge)
        self.addItem(item)
        self.edge_items.append(item)
        # 양 끝 노드 인덱스에 등록(증분 갱신용).
        self._edges_by_node.setdefault(edge.from_node, []).append(item)
        self._edges_by_node.setdefault(edge.to_node, []).append(item)

    def _add_group_item(self, group: Group) -> GroupItem:
        item = GroupItem(group, self)
        self.addItem(item)
        self.group_items.append(item)
        return item

    def begin_interactive_drag(self, kind: str = "node") -> None:
        """노드/그룹 이동 드래그 시작. 이동 중에는 가벼운 경로만 갱신한다.

        드롭 시 겹침을 판정·복귀하기 위해 모든 노드/그룹의 시작 상태를 스냅샷한다.
        kind="node" 일 때만 단일 노드 드래그에서 자석 정렬(스냅)을 적용한다.
        """
        self._stop_node_anim()
        self._drag_active = True
        self._drag_moved = False
        self._drag_kind = kind
        self._drag_single = kind == "node" and len(self._selected_node_items()) == 1
        self._drag_start_pos = {nid: it.pos() for nid, it in self.node_items.items()}
        self._drag_start_grect = {gi.group.id: QRectF(gi._rect) for gi in self.group_items}
        # 그룹별 소속 노드 스냅샷 — 다중선택 드래그에서 선택된 그룹을 한 덩어리로
        # 따라 움직일 때, 선택되지 않은 멤버 노드도 함께 옮기기 위함.
        self._drag_group_members = {
            gi.group.id: [it.node.id for it in gi.contained_node_items()]
            for gi in self.group_items
        }
        # 노드 드래그(rubber band 등)는 내용물만 옮긴다. 그룹을 통째로 옮기려면
        # 그룹 프레임을 직접 드래그한다(kind="group").

    def end_interactive_drag(self) -> None:
        """드래그 종료. 겹침(노드↔노드 / 노드↔그룹 / 그룹↔그룹)이 생기면 시작
        상태로 부드럽게 되돌리고, 아니면 전체 경로를 정밀(A*)하게 한 번 다시 그린다."""
        if not self._drag_active:
            return
        self._drag_active = False
        kind = self._drag_kind
        self._drag_moved = False
        self._drag_kind = None
        self._set_snap_guides([])
        moved_nodes = self._moved_node_ids()
        moved_groups = self._moved_group_ids()
        if not moved_nodes and not moved_groups:
            return
        if self._drag_has_conflict(kind, moved_nodes, moved_groups):
            self._revert_drag(moved_nodes, moved_groups)
            return
        self.refresh_edges()
        self.refresh_groups()

    def node_moved(self, item: "NodeItem") -> None:
        """노드 위치 변경 통지. 드래그/복귀 애니메이션 중에는 가볍게 갱신한다."""
        if self._drag_active or self._animating:
            if self._drag_active:
                self._drag_moved = True
                if not self._following:
                    self._follow_selected_groups(item)
            self.refresh_edges_for(item.node.id, fast=True)
        else:
            # 드래그가 아닌 단발 이동(프로그램적 setPos 등): 정밀하게 갱신.
            self.refresh_edges_for(item.node.id, fast=False)

    def _follow_selected_groups(self, item: "NodeItem") -> None:
        """노드 드래그 시, 함께 선택된 그룹을 같은 변위만큼 따라 움직인다.

        다중선택(그룹 프레임 + 노드들)으로 끌 때 그룹이 한 덩어리처럼 따라오게
        한다. 그룹 영역과 그 안의 (선택되지 않은) 멤버 노드까지 동일 delta 로
        옮겨, 상대 위치를 보존한다. setPos 재진입은 _following 으로 막는다.
        """
        if self._drag_kind != "node":
            return  # 그룹 프레임 자체 드래그(kind="group")는 자체 처리됨
        start = self._drag_start_pos.get(item.node.id)
        if start is None:
            return
        delta = item.pos() - start
        if delta.isNull():
            return
        self._following = True
        try:
            for gi in self.group_items:
                if not gi.isSelected():
                    continue
                g0 = self._drag_start_grect.get(gi.group.id)
                if g0 is not None:
                    gi._set_rect(QRectF(g0).translated(delta))
                # 그룹 안의 선택 안 된 멤버 노드도 함께 이동(이미 선택된 노드는
                # Qt 가 같은 delta 로 옮기므로 건드리지 않는다).
                for nid in self._drag_group_members.get(gi.group.id, []):
                    ni = self.node_items.get(nid)
                    if ni is None or ni.isSelected():
                        continue
                    sp = self._drag_start_pos.get(nid)
                    if sp is not None:
                        ni.setPos(sp + delta)
        finally:
            self._following = False

    def nudge_selection(self, dx: float, dy: float) -> None:
        """선택된 노드/그룹/메모를 (dx, dy) 만큼 미세 이동한다(방향키 세부조정).

        드래그가 아니므로 자석 정렬(스냅)이 적용되지 않아 1px 단위로 정밀하게
        옮길 수 있다. 그룹은 영역과 소속 노드를 함께 옮기며, 그룹으로 이미 옮긴
        노드가 따로 선택돼 있어도 두 번 옮기지 않는다.
        """
        sel = self.selectedItems()
        groups = [it for it in sel if isinstance(it, GroupItem)]
        nodes = [it for it in sel if isinstance(it, NodeItem)]
        notes = [it for it in sel if isinstance(it, TextNoteItem)]
        if not (groups or nodes or notes):
            return
        delta = QPointF(dx, dy)
        moved_ids: set[str] = set()
        for gi in groups:
            moved_ids.update(gi.nudge(dx, dy))
        for it in nodes:
            if it.node.id in moved_ids:
                continue
            it.setPos(it.pos() + delta)
        for it in notes:
            it.setPos(it.pos() + delta)
        self.refresh_edges()
        self.refresh_groups()

    def _selected_node_items(self) -> list["NodeItem"]:
        """현재 선택된 노드 아이템 목록."""
        return [i for i in self.selectedItems() if isinstance(i, NodeItem)]

    def _group_item_by_id(self, gid: str) -> "GroupItem | None":
        for gi in self.group_items:
            if gi.group.id == gid:
                return gi
        return None

    # --- 겹침 판정 ---
    def _moved_node_ids(self) -> list[str]:
        """드래그 시작 위치 대비 실제로 움직인 노드 id 목록."""
        out = []
        for nid, it in self.node_items.items():
            start = self._drag_start_pos.get(nid)
            if start is not None and (it.pos() - start).manhattanLength() > 0.5:
                out.append(nid)
        return out

    def _moved_group_ids(self) -> list[str]:
        """드래그 시작 사각형 대비 이동/크기변경된 그룹 id 목록."""
        out = []
        for gi in self.group_items:
            s = self._drag_start_grect.get(gi.group.id)
            r = gi._rect
            if s is not None and (abs(s.x() - r.x()) > 0.5 or abs(s.y() - r.y()) > 0.5
                                  or abs(s.width() - r.width()) > 0.5
                                  or abs(s.height() - r.height()) > 0.5):
                out.append(gi.group.id)
        return out

    @staticmethod
    def _rects_overlap(a: QRectF, b: QRectF) -> bool:
        """두 사각형이 면적 기준으로 겹치는지(가장자리 접촉은 무시)."""
        inter = a.intersected(b)
        return inter.width() > 1.0 and inter.height() > 1.0

    def _node_has_conflict(self, item: "NodeItem") -> bool:
        """노드가 다른 노드와 겹치거나, 멤버가 아닌 그룹 영역에 걸치면 True.

        노드 중심이 그룹 영역 안이면 그 그룹의 멤버(정상)로 보고 무시한다 —
        노드를 그룹 안으로 끌어다 넣는 동작을 막지 않기 위함.
        """
        nr = item.body_scene_rect(margin=0.0)
        for other in self.node_items.values():
            if other is not item and self._rects_overlap(nr, other.body_scene_rect(margin=0.0)):
                return True
        center = item.mapToScene(item.boundingRect().center())
        for gi in self.group_items:
            if self._rects_overlap(nr, gi._rect) and not gi._rect.contains(center):
                return True
        return False

    def _group_has_conflict(self, gi: "GroupItem") -> bool:
        """그룹 영역이 다른 그룹과 겹치거나, 멤버가 아닌 노드에 걸치면 True."""
        gr = gi._rect
        for other in self.group_items:
            if other is not gi and self._rects_overlap(gr, other._rect):
                return True
        for it in self.node_items.values():
            center = it.mapToScene(it.boundingRect().center())
            if self._rects_overlap(gr, it.body_scene_rect(margin=0.0)) and not gr.contains(center):
                return True
        return False

    def _drag_has_conflict(self, kind: str | None,
                           moved_nodes: list[str], moved_groups: list[str]) -> bool:
        """이번 드래그 결과 겹침이 생겼는지 판정한다(움직인 노드·그룹 모두 검사)."""
        if any(self._node_has_conflict(self.node_items[nid]) for nid in moved_nodes):
            return True
        return any(self._group_has_conflict(gi) for gi in self.group_items
                   if gi.group.id in moved_groups)

    # --- 시작 상태 복귀 / 정렬 애니메이션 (ease in/out 통합 트윈) ---
    REVERT_MS = 220  # 애니메이션 길이(ms)

    def _revert_drag(self, moved_nodes: list[str], moved_groups: list[str]) -> None:
        """움직인 노드/그룹을 드래그 시작 상태로 부드럽게 되돌린다."""
        node_targets = {nid: self._drag_start_pos[nid] for nid in moved_nodes
                        if nid in self._drag_start_pos}
        group_targets = {gid: self._drag_start_grect[gid] for gid in moved_groups
                         if gid in self._drag_start_grect}
        self._start_tween(node_targets, group_targets)

    def _start_tween(self, node_targets: dict[str, QPointF],
                     group_targets: dict[str, QRectF]) -> None:
        """노드 위치 + 그룹 사각형을 동시에 보간하는 ease in/out 애니메이션."""
        self._stop_node_anim()
        node_plan = [(self.node_items[nid], self.node_items[nid].pos(), end)
                     for nid, end in node_targets.items()
                     if nid in self.node_items
                     and (self.node_items[nid].pos() - end).manhattanLength() > 0.5]
        group_plan = []
        for gid, end in group_targets.items():
            gi = self._group_item_by_id(gid)
            if gi is not None:
                group_plan.append((gi, QRectF(gi._rect), end))
        if not node_plan and not group_plan:
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(self.REVERT_MS)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.valueChanged.connect(
            lambda v: self._apply_tween(float(v), node_plan, group_plan))
        anim.finished.connect(self._on_anim_finished)
        self._animating = True
        self._node_anim = anim
        anim.start()

    @staticmethod
    def _apply_tween(t: float, node_plan: list, group_plan: list) -> None:
        """보간 t(0..1)에 따라 노드 위치/그룹 사각형을 설정한다."""
        for it, start, end in node_plan:
            it.setPos(start + (end - start) * t)
        for gi, s, e in group_plan:
            gi._set_rect(QRectF(
                s.x() + (e.x() - s.x()) * t, s.y() + (e.y() - s.y()) * t,
                s.width() + (e.width() - s.width()) * t,
                s.height() + (e.height() - s.height()) * t))

    def _on_anim_finished(self) -> None:
        """애니메이션 종료 → 정밀 경로로 마무리."""
        self._animating = False
        self._node_anim = None
        self.refresh_edges()
        self.refresh_groups()

    def _stop_node_anim(self) -> None:
        """진행 중인 노드/그룹 애니메이션을 중단한다(새 드래그/정렬 시작 시)."""
        if self._node_anim is not None:
            self._node_anim.stop()
            self._node_anim = None
        self._animating = False

    # --- 다중 선택 정렬 / 간격 균등 ---
    def _alignment_units(self) -> list[dict]:
        """정렬/간격의 단위 목록.

        선택된 그룹은 박스 하나(그 영역 사각형)로 다루고, 그룹에 속하지 않은
        선택 노드는 각각 하나의 단위로 본다. 그룹의 멤버 노드는 독립 단위가 아니라
        그룹과 함께 같은 양만큼 움직여(상대 위치 유지) 그룹을 노드처럼 정렬한다.

        Returns:
            각 단위의 {"rect": 박스, "nodes": 함께 옮길 노드 id, "gid": 그룹id|None}.
        """
        sel_groups = [i for i in self.selectedItems() if isinstance(i, GroupItem)]
        units: list[dict] = []
        grouped: set[str] = set()
        for gi in sel_groups:
            members = [it.node.id for it in gi.contained_node_items()]
            grouped.update(members)
            units.append({"rect": QRectF(gi._rect), "nodes": members, "gid": gi.group.id})
        for it in self._selected_node_items():
            if it.node.id in grouped:
                continue
            units.append({"rect": it.body_scene_rect(margin=0.0),
                          "nodes": [it.node.id], "gid": None})
        return units

    @staticmethod
    def _is_single_group(units: list[dict]) -> bool:
        """선택이 그룹 하나뿐인지(→ 그 그룹 내부 노드를 정렬하는 모드)."""
        return len(units) == 1 and units[0]["gid"] is not None

    def alignment_unit_count(self) -> int:
        """정렬 단위 개수. 그룹 하나만 선택했으면 그 내부 노드 수로 환산한다.

        (그룹 하나 선택 = 내부 노드 정렬 모드이므로 버튼 활성화 기준이 멤버 수)
        """
        units = self._alignment_units()
        if self._is_single_group(units):
            return len(units[0]["nodes"])
        return len(units)

    def _unit_delta(self, unit: dict, dx: float, dy: float,
                    node_targets: dict[str, QPointF],
                    group_targets: dict[str, QRectF]) -> None:
        """단위를 (dx, dy) 만큼 옮기는 목표를 누적한다(소속 노드 + 그룹 사각형)."""
        for nid in unit["nodes"]:
            it = self.node_items.get(nid)
            if it is not None:
                node_targets[nid] = it.pos() + QPointF(dx, dy)
        if unit["gid"] is not None:
            group_targets[unit["gid"]] = unit["rect"].translated(dx, dy)

    def align_selected(self, mode: str) -> None:
        """선택 단위(노드/그룹)를 한 변/중심선 기준으로 정렬한다.

        Args:
            mode: left/right/cx(가로중앙) / top/bottom/cy(세로중앙).
        """
        units = self._alignment_units()
        if self._is_single_group(units):
            self._align_node_ids(units[0]["nodes"], mode)  # 그룹 하나 → 내부 노드 정렬
            return
        if len(units) < 2:
            return
        lefts = [u["rect"].left() for u in units]
        rights = [u["rect"].right() for u in units]
        tops = [u["rect"].top() for u in units]
        bottoms = [u["rect"].bottom() for u in units]
        node_targets: dict[str, QPointF] = {}
        group_targets: dict[str, QRectF] = {}
        for u in units:
            r = u["rect"]
            dx = dy = 0.0
            if mode == "left":
                dx = min(lefts) - r.left()
            elif mode == "right":
                dx = max(rights) - r.right()
            elif mode == "cx":
                dx = (min(lefts) + max(rights)) / 2 - r.center().x()
            elif mode == "top":
                dy = min(tops) - r.top()
            elif mode == "bottom":
                dy = max(bottoms) - r.bottom()
            elif mode == "cy":
                dy = (min(tops) + max(bottoms)) / 2 - r.center().y()
            self._unit_delta(u, dx, dy, node_targets, group_targets)
        self._start_tween(node_targets, group_targets)

    def distribute_selected(self, orient: str) -> None:
        """선택 단위(노드/그룹)를 가장자리 간격이 같도록 균등 배치한다.

        Args:
            orient: "h"(가로) 또는 "v"(세로).
        """
        units = self._alignment_units()
        if self._is_single_group(units):
            self._distribute_node_ids(units[0]["nodes"], orient)  # 그룹 하나 → 내부 노드
            return
        if len(units) < 3:
            return
        node_targets: dict[str, QPointF] = {}
        group_targets: dict[str, QRectF] = {}
        if orient == "h":
            units.sort(key=lambda u: u["rect"].left())
            span = units[-1]["rect"].right() - units[0]["rect"].left()
            gap = (span - sum(u["rect"].width() for u in units)) / (len(units) - 1)
            x = units[0]["rect"].left()
            for u in units:
                self._unit_delta(u, x - u["rect"].left(), 0.0, node_targets, group_targets)
                x += u["rect"].width() + gap
        else:
            units.sort(key=lambda u: u["rect"].top())
            span = units[-1]["rect"].bottom() - units[0]["rect"].top()
            gap = (span - sum(u["rect"].height() for u in units)) / (len(units) - 1)
            y = units[0]["rect"].top()
            for u in units:
                self._unit_delta(u, 0.0, y - u["rect"].top(), node_targets, group_targets)
                y += u["rect"].height() + gap
        self._start_tween(node_targets, group_targets)

    def _align_node_ids(self, ids: list[str], mode: str) -> None:
        """주어진 노드들끼리 한 변/중심선 기준으로 정렬한다(그룹 내부 정렬용)."""
        items = [self.node_items[i] for i in ids if i in self.node_items]
        if len(items) < 2:
            return
        lefts = [it.pos().x() for it in items]
        rights = [it.pos().x() + NODE_W for it in items]
        tops = [it.pos().y() for it in items]
        bottoms = [it.pos().y() + it._height for it in items]
        targets: dict[str, QPointF] = {}
        for it in items:
            p, h = it.pos(), it._height
            nx, ny = p.x(), p.y()
            if mode == "left":
                nx = min(lefts)
            elif mode == "right":
                nx = max(rights) - NODE_W
            elif mode == "cx":
                nx = (min(lefts) + max(rights)) / 2 - NODE_W / 2
            elif mode == "top":
                ny = min(tops)
            elif mode == "bottom":
                ny = max(bottoms) - h
            elif mode == "cy":
                ny = (min(tops) + max(bottoms)) / 2 - h / 2
            targets[it.node.id] = QPointF(nx, ny)
        self._start_tween(targets, {})

    def _distribute_node_ids(self, ids: list[str], orient: str) -> None:
        """주어진 노드들을 가장자리 간격이 같도록 균등 배치한다(그룹 내부용)."""
        items = [self.node_items[i] for i in ids if i in self.node_items]
        if len(items) < 3:
            return
        targets: dict[str, QPointF] = {}
        if orient == "h":
            items.sort(key=lambda it: it.pos().x())
            span = (items[-1].pos().x() + NODE_W) - items[0].pos().x()
            gap = (span - NODE_W * len(items)) / (len(items) - 1)
            x = items[0].pos().x()
            for it in items:
                targets[it.node.id] = QPointF(x, it.pos().y())
                x += NODE_W + gap
        else:
            items.sort(key=lambda it: it.pos().y())
            span = (items[-1].pos().y() + items[-1]._height) - items[0].pos().y()
            gap = (span - sum(it._height for it in items)) / (len(items) - 1)
            y = items[0].pos().y()
            for it in items:
                targets[it.node.id] = QPointF(it.pos().x(), y)
                y += it._height + gap
        self._start_tween(targets, {})

    # --- 드래그 중 자석 정렬(스냅) ---
    SNAP_DIST = 7.0       # 스냅이 걸리는 거리(씬 좌표)
    SNAP_MIN_DRAG = 4.0   # 이만큼 끌기 전에는 가이드를 표시하지 않음(클릭과 구분)

    def snap_position(self, item: "NodeItem", pos: QPointF) -> QPointF:
        """단일 노드 드래그 중 다른 노드의 변/중심선에 가까우면 그 좌표로 스냅한다."""
        if self._animating or not self._drag_active or not self._drag_single:
            self._set_snap_guides([])
            return pos
        # 실제로 끌기 시작한 뒤에만 가이드를 보인다(클릭/미세 떨림에선 표시 안 함).
        start = self._drag_start_pos.get(item.node.id)
        if start is not None and (pos - start).manhattanLength() < self.SNAP_MIN_DRAG:
            self._set_snap_guides([])
            return pos
        guides: list[tuple[str, float]] = []
        nx = self._snap_axis(pos.x(), NODE_W, item, "x", guides)
        ny = self._snap_axis(pos.y(), item._height, item, "y", guides)
        self._set_snap_guides(guides)
        return QPointF(nx, ny)

    def _snap_axis(self, coord: float, size: float, item: "NodeItem",
                   axis: str, guides: list[tuple[str, float]]) -> float:
        """한 축(x 또는 y)에서 가장 가까운 정렬선에 스냅한 좌표를 반환한다."""
        moving = (coord, coord + size / 2, coord + size)  # near, center, far
        best: tuple[float, float, float] | None = None  # (거리, 스냅좌표, 가이드선)
        for other in self.node_items.values():
            if other is item:
                continue
            op = other.pos()
            base = op.x() if axis == "x" else op.y()
            osize = NODE_W if axis == "x" else other._height
            targets = (base, base + osize / 2, base + osize)
            for k in range(3):
                d = abs(moving[k] - targets[k])
                if d <= self.SNAP_DIST and (best is None or d < best[0]):
                    snapped = targets[k] - (0.0, size / 2, size)[k]
                    best = (d, snapped, targets[k])
        if best is None:
            return coord
        guides.append(("v" if axis == "x" else "h", best[2]))
        return best[1]

    def _set_snap_guides(self, guides: list[tuple[str, float]]) -> None:
        """자석 정렬 안내선을 갱신한다(변할 때만 다시 그림).

        가이드선은 뷰포트 전체 높이/폭으로 그려지므로, sceneRect 만 무효화하는
        self.update() 로는 화면 가장자리에 잔상이 남는다. 각 뷰의 viewport 전체를
        다시 그려 완전히 지운다.
        """
        if guides == self._snap_guides:
            return
        self._snap_guides = guides
        for view in self.views():
            view.viewport().update()

    def drawForeground(self, painter, rect) -> None:
        """드래그 중 자석 정렬 안내선을 화면 위에 점선으로 그린다."""
        if not self._snap_guides:
            return
        pen = QPen(QColor("#f472b6"), 0)  # cosmetic 1px(줌 무관)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        for orient, c in self._snap_guides:
            if orient == "v":
                painter.drawLine(QPointF(c, rect.top()), QPointF(c, rect.bottom()))
            else:
                painter.drawLine(QPointF(rect.left(), c), QPointF(rect.right(), c))

    def _all_body_rects(self) -> dict[str, QRectF]:
        """모든 노드의 씬 좌표 본체 사각형(엣지 우회 장애물용)."""
        return {nid: it.body_scene_rect() for nid, it in self.node_items.items()}

    def _refresh_edge(self, item: EdgeItem,
                      rects: dict[str, QRectF] | None, fast: bool,
                      override: tuple[str, float | None] | None = None) -> None:
        """엣지 하나의 경로를 갱신한다. fast=True 면 A* 우회 없이 가볍게 그린다.

        Args:
            item: 갱신할 엣지 아이템.
            rects: 노드별 본체 사각형(정밀 모드 장애물). fast 모드면 None.
            fast: True 면 장애물/A* 없이 기본 경로만 그린다(드래그 중 사용).
            override: 팬아웃 정렬용 (강제 출력 변, 공통 채널 좌표). None 이면 자동.
        """
        src = self.node_items.get(item.edge.from_node)
        dst = self.node_items.get(item.edge.to_node)
        if not (src and dst):
            return
        # 명시 지정된 변(from_side/to_side)이 있으면 그 연결점을 쓰고, 없으면 자동.
        # 팬아웃 정렬 override 가 있으면 출력 변을 그쪽으로 통일한다.
        force_side, channel = override if override else (None, None)
        p1, es = src.output_point(item.edge.from_port, toward=dst.center_scene(),
                                  side=item.edge.from_side or force_side)
        p2, en = dst.input_point(toward=src.center_scene(), side=item.edge.to_side)
        if fast or rects is None:
            item.update_path(p1, p2, es, en, channel=channel)  # 장애물 없음 → 가벼움
            return
        obstacles = [r for nid, r in rects.items()
                     if nid != item.edge.from_node and nid != item.edge.to_node]
        # 양 끝 노드는 A* 전용 장애물(작은 여백)로 — 선이 자기 노드 위를 지나지 않게.
        endpoints = [src.body_scene_rect(margin=6.0), dst.body_scene_rect(margin=6.0)]
        item.update_path(p1, p2, es, en, obstacles, endpoints, channel=channel)

    def _fanout_overrides(self) -> dict[EdgeItem, tuple[str, float | None]]:
        """같은 출력 포트에서 여러 노드로 나가는 엣지를 한 변·공통 채널로 정렬한다.

        팬아웃이 타깃마다 다른 변(bottom/right)에서 나가거나 꺾이는 높이가
        달라 비대칭으로 보이는 걸 막는다. 사용자가 변을 직접 지정(from_side)한
        엣지는 건드리지 않는다.

        Returns:
            엣지 아이템 → (강제 출력 변, 공통 채널 좌표 또는 None) 매핑.
            팬아웃이 아닌(포트당 1개) 엣지는 포함하지 않는다.
        """
        groups: dict[tuple[str, str], list[EdgeItem]] = defaultdict(list)
        for item in self.edge_items:
            groups[(item.edge.from_node, item.edge.from_port)].append(item)

        overrides: dict[EdgeItem, tuple[str, float | None]] = {}
        for (from_node, from_port), items in groups.items():
            if len(items) < 2:
                continue
            src = self.node_items.get(from_node)
            if not src or any(it.edge.from_side for it in items):
                continue
            pairs = [(it, self.node_items.get(it.edge.to_node)) for it in items]
            pairs = [(it, d) for it, d in pairs if d]
            if len(pairs) < 2:
                continue
            # 타깃 무게중심 방향으로 공통 출력 변을 정한다.
            cx = sum(d.center_scene().x() for _, d in pairs) / len(pairs)
            cy = sum(d.center_scene().y() for _, d in pairs) / len(pairs)
            p1, es = src.output_point(from_port, toward=QPointF(cx, cy))
            ins = [d.input_point(toward=p1, side=it.edge.to_side)[0]
                   for it, d in pairs]
            # 공통 채널: 출력점과 가장 가까운 타깃 입력점 사이(모두 진행 방향일 때만).
            channel: float | None = None
            if es == "bottom":
                tops = [q.y() for q in ins]
                if all(t > p1.y() for t in tops):
                    channel = (p1.y() + min(tops)) / 2.0
            elif es == "right":
                lefts = [q.x() for q in ins]
                if all(left > p1.x() for left in lefts):
                    channel = (p1.x() + min(lefts)) / 2.0
            for it, _ in pairs:
                overrides[it] = (es, channel)
        return overrides

    def refresh_edges(self, fast: bool = False) -> None:
        """모든 엣지 경로를 양 끝 노드의 가장 가까운 In/Out 연결점에 맞추고,
        다른 노드와 겹치면 우회하도록 갱신한다. fast=True 면 우회를 생략한다."""
        rects = None if fast else self._all_body_rects()
        overrides = self._fanout_overrides()
        for item in self.edge_items:
            self._refresh_edge(item, rects, fast=fast, override=overrides.get(item))

    def refresh_edges_for(self, node_id: str, fast: bool = False) -> None:
        """특정 노드에 연결된 엣지만 갱신한다(드래그 중 증분 갱신용)."""
        items = self._edges_by_node.get(node_id)
        if not items:
            return
        rects = None if fast else self._all_body_rects()
        for item in items:
            self._refresh_edge(item, rects, fast=fast)

    def refresh_groups(self) -> None:
        """모든 그룹 영역을 소속 노드 위치에 맞춰 다시 계산한다."""
        for item in self.group_items:
            item.recompute()

    def group_selected(self) -> None:
        """선택된 2개 이상의 노드를 감싸는 그룹 영역을 만든다."""
        items = [it for it in self.selectedItems() if isinstance(it, NodeItem)]
        if len(items) < 2:
            return
        rect = None
        for it in items:
            br = it.mapToScene(it.boundingRect()).boundingRect()
            rect = br if rect is None else rect.united(br)
        rect = rect.adjusted(-GroupItem.PAD, -GroupItem.PAD - GroupItem.TITLE_H,
                             GroupItem.PAD, GroupItem.PAD)
        self._group_counter += 1
        color = ["#3b82f6", "#22c55e", "#a855f7", "#f59e0b", "#ef4444"][self._group_counter % 5]
        group = Group(id=f"g{self._group_counter}", label=f"그룹 {self._group_counter}",
                      x=rect.x(), y=rect.y(), w=rect.width(), h=rect.height(),
                      color=color, members=[it.node.id for it in items])
        self.sequence.groups.append(group)
        item = self._add_group_item(group)
        # 새 그룹을 선택 상태로 만들어 속성 패널에서 바로 이름을 지정할 수 있게 한다.
        self.clearSelection()
        item.setSelected(True)

    def ungroup_selected(self) -> None:
        """선택된 그룹, 또는 선택된 노드를 포함하는 그룹(들)을 해제한다."""
        remove_ids = {gi.group.id for gi in self.selectedItems() if isinstance(gi, GroupItem)}
        centers = [it.mapToScene(it.boundingRect().center())
                   for it in self.selectedItems() if isinstance(it, NodeItem)]
        for gi in self.group_items:
            g = gi.group
            if any(QRectF(g.x, g.y, g.w, g.h).contains(c) for c in centers):
                remove_ids.add(g.id)
        if not remove_ids:
            return
        self.sequence.groups = [g for g in self.sequence.groups if g.id not in remove_ids]
        self._rebuild_groups()

    def _rebuild_groups(self) -> None:
        """그룹 아이템만 다시 생성한다(노드/엣지는 그대로)."""
        for item in self.group_items:
            self.removeItem(item)
        self.group_items.clear()
        for group in self.sequence.groups:
            self._add_group_item(group)

    def _port_at(self, scene_pos: QPointF) -> tuple[str, str, str] | None:
        """씬 좌표 근처의 출력 (node_id, port, 변) 을 찾는다."""
        for node_id, item in self.node_items.items():
            hit = item.out_hit_side(scene_pos)
            if hit is not None:
                return (node_id, hit[0], hit[1])
        return None

    def _input_hit(self, scene_pos: QPointF) -> tuple[str, str] | None:
        """씬 좌표 근처의 입력 (node_id, 변) 을 찾는다."""
        for node_id, item in self.node_items.items():
            side = item.in_hit_side(scene_pos)
            if side is not None:
                return (node_id, side)
        return None

    def _edge_endpoint_at(self, scene_pos: QPointF) -> tuple[EdgeItem, str] | None:
        """엣지 끝점 근처면 (엣지, "in"|"out"). IN 끝은 항상, OUT 끝은 선택된 엣지만."""
        thr = PORT_R * 2
        for item in self.edge_items:
            if item.p2 is not None and (item.p2 - scene_pos).manhattanLength() <= thr:
                return (item, "in")
        for item in self.edge_items:
            if (item.isSelected() and item.p1 is not None
                    and (item.p1 - scene_pos).manhattanLength() <= thr):
                return (item, "out")
        return None

    def _reindex_edges(self) -> None:
        """엣지 인덱스를 현재 from/to 노드 기준으로 다시 만든다(재연결 후)."""
        self._edges_by_node.clear()
        for item in self.edge_items:
            self._edges_by_node.setdefault(item.edge.from_node, []).append(item)
            self._edges_by_node.setdefault(item.edge.to_node, []).append(item)

    def mousePressEvent(self, event) -> None:
        pos = event.scenePos()
        # 1) 기존 엣지의 끝점을 잡았으면 재연결 모드로 시작
        ep = self._edge_endpoint_at(pos)
        if ep is not None:
            self._reroute = ep
            ep[0].setVisible(False)  # 원본 숨기고 임시선만 표시
            self._temp_edge = EdgeItem(Edge("", "", ""), temp=True)
            self.addItem(self._temp_edge)
            event.accept()
            return
        # 2) 출력 포트에서 새 엣지 시작
        hit = self._port_at(pos)
        if hit is not None:
            self._drag_from = (hit[0], hit[1])
            self._drag_from_side = hit[2]
            self._temp_edge = EdgeItem(Edge(hit[0], hit[1], ""), temp=True)
            self.addItem(self._temp_edge)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.scenePos()
        if self._temp_edge is not None and self._reroute is not None:
            self._draw_reroute_preview(pos)
            event.accept()
            return
        if self._temp_edge is not None and self._drag_from is not None:
            src = self.node_items[self._drag_from[0]]
            p1, es = src.output_point(self._drag_from[1], toward=pos, side=self._drag_from_side)
            # 커서가 출발점보다 왼쪽이면 좌측 진입, 아니면 상단 진입으로 미리보기
            entry = "left" if pos.x() < p1.x() else "top"
            self._temp_edge.update_path(p1, pos, es, entry)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def _draw_reroute_preview(self, pos: QPointF) -> None:
        """재연결 중 임시선 미리보기(고정된 반대쪽 끝 → 커서)."""
        item, end = self._reroute
        if end == "in":
            src = self.node_items.get(item.edge.from_node)
            if src is not None:
                p1, es = src.output_point(item.edge.from_port, toward=pos,
                                          side=item.edge.from_side)
                entry = "left" if pos.x() < p1.x() else "top"
                self._temp_edge.update_path(p1, pos, es, entry)
        else:  # "out": 입력 끝은 고정, 출력 끝(커서)을 옮기는 미리보기
            dst = self.node_items.get(item.edge.to_node)
            if dst is not None:
                p2, en = dst.input_point(toward=pos, side=item.edge.to_side)
                exit_s = "right" if pos.x() > p2.x() else "bottom"
                self._temp_edge.update_path(pos, p2, exit_s, en)

    def mouseReleaseEvent(self, event) -> None:
        pos = event.scenePos()
        if self._temp_edge is not None and self._reroute is not None:
            self._finish_reroute(pos)
            event.accept()
            return
        if self._temp_edge is not None and self._drag_from is not None:
            hit = self._input_hit(pos)
            self.removeItem(self._temp_edge)
            self._temp_edge = None
            if hit is not None and hit[0] != self._drag_from[0]:
                self.connect_ports(self._drag_from[0], self._drag_from[1], hit[0],
                                   from_side=self._drag_from_side, to_side=hit[1])
            self._drag_from = None
            self._drag_from_side = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _finish_reroute(self, pos: QPointF) -> None:
        """재연결 드롭 처리: 유효한 포트에 놓이면 끝점을 옮기고 변을 명시 저장한다."""
        item, end = self._reroute
        self.removeItem(self._temp_edge)
        self._temp_edge = None
        self._reroute = None
        item.setVisible(True)
        edge = item.edge
        if end == "in":
            hit = self._input_hit(pos)         # 다른 IN 포트(같은/다른 노드)로 이동
            if hit is not None and hit[0] != edge.from_node:
                edge.to_node, edge.to_side = hit
        else:  # "out": 다른 OUT 포트로 이동
            hit = self._port_at(pos)
            if hit is not None and hit[0] != edge.to_node:
                edge.from_node, edge.from_port, edge.from_side = hit
        self._reindex_edges()
        self.refresh_edges()

    def connect_ports(self, from_node: str, from_port: str, to_node: str,
                      from_side: str | None = None, to_side: str | None = None) -> None:
        """포트 연결을 추가한다(한 포트에서 여러 대상으로 보낼 수 있음 = fan-out).

        from_side/to_side 가 주어지면 그 연결점(변)을 명시 저장한다. 같은
        (출발 노드/포트 → 도착) 연결이 이미 있으면 중복 추가하지 않는다.
        """
        if any(e.from_node == from_node and e.from_port == from_port and e.to_node == to_node
               for e in self.sequence.edges):
            return
        edge = Edge(from_node, from_port, to_node, from_side=from_side, to_side=to_side)
        self.sequence.edges.append(edge)
        self._add_edge_item(edge)
        self.refresh_edges()

    def _on_selection(self) -> None:
        nodes = [i for i in self.selectedItems() if isinstance(i, NodeItem)]
        if nodes:
            self.selection_changed.emit(nodes[0])
            return
        groups = [i for i in self.selectedItems() if isinstance(i, GroupItem)]
        if groups:
            self.selection_changed.emit(groups[0].group)
            return
        self.selection_changed.emit(None)

    def add_node(self, node: Node) -> None:
        """새 노드를 모델과 씬에 추가한다."""
        self.sequence.nodes.append(node)
        item = NodeItem(node)
        self.addItem(item)
        self.node_items[node.id] = item

    def delete_selected(self, keep_notes: bool = False) -> None:
        """선택된 노드(+연결 엣지) / 그룹 / 개별 연결선 / 메모를 삭제한다.

        Args:
            keep_notes: True 면 메모(TextNote)는 삭제하지 않는다(Backspace 보호).
        """
        removed: set[str] = set()
        removed_groups: set[str] = set()
        removed_edges: set[int] = set()
        removed_notes: set[str] = set()
        for item in list(self.selectedItems()):
            if isinstance(item, NodeItem):
                nid = item.node.id
                removed.add(nid)
                self.sequence.nodes = [n for n in self.sequence.nodes if n.id != nid]
                self.sequence.edges = [
                    e for e in self.sequence.edges if e.from_node != nid and e.to_node != nid
                ]
            elif isinstance(item, GroupItem):
                removed_groups.add(item.group.id)
            elif isinstance(item, EdgeItem):
                removed_edges.add(id(item.edge))
            elif isinstance(item, TextNoteItem):
                if keep_notes:
                    continue
                removed_notes.add(item.note.id)
        if removed_edges:
            # 선택된 연결선만 제거(노드는 유지)
            self.sequence.edges = [e for e in self.sequence.edges if id(e) not in removed_edges]
        if removed_groups:
            # 그룹만 삭제(소속 노드는 그대로 유지)
            self.sequence.groups = [g for g in self.sequence.groups if g.id not in removed_groups]
        if removed_notes:
            self.sequence.notes = [t for t in self.sequence.notes if t.id not in removed_notes]
        self.rebuild()


class PropertyPanel(QWidget):
    """선택된 노드의 config 를 마우스(콤보/스핀박스/버튼)로 편집하는 폼."""

    changed = Signal()          # 노드 편집 발생 시(엣지/그래프 갱신 트리거)
    group_changed = Signal()    # 그룹 편집 발생 시(그룹 영역 갱신 트리거)
    close_requested = Signal()  # 패널 닫기 요청

    PANEL_WIDTH = 340

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("prop_panel")
        self._item: NodeItem | None = None
        self._node: Node | None = None
        self._group: Group | None = None
        self.setFixedWidth(self.PANEL_WIDTH)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("prop_scroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        content = QWidget()
        content.setObjectName("prop_content")
        self._form = QVBoxLayout(content)
        self._form.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._form.setContentsMargins(14, 14, 14, 14)
        self._form.setSpacing(8)
        scroll.setWidget(content)
        self._render()

    def _clear(self) -> None:
        while self._form.count():
            item = self._form.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    # --- 레이아웃 헬퍼(가독성 위해 라벨을 필드 위에 쌓는다) ---
    def _section(self, text: str) -> None:
        lbl = QLabel(text)
        lbl.setObjectName("prop_section")
        lbl.setWordWrap(True)
        self._form.addWidget(lbl)

    def _field(self, label: str, widget: QWidget) -> QWidget:
        """'라벨 위 / 위젯 아래' 형태의 한 필드 묶음을 만든다."""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        cap = QLabel(label)
        cap.setObjectName("prop_field_label")
        v.addWidget(cap)
        v.addWidget(widget)
        return box

    def _card(self, title: str, on_delete) -> tuple[QFrame, QVBoxLayout]:
        """제목 + 삭제 버튼을 가진 카드(QFrame)와 본문 레이아웃을 만든다."""
        frame = QFrame()
        frame.setObjectName("prop_card")
        v = QVBoxLayout(frame)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)
        head = QHBoxLayout()
        cap = QLabel(title)
        cap.setObjectName("prop_card_title")
        del_btn = QPushButton("✕")
        del_btn.setObjectName("prop_del")
        del_btn.setFixedSize(26, 26)
        del_btn.clicked.connect(lambda: on_delete())
        head.addWidget(cap)
        head.addStretch(1)
        head.addWidget(del_btn)
        v.addLayout(head)
        self._form.addWidget(frame)
        return frame, v

    def _add_button(self, text: str, on_click) -> None:
        btn = QPushButton(text)
        btn.setObjectName("prop_add")
        btn.clicked.connect(lambda: on_click())
        self._form.addWidget(btn)

    def set_node(self, item: NodeItem | None) -> None:
        """선택 노드를 표시한다(None 이면 안내 문구)."""
        self._item = item
        self._node = item.node if item else None
        self._group = None
        self._render()

    def set_group(self, group: Group) -> None:
        """선택 그룹의 속성(이름)을 표시한다."""
        self._item = None
        self._node = None
        self._group = group
        self._render()

    def refresh(self) -> None:
        """현재 노드 기준으로 폼을 다시 그린다(항목 추가/삭제 후)."""
        self._render()

    def _render(self) -> None:
        self._clear()
        # 헤더: 제목 + 닫기 버튼
        header = QWidget()
        head_row = QHBoxLayout(header)
        head_row.setContentsMargins(0, 0, 0, 0)
        title = QLabel("그룹 속성" if self._group is not None else "속성")
        title.setObjectName("prop_title")
        head_row.addWidget(title)
        head_row.addStretch(1)
        close_btn = QPushButton("✕")
        close_btn.setObjectName("prop_close")
        close_btn.setFixedSize(26, 26)
        close_btn.setToolTip("패널 닫기")
        close_btn.clicked.connect(self.close_requested.emit)
        head_row.addWidget(close_btn)
        self._form.addWidget(header)

        if self._group is not None:
            self._render_group(self._group)
            return

        node = self._node
        if node is None:
            hint = QLabel("노드를 선택하면\n여기에서 설정할 수 있습니다.")
            hint.setObjectName("prop_empty")
            hint.setWordWrap(True)
            self._form.addWidget(hint)
            return

        subtitle = QLabel(f"{node.type.value}  ·  {node.id}")
        subtitle.setObjectName("prop_subtitle")
        self._form.addWidget(subtitle)
        label_edit = QLineEdit(node.label)
        label_edit.setPlaceholderText("노드 이름 (최대 10자)")
        label_edit.setMaxLength(LABEL_MAX)  # 노드 헤더에 함께 표시되므로 길이 제한
        label_edit.textChanged.connect(self._on_label_changed)
        self._form.addWidget(self._field("라벨", label_edit))

        builder = {
            NodeType.START: self._build_start,
            NodeType.SEND: self._build_send,
            NodeType.WAIT: self._build_wait,
            NodeType.BRANCH: self._build_branch,
            NodeType.DELAY: self._build_delay,
            NodeType.END: self._build_end,
        }.get(node.type)
        if builder is not None:
            builder(node)
        self._form.addStretch(1)

    def _render_group(self, group: Group) -> None:
        """그룹 속성(이름 + 동작: 없음/반복/배열)을 표시한다."""
        subtitle = QLabel("그룹 영역 · 핸들을 드래그해 크기 조절")
        subtitle.setObjectName("prop_subtitle")
        subtitle.setWordWrap(True)
        self._form.addWidget(subtitle)
        name_edit = QLineEdit(group.label)
        name_edit.setPlaceholderText("그룹 이름")
        name_edit.setMaxLength(20)
        name_edit.textChanged.connect(lambda t: (setattr(group, "label", t), self.group_changed.emit()))
        self._form.addWidget(self._field("그룹 이름", name_edit))

        self._section("동작")
        mode_combo = QComboBox()
        for key, label in (("none", "없음 (일반 그룹)"), ("loop", "반복 (N회)"), ("iter", "배열 순회")):
            mode_combo.addItem(label, key)
        mode_combo.setCurrentIndex(max(0, mode_combo.findData(
            group.mode if group.mode in GROUP_MODES else "none")))

        def on_mode(_i: int) -> None:
            group.mode = mode_combo.currentData()
            self.group_changed.emit()
            QTimer.singleShot(0, self.refresh)  # 모드별 입력 필드 다시 구성

        mode_combo.currentIndexChanged.connect(on_mode)
        self._form.addWidget(self._field("그룹 종류", mode_combo))
        hint = QLabel("반복/배열은 영역 안에 노드만 넣으면 됩니다(START·END 불필요). "
                      "밖에서 들어오는 연결의 노드가 시작점이 되고, 영역 밖으로 나가는 "
                      "연결이 반복 종료 후 진행 경로가 됩니다.")
        hint.setObjectName("prop_field_label")
        hint.setWordWrap(True)
        self._form.addWidget(hint)

        if group.mode == "loop":
            self._section("반복 횟수")
            spin = QSpinBox()
            spin.setRange(1, 100000)
            spin.setValue(max(1, group.loop_count))
            spin.valueChanged.connect(lambda v: (setattr(group, "loop_count", v), self.group_changed.emit()))
            self._form.addWidget(self._field("횟수", spin))
        elif group.mode == "iter":
            group.iter_reg_type = group.iter_reg_type or "holding_registers"
            group.iter_addr = group.iter_addr or 0
            self._section("대상  (반복마다 이 번지에 배열값을 씀)")
            reg = self._reg_combo(group.iter_reg_type)
            reg.currentTextChanged.connect(
                lambda t: (setattr(group, "iter_reg_type", t), self.group_changed.emit()))
            self._form.addWidget(self._field("레지스터", reg))
            addr = self._num_spin(group.iter_addr or 0)
            addr.valueChanged.connect(lambda v: (setattr(group, "iter_addr", v), self.group_changed.emit()))
            self._form.addWidget(self._field("주소", addr))
            self._section("배열 값  (반복 횟수 = 값 개수)")
            for i in range(len(group.iter_values)):
                self._add_group_value_row(group, i)
            self._add_button("+ 값 추가", lambda: self._group_add_value(group))

        self._form.addStretch(1)

    def _group_add_value(self, group: Group) -> None:
        group.iter_values.append(0)
        self.group_changed.emit()
        QTimer.singleShot(0, self.refresh)

    def _group_remove_value(self, group: Group, idx: int) -> None:
        if 0 <= idx < len(group.iter_values):
            group.iter_values.pop(idx)
        self.group_changed.emit()
        QTimer.singleShot(0, self.refresh)

    def _add_group_value_row(self, group: Group, idx: int) -> None:
        _, v = self._card(f"[{idx}]", lambda i=idx: self._group_remove_value(group, i))
        spin = self._num_spin(group.iter_values[idx])
        spin.valueChanged.connect(
            lambda val, i=idx: (group.iter_values.__setitem__(i, val), self.group_changed.emit()))
        v.addWidget(self._field("값", spin))

    # --- 공용 입력 위젯 ---
    def _reg_combo(self, current: str | None) -> QComboBox:
        combo = QComboBox()
        combo.addItems(list(REGISTER_TYPES))
        # 지정값이 없으면 기본은 holding_registers 로 보이게 한다.
        combo.setCurrentText(current if current in REGISTER_TYPES else "holding_registers")
        return combo

    def _op_combo(self, current: str | None) -> QComboBox:
        combo = QComboBox()
        combo.addItems(list(OPERATORS))
        if current in OPERATORS:
            combo.setCurrentText(current)
        return combo

    def _write_op_combo(self, current: str | None) -> QComboBox:
        """쓰기 연산(지정/더하기/빼기) 선택 콤보. 내부값은 userData 로 보관."""
        combo = QComboBox()
        labels = {"set": "= 지정", "add": "+= 더하기", "sub": "-= 빼기"}
        for key in WRITE_OPS:
            combo.addItem(labels[key], key)
        idx = combo.findData(current if current in WRITE_OPS else "set")
        combo.setCurrentIndex(max(0, idx))
        return combo

    def _num_spin(self, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 65535)
        spin.setValue(int(value or 0))
        return spin

    def _on_label_changed(self, text: str) -> None:
        if self._node is not None:
            self._node.label = text
            self.changed.emit()

    # --- 리스트 항목 추가/삭제(구조 변경 후 폼 재구성) ---
    def _add_item(self, lst: list, item) -> None:
        lst.append(item)
        self.changed.emit()
        QTimer.singleShot(0, self.refresh)

    def _remove_item(self, lst: list, item) -> None:
        if item in lst:
            lst.remove(item)
        self.changed.emit()
        QTimer.singleShot(0, self.refresh)

    def _remove_index(self, lst: list, idx: int) -> None:
        if 0 <= idx < len(lst):
            lst.pop(idx)
        self.changed.emit()
        QTimer.singleShot(0, self.refresh)

    # --- START ---
    def _build_start(self, node: Node) -> None:
        """START 노드: 실행 활성/비활성 토글.

        여러 START 를 둔 채 일부만 켜고 끌 수 있게 한다. 비활성 START 는 실행에서
        제외되므로, 테스트용 흐름을 지우지 않고 잠시 꺼둘 때 쓴다.
        """
        self._section("실행")
        btn = QPushButton("활성화됨 (실행 포함)" if node.enabled else "비활성화됨 (실행 제외)")
        btn.setObjectName("start_toggle")
        btn.setCheckable(True)
        btn.setChecked(node.enabled)  # connect 전에 설정 → 초기 렌더 시 시그널 안 남
        btn.setCursor(Qt.CursorShape.PointingHandCursor)

        def apply(checked: bool) -> None:
            node.enabled = checked
            btn.setText("활성화됨 (실행 포함)" if checked else "비활성화됨 (실행 제외)")
            if self._item is not None:
                self._item.update()  # 노드 디밍/배지 즉시 갱신
            self.changed.emit()

        btn.toggled.connect(apply)
        self._form.addWidget(self._field("이 START 흐름", btn))
        hint = QLabel("비활성화하면 이 START 에서 시작하는 흐름이 실행되지 않습니다. "
                      "노드를 지우지 않고 잠시 꺼둘 때 사용하세요.")
        hint.setObjectName("prop_field_label")
        hint.setWordWrap(True)
        self._form.addWidget(hint)

    # --- SEND ---
    def _build_send(self, node: Node) -> None:
        self._section("쓰기 동작")
        for i, action in enumerate(list(node.writes)):
            self._add_write_row(node, action, i)
        self._add_button("+ 쓰기 추가",
                         lambda: self._add_item(node.writes, WriteAction("holding_registers", 0, 0)))

    def _add_write_row(self, node: Node, action: WriteAction, idx: int) -> None:
        _, v = self._card(f"쓰기 #{idx + 1}", lambda: self._remove_item(node.writes, action))
        reg = self._reg_combo(action.reg_type)
        op = self._write_op_combo(action.op)
        addr = self._num_spin(action.addr)
        val = self._num_spin(action.value)

        def apply() -> None:
            action.reg_type = reg.currentText()
            action.op = op.currentData()
            action.addr = addr.value()
            action.value = clamp_value(action.reg_type, val.value())
            self.changed.emit()

        reg.currentTextChanged.connect(lambda _t: apply())
        op.currentIndexChanged.connect(lambda _i: apply())
        addr.valueChanged.connect(lambda _v: apply())
        val.valueChanged.connect(lambda _v: apply())

        v.addWidget(self._field("레지스터", reg))
        v.addWidget(self._field("연산", op))
        pair = QHBoxLayout()
        pair.setContentsMargins(0, 0, 0, 0)
        pair.setSpacing(8)
        pair.addWidget(self._field("주소", addr))
        pair.addWidget(self._field("값", val))
        v.addLayout(pair)

    # --- WAIT ---
    def _build_wait(self, node: Node) -> None:
        self._section("조건  (순서 = 출력 포트 cond_i)")
        for i, cond in enumerate(list(node.conditions)):
            self._add_cond_row(node, cond, i)
        self._add_button("+ 조건 추가",
                         lambda: self._add_item(node.conditions, Condition("holding_registers", 0, "==", 1)))
        self._section("타임아웃")
        spin = QSpinBox()
        spin.setRange(0, 3_600_000)
        spin.setValue(node.timeout_ms or 0)
        spin.valueChanged.connect(lambda v: (setattr(node, "timeout_ms", v or None), self.changed.emit()))
        self._form.addWidget(self._field("제한 시간 (ms, 0 = 무한)", spin))

    def _add_cond_row(self, node: Node, cond: Condition, idx: int) -> None:
        _, v = self._card(f"조건 cond_{idx}", lambda: self._remove_item(node.conditions, cond))
        reg = self._reg_combo(cond.reg_type)
        addr = self._num_spin(cond.addr)
        op = self._op_combo(cond.op)
        val = self._num_spin(cond.value)

        def apply() -> None:
            cond.reg_type = reg.currentText()
            cond.addr = addr.value()
            cond.op = op.currentText()
            cond.value = clamp_value(cond.reg_type, val.value())
            self.changed.emit()

        reg.currentTextChanged.connect(lambda _t: apply())
        addr.valueChanged.connect(lambda _v: apply())
        op.currentTextChanged.connect(lambda _t: apply())
        val.valueChanged.connect(lambda _v: apply())

        v.addWidget(self._field("레지스터", reg))
        v.addWidget(self._field("연산자", op))
        pair = QHBoxLayout()
        pair.setContentsMargins(0, 0, 0, 0)
        pair.setSpacing(8)
        pair.addWidget(self._field("주소", addr))
        pair.addWidget(self._field("값", val))
        v.addLayout(pair)

    # --- BRANCH ---
    def _build_branch(self, node: Node) -> None:
        node.branch_reg_type = node.branch_reg_type or "holding_registers"
        node.branch_addr = node.branch_addr or 0
        self._section("대상")
        reg = self._reg_combo(node.branch_reg_type)
        reg.currentTextChanged.connect(lambda t: (setattr(node, "branch_reg_type", t), self.changed.emit()))
        self._form.addWidget(self._field("레지스터", reg))
        addr = self._num_spin(node.branch_addr)
        addr.valueChanged.connect(lambda v: (setattr(node, "branch_addr", v), self.changed.emit()))
        self._form.addWidget(self._field("주소", addr))
        self._section("case 값  (순서 = 출력 포트 case_i)")
        for idx in range(len(node.cases)):
            self._add_case_row(node, idx)
        self._add_button("+ case 추가", lambda: self._add_item(node.cases, 0))

    def _add_case_row(self, node: Node, idx: int) -> None:
        _, v = self._card(f"case_{idx}", lambda i=idx: self._remove_index(node.cases, i))
        spin = self._num_spin(node.cases[idx])
        spin.valueChanged.connect(lambda val, i=idx: (node.cases.__setitem__(i, val), self.changed.emit()))
        v.addWidget(self._field("값", spin))

    # --- DELAY ---
    def _build_delay(self, node: Node) -> None:
        self._section("지연")
        spin = QSpinBox()
        spin.setRange(0, 3_600_000)
        spin.setValue(node.delay_ms)
        spin.valueChanged.connect(lambda v: (setattr(node, "delay_ms", v), self.changed.emit()))
        self._form.addWidget(self._field("대기 시간 (ms)", spin))

    # --- END ---
    def _build_end(self, node: Node) -> None:
        self._section("종료")
        edit = QLineEdit(node.result)
        edit.setPlaceholderText("예: 성공 / 실패")
        edit.textChanged.connect(lambda t: (setattr(node, "result", t), self.changed.emit()))
        self._form.addWidget(self._field("결과 라벨", edit))


class SequenceView(QGraphicsView):
    """노드 드롭/Delete 삭제 + 줌(버튼·Ctrl+휠) 을 처리하는 그래프 뷰."""

    node_dropped = Signal(object, QPointF)  # (NodeType, 씬 좌표)
    note_dropped = Signal(QPointF)          # 텍스트 메모 드롭 위치(씬 좌표)
    zoom_changed = Signal(float)            # 현재 배율(1.0 = 100%)

    MIN_SCALE = 0.25
    MAX_SCALE = 3.0

    group_requested = Signal()  # 선택된 노드들을 그룹으로 묶기 요청(Ctrl+G)
    undo_requested = Signal()   # Ctrl+Z
    redo_requested = Signal()   # Ctrl+Y / Ctrl+Shift+Z
    copy_requested = Signal()   # Ctrl+C
    paste_requested = Signal()  # Ctrl+V

    def __init__(self, scene: SequenceScene) -> None:
        super().__init__(scene)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._scale = 1.0
        self._space_pan = False
        self._last_band = QRect()  # 직전 러버밴드 사각형(그룹 선택 정제용)

    # --- 줌 ---
    def _apply_zoom(self, factor: float, anchor) -> None:
        target = max(self.MIN_SCALE, min(self.MAX_SCALE, self._scale * factor))
        factor = target / self._scale
        if abs(factor - 1.0) < 1e-3:
            return
        self.setTransformationAnchor(anchor)
        self.scale(factor, factor)
        self._scale = target
        self.zoom_changed.emit(self._scale)

    def zoom_in(self) -> None:
        self._apply_zoom(1.2, QGraphicsView.ViewportAnchor.AnchorViewCenter)

    def zoom_out(self) -> None:
        self._apply_zoom(1 / 1.2, QGraphicsView.ViewportAnchor.AnchorViewCenter)

    def reset_zoom(self) -> None:
        """배율을 100%(1:1)로 되돌린다."""
        self.resetTransform()
        self._scale = 1.0
        self.zoom_changed.emit(self._scale)

    def fit_contents(self) -> None:
        """모든 노드가 보이도록 화면에 맞춘다(Fit)."""
        rect = self.scene().itemsBoundingRect()
        if rect.isNull():
            return
        self.fitInView(rect.adjusted(-40, -40, 40, 40), Qt.AspectRatioMode.KeepAspectRatio)
        self._scale = self.transform().m11()
        self.zoom_changed.emit(self._scale)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            up = event.angleDelta().y() > 0
            self._apply_zoom(1.15 if up else 1 / 1.15,
                             QGraphicsView.ViewportAnchor.AnchorUnderMouse)
            event.accept()
            return
        super().wheelEvent(event)

    # --- 우클릭 무효화: 컨텍스트 메뉴/엣지 드래그/선택 등 어떤 동작도 하지 않음 ---
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        self._last_band = QRect()  # 새 상호작용 시작 → 직전 밴드 초기화
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        band = self.rubberBandRect()  # 러버밴드 드래그 중에만 유효(아니면 빈 사각형)
        if not band.isNull():
            self._last_band = QRect(band)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if not self._last_band.isNull():
            self._refine_group_selection(self._last_band)
            self._last_band = QRect()

    def _refine_group_selection(self, band_viewport: QRect) -> None:
        """러버밴드가 그룹을 '완전히 감싸지' 않으면 그 그룹은 선택에서 뺀다.

        그룹 안쪽에서 노드만 박스 선택하면 그룹 프레임이 함께 잡히는데(intersect),
        밴드가 그룹 전체를 포함할 때만 그룹을 선택으로 남겨 노드만 따로 옮길 수
        있게 한다. 그룹 프레임 클릭 선택(밴드 없음)은 영향받지 않는다.
        """
        scene = self.scene()
        if not isinstance(scene, SequenceScene):
            return
        band_scene = self.mapToScene(band_viewport).boundingRect()
        for gi in scene.group_items:
            if gi.isSelected() and not band_scene.contains(gi._rect):
                gi.setSelected(False)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        event.accept()  # 기본 컨텍스트 메뉴 차단

    def _accepts(self, mime) -> bool:
        return mime.hasFormat(NODE_MIME) or mime.hasFormat(NOTE_MIME)

    def dragEnterEvent(self, event) -> None:
        if self._accepts(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._accepts(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        scene_pos = self.mapToScene(event.position().toPoint())
        if mime.hasFormat(NODE_MIME):
            value = bytes(mime.data(NODE_MIME)).decode("utf-8")
            self.node_dropped.emit(NodeType(value), scene_pos)
            event.acceptProposedAction()
        elif mime.hasFormat(NOTE_MIME):
            self.note_dropped.emit(scene_pos)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def keyPressEvent(self, event) -> None:
        # 텍스트 메모 편집 중에는 모든 키(스페이스 포함)를 편집기로 넘긴다.
        # 그렇지 않으면 스페이스가 화면 이동(손바닥 모드)에 가려져 입력되지 않는다.
        scene = self.scene()
        focus = scene.focusItem() if scene else None
        if (isinstance(focus, QGraphicsTextItem)
                and focus.textInteractionFlags()
                & Qt.TextInteractionFlag.TextEditorInteraction):
            super().keyPressEvent(event)
            return
        # 스페이스바: 손바닥 모드(ScrollHandDrag)로 전환 → 좌클릭 드래그로 화면 이동
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pan = True
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        # Ctrl+G: 선택 노드 그룹화
        if event.key() == Qt.Key.Key_G and ctrl:
            self.group_requested.emit()
            event.accept()
            return
        # Ctrl+Z: 실행취소 / Ctrl+Y · Ctrl+Shift+Z: 다시실행
        if event.key() == Qt.Key.Key_Z and ctrl and not shift:
            self.undo_requested.emit()
            event.accept()
            return
        if ctrl and (event.key() == Qt.Key.Key_Y or (event.key() == Qt.Key.Key_Z and shift)):
            self.redo_requested.emit()
            event.accept()
            return
        # Ctrl+C / Ctrl+V: 선택 노드(+내부 연결) 복사·붙여넣기
        if event.key() == Qt.Key.Key_C and ctrl:
            self.copy_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_V and ctrl:
            self.paste_requested.emit()
            event.accept()
            return
        # Ctrl++ / Ctrl+= : 확대,  Ctrl+- : 축소,  Ctrl+0 : 실제 크기
        if ctrl and event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_in()
            event.accept()
            return
        if ctrl and event.key() in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self.zoom_out()
            event.accept()
            return
        if ctrl and event.key() == Qt.Key.Key_0:
            self.reset_zoom()
            event.accept()
            return
        # 방향키: 선택한 노드/그룹/메모를 미세 이동(세부조정). Shift 면 크게.
        arrows = (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down)
        if event.key() in arrows:
            scene = self.scene()
            # 메모 편집 중이면 방향키는 텍스트 커서 이동에 쓴다.
            focus = scene.focusItem() if scene else None
            if (isinstance(focus, QGraphicsTextItem)
                    and focus.textInteractionFlags()
                    & Qt.TextInteractionFlag.TextEditorInteraction):
                super().keyPressEvent(event)
                return
            if isinstance(scene, SequenceScene) and scene.selectedItems():
                step = 10.0 if shift else 1.0
                dx = (-step if event.key() == Qt.Key.Key_Left
                      else step if event.key() == Qt.Key.Key_Right else 0.0)
                dy = (-step if event.key() == Qt.Key.Key_Up
                      else step if event.key() == Qt.Key.Key_Down else 0.0)
                scene.nudge_selection(dx, dy)
                event.accept()
                return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            scene = self.scene()
            # 메모 편집 중이면 키를 텍스트 편집기로 넘겨, 글자만 지우고 노드는 보존.
            focus = scene.focusItem() if scene else None
            if (isinstance(focus, QGraphicsTextItem)
                    and focus.textInteractionFlags()
                    & Qt.TextInteractionFlag.TextEditorInteraction):
                super().keyPressEvent(event)
                return
            if isinstance(scene, SequenceScene):
                # Backspace 로는 메모를 지우지 않는다(실수로 사라지는 것 방지).
                # 메모 삭제는 Delete 키로만 가능.
                keep_notes = event.key() == Qt.Key.Key_Backspace
                scene.delete_selected(keep_notes=keep_notes)
                event.accept()
                return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        # 스페이스바 해제: 일반(고무줄 선택) 모드로 복귀
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pan = False
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            self.viewport().unsetCursor()
            event.accept()
            return
        super().keyReleaseEvent(event)


class NodePaletteButton(QPushButton):
    """클릭(중앙 추가) + 드래그(원하는 위치에 드롭)를 지원하는 팔레트 버튼."""

    def __init__(self, ntype: NodeType) -> None:
        super().__init__(ntype.value)
        self.ntype = ntype
        self._press_pos: QPointF | None = None
        self.setToolTip("클릭: 화면 중앙에 추가 · 드래그: 원하는 위치에 놓기")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_pos is not None and (
            (event.position() - self._press_pos).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self._press_pos = None
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(NODE_MIME, self.ntype.value.encode("utf-8"))
            drag.setMimeData(mime)
            pixmap = self.grab()
            drag.setPixmap(pixmap)
            drag.setHotSpot(event.position().toPoint())
            drag.exec(Qt.DropAction.CopyAction)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._press_pos = None
        super().mouseReleaseEvent(event)


class NotePaletteButton(QPushButton):
    """클릭(중앙 추가) + 드래그(원하는 위치에 드롭)를 지원하는 텍스트 메모 버튼."""

    def __init__(self) -> None:
        super().__init__("＋ 텍스트 메모")
        self._press_pos: QPointF | None = None
        self.setToolTip("클릭: 화면 중앙에 추가 · 드래그: 원하는 위치에 놓기 (더블클릭하여 편집)")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_pos is not None and (
            (event.position() - self._press_pos).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self._press_pos = None
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(NOTE_MIME, b"1")
            drag.setMimeData(mime)
            drag.setPixmap(self.grab())
            drag.setHotSpot(event.position().toPoint())
            drag.exec(Qt.DropAction.CopyAction)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._press_pos = None
        super().mouseReleaseEvent(event)


class SequenceEditor(QWidget):
    """팔레트 + 그래프 뷰 + 속성 패널을 묶은 에디터 위젯."""

    def __init__(self, sequence: Sequence) -> None:
        super().__init__()
        self.sequence = sequence
        self.scene = SequenceScene(sequence)
        self.view = SequenceView(self.scene)
        self.view.setObjectName("seq_canvas")
        self.view.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.view.node_dropped.connect(self._add_at)
        self.view.note_dropped.connect(self._add_note_at)
        self.panel = PropertyPanel()

        palette = QVBoxLayout()
        palette.setSpacing(6)
        pal_title = QLabel("노드 추가")
        pal_title.setObjectName("prop_section")
        palette.addWidget(pal_title)
        pal_hint = QLabel("드래그하여 캔버스에 놓기")
        pal_hint.setObjectName("prop_field_label")
        palette.addWidget(pal_hint)
        for ntype in NodeType:
            btn = NodePaletteButton(ntype)
            btn.setObjectName("seq_palette_btn")
            color = NODE_COLORS[ntype]
            btn.setStyleSheet(f"#seq_palette_btn {{ border-left: 4px solid {color.name()}; text-align: left; }}")
            btn.clicked.connect(lambda _=False, t=ntype: self._add(t))
            palette.addWidget(btn)
        group_btn = QPushButton("그룹화 (Ctrl+G)")
        group_btn.setObjectName("seq_group_btn")
        group_btn.setToolTip("드래그로 여러 노드를 선택한 뒤 묶습니다")
        group_btn.clicked.connect(self.scene.group_selected)
        ungroup_btn = QPushButton("그룹 해제")
        ungroup_btn.setObjectName("seq_group_btn")
        ungroup_btn.setToolTip("선택한 노드가 속한 그룹을 해제합니다")
        ungroup_btn.clicked.connect(self.scene.ungroup_selected)
        palette.addSpacing(8)
        palette.addWidget(group_btn)
        palette.addWidget(ungroup_btn)

        note_btn = NotePaletteButton()
        note_btn.setObjectName("seq_group_btn")
        note_btn.clicked.connect(self._add_note)
        palette.addSpacing(8)
        palette.addWidget(note_btn)

        del_btn = QPushButton("선택 삭제 (Del)")
        del_btn.setObjectName("seq_delete_btn")
        del_btn.clicked.connect(self.scene.delete_selected)
        palette.addSpacing(8)
        palette.addWidget(del_btn)

        undo_redo = QHBoxLayout()
        undo_redo.setSpacing(6)
        self.undo_btn = QPushButton("↶ 취소")
        self.undo_btn.setObjectName("seq_group_btn")
        self.undo_btn.setToolTip("실행취소 (Ctrl+Z)")
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn = QPushButton("↷ 복원")
        self.redo_btn.setObjectName("seq_group_btn")
        self.redo_btn.setToolTip("다시실행 (Ctrl+Y)")
        self.redo_btn.clicked.connect(self.redo)
        undo_redo.addWidget(self.undo_btn)
        undo_redo.addWidget(self.redo_btn)
        palette.addSpacing(8)
        palette.addLayout(undo_redo)
        palette.addStretch(1)

        # 단축키 안내(패널 최하단). 좁은 폭에 맞춰 키/설명 2열 표로 간결하게.
        sc_title = QLabel("단축키")
        sc_title.setObjectName("prop_section")
        palette.addWidget(sc_title)
        shortcuts = [
            ("Space+드래그", "화면 이동"),
            ("Ctrl + ＋/－", "확대/축소"),
            ("Ctrl+휠", "확대/축소"),
            ("Ctrl+0", "실제 크기"),
            ("방향키", "선택 미세 이동"),
            ("Shift+방향키", "선택 크게 이동"),
            ("Ctrl+Z", "실행취소"),
            ("Ctrl+Y", "다시실행"),
            ("Ctrl+C / V", "복사/붙여넣기"),
            ("Ctrl+G", "그룹화"),
            ("Ctrl+S", "저장"),
            ("Del", "선택 삭제"),
        ]
        rows = "".join(
            f"<tr><td style='color:#93c5fd;padding-right:6px;white-space:nowrap;'>{k}</td>"
            f"<td style='color:#94a3b8;'>{d}</td></tr>"
            for k, d in shortcuts
        )
        sc_guide = QLabel(f"<table style='font-size:10px;'>{rows}</table>")
        sc_guide.setObjectName("seq_shortcut_guide")
        sc_guide.setTextFormat(Qt.TextFormat.RichText)
        sc_guide.setWordWrap(True)
        palette.addWidget(sc_guide)

        self._root = QHBoxLayout(self)
        self._root.setSpacing(8)
        left = QWidget()
        left.setObjectName("seq_palette")
        left.setLayout(palette)
        left.setMaximumWidth(160)
        self._root.addWidget(left)
        self._root.addWidget(self._build_canvas_area(), 1)

        # 속성 패널은 캔버스 오른쪽에 둔다. 표시/숨김 시 창 너비를 패널 폭만큼
        # 늘리고 줄여, 그래프(캔버스) 영역 크기에는 영향이 없도록 한다.
        self._root.addWidget(self.panel)
        self.panel.hide()
        self.panel.close_requested.connect(self.scene.clearSelection)

        self.view.group_requested.connect(self.scene.group_selected)
        self.view.undo_requested.connect(self.undo)
        self.view.redo_requested.connect(self.redo)
        self.view.copy_requested.connect(self.copy_selection)
        self.view.paste_requested.connect(self.paste_clipboard)
        self._clipboard: dict | None = None
        self.scene.selection_changed.connect(self._on_selection)
        self.panel.changed.connect(self._on_panel_changed)
        self.panel.group_changed.connect(self.scene.refresh_groups)
        self._counter = len(sequence.nodes)
        self._note_counter = len(sequence.notes)

        # --- 실행취소/다시실행 히스토리(최대 20단계) ---
        self.UNDO_LIMIT = 20
        self._undo: list[str] = []
        self._redo: list[str] = []
        self._restoring = False
        self._history_timer = QTimer(self)
        self._history_timer.setSingleShot(True)
        self._history_timer.timeout.connect(self._commit)
        # 씬이 바뀔 때마다(이동/편집/추가/삭제) 잠시 후 스냅샷을 시도한다.
        self.scene.changed.connect(lambda *_: self._schedule_commit())
        self.reset_history()

    def _build_canvas_area(self) -> QWidget:
        """줌 컨트롤 바 + 그래프 뷰를 묶은 중앙 영역을 만든다."""
        area = QWidget()
        self._canvas_area = area
        col = QVBoxLayout(area)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        bar = QHBoxLayout()
        bar.addStretch(1)
        self._build_align_buttons(bar)  # 줌 컨트롤 옆 정렬/간격 버튼
        sep = QLabel("│")
        sep.setObjectName("seq_zoom_label")
        bar.addWidget(sep)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("seq_zoom_label")
        bar.addWidget(self.zoom_label)
        specs = (
            ("－", "축소 (Ctrl+휠)", self.view.zoom_out),
            ("＋", "확대 (Ctrl+휠)", self.view.zoom_in),
            ("⤢ Fit", "전체 보기", self.view.fit_contents),
            ("100%", "실제 크기", self.view.reset_zoom),
        )
        for text, tip, slot in specs:
            btn = QPushButton(text)
            btn.setObjectName("seq_zoom_btn")
            btn.setToolTip(tip)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(slot)
            bar.addWidget(btn)
        col.addLayout(bar)
        self.scene.selectionChanged.connect(self._update_align_buttons)
        self._update_align_buttons()
        col.addWidget(self.view, 1)

        self.view.zoom_changed.connect(
            lambda s: self.zoom_label.setText(f"{round(s * 100)}%")
        )
        return area

    def _build_align_buttons(self, bar: QHBoxLayout) -> None:
        """선택 노드 정렬/간격 버튼들을 바에 추가한다(줌 컨트롤 옆)."""
        self._align_buttons: list[QPushButton] = []
        self._distribute_buttons: list[QPushButton] = []
        align_specs = (
            ("left", "왼쪽 정렬"),
            ("cx", "좌우 가운데 정렬"),
            ("right", "오른쪽 정렬"),
            ("top", "위쪽 정렬"),
            ("cy", "위아래 가운데 정렬"),
            ("bottom", "아래쪽 정렬"),
        )
        for kind, tip in align_specs:
            btn = self._icon_btn(kind, f"{tip} (2개 이상 선택)",
                                 lambda m=kind: self.scene.align_selected(m))
            self._align_buttons.append(btn)
            bar.addWidget(btn)
        dist_specs = (
            ("dist_h", "h", "가로 간격 균등"),
            ("dist_v", "v", "세로 간격 균등"),
        )
        for kind, orient, tip in dist_specs:
            btn = self._icon_btn(kind, f"{tip} (3개 이상 선택)",
                                 lambda o=orient: self.scene.distribute_selected(o))
            self._distribute_buttons.append(btn)
            bar.addWidget(btn)

    def _icon_btn(self, kind: str, tip: str, slot) -> QPushButton:
        """직접 그린 정렬 아이콘을 가진 작은 컨트롤 버튼을 만든다(줌 버튼 스타일)."""
        btn = QPushButton()
        btn.setObjectName("seq_zoom_btn")
        btn.setIcon(_align_icon(kind))
        btn.setIconSize(QSize(18, 18))
        btn.setToolTip(tip)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.clicked.connect(lambda: slot())
        return btn

    def _update_align_buttons(self) -> None:
        """정렬 단위(노드/그룹) 수에 따라 정렬(2개+)/간격(3개+) 버튼 활성화를 갱신한다."""
        count = self.scene.alignment_unit_count()
        for btn in self._align_buttons:
            btn.setEnabled(count >= 2)
        for btn in self._distribute_buttons:
            btn.setEnabled(count >= 3)

    def _on_selection(self, item: "NodeItem | None") -> None:
        """노드 선택 시 우측에 속성 패널을 더하고, 해제 시 빼낸다.

        패널 표시/숨김에 맞춰 창 너비를 패널 폭만큼 늘리고 줄여서
        그래프(캔버스) 영역의 크기는 변하지 않게 한다.
        """
        if item is None:
            self._set_panel_visible(False)
            return
        if isinstance(item, Group):
            self.panel.set_group(item)
        else:
            self.panel.set_node(item)
        self._set_panel_visible(True)

    def _set_panel_visible(self, visible: bool) -> None:
        if visible == self.panel.isVisible():
            return  # 상태 변화 없음 → 창 크기도 그대로(내용만 갱신됨)
        # 선택은 마우스 누름 이벤트 도중 발생한다. 그 자리에서 창 크기를 바꾸면
        # 진행 중인 노드 클릭/드래그가 흐트러져 노드가 튀는 문제가 생기므로,
        # 이벤트가 끝난 뒤(다음 이벤트 루프)로 미뤄서 처리한다.
        QTimer.singleShot(0, lambda: self._apply_panel_visible(visible))

    def _apply_panel_visible(self, visible: bool) -> None:
        if visible == self.panel.isVisible():
            return
        delta = self.panel.PANEL_WIDTH + self._root.spacing()
        win = self.window()
        if visible:
            self.panel.show()
            if win is not None:
                win.resize(win.width() + delta, win.height())
        else:
            self.panel.hide()
            if win is not None:
                win.resize(max(win.minimumWidth(), win.width() - delta), win.height())

    def _add(self, ntype: NodeType) -> None:
        """클릭 폴백: 현재 보이는 화면 중앙에 노드를 추가한다."""
        center = self.view.mapToScene(self.view.viewport().rect().center())
        self._place(ntype, center)

    def _add_at(self, ntype: NodeType, scene_pos: QPointF) -> None:
        """드롭 위치(씬 좌표)에 노드를 추가한다."""
        self._place(ntype, scene_pos)

    def _place(self, ntype: NodeType, center: QPointF) -> None:
        """center 를 노드 중심으로 하여 새 노드를 배치한다."""
        self._counter += 1
        while any(n.id == f"n{self._counter}" for n in self.sequence.nodes):
            self._counter += 1
        node = Node(
            id=f"n{self._counter}",
            type=ntype,
            x=center.x() - NODE_W / 2,
            y=center.y() - NODE_H / 2,
        )
        self.scene.add_node(node)

    def _add_note(self) -> None:
        """클릭 폴백: 화면 중앙에 빈 텍스트 메모를 추가한다."""
        self._place_note(self.view.mapToScene(self.view.viewport().rect().center()))

    def _add_note_at(self, scene_pos: QPointF) -> None:
        """드롭 위치(씬 좌표)에 텍스트 메모를 추가한다."""
        self._place_note(scene_pos)

    def _place_note(self, center: QPointF) -> None:
        """center 를 메모 중심으로 하여 빈 메모를 추가하고 바로 편집 상태로 만든다."""
        self._note_counter += 1
        while any(t.id == f"t{self._note_counter}" for t in self.sequence.notes):
            self._note_counter += 1
        note = TextNote(id=f"t{self._note_counter}", text="",
                        x=center.x() - 110, y=center.y() - 40)
        item = self.scene.add_note(note)
        self.scene.clearSelection()
        item.setSelected(True)
        # 드래그-드롭/버튼 클릭 직후엔 View 에 키보드 포커스가 없어 텍스트 편집기가
        # 입력을 못 받는다. View 에 포커스를 준 뒤, 드롭 이벤트가 끝난 다음 틱에
        # 편집을 시작해야 포커스가 안정적으로 잡혀 바로 타이핑이 가능하다.
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)
        QTimer.singleShot(0, item.begin_edit)

    def _on_panel_changed(self) -> None:
        # 값/라벨/포트 수가 바뀌면 노드 높이가 달라질 수 있어 지오메트리를 다시 계산한다.
        for item in self.scene.node_items.values():
            item.refresh_geometry()
        self.scene.refresh_edges()

    # --- 복사 / 붙여넣기 (Ctrl+C / Ctrl+V) ---
    def copy_selection(self) -> None:
        """선택된 노드(+그들 사이 연결)·텍스트 메모·그룹을 클립보드에 담는다.

        그룹을 복사하면 그 영역 안의 노드/메모도 함께 복사해 한 덩어리로
        붙여넣을 수 있게 한다.
        """
        sel = self.scene.selectedItems()
        groups = [it for it in sel if isinstance(it, GroupItem)]
        nodes = {it.node.id: it for it in sel if isinstance(it, NodeItem)}
        notes = {it.note.id: it for it in sel if isinstance(it, TextNoteItem)}
        for gi in groups:  # 선택된 그룹의 내용물(노드/메모)도 포함
            grect = QRectF(gi.group.x, gi.group.y, gi.group.w, gi.group.h)
            for it in self.scene.node_items.values():
                if grect.contains(it.mapToScene(it.boundingRect().center())):
                    nodes[it.node.id] = it
            for it in self.scene.note_items:
                nrect = QRectF(it.note.x, it.note.y, it.note.w, it.note.h)
                if grect.contains(nrect.center()):
                    notes[it.note.id] = it
        if not (nodes or notes or groups):
            return
        ids = set(nodes)
        self._clipboard = {
            "nodes": [it.node.to_dict() for it in nodes.values()],
            "edges": [e.to_dict() for e in self.sequence.edges
                      if e.from_node in ids and e.to_node in ids],
            "notes": [it.note.to_dict() for it in notes.values()],
            "groups": [gi.group.to_dict() for gi in groups],
        }

    def paste_clipboard(self) -> None:
        """클립보드의 노드(+내부 연결)와 텍스트 메모를 새 id 로 약간 옮겨 붙여넣고 선택한다."""
        clip = self._clipboard
        if not clip or not (clip.get("nodes") or clip.get("notes") or clip.get("groups")):
            return
        off = 40.0
        id_map: dict[str, str] = {}
        new_items: list[NodeItem] = []
        for nd in clip.get("nodes", []):
            self._counter += 1
            while any(n.id == f"n{self._counter}" for n in self.sequence.nodes):
                self._counter += 1
            new_id = f"n{self._counter}"
            id_map[nd["id"]] = new_id
            data = dict(nd)
            data["id"] = new_id
            data["x"] = data.get("x", 0.0) + off
            data["y"] = data.get("y", 0.0) + off
            self.scene.add_node(Node.from_dict(data))
            new_items.append(self.scene.node_items[new_id])
        for ed in clip.get("edges", []):
            f, t = id_map.get(ed["from_node"]), id_map.get(ed["to_node"])
            if f and t:
                edge = Edge(f, ed["from_port"], t)
                self.sequence.edges.append(edge)
                self.scene._add_edge_item(edge)
        new_notes: list[TextNoteItem] = []
        for nt in clip.get("notes", []):
            self._note_counter += 1
            while any(t.id == f"t{self._note_counter}" for t in self.sequence.notes):
                self._note_counter += 1
            data = dict(nt)
            data["id"] = f"t{self._note_counter}"
            data["x"] = data.get("x", 0.0) + off
            data["y"] = data.get("y", 0.0) + off
            new_notes.append(self.scene.add_note(TextNote.from_dict(data)))
        new_groups: list[GroupItem] = []
        for gd in clip.get("groups", []):
            self.scene._group_counter += 1
            while any(g.id == f"g{self.scene._group_counter}" for g in self.sequence.groups):
                self.scene._group_counter += 1
            data = dict(gd)
            data["id"] = f"g{self.scene._group_counter}"
            data["x"] = data.get("x", 0.0) + off
            data["y"] = data.get("y", 0.0) + off
            data["members"] = []  # 소속은 위치로 재계산되므로 레거시 멤버는 비운다
            grp = Group.from_dict(data)
            self.sequence.groups.append(grp)
            new_groups.append(self.scene._add_group_item(grp))
        self.scene.refresh_edges()
        self.scene.clearSelection()
        for it in new_items:
            it.setSelected(True)
        for it in new_notes:
            it.setSelected(True)
        for it in new_groups:
            it.setSelected(True)

    def set_active(self, ids) -> None:
        """실행 중 활성 노드(여러 흐름 동시)를 모두 강조한다."""
        active = set(ids)
        for nid, item in self.scene.node_items.items():
            item.set_highlight(nid in active)

    # --- 실행취소 / 다시실행 ---
    def _snapshot(self) -> str:
        return json.dumps(self.sequence.to_dict(), ensure_ascii=False, sort_keys=True)

    def reset_history(self) -> None:
        """현재 상태를 기준점으로 히스토리를 초기화한다(파일 새로 열기 등)."""
        self._undo.clear()
        self._redo.clear()
        self._baseline = self._snapshot()
        self._update_history_buttons()

    def _schedule_commit(self) -> None:
        if not self._restoring:
            self._history_timer.start(400)  # 변경이 멈춘 뒤 한 번만 스냅샷(연속 동작 합치기)

    def _commit(self) -> None:
        """모델이 실제로 바뀌었으면 직전 상태를 실행취소 스택에 쌓는다."""
        if self._restoring:
            return
        cur = self._snapshot()
        if cur == self._baseline:
            return
        self._undo.append(self._baseline)
        if len(self._undo) > self.UNDO_LIMIT:
            self._undo.pop(0)
        self._redo.clear()
        self._baseline = cur
        self._update_history_buttons()

    def _restore(self, snapshot: str) -> None:
        data = json.loads(snapshot)
        self._restoring = True
        self.sequence.nodes = [Node.from_dict(n) for n in data.get("nodes", [])]
        self.sequence.edges = [Edge.from_dict(e) for e in data.get("edges", [])]
        self.sequence.groups = [Group.from_dict(g) for g in data.get("groups", [])]
        self.sequence.notes = [TextNote.from_dict(t) for t in data.get("notes", [])]
        self.scene.rebuild()
        self.scene.refresh_edges()
        self._baseline = snapshot
        self._restoring = False
        self._update_history_buttons()

    def undo(self) -> None:
        self._history_timer.stop()
        self._commit()  # 보류 중인 변경을 먼저 확정
        if not self._undo:
            return
        self._redo.append(self._snapshot())
        if len(self._redo) > self.UNDO_LIMIT:
            self._redo.pop(0)
        self._restore(self._undo.pop())

    def redo(self) -> None:
        if not self._redo:
            return
        self._undo.append(self._snapshot())
        if len(self._undo) > self.UNDO_LIMIT:
            self._undo.pop(0)
        self._restore(self._redo.pop())

    def _update_history_buttons(self) -> None:
        self.undo_btn.setEnabled(bool(self._undo))
        self.redo_btn.setEnabled(bool(self._redo))

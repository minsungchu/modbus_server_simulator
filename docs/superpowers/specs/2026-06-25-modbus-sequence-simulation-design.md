# Modbus 시퀀스 시뮬레이션 설계 문서

- 작성일: 2026-06-25
- 대상 앱: `modbus_tcp_server.py` (PySide6 기반 Modbus TCP 서버 시뮬레이터)
- 목표: 시퀀스(단계별 신호 전송/대기/분기) 시뮬레이션을 **비주얼 노드 그래프**로 구축·실행·편집하는 기능 추가

## 1. 요구사항 요약

브레인스토밍에서 확정된 핵심 결정:

| 항목 | 결정 |
|---|---|
| 신호(signal) 정의 | **Modbus 레지스터 값** (코일/디스크릿/홀딩/입력 레지스터) |
| 실행 주체 | **둘 다 지원** — 서버가 능동 오케스트레이터(자기 레지스터에 쓰고 외부 클라이언트의 쓰기를 대기)이자 자가 시뮬레이션도 가능 |
| 분기 방식 | ① 다중 조건 대기 → 선착순 분기, ② 값 기반 분기, ③ 타임아웃 분기 |
| 편집 UI | **비주얼 노드 그래프** |
| 저장 | 시퀀스는 별도 파일 `modbus_sequence.json` |

기능 요약: 각 단계는 (a) 특정 신호를 전송하고, (b) 특정 신호를 기다리며, (c) 기다리는 신호의 종류/값에 따라 다른 경로로 분기한다.

## 2. 기존 코드 통합 지점 (확인 완료)

- 값 읽기: `self.store.getValues(fc, addr, count)` → 리스트 반환
  - 함수코드: `1`=coils, `2`=discrete inputs, `3`=holding registers, `4`=input registers
- 값 쓰기: `self.store.setValues(fc, addr, [value])`
  - 주의: `CustomModbusSlaveContext.setValues`는 호출자 함수명이 `on_register_value_changed`/`update_context_from_ui`가 **아니면** 외부 클라이언트 쓰기로 간주하여 `client_write_detected` 시그널을 emit한다. 엔진의 쓰기가 외부 쓰기로 오탐되지 않도록 쓰기 경로를 `on_register_value_changed`를 거치도록 한다.
- UI ↔ store 동기화: `update_from_context()` QTimer가 1초마다 수행 (`update_ui_from_context`, `update_context_from_ui`)
- 외부 클라이언트 쓰기 감지: `signals.client_write_detected(fc, addr, value)` → `on_client_write_detected`
- 레지스터 값/메모/컬럼 설정 저장: `modbus_registers.json` (단일 파일)
- 서버 수명주기: `toggle_server` → `start_server`/`stop_server`, `ModbusServerThread`(전용 asyncio 루프)
- 홀딩 레지스터는 컬럼별 절대 주소 방식, 전역 오프셋 미사용(`hr_offset=0`)

## 3. 아키텍처

### 3.1 접근 방식

선택: **별도 모듈 + 전용 창 (A안)**. 기존 `modbus_tcp_server.py`(2044줄)에는 "시퀀스 시뮬레이션" 버튼과 store/signals 접근용 어댑터 메서드만 추가하고, 신규 기능은 별도 패키지로 분리한다.

### 3.2 모듈 구조

```
sequence/
  __init__.py
  sequence_model.py    # 노드/연결 데이터모델 + JSON 직렬화 (dataclass)
  sequence_engine.py   # 실행 상태머신 (QObject, GUI 스레드 QTimer 50ms)
  sequence_editor.py   # QGraphicsView 기반 노드 그래프 에디터 위젯
  sequence_window.py   # 에디터 + 실행 컨트롤(Run/Stop/Step/Save/Load)을 담은 QMainWindow
```

`modbus_tcp_server.py` 변경:
- 연결 설정 영역 근처에 `"시퀀스 시뮬레이션"` 버튼 추가 → 클릭 시 `SequenceWindow` 오픈(단일 인스턴스 재사용).
- 어댑터 메서드 추가:
  - `read_register(reg_type, addr) -> int`: 적절한 fc로 `store.getValues` 호출.
  - `engine_write(reg_type, addr, value)`: `on_register_value_changed(reg_type, addr, value)`로 위임하여 store 갱신 + 파일 저장 + 외부쓰기 오탐 회피. 해당 위젯 즉시 갱신도 호출.
- `SequenceWindow`에 `self`(메인 윈도우)와 `self.store`/`self.signals` 접근 핸들 전달.

### 3.3 노드(스텝) 데이터 모델 (`sequence_model.py`)

`@dataclass`로 정의, Google 스타일 docstring + type hints.

```
NodeType = Enum: START, SEND, WAIT, BRANCH, DELAY, END

@dataclass Condition:
    reg_type: str        # "coils"|"discrete_inputs"|"holding_registers"|"input_registers"
    addr: int
    op: str              # "==","!=",">","<",">=","<="
    value: int

@dataclass WriteAction:
    reg_type: str
    addr: int
    value: int

@dataclass Node:
    id: str
    type: NodeType
    x: float
    y: float
    label: str = ""
    # 타입별 config (사용되는 필드만):
    writes: list[WriteAction]      # SEND
    conditions: list[Condition]    # WAIT (조건별 출력 포트)
    timeout_ms: int | None         # WAIT (None=무한대기)
    branch_reg: Condition?/read    # BRANCH: {reg_type, addr}
    cases: list[tuple[int, str]]   # BRANCH: (value, port_key) 목록, else 포트 별도
    delay_ms: int                  # DELAY
    result: str                    # END: "success"|"fail"|자유 라벨

@dataclass Edge:
    from_node: str
    from_port: str   # "next" | "cond_0".."cond_n" | "timeout" | "case_0".. | "else"
    to_node: str

@dataclass Sequence:
    nodes: list[Node]
    edges: list[Edge]
```

포트 규약:
- START/SEND/DELAY: 단일 출력 포트 `"next"`.
- WAIT: 조건 i마다 `"cond_i"`, 타임아웃 시 `"timeout"`.
- BRANCH: case i마다 `"case_i"`, 그 외 `"else"`.
- END: 출력 포트 없음.

JSON 직렬화: `to_dict()`/`from_dict()`로 `modbus_sequence.json` 저장/로드.

### 3.4 실행 엔진 (`sequence_engine.py`)

`SequenceEngine(QObject)`:
- 생성자: `Sequence`, 어댑터 콜백(`read_register`, `engine_write`)을 받음.
- `QTimer`(50ms 기본, 설정 가능)로 틱 구동. **GUI 스레드에서 동작** → Qt 위젯 안전 + store 접근은 기존 패턴과 동일.
- 상태: `current_node_id`, `node_entered_monotonic`(DELAY/타임아웃), `running`, `paused`.
- 시그널:
  - `node_activated(str node_id)` — 현재 노드 하이라이트용
  - `step_logged(str text)` — 실행 로그
  - `finished(str reason)` — 종료(END 도달/Stop/검증실패)

틱 처리 로직(노드 타입별):
- **START**: 즉시 `"next"` 엣지로 전진.
- **SEND**: `writes` 전부 `engine_write` 호출 후 `"next"` 전진.
- **WAIT**: `conditions`를 순서대로 평가, 처음 충족된 조건의 `"cond_i"` 포트로 분기. 아무것도 충족 안 되고 `timeout_ms` 경과 시 `"timeout"` 포트로 분기. timeout 포트 미연결 + 타임아웃 발생 시 `finished("timeout")`.
- **BRANCH**: `branch` 레지스터를 읽어 `cases`와 비교, 일치하는 `"case_i"`로, 없으면 `"else"`로 분기(즉시, 대기 없음).
- **DELAY**: `delay_ms` 경과 후 `"next"` 전진.
- **END**: `finished(result)`.

포트→다음노드 해석: `edges`에서 `(current_node, port)` 매칭. 미연결 포트로 진행 시 `finished("dangling:<port>")` + 로그.

`step()`: 일시정지 상태에서 한 틱만 강제 진행(디버깅용).
`stop()`: 타이머 중지 + `finished("stopped")`.

조건 평가 `_eval(cond) -> bool`: `read_register(cond.reg_type, cond.addr)`와 `cond.value`를 `cond.op`로 비교.

### 3.5 노드 그래프 에디터 (`sequence_editor.py`)

`SequenceEditor(QWidget)`:
- `QGraphicsScene` + `QGraphicsView`.
- 노드: 둥근 사각 `QGraphicsItem`(제목 + 타입색 + 입력/출력 포트 점). 드래그로 이동, 좌표는 모델 `x,y`에 반영.
- 엣지: 출력 포트에서 드래그 시작 → 입력 포트에 드롭 → `Edge` 생성. 베지어 `QGraphicsPathItem`로 렌더.
- 좌측 팔레트: 노드 타입 추가 버튼(START/SEND/WAIT/BRANCH/DELAY/END).
- 우측 속성 패널: 선택 노드의 config 편집 폼(타입별 동적 구성). 레지스터 타입 콤보, 주소 스핀, 값 입력(hex), 연산자 콤보, 조건/케이스 추가·삭제.
- 노드/엣지 삭제(Delete 키, 우클릭 메뉴).
- 실행 중 `node_activated`로 현재 노드 테두리 강조, `step_logged`를 하단 로그뷰에 출력.

### 3.6 전용 창 (`sequence_window.py`)

`SequenceWindow(QMainWindow)`:
- 중앙: `SequenceEditor`.
- 상단 툴바: New / Open(.json) / Save / Save As / Run / Stop / Step / 틱 간격 설정.
- 메인 윈도우 참조를 받아 `read_register`/`engine_write` 어댑터를 엔진에 연결.
- 서버 미실행 시 Run 누르면 경고(레지스터 store는 서버 미실행시에도 존재하므로 자가 시뮬은 가능 — 다만 외부 클라이언트 연동은 서버 실행 필요. 상태 라벨로 안내).

## 4. 영속성

- 파일: `modbus_sequence.json` (앱 실행 디렉터리, 기존 `modbus_registers.json`과 분리).
- 포맷:
```json
{
  "version": 1,
  "nodes": [
    {"id": "n1", "type": "START", "x": 40, "y": 100, "label": ""},
    {"id": "n2", "type": "SEND", "x": 200, "y": 100, "label": "start signal",
     "writes": [{"reg_type": "coils", "addr": 0, "value": 1}]},
    {"id": "n3", "type": "WAIT", "x": 380, "y": 100, "label": "wait ack",
     "conditions": [{"reg_type": "discrete_inputs", "addr": 0, "op": "==", "value": 1}],
     "timeout_ms": 5000}
  ],
  "edges": [
    {"from_node": "n1", "from_port": "next", "to_node": "n2"},
    {"from_node": "n2", "from_port": "next", "to_node": "n3"}
  ]
}
```

## 5. 에러 처리·검증

- 실행 전 검증(`Sequence.validate()`): START 노드 정확히 1개 존재, START에서 도달 가능, (경고) 미연결 출력 포트.
- 순환 연결(루프)은 사용자 의도로 허용하되 Stop 버튼으로 항상 중단 가능.
- 잘못된 hex/주소/범위 값은 모델 로드·편집 단계에서 정규화(0~65535 클램프). 비트 타입은 0/1.
- 구체적 예외 타입 사용(bare except 금지), `logging` 사용(print 금지).

## 6. 코드 규칙 준수

- Type hints 필수, Google 스타일 docstring.
- 함수 30줄 soft / 60줄 hard limit 지향 → 틱 처리는 노드 타입별 핸들러로 분리.
- import 순서: stdlib → PySide6 → local.
- 신규 모듈로 분리하여 `modbus_tcp_server.py` 비대화 방지.

## 7. 범위 밖 (YAGNI)

- 다중 시퀀스 동시 실행, 서브-시퀀스/함수 호출, 변수/연산식, 외부 스크립팅은 이번 범위에서 제외.
- 실행 이력 영구 저장/리플레이는 제외(로그뷰 표시만).
```

#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Modbus TCP 서버 시뮬레이터

이 프로그램은 Modbus TCP 프로토콜을 사용하는 서버를 시뮬레이션합니다.
주요 기능:
- Modbus TCP 서버 시작/중지
- 코일, 디스크릿 입력, 홀딩 레지스터, 입력 레지스터 값 조작
- 클라이언트 연결 및 요청 처리
- 레지스터 값 저장 및 불러오기
"""

import sys
import time
import logging
import threading
import time
import os
import sys
import json
from datetime import datetime
import socket
from contextlib import closing


# 내장된 스타일시트 (resources/style.qss 파일 내용을 직접 포함)
# 내장 스타일시트: resources/style.qss 를 단일 소스로 읽어 사용한다.
# (PyInstaller 번들에서도 동작하도록 _MEIPASS 경로를 우선 확인)
def _load_embedded_qss():
    """resources/style.qss 내용을 반환한다(실패 시 최소 다크 테마 기본값)."""
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    qss_path = os.path.join(base, "resources", "style.qss")
    try:
        with open(qss_path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return (
            "QWidget { background-color: #0f172a; color: #e2e8f0; "
            "font-family: 'Segoe UI', 'Malgun Gothic', Arial, sans-serif; font-size: 10pt; }"
        )


EMBEDDED_QSS_STYLE = _load_embedded_qss()

# PySide6 관련 임포트
from PySide6.QtCore import (
    QObject, Signal, Slot, QTimer, Qt, QRegularExpression, QThread, QSize,
    QPoint, QRect, QVariantAnimation, QEasingCurve,
)
from PySide6.QtGui import QIntValidator, QRegularExpressionValidator, QPixmap, QPainter, QColor, QLinearGradient, QBrush, QPen, QFont, QIcon, QPolygon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox, QCheckBox, QGridLayout,
    QScrollArea, QMessageBox, QComboBox, QGraphicsDropShadowEffect,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QSizePolicy,
)

# Pymodbus 관련 임포트
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext, ModbusSlaveContext
from pymodbus.device import ModbusDeviceIdentification

# pymodbus 버전 호환성을 위한 import 처리
import asyncio
from pymodbus.server import StartAsyncTcpServer, ServerAsyncStop

# 애플리케이션 버전 (pyproject.toml 단일 소스)
from appversion import APP_VERSION


def _ensure_writable_data_dir() -> None:
    """패키징(frozen) 실행 시 작업 디렉터리를 사용자 쓰기 가능 폴더로 옮긴다.

    설치형 실행파일은 보통 'C:\\Program Files\\...' 처럼 일반 사용자가 쓸 수 없는
    위치에 설치된다. 로그/레지스터/시퀀스 등 런타임 파일은 모두 상대경로로
    저장되므로, 이 폴더로 CWD 를 옮겨 PermissionError 를 막는다. (리소스는
    sys._MEIPASS 절대경로로 읽으므로 영향 없음.) 소스 실행에는 영향이 없다.
    """
    if not getattr(sys, "frozen", False):
        return
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        root = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    data_dir = os.path.join(root, "ModbusTcpServer")
    try:
        os.makedirs(data_dir, exist_ok=True)
        os.chdir(data_dir)
    except OSError:
        pass  # 폴더 생성/이동 실패 시 기존 동작 유지


_ensure_writable_data_dir()

# 로깅 설정
# - 파일은 회전 핸들러로 용량을 제한(최대 1MB × 3개)하여 로그 파일이 무한히 커지지 않도록 한다.
# - 평상시에는 WARNING 이상(에러/경고)만 기록한다. 자세한 디버그가 필요하면
#   환경변수 MODBUS_DEBUG=1 로 실행하면 INFO 까지 기록된다.
from logging.handlers import RotatingFileHandler

_LOG_LEVEL = logging.INFO if os.environ.get("MODBUS_DEBUG") == "1" else logging.WARNING

logger = logging.getLogger("ModbusServerSim")
logger.setLevel(_LOG_LEVEL)
logger.propagate = False

_log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

file_handler = RotatingFileHandler(
    'modbus_server.log', maxBytes=1_000_000, backupCount=3, encoding='utf-8'
)
file_handler.setLevel(_LOG_LEVEL)
file_handler.setFormatter(_log_formatter)
logger.addHandler(file_handler)

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.WARNING)
_console_handler.setFormatter(_log_formatter)
logger.addHandler(_console_handler)

class ModbusSignals(QObject):
    """스레드 간 시그널 처리를 위한 클래스
    
    서버 상태 변경 및 데이터 변경 시 시그널을 발생시켜 UI 업데이트를 처리합니다.
    """
    server_started = Signal()  # 서버 시작 시그널
    server_stopped = Signal()  # 서버 중지 시그널
    data_changed = Signal(str, int, int)  # 데이터 변경 시그널 (register_type, address, value)
    register_changed = Signal(str, int, int)  # 레지스터 변경 시그널 (register_type, address, value)
    client_write_detected = Signal(int, int, int)  # 클라이언트 쓰기 감지 시그널 (function_code, address, value)


class CustomModbusSlaveContext(ModbusSlaveContext):
    """클라이언트에 의해 값이 변경될 때 시그널을 발생시키는 커스텀 ModbusSlaveContext
    
    클라이언트의 읽기/쓰기 작업을 감지하고 로깅하며, UI 업데이트를 위한 시그널을 발생시킵니다.
    """
    def __init__(self, signals=None, *args, hr_offset=0, **kwargs):
        super().__init__(*args, **kwargs)
        self.signals = signals if signals is not None else ModbusSignals()
        self.last_write_source = None
        self.hr_offset = hr_offset  # 홀딩 레지스터 오프셋 값 설정
        logger.info(f"커스텀 ModbusSlaveContext 초기화 완료 - HR 오프셋: {self.hr_offset}")
        
    def getValues(self, fx, address, count=1):
        """부모 getValues 를 위임 호출한다.

        컬럼별 절대 주소 방식을 사용하므로(오프셋 미사용) 주소 변환이 필요 없다.
        성능을 위해 정상 경로에서는 로깅하지 않고, 오류 시에만 기록한다.
        """
        try:
            return super().getValues(fx, address, count)
        except Exception as e:
            logger.error(f"Error in getValues (fx={fx}, addr={address}, count={count}): {e}")
            return [0] * count

    def setValues(self, fx, address, values):
        """부모 setValues 호출 후, 외부 클라이언트 쓰기면 UI 갱신 시그널을 발생시킨다.

        컬럼별 절대 주소 방식을 사용하므로 오프셋 변환은 하지 않는다.
        호출자가 UI 자체(자기 자신)인 경우에는 피드백 루프를 막기 위해 시그널을 보내지 않는다.
        성능을 위해 정상 경로에서는 로깅하지 않는다.
        """
        # 호출자 이름을 저렴하게 확인 (전체 스택 추출 대신 한 프레임만 조회)
        try:
            caller = sys._getframe(1).f_code.co_name
        except Exception:
            caller = ""

        try:
            super().setValues(fx, address, values)

            # 우리 UI 자체의 쓰기가 아니면(=외부 클라이언트 쓰기) UI 갱신 시그널 발생
            if caller not in ("on_register_value_changed", "update_context_from_ui"):
                for i, value in enumerate(values):
                    self.signals.client_write_detected.emit(fx, address + i, value)
        except Exception as e:
            logger.error(f"Error in setValues (fx={fx}, addr={address}): {e}")


class ModbusServerThread(QThread):
    """Modbus 서버를 실행하기 위한 스레드 클래스
    
    별도의 스레드에서 Modbus TCP 서버를 실행하여 UI 블로킹을 방지합니다.
    서버의 시작, 실행, 종료를 관리합니다.
    """
    def __init__(self, address, port, context, signals):
        super().__init__()
        self.address = address  # 서버 주소
        self.port = port  # 서버 포트
        self.context = context  # Modbus 컨텍스트
        self.signals = signals  # 시그널 객체
        self.running = False  # 서버 실행 상태
        self._server_started = False  # 서버 시작 완료 상태
        self.loop = None  # 이 스레드 전용 asyncio 이벤트 루프

        logger.info(f"ModbusServerThread 초기화: {address}:{port}")

    def _port_in_use(self):
        """대상 주소/포트가 이미 사용 중인지 확인한다."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        try:
            return sock.connect_ex((self.address, self.port)) == 0
        except OSError:
            return False
        finally:
            sock.close()

    def run(self):
        """전용 asyncio 이벤트 루프에서 Modbus TCP 서버를 실행한다.

        StartAsyncTcpServer 는 서버가 종료(ServerAsyncStop)될 때까지 블록하며,
        종료되면 루프가 정상적으로 끝나 포트가 해제된다.
        """
        # 포트 선점 여부 사전 점검
        if self._port_in_use():
            logger.error(f"Port {self.port} is already in use.")
            self.signals.server_stopped.emit()
            return

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.running = True
        try:
            self.loop.run_until_complete(self._serve())
        except Exception as e:
            logger.error(f"Modbus 서버 실행 오류: {e}")
        finally:
            try:
                self.loop.close()
            except Exception:
                pass
            self.loop = None
            self.running = False
            self._server_started = False
            self.signals.server_stopped.emit()
            logger.info("Server stopped")

    async def _serve(self):
        """서버 시작 코루틴. ServerAsyncStop 호출 시 반환된다."""
        identity = ModbusDeviceIdentification()
        identity.VendorName = 'Modbus Server Simulator'
        identity.ProductCode = 'MODSIM'
        identity.ProductName = 'Modbus Server Simulator'
        identity.ModelName = 'Simulator'
        identity.MajorMinorRevision = '1.0'

        self._server_started = True
        self.signals.server_started.emit()
        # 서버가 종료될 때까지 블록 (ServerAsyncStop 으로 해제)
        await StartAsyncTcpServer(
            context=self.context,
            identity=identity,
            address=(self.address, self.port),
        )

    def stop(self):
        """서버를 정상 종료한다.

        UI(메인) 스레드에서 호출되며, 서버 스레드의 asyncio 루프에 ServerAsyncStop 을
        안전하게 예약하여 서버를 깔끔하게 멈추고 포트를 해제한다. 이후 재연결이 가능하다.
        """
        loop = self.loop
        if not self.running or loop is None:
            return

        async def _shutdown():
            try:
                await ServerAsyncStop()
            except Exception as e:
                logger.warning(f"ServerAsyncStop 오류(무시 가능): {e}")

        try:
            future = asyncio.run_coroutine_threadsafe(_shutdown(), loop)
            future.result(timeout=3)
        except Exception as e:
            logger.warning(f"서버 종료 예약 중 오류: {e}")

        # 루프가 끝나고 스레드가 완전히 종료될 때까지 대기
        if not self.wait(3000):
            logger.warning("서버 스레드가 제한 시간 내에 종료되지 않았습니다.")


class RegisterWidget(QWidget):
    """레지스터 값을 표시하고 편집하기 위한 위젯
    
    코일, 디스크릿 입력, 홀딩 레지스터, 입력 레지스터의 값을 표시하고
    사용자가 값을 편집할 수 있도록 합니다. 또한 각 레지스터에 메모를 추가할 수 있습니다.
    """
    # 값 변경 시그널 추가
    value_changed = Signal(str, int, int)  # register_type, address, value
    # 메모 변경 시그널 추가
    memo_changed = Signal(str, int, str)  # register_type, address, memo_text
    
    def __init__(self, register_type, register_count=100, parent=None, columns=None):
        super().__init__(parent)
        self.register_type = register_type
        # 홀딩 레지스터는 컬럼별 (시작주소, 개수) 설정으로 동작한다.
        # columns 예: [(0, 100), (100, 100)] -> 컬럼1=0~99, 컬럼2=100~199
        if register_type == "holding_registers":
            self.columns = columns if columns else [(0, 100), (100, 100)]
            self.register_count = sum(count for _, count in self.columns)
        else:
            self.columns = []
            self.register_count = register_count
        self.is_bit_type = register_type in ["coils", "discrete_inputs"]
        # 모든 딕셔너리는 홀딩 레지스터의 경우 '절대 주소'를 키로 사용한다.
        self.values = {}
        self.checkboxes = {}
        self.line_edits = {}
        self.memo_edits = {}  # 메모 텍스트 필드 저장용
        self.address_labels = {}  # 주소 라벨 저장용
        self.offset = 0  # (하위 호환용, 더 이상 사용하지 않음)

        self.init_ui()
        
    def init_ui(self):
        # 홀딩 레지스터인 경우 컬럼별 (시작주소, 개수) 설정에 따라 표시
        if self.register_type == "holding_registers":
            # 기존 레이아웃이 있으면 삭제
            if self.layout():
                QWidget().setLayout(self.layout())

            # 메인 레이아웃을 수평 레이아웃으로 생성
            main_layout = QHBoxLayout()

            # 각 컬럼을 설정된 시작 주소/개수로 구성한다 (절대 주소를 키로 사용)
            for start, count in self.columns:
                col_widget = QWidget()
                col_layout = QGridLayout(col_widget)
                col_layout.setSpacing(5)

                # 헤더 추가 (패딩 0 적용)
                address_header = QLabel("Address")
                address_header.setStyleSheet("padding: 0px;")
                value_header = QLabel("Value (Hex)")
                value_header.setStyleSheet("padding: 0px;")
                memo_header = QLabel("Memo")
                memo_header.setStyleSheet("padding: 0px;")
                col_layout.addWidget(address_header, 0, 0)
                col_layout.addWidget(value_header, 0, 1)
                col_layout.addWidget(memo_header, 0, 2)

                for row in range(count):
                    addr = start + row

                    # 주소 레이블 (절대 주소 표시)
                    address_label = QLabel(str(addr))
                    address_label.setStyleSheet("padding: 0px;")
                    col_layout.addWidget(address_label, row + 1, 0)
                    self.address_labels[addr] = address_label

                    # 값 입력 필드
                    line_edit = self.create_register_widget(addr)
                    self.line_edits[addr] = line_edit
                    col_layout.addWidget(line_edit, row + 1, 1)
                    self.values[addr] = "0000"

                    # 메모 필드
                    memo_edit = QLineEdit()
                    memo_edit.setPlaceholderText("메모 입력")
                    memo_edit.setMinimumWidth(100)
                    memo_edit.setStyleSheet("border: 1px solid #334155; border-radius: 10px; padding: 0px;")
                    memo_edit.textChanged.connect(lambda text, a=addr: self.on_memo_changed(a, text))
                    self.memo_edits[addr] = memo_edit
                    col_layout.addWidget(memo_edit, row + 1, 2)

                # 스크롤 영역 추가
                col_scroll = QScrollArea()
                col_scroll.setWidget(col_widget)
                col_scroll.setWidgetResizable(True)
                main_layout.addWidget(col_scroll)

            # 값 입력 시그널 연결
            for addr in self.line_edits:
                self.connect_line_edit(addr)

            self.setLayout(main_layout)
            return
        
        # 비트 타입 또는 다른 레지스터 타입인 경우 기존 레이아웃 사용
        # 기존 레이아웃이 있으면 삭제
        if self.layout():
            QWidget().setLayout(self.layout())
            
        # 새 레이아웃 생성
        layout = QGridLayout()
        layout.setSpacing(5)
        
        # Headers
        layout.addWidget(QLabel("Address"), 0, 0)
        if self.is_bit_type:
            layout.addWidget(QLabel("Value"), 0, 1)
        else:
            layout.addWidget(QLabel("Value (Hexadecimal A-F, a-f)"), 0, 1)
        
        # Create register controls
        for i in range(self.register_count):
            # Address label
            layout.addWidget(QLabel(str(i)), i+1, 0)
            
            if self.is_bit_type:
                # Checkbox for bit types (coils, discrete inputs)
                checkbox = QCheckBox()
                self.checkboxes[i] = checkbox
                layout.addWidget(checkbox, i+1, 1)
                self.values[i] = 0
            else:
                # Line edit for register types (input)
                line_edit = self.create_register_widget(i)
                self.line_edits[i] = line_edit
                layout.addWidget(line_edit, i+1, 1)
                self.values[i] = "0000"
        
        # Connect signals - 람다 함수 문제 해결
        if self.is_bit_type and (self.register_type == "coils" or self.register_type == "discrete_inputs"):
            for addr in self.checkboxes.keys():
                # 각 체크박스마다 개별 함수 연결
                self.connect_checkbox(addr)
        
        if not self.is_bit_type and self.register_type == "holding_registers":
            for addr in self.line_edits.keys():
                # 각 라인 에디트마다 개별 함수 연결
                self.connect_line_edit(addr)
        
        layout.setRowStretch(self.register_count + 1, 1)
        self.setLayout(layout)
    
    def create_register_widget(self, address):
        line_edit = QLineEdit()
        line_edit.setObjectName(f"register_{address}")
        # Set hex validator that allows 0-9, A-F, a-f
        line_edit.setValidator(QRegularExpressionValidator(QRegularExpression(r'^[0-9A-Fa-f]{0,4}$')))
        line_edit.textChanged.connect(lambda text, addr=address: self.on_value_changed(addr, text.upper()))
        
        # 홀딩 레지스터인 경우 width를 4글자 크기로 설정하고 가운데 정렬
        if self.register_type == "holding_registers":
            # 4글자 들어갈 만한 width 계산 (약 60px)
            line_edit.setFixedWidth(60)
            # 가운데 정렬 설정
            line_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # 레지스터 입력창에 테두리 직접 적용 및 패딩 0으로 설정
        line_edit.setStyleSheet("border: 1px solid #334155; border-radius: 10px; padding: 0px;")
        return line_edit
    
    def connect_checkbox(self, addr):
        """체크박스 시그널 연결을 위한 헬퍼 함수"""
        self.checkboxes[addr].stateChanged.connect(lambda state: self.on_bit_changed(addr, state))
    
    def connect_line_edit(self, addr):
        """라인 에디트 시그널 연결을 위한 헬퍼 함수"""
        self.line_edits[addr].editingFinished.connect(lambda: self.on_register_changed(addr))
    
    def on_bit_changed(self, addr, state):
        # 디버깅을 위해 실제 상태 값 출력
        logger.info(f"Raw checkbox state: {state}, type: {type(state)}, Qt.Checked: {Qt.CheckState.Checked}")
        
        # 체크박스 상태 확인 - isChecked() 사용
        is_checked = self.checkboxes[addr].isChecked()
        value = 1 if is_checked else 0
        
        self.values[addr] = value
        logger.info(f"Bit changed: {self.register_type}[{addr}] = {value}, checkbox is checked: {is_checked}")
        # 값 변경 시그널 발생
        self.value_changed.emit(self.register_type, addr, value)
    
    def on_register_changed(self, addr):
        """입력 완료(editingFinished) 시 값을 4자리 16진수로 정규화하여 표시한다.

        Args:
            addr (int): 대상 레지스터의 절대 주소.
        """
        text = self.line_edits[addr].text().strip()
        try:
            value = int(text, 16) if text else 0
        except ValueError:
            # 파싱 실패 시 직전에 보관된 값으로 되돌린다.
            try:
                value = int(self.values.get(addr, "0"), 16)
            except (ValueError, TypeError):
                value = 0

        # 16비트 레지스터 유효 범위로 제한
        value = max(0, min(value, 65535))
        hex_value = f"{value:04X}"
        self.values[addr] = hex_value

        # 입력 완료 시점에만 4자리로 정규화하여 표시 (시그널 차단으로 재귀 방지)
        old_state = self.line_edits[addr].blockSignals(True)
        self.line_edits[addr].setText(hex_value)
        self.line_edits[addr].blockSignals(old_state)

        logger.info(f"Register changed: {self.register_type}[{addr}] = {hex_value} (int: {value})")
        self.value_changed.emit(self.register_type, addr, value)

    def on_value_changed(self, addr, text):
        """타이핑 도중(textChanged) 처리.

        입력창 텍스트를 다시 쓰지 않으므로 커서가 유지되어 4자리 16진수를
        자연스럽게 연속 입력할 수 있다. 4자리 정규화는 on_register_changed에서 수행한다.

        Args:
            addr (int): 대상 레지스터의 절대 주소.
            text (str): 현재 입력창의 텍스트.
        """
        hex_text = text.strip()
        try:
            value = int(hex_text, 16) if hex_text else 0
        except ValueError:
            # 검증기가 16진수만 허용하므로 일반적으로 도달하지 않음
            return

        # 16비트 레지스터 유효 범위로 제한
        value = max(0, min(value, 65535))
        # 내부 값은 4자리 16진수 문자열로 보관하되 입력창은 건드리지 않는다.
        self.values[addr] = f"{value:04X}"
        self.value_changed.emit(self.register_type, addr, value)
    
    def on_memo_changed(self, addr, text):
        """메모 입력 필드 변경 처리"""
        # 메모 변경 시그널 발생 (홀딩 레지스터만 해당)
        if self.register_type == "holding_registers":
            self.memo_changed.emit(self.register_type, addr, text)
            logger.debug(f"Memo changed: {self.register_type}[{addr}] = {text}")
    
    def update_value(self, addr, value):
        """서버 값으로 레지스터 표시를 갱신한다 (절대 주소 기준)."""
        if self.is_bit_type:
            if addr not in self.checkboxes:
                return
            self.values[addr] = value
            self.checkboxes[addr].setChecked(bool(value))
        else:
            if addr not in self.line_edits:
                return
            # 4자리 16진수 문자열로 포맷
            hex_value = f"{value:04X}"

            # 편집 중(포커스 보유)이 아닐 때만 갱신
            if not self.line_edits[addr].hasFocus():
                old_state = self.line_edits[addr].blockSignals(True)
                self.line_edits[addr].setText(hex_value)
                self.line_edits[addr].blockSignals(old_state)
                self.values[addr] = hex_value
                logger.debug(f"Updated {self.register_type}[{addr}] to {hex_value} from server")


# 값 변경 시 행 하이라이트(플래시) 연출 설정
FLASH_COLOR = QColor(251, 191, 36)   # amber (#fbbf24) — "변경됨" 신호
FLASH_MAX_ALPHA = 150                # 시작 알파(0~255)
FLASH_DURATION_MS = 2000             # 서서히 사라지는 시간(클수록 느리게 사라짐)


class HoldingRegisterTable(QWidget):
    """홀딩 레지스터 맵을 표(QTableWidget) 형태로 표시·편집하는 위젯.

    각 컬럼 설정(start, count)마다 Address / Value(Hex) / Memo 3개 열을 가진
    표 하나를 만들어 좌우로 배치한다(기존 2분할 레이아웃 유지). 외부 클라이언트
    쓰기나 시퀀스 시뮬레이션 쓰기로 값이 갱신되면 해당 행 전체가 잠깐
    하이라이트되었다가 서서히 사라진다.

    기존 RegisterWidget 과 동일한 공개 인터페이스(values/line_edits/memo_edits/
    value_changed/memo_changed/update_value)를 제공하여 메인 창의 저장·로드·
    동기화 코드와 그대로 호환된다.
    """

    value_changed = Signal(str, int, int)   # register_type, address, value
    memo_changed = Signal(str, int, str)     # register_type, address, memo_text

    def __init__(self, columns=None, parent=None):
        """테이블 위젯을 초기화한다.

        Args:
            columns: [(start, count), ...] 형식의 주소 범위 설정.
            parent: 부모 위젯.
        """
        super().__init__(parent)
        self.register_type = "holding_registers"
        self.is_bit_type = False
        self.columns = columns if columns else [(0, 100), (100, 100)]

        # 호환용 자료구조(메인 창 코드가 그대로 참조한다)
        self.values = {}
        self.checkboxes = {}
        self.line_edits = {}
        self.memo_edits = {}
        self.address_labels = {}

        # 행 하이라이트 관리용
        self._row_of = {}          # addr -> row index
        self._row_items = {}       # addr -> (addr_item, value_item, memo_item)
        self._flash_anims = {}     # addr -> QVariantAnimation

        self.init_ui()

    def init_ui(self):
        """컬럼 설정마다 별도의 표를 만들어 좌우로 배치한다.

        각 (start, count) 범위가 하나의 테이블이 되며, 행 하이라이트와 값/메모는
        절대 주소를 키로 관리하므로 어느 테이블에 속하든 동일하게 동작한다.
        """
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.tables = []
        for start, count in self.columns:
            table = self._create_column_table(start, count)
            self.tables.append(table)
            main_layout.addWidget(table)

        # 일부 코드/도구가 단일 참조를 기대할 수 있어 첫 테이블을 노출
        self.table = self.tables[0] if self.tables else None

        # 값 입력 완료 시그널 연결
        for addr in self.line_edits:
            self.connect_line_edit(addr)

    def _create_column_table(self, start, count):
        """한 주소 범위(start~start+count-1)에 대한 테이블 하나를 생성한다.

        Args:
            start: 범위 시작 주소.
            count: 범위 내 레지스터 개수.

        Returns:
            QTableWidget: 구성이 완료된 테이블.
        """
        table = QTableWidget(count, 3, self)
        table.setHorizontalHeaderLabels(["Address", "Value (Hex)", "Memo"])
        table.verticalHeader().setVisible(False)
        # 값/메모는 셀에 올린 QLineEdit 으로 편집하므로 아이템 직접 편집은 끈다.
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        for row in range(count):
            addr = start + row

            # Address 컬럼: 편집 불가 아이템
            addr_item = QTableWidgetItem(str(addr))
            addr_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            addr_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            table.setItem(row, 0, addr_item)

            # Value 컬럼: 16진수 입력용 QLineEdit 을 셀 위젯으로 배치
            value_item = QTableWidgetItem()
            value_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            table.setItem(row, 1, value_item)
            line_edit = self._create_value_edit(addr)
            table.setCellWidget(row, 1, line_edit)
            self.line_edits[addr] = line_edit
            self.values[addr] = "0000"

            # Memo 컬럼: 메모 입력용 QLineEdit
            memo_item = QTableWidgetItem()
            memo_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            table.setItem(row, 2, memo_item)
            memo_edit = QLineEdit()
            memo_edit.setPlaceholderText("메모 입력")
            memo_edit.setStyleSheet("border: none; background: transparent; padding: 1px 4px;")
            memo_edit.setMaximumHeight(20)
            memo_edit.textChanged.connect(lambda text, a=addr: self.on_memo_changed(a, text))
            table.setCellWidget(row, 2, memo_edit)
            self.memo_edits[addr] = memo_edit

            self._row_of[addr] = row
            self._row_items[addr] = (addr_item, value_item, memo_item)

        # 각 행 높이를 콤팩트하게 고정한다(콘텐츠 자동 높이보다 더 낮게).
        vheader = table.verticalHeader()
        vheader.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        vheader.setDefaultSectionSize(20)

        # 표가 영역(스크롤 뷰포트)에 맞춰 세로로 늘어나도록 한다.
        # 행이 많아 영역을 넘기면 표 자체의 세로 스크롤바가 처리한다.
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return table

    def _create_value_edit(self, address):
        """값 입력용 QLineEdit 을 생성한다(투명 배경으로 행 하이라이트가 비치도록).

        Args:
            address: 대상 레지스터 주소.

        Returns:
            QLineEdit: 16진수 검증기가 적용된 입력 위젯.
        """
        line_edit = QLineEdit()
        line_edit.setObjectName(f"register_{address}")
        line_edit.setValidator(QRegularExpressionValidator(QRegularExpression(r'^[0-9A-Fa-f]{0,4}$')))
        line_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 셀 배경(하이라이트)이 비치도록 투명 처리하고 테두리는 표의 격자선에 맡긴다.
        line_edit.setStyleSheet("border: none; background: transparent; padding: 1px 4px;")
        line_edit.setMaximumHeight(20)
        line_edit.textChanged.connect(lambda text, addr=address: self.on_value_changed(addr, text.upper()))
        return line_edit

    def connect_line_edit(self, addr):
        """라인 에디트의 editingFinished 시그널을 연결한다."""
        self.line_edits[addr].editingFinished.connect(lambda: self.on_register_changed(addr))

    def on_register_changed(self, addr):
        """입력 완료 시 4자리 16진수로 정규화하고 값 변경 시그널을 발생시킨다."""
        text = self.line_edits[addr].text().strip()
        try:
            value = int(text, 16) if text else 0
        except ValueError:
            try:
                value = int(self.values.get(addr, "0"), 16)
            except (ValueError, TypeError):
                value = 0

        value = max(0, min(value, 65535))
        hex_value = f"{value:04X}"
        self.values[addr] = hex_value

        old_state = self.line_edits[addr].blockSignals(True)
        self.line_edits[addr].setText(hex_value)
        self.line_edits[addr].blockSignals(old_state)

        logger.info(f"Register changed: {self.register_type}[{addr}] = {hex_value} (int: {value})")
        self.value_changed.emit(self.register_type, addr, value)

    def on_value_changed(self, addr, text):
        """타이핑 중 내부 값을 갱신한다(입력창은 건드리지 않아 커서를 유지)."""
        hex_text = text.strip()
        try:
            value = int(hex_text, 16) if hex_text else 0
        except ValueError:
            return
        value = max(0, min(value, 65535))
        self.values[addr] = f"{value:04X}"
        self.value_changed.emit(self.register_type, addr, value)

    def on_memo_changed(self, addr, text):
        """메모 변경 시그널을 발생시킨다."""
        self.memo_changed.emit(self.register_type, addr, text)
        logger.debug(f"Memo changed: {self.register_type}[{addr}] = {text}")

    def update_value(self, addr, value):
        """서버/시퀀스 값으로 셀을 갱신하고 해당 행을 하이라이트한다.

        사용자가 직접 편집해서 발생한 변경이 아니라, 외부 클라이언트 쓰기나
        시퀀스 시뮬레이션 쓰기로 값이 들어올 때 호출된다.

        Args:
            addr: 대상 레지스터 주소.
            value: 정수 값.
        """
        if addr not in self.line_edits:
            return
        hex_value = f"{value:04X}"
        # 사용자가 편집 중(포커스 보유)이 아닐 때만 표시 갱신
        if not self.line_edits[addr].hasFocus():
            old_state = self.line_edits[addr].blockSignals(True)
            self.line_edits[addr].setText(hex_value)
            self.line_edits[addr].blockSignals(old_state)
            self.values[addr] = hex_value
            logger.debug(f"Updated {self.register_type}[{addr}] to {hex_value} from server")
        self.flash_row(addr)

    def flash_row(self, addr):
        """주어진 주소의 행 전체를 하이라이트했다가 서서히 사라지게 한다.

        Args:
            addr: 하이라이트할 레지스터 주소.
        """
        items = self._row_items.get(addr)
        if not items:
            return

        # 진행 중인 애니메이션이 있으면 정지 후 재시작(연속 변경 대응)
        prev = self._flash_anims.get(addr)
        if prev is not None:
            prev.stop()

        anim = QVariantAnimation(self)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setDuration(FLASH_DURATION_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _apply(progress):
            alpha = int(FLASH_MAX_ALPHA * max(0.0, float(progress)))
            brush = QBrush(QColor(FLASH_COLOR.red(), FLASH_COLOR.green(), FLASH_COLOR.blue(), alpha))
            for item in items:
                item.setBackground(brush)

        def _finish():
            empty = QBrush()
            for item in items:
                item.setBackground(empty)
            self._flash_anims.pop(addr, None)

        anim.valueChanged.connect(_apply)
        anim.finished.connect(_finish)
        self._flash_anims[addr] = anim
        anim.start()


class ModbusServerSimulator(QMainWindow):
    """Modbus 서버 시뮬레이터 메인 애플리케이션 창
    
    사용자 인터페이스를 제공하고 Modbus 서버의 동작을 제어합니다.
    레지스터 값 표시, 서버 시작/중지, 설정 관리 등의 기능을 제공합니다.
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Modbus Server Simulator  v{APP_VERSION}")
        # 초기 창 크기: 세로를 가로의 약 1.1배로(세로로 살짝 긴 형태)
        self.resize(900, int(900 * 1.1))
        
        # 최소 창 크기 설정 - UI 리사이징 시 글자가 가려지지 않도록 최소 크기 설정
        self.setMinimumSize(900, 600)
        
        # 애플리케이션 아이콘 설정
        self.create_server_icon()
        
        # 버튼 아이콘 생성
        self.create_button_icons()
        
        # 스타일시트 적용
        self.load_stylesheet()
        
        # 레지스터 값 저장 파일 경로 설정
        self.register_file = "modbus_registers.json"

        # 홀딩 레지스터 컬럼 설정: [(시작주소, 개수), ...]
        # 기본값은 기존 동작과 동일하게 컬럼1=0~99, 컬럼2=100~199
        self.holding_columns = [(0, 100), (100, 100)]

        # Initialize Modbus data store
        self.init_modbus_store()
        
        # Create signals object
        self.signals = ModbusSignals()
        self.signals.server_started.connect(self.on_server_started)
        self.signals.server_stopped.connect(self.on_server_stopped)
        self.signals.client_write_detected.connect(self.on_client_write_detected)
        
        # 자동 저장 타이머 설정
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save_registers_to_file)
        self.save_pending = False
        
        # Initialize UI
        self.init_ui()
        
        # Server thread
        self.server_thread = None
        self.server_running = False

        # 시퀀스 시뮬레이션 창 (단일 인스턴스, 최초 열 때 생성)
        self._sequence_window = None
        
        # Timer for polling data changes
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_from_context)
        self.timer.start(1000)  # Update every 1000ms (1초) - 업데이트 빈도 감소

        # 앱 시작 시 저장된 컬럼 설정·레지스터 값·메모 복원 (껐다 켜도 유지)
        self.restore_saved_state()
    
    def create_server_icon(self):
        """서버 애플리케이션의 아이콘 생성
        
        Modbus TCP 서버 애플리케이션을 식별하는 데 사용되는 아이콘입니다.
        'S' 문자가 포함된 아이콘을 생성합니다.
        """
        # 서버 아이콘 생성 (S 문자 포함)
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        gradient = QLinearGradient(0, 0, 64, 64)
        gradient.setColorAt(0, QColor(30, 58, 95))  # 더 어두운 파란색
        gradient.setColorAt(1, QColor(44, 76, 124))  # 약간 더 밝은 파란색
        painter.setBrush(QBrush(gradient))
        painter.setPen(QPen(QColor(58, 94, 140), 2))
        painter.drawRoundedRect(4, 4, 56, 56, 10, 10)
        painter.setPen(QPen(QColor(255, 255, 255), 2))  # 흰색 펜
        painter.setFont(QFont("Arial", 32, QFont.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "S")
        painter.end()
        icon = QIcon(pixmap)
        self.setWindowIcon(icon)
        
        # 더 이상 파일로 저장하지 않음 (외부 파일 생성 방지)
        logger.debug("서버 아이콘 생성 및 설정 완료")

    def create_button_icons(self):
        """모든 버튼 아이콘을 동적으로 생성
        
        외부 SVG 파일 대신 코드에서 직접 아이콘을 생성하여 
        실행 파일에 모든 아이콘이 포함되도록 합니다.
        """
        # 아이콘 저장 딕셔너리
        self.dynamic_icons = {}
        
        # 시작(연결) 아이콘 생성
        start_icon = QPixmap(64, 64)
        start_icon.fill(Qt.transparent)
        painter = QPainter(start_icon)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 초록색 원형 배경
        painter.setBrush(QColor(0, 180, 0))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(4, 4, 56, 56)
        
        # 삼각형 재생 심볼
        painter.setBrush(QColor(255, 255, 255))
        points = [QPoint(20, 16), QPoint(20, 48), QPoint(48, 32)]
        painter.drawPolygon(QPolygon(points))
        painter.end()
        
        self.dynamic_icons["start"] = QIcon(start_icon)
        self.start_icon = self.dynamic_icons["start"]
        
        # 중지(연결 해제) 아이콘 생성
        stop_icon = QPixmap(64, 64)
        stop_icon.fill(Qt.transparent)
        painter = QPainter(stop_icon)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 빨간색 원형 배경
        painter.setBrush(QColor(180, 0, 0))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(4, 4, 56, 56)
        
        # 정사각형 중지 심볼
        painter.setBrush(QColor(255, 255, 255))
        painter.drawRect(20, 20, 24, 24)
        painter.end()
        
        self.dynamic_icons["stop"] = QIcon(stop_icon)
        self.stop_icon = self.dynamic_icons["stop"]
        
        # 코일 아이콘 생성
        coil_icon = QPixmap(64, 64)
        coil_icon.fill(Qt.transparent)
        painter = QPainter(coil_icon)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 파란색 배경
        painter.setBrush(QColor(47, 108, 146))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(4, 4, 56, 56, 10, 10)
        
        # 코일 심볼 (원형으로 감긴 선)
        painter.setPen(QPen(QColor(255, 255, 255), 3))
        painter.drawEllipse(16, 16, 32, 32)
        painter.drawEllipse(22, 22, 20, 20)
        painter.end()
        
        self.dynamic_icons["coil"] = QIcon(coil_icon)
        
        # Discrete Input 아이콘 생성
        discrete_icon = QPixmap(64, 64)
        discrete_icon.fill(Qt.transparent)
        painter = QPainter(discrete_icon)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 초록색 배경
        painter.setBrush(QColor(40, 140, 40))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(4, 4, 56, 56, 10, 10)
        
        # 디지털 입력 심볼 (스위치 모양)
        painter.setPen(QPen(QColor(255, 255, 255), 3))
        painter.drawLine(20, 32, 32, 32)
        painter.drawLine(32, 32, 44, 20)
        painter.drawEllipse(16, 30, 4, 4)
        painter.drawEllipse(44, 18, 4, 4)
        painter.end()
        
        self.dynamic_icons["discrete"] = QIcon(discrete_icon)
        
        # Holding Register 아이콘 생성
        holding_icon = QPixmap(64, 64)
        holding_icon.fill(Qt.transparent)
        painter = QPainter(holding_icon)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 주황색 배경
        painter.setBrush(QColor(210, 140, 20))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(4, 4, 56, 56, 10, 10)
        
        # 'H' 문자 그리기
        font = QFont("Arial", 28, QFont.Bold)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(QRect(0, 0, 64, 64), Qt.AlignCenter, "H")
        painter.end()
        
        self.dynamic_icons["holding"] = QIcon(holding_icon)
        
        # Input Register 아이콘 생성
        input_icon = QPixmap(64, 64)
        input_icon.fill(Qt.transparent)
        painter = QPainter(input_icon)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 보라색 배경
        painter.setBrush(QColor(140, 40, 140))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(4, 4, 56, 56, 10, 10)
        
        # 'I' 문자 그리기
        font = QFont("Arial", 28, QFont.Bold)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(QRect(0, 0, 64, 64), Qt.AlignCenter, "I")
        painter.end()
        
        self.dynamic_icons["input"] = QIcon(input_icon)
        
        logger.debug("버튼 아이콘 생성 완료")
            
    def load_stylesheet(self):
        """서버 애플리케이션의 스타일시트 로드
        
        내장된 스타일시트를 직접 적용하여 외부 파일 의존성을 제거합니다.
        이는 원래 resources/style.qss 파일에 있던 내용이 직접 코드에 포함된 것입니다.
        """
        try:
            # 내장된 QSS 스타일 정의 (원래 style.qss 파일에서 가져온 내용)
            self.setStyleSheet(EMBEDDED_QSS_STYLE)
            logger.info("내장된 스타일시트 적용 성공")
                
        except Exception as e:
            logger.error(f"스타일시트 로드 중 오류 발생: {e}")
            self._apply_default_style()
    
    def _apply_default_style(self):
        """기본 스타일시트 적용
        
        QSS 파일 로드에 실패한 경우 하드코딩된 기본 스타일을 적용합니다.
        """
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e3a5f;
                color: #ffffff;
            }
            QPushButton {
                background-color: #2c4c7c;
                border: none;
                border-radius: 10px;
                padding: 10px;
                color: #ffffff;
            }
            QPushButton:hover {
                background-color: #3a5e8c;
            }
            QPushButton:pressed {
                background-color: #4a6e9c;
            }
            QLineEdit, QSpinBox {
                background-color: #2c4c7c;
                border: none;
                border-radius: 10px;
                padding: 5px;
                color: #ffffff;
            }
            QCheckBox {
                color: #ffffff;
            }
            QGroupBox {
                border: 1px solid #3a5e8c;
                border-radius: 10px;
                margin-top: 10px;
                padding-top: 10px;
                color: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 5px;
                color: #ffffff;
            }
            QLabel {
                color: #ffffff;
            }
            QComboBox {
                background-color: #2c4c7c;
                border-radius: 10px;
                padding: 5px;
                color: #ffffff;
            }
            QComboBox QAbstractItemView {
                background-color: #2c4c7c;
                color: #ffffff;
            }
        """)
        
    def init_modbus_store(self, hr_size=200):
        """Modbus 데이터 저장소를 초기화한다.

        홀딩 레지스터는 컬럼별 절대 주소 방식을 사용하므로 전역 오프셋(hr_offset)은
        항상 0이며, 데이터 블록은 0번지부터 hr_size개를 연속 할당한다. 따라서 클라이언트가
        요청하는 주소가 곧 데이터 블록의 인덱스가 된다.

        Args:
            hr_size (int): 홀딩 레지스터 데이터 블록 크기(= 사용하는 최대 주소 + 1).
        """
        # 컬럼별 절대 주소 방식이므로 전역 오프셋은 사용하지 않는다.
        self.hr_offset = 0

        # Create signals object first if not already created
        if not hasattr(self, 'signals'):
            self.signals = ModbusSignals()

        # 컬럼 범위를 모두 포함할 수 있도록 최소 1개 이상으로 보정
        hr_size = max(1, int(hr_size))

        self.store = CustomModbusSlaveContext(
            signals=self.signals,
            di=ModbusSequentialDataBlock(0, [0] * 100),   # Discrete Inputs
            co=ModbusSequentialDataBlock(0, [0] * 100),   # Coils
            hr=ModbusSequentialDataBlock(0, [0] * hr_size),   # Holding Registers (0 ~ hr_size-1)
            ir=ModbusSequentialDataBlock(0, [0] * 100),   # Input Registers
            hr_offset=0                                   # 전역 오프셋 미사용
        )
        self.context = ModbusServerContext(slaves=self.store, single=True)

        logger.info(f"Modbus store initialized. Holding register size: {hr_size}")
    
    def load_stylesheet(self):
        """스타일시트 로드 및 적용"""
        try:
            style_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources/style.qss")
            if os.path.exists(style_path):
                with open(style_path, "r") as f:
                    self.setStyleSheet(f.read())
                    logger.info("스타일시트가 성공적으로 적용되었습니다.")
            else:
                logger.warning(f"스타일시트 파일을 찾을 수 없습니다: {style_path}")
        except Exception as e:
            logger.error(f"스타일시트 적용 중 오류 발생: {e}")
            
    def apply_neumorphism_effect(self, widget):
        """뉴모피즘 효과를 적용하는 헬퍼 함수"""
        # 뉴모피즘 효과를 위한 그림자 효과 생성
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 30))
        shadow.setOffset(5, 5)
        widget.setGraphicsEffect(shadow)
        
    def toggle_register_group(self, register_type, state):
        """레지스터 그룹 표시/숨김 토글 함수"""
        if register_type in self.register_groups:
            # Qt.CheckState.Checked는 값이 2
            is_checked = (state == Qt.CheckState.Checked.value)  # 2
            self.register_groups[register_type].setVisible(is_checked)
            logger.debug(f"Toggle {register_type}: {'visible' if is_checked else 'hidden'}")

    @Slot()
    def toggle_register_panel(self):
        """레지스터 표시 설정(체크박스) 패널을 펼치거나 접는다."""
        self._register_panel_open = not self._register_panel_open
        self.register_control_panel.setVisible(self._register_panel_open)
        # 버튼 화살표 표시 갱신 (▸ 접힘 / ▾ 펼침)
        self.register_panel_button.setText(
            "▾ 레지스터 표시 설정" if self._register_panel_open else "▸ 레지스터 표시 설정"
        )

    def init_ui(self):
        """Initialize the user interface"""
        # 전체 애플리케이션 창의 최소 크기 설정
        # 세로 최소값을 낮춰, 레지스터가 적을 때 창을 내용 높이에 맞게 줄일 수 있도록 한다.
        self.setMinimumSize(800, 600)  # 가로, 세로 최소 크기
        
        central_widget = QWidget()
        main_layout = QVBoxLayout(central_widget)
        # 섹션 간 간격을 적절히 좁혀 답답하지 않으면서 빈 공간을 줄인다.
        main_layout.setContentsMargins(14, 12, 14, 10)
        main_layout.setSpacing(8)

        # 타이틀 레이블
        title_label = QLabel(f"Modbus TCP Server Simulator  v{APP_VERSION}")
        title_label.setObjectName("title_label")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title_label)
        self.apply_neumorphism_effect(title_label)

        # 제목 하단 헤더 바: STATUS 영역(좌) + 액션 버튼 묶음(우)
        header_bar = QHBoxLayout()
        header_bar.setContentsMargins(10, 0, 10, 4)
        header_bar.setSpacing(8)

        # STATUS 영역 — 캡션 배지 + 상태 메시지를 하나의 박스로 명확히 구분
        status_box = QWidget()
        status_box.setObjectName("status_box")
        status_box_layout = QHBoxLayout(status_box)
        status_box_layout.setContentsMargins(12, 6, 12, 6)
        status_box_layout.setSpacing(10)

        status_caption = QLabel("STATUS")
        status_caption.setObjectName("status_caption")
        status_box_layout.addWidget(status_caption)

        self.status_label = QLabel("Server not running")
        self.status_label.setObjectName("status_label")
        self.status_label.setStyleSheet("color: #94a3b8; font-weight: bold;")
        status_box_layout.addWidget(self.status_label)
        status_box_layout.addStretch(1)

        self.apply_neumorphism_effect(status_box)
        header_bar.addWidget(status_box, 1)

        # 레지스터 표시 설정 패널을 펼치고 접는 토글 버튼 (기본은 접힘)
        self.register_panel_button = QPushButton("▸ 레지스터 표시 설정")
        self.register_panel_button.setObjectName("register_panel_button")
        self.register_panel_button.setToolTip("표시할 레지스터 종류를 선택하는 패널을 열고 닫습니다")
        self.register_panel_button.setMinimumWidth(150)
        self.register_panel_button.clicked.connect(self.toggle_register_panel)
        header_bar.addWidget(self.register_panel_button)

        # 시퀀스 시뮬레이션 창 열기 버튼
        self.sequence_button = QPushButton("시퀀스 시뮬레이션")
        self.sequence_button.setObjectName("sequence_button")
        self.sequence_button.setToolTip("노드 그래프로 신호 전송/대기/분기 시퀀스를 편집·실행합니다")
        self.sequence_button.setMinimumWidth(150)
        self.sequence_button.clicked.connect(self.open_sequence_window)
        header_bar.addWidget(self.sequence_button)

        main_layout.addLayout(header_bar)

        # Connection settings
        conn_group = QGroupBox("")
        conn_group.setObjectName("connection_group")
        conn_layout = QHBoxLayout()
        
        # 그룹박스에 뉴모피즘 효과 추가
        self.apply_neumorphism_effect(conn_group)
        
        # Connection type
        conn_layout.addWidget(QLabel("Type:"))
        self.conn_type_combo = QComboBox()
        self.conn_type_combo.addItem("TCP")
        conn_layout.addWidget(self.conn_type_combo)
        
        # Port
        conn_layout.addWidget(QLabel("Port:"))
        self.port_edit = QLineEdit("502")
        self.port_edit.setValidator(QIntValidator(1, 65535))  # 포트 번호 유효성 검사 추가
        self.port_edit.setStyleSheet("border: 1px solid #334155; border-radius: 10px;")
        conn_layout.addWidget(self.port_edit)
        
        # Server address
        conn_layout.addWidget(QLabel("Server Address:"))
        self.address_edit = QLineEdit("127.0.0.1")
        self.address_edit.setStyleSheet("border: 1px solid #334155; border-radius: 10px;")
        conn_layout.addWidget(self.address_edit)
        
        # (HR Offset 전역 입력 필드는 제거됨 — 홀딩 레지스터 그룹의 컬럼별 시작 주소로 대체)

        # Connect/Disconnect button
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("connect_button")
        self.connect_button.clicked.connect(self.toggle_server)
        self.connect_button.setMinimumWidth(130)
        # 시작 아이콘 추가
        self.connect_button.setIcon(self.dynamic_icons["start"])
        self.connect_button.setIconSize(QSize(16, 16))
        conn_layout.addWidget(self.connect_button)
        
        conn_group.setLayout(conn_layout)
        main_layout.addWidget(conn_group)

        # 레지스터 그룹 표시/숨김 체크박스 (접을 수 있는 패널 안에 배치)
        self.register_control_panel = QWidget()
        control_layout = QHBoxLayout(self.register_control_panel)
        control_layout.setContentsMargins(10, 5, 10, 5)

        self.register_checkboxes = {}
        # Initialize register_groups dictionary before creating checkboxes
        self.register_groups = {}
        for reg_type, reg_name in [
            ("coils", "Coils"),
            ("discrete_inputs", "Discrete Inputs"),
            ("holding_registers", "Holding Registers"),
            ("input_registers", "Input Registers")
        ]:
            checkbox = QCheckBox(reg_name)
            checkbox.setObjectName(f"{reg_type}_checkbox")

            # 체크박스와 토글 함수 연결 - 클로저 문제 해결
            # 정적 메소드를 생성하여 연결
            def create_toggle_handler(r_type):
                def toggle_handler(state):
                    self.toggle_register_group(r_type, state)
                return toggle_handler

            checkbox.stateChanged.connect(create_toggle_handler(reg_type))
            control_layout.addWidget(checkbox)
            self.register_checkboxes[reg_type] = checkbox

            # 초기 상태 설정 - holding register만 visible
            # holding_registers만 체크하고 나머지는 체크 해제
            if reg_type in ["holding_registers"]:
                checkbox.setChecked(True)  # visible 상태
            else:
                checkbox.setChecked(False)  # hidden 상태

        # 체크박스 패널 추가 (기본적으로 숨김 상태)
        self._register_panel_open = False
        self.register_control_panel.setVisible(False)
        main_layout.addWidget(self.register_control_panel)
        
        # Register tabs
        self.registers_layout = QHBoxLayout()
        
        # Create register widgets - these will set both _group and _widget attributes
        coils_group = self.create_register_group("Coils", "coils")
        discrete_inputs_group = self.create_register_group("Discrete Inputs", "discrete_inputs")
        holding_registers_group = self.create_register_group("Holding Registers", "holding_registers")
        input_registers_group = self.create_register_group("Input Registers", "input_registers")
        
        # 그룹 저장
        self.register_groups["coils"] = coils_group
        self.register_groups["discrete_inputs"] = discrete_inputs_group
        self.register_groups["holding_registers"] = holding_registers_group
        self.register_groups["input_registers"] = input_registers_group
        
        # 레이아웃에 추가
        self.registers_layout.addWidget(coils_group)
        self.registers_layout.addWidget(discrete_inputs_group)
        self.registers_layout.addWidget(holding_registers_group)
        self.registers_layout.addWidget(input_registers_group)
        
        # 초기 표시 상태 설정 - holding register만 visible
        coils_group.setVisible(False)  # 코일 레지스터 숨김 처리
        discrete_inputs_group.setVisible(False)
        input_registers_group.setVisible(False)
        holding_registers_group.setVisible(True)  # 홀딩 레지스터 명시적으로 표시
        
        # 레지스터 영역이 창 크기에 맞춰 남는 세로 공간을 모두 차지하도록 신축 계수 부여.
        # (표는 QScrollArea(widgetResizable) 안에서 영역에 맞게 함께 리사이징된다.)
        main_layout.addLayout(self.registers_layout, 1)

        # 상태바 추가
        self.statusBar().setObjectName("status_bar")
        self.statusBar().showMessage("Ready")
        
        # 상태바에 뉴모피즘 효과 적용
        self.apply_neumorphism_effect(self.statusBar())
        
        self.setCentralWidget(central_widget)

    def create_register_group(self, title, register_type):
        """Create a group box with scrollable register widget"""
        # 레지스터 타입에 따른 아이콘 설정
        icon_path = {
            "coils": "resources/coil_icon.svg",
            "discrete_inputs": "resources/discrete_icon.svg",
            "holding_registers": "resources/holding_icon.svg",
            "input_registers": "resources/input_icon.svg"
        }.get(register_type, "")
        
        # icon_type 정의 - register_type과 동일하게 사용
        icon_type = register_type
        
        # 아이콘이 있는 제목 생성
        title_layout = QHBoxLayout()
        if icon_type and icon_type in self.dynamic_icons:
            icon_label = QLabel()
            icon_label.setPixmap(self.dynamic_icons[icon_type].pixmap(16, 16))
            title_layout.addWidget(icon_label)
        
        title_label = QLabel(title)
        title_label.setObjectName("group_title_label")
        title_layout.addWidget(title_label)
        title_layout.addStretch(1)
        
        # 그룹박스 생성
        group = QGroupBox()
        # 가로 스크롤이 생기지 않도록 최소 너비 증가
        if register_type in ["coils", "discrete_inputs"]:
            group.setMinimumWidth(150)  # 비트 타입 레지스터는 너비가 작음
        elif register_type == "holding_registers":
            group.setMinimumWidth(350)  # 메모 필드가 있어서 더 넓게 설정
        else:
            group.setMinimumWidth(250)  # 기본 레지스터 너비
        group.setObjectName(f"{register_type}_group")
        layout = QVBoxLayout()
        
        # 제목 레이아웃 추가
        layout.addLayout(title_layout)
        
        # 그룹박스에 뉴모피즘 효과 추가
        self.apply_neumorphism_effect(group)
        
        # 홀딩 레지스터: 컬럼별 시작 주소/개수 설정 바 추가
        if register_type == "holding_registers":
            config_layout = QHBoxLayout()

            config_layout.addWidget(QLabel("컬럼1 시작:"))
            self.c1_start_edit = QLineEdit(str(self.holding_columns[0][0]))
            self.c1_start_edit.setValidator(QIntValidator(0, 65535))
            self.c1_start_edit.setFixedWidth(60)
            self.c1_start_edit.setStyleSheet("border: 1px solid #334155; border-radius: 10px;")
            config_layout.addWidget(self.c1_start_edit)

            config_layout.addWidget(QLabel("개수:"))
            self.c1_count_edit = QLineEdit(str(self.holding_columns[0][1]))
            self.c1_count_edit.setValidator(QIntValidator(1, 2000))
            self.c1_count_edit.setFixedWidth(55)
            self.c1_count_edit.setStyleSheet("border: 1px solid #334155; border-radius: 10px;")
            config_layout.addWidget(self.c1_count_edit)

            config_layout.addWidget(QLabel("컬럼2 시작:"))
            self.c2_start_edit = QLineEdit(str(self.holding_columns[1][0]))
            self.c2_start_edit.setValidator(QIntValidator(0, 65535))
            self.c2_start_edit.setFixedWidth(60)
            self.c2_start_edit.setStyleSheet("border: 1px solid #334155; border-radius: 10px;")
            config_layout.addWidget(self.c2_start_edit)

            config_layout.addWidget(QLabel("개수:"))
            self.c2_count_edit = QLineEdit(str(self.holding_columns[1][1]))
            self.c2_count_edit.setValidator(QIntValidator(1, 2000))
            self.c2_count_edit.setFixedWidth(55)
            self.c2_count_edit.setStyleSheet("border: 1px solid #334155; border-radius: 10px;")
            config_layout.addWidget(self.c2_count_edit)

            apply_columns_button = QPushButton("적용")
            apply_columns_button.setMinimumWidth(50)
            apply_columns_button.setToolTip("컬럼 시작 주소/개수를 적용합니다 (서버 실행 중에는 변경 불가)")
            apply_columns_button.clicked.connect(self.on_apply_columns_clicked)
            config_layout.addWidget(apply_columns_button)
            config_layout.addStretch(1)
            layout.addLayout(config_layout)

            # 서버 실행 중 비활성화할 컬럼 설정 위젯 모음
            self.column_config_edits = [
                self.c1_start_edit, self.c1_count_edit,
                self.c2_start_edit, self.c2_count_edit,
                apply_columns_button,
            ]

        # 홀딩 레지스터에 테스트 입력 필드 추가
        if register_type == "holding_registers":
            test_layout = QHBoxLayout()
            test_layout.addWidget(QLabel("테스트 입력:"))
            
            # 테스트 입력 필드 생성
            self.test_input = QLineEdit()
            self.test_input.setPlaceholderText("테스트 값 입력 (16진수)")
            self.test_input.setMinimumWidth(80)  # 입력 필드 최소 너비 설정
            self.test_input.setStyleSheet("border: 1px solid #334155; border-radius: 10px;")
            
            # 16진수 입력 검증기 설정
            hex_validator = QRegularExpressionValidator(QRegularExpression("[0-9A-Fa-f]{0,4}"))
            self.test_input.setValidator(hex_validator)
            
            # 테스트 버튼 생성
            test_button = QPushButton("테스트")
            test_button.setObjectName("test_button")
            test_button.setMinimumWidth(60)  # 버튼 최소 너비 설정
            test_button.clicked.connect(self.on_test_button_clicked)
            # 테스트 버튼에 아이콘 추가
            test_button.setIcon(self.dynamic_icons["holding"])
            test_button.setIconSize(QSize(16, 16))
            
            test_layout.addWidget(self.test_input)
            test_layout.addWidget(test_button)

            # 메모 전체 삭제 버튼
            clear_memo_button = QPushButton("메모 전체 삭제")
            clear_memo_button.setObjectName("clear_memo_button")
            clear_memo_button.setMinimumWidth(110)
            clear_memo_button.setToolTip("모든 홀딩 레지스터의 메모를 한 번에 삭제합니다")
            clear_memo_button.clicked.connect(self.clear_all_memos)
            test_layout.addWidget(clear_memo_button)

            layout.addLayout(test_layout)

        # Create register widget
        if register_type == "holding_registers":
            register_widget = HoldingRegisterTable(columns=self.holding_columns)
        else:
            register_widget = RegisterWidget(register_type)
        register_widget.value_changed.connect(self.on_register_value_changed)
        # Connect memo_changed signal to handle memo auto-save
        register_widget.memo_changed.connect(self.on_memo_changed)

        # Create scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(register_widget)
        scroll.setObjectName(f"{register_type}_scroll")
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)  # 테두리 제거
        # 스크롤 영역이 창 크기에 맞춰 세로로 늘어나도록 한다.
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # 홀딩 레지스터는 컬럼 재구성을 위해 스크롤 영역 참조를 보관
        if register_type == "holding_registers":
            self.holding_scroll = scroll

        # Add to layout
        layout.addWidget(scroll)
        group.setLayout(layout)
        # 그룹도 창 크기에 맞춰 세로로 늘어나도록 한다.
        group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        # Store reference to widget
        setattr(self, f"{register_type}_widget", register_widget)

        return group

    def get_columns_from_ui(self):
        """컬럼 설정 입력값을 읽어 검증한다.

        Returns:
            tuple: (columns, error_message). 검증 실패 시 columns는 None이고
                error_message에 사유 문자열이 담긴다. 성공 시 error_message는 None.
                columns는 [(start, count), (start, count)] 형식.
        """
        def _parse(edit, default):
            try:
                return int(edit.text())
            except (ValueError, AttributeError):
                return default

        c1_start = _parse(self.c1_start_edit, 0)
        c1_count = _parse(self.c1_count_edit, 100)
        c2_start = _parse(self.c2_start_edit, 100)
        c2_count = _parse(self.c2_count_edit, 100)

        columns = [(c1_start, c1_count), (c2_start, c2_count)]

        for start, count in columns:
            if count < 1:
                return None, "각 컬럼의 레지스터 개수는 1 이상이어야 합니다."
            if start < 0 or start + count > 65536:
                return None, "주소 범위는 0 ~ 65535 이내여야 합니다."

        # 두 컬럼의 주소 범위가 겹치는지 검사
        s1, c1 = columns[0]
        s2, c2 = columns[1]
        if max(s1, s2) < min(s1 + c1, s2 + c2):
            return None, "두 컬럼의 주소 범위가 겹칩니다. 시작 주소 또는 개수를 조정하세요."

        return columns, None

    def rebuild_holding_widget(self, columns):
        """주어진 컬럼 설정으로 홀딩 레지스터 위젯을 재생성한다.

        Args:
            columns (list): [(start, count), ...] 형식의 컬럼 설정.
        """
        self.holding_columns = columns
        new_widget = HoldingRegisterTable(columns=columns)
        new_widget.value_changed.connect(self.on_register_value_changed)
        new_widget.memo_changed.connect(self.on_memo_changed)

        # QScrollArea.setWidget()은 기존 위젯을 자동으로 삭제한다.
        self.holding_scroll.setWidget(new_widget)
        self.holding_registers_widget = new_widget
        logger.info(f"홀딩 레지스터 위젯 재구성 완료: {columns}")

    @Slot()
    def on_apply_columns_clicked(self):
        """'적용' 버튼: 입력한 컬럼 설정을 검증·적용하고 저장값을 다시 로드한다."""
        if self.server_running:
            QMessageBox.warning(self, "변경 불가", "서버 실행 중에는 컬럼 설정을 변경할 수 없습니다.")
            return

        columns, error = self.get_columns_from_ui()
        if error:
            QMessageBox.warning(self, "설정 오류", error)
            return

        self.rebuild_holding_widget(columns)
        # 변경된 주소 범위에 맞춰 저장소 크기를 재설정한 뒤 저장값/메모 로드
        hr_size = max(start + count for start, count in columns)
        self.init_modbus_store(hr_size)
        self.load_registers_from_file()
        # 변경된 컬럼 설정을 즉시 저장
        self.save_registers_to_file()
        QMessageBox.information(self, "적용 완료", "컬럼 설정이 적용되었습니다.")

    @Slot()
    def clear_all_memos(self):
        """모든 홀딩 레지스터 메모를 삭제한다."""
        if not hasattr(self, 'holding_registers_widget'):
            return

        reply = QMessageBox.question(
            self,
            "메모 전체 삭제",
            "모든 홀딩 레지스터 메모를 삭제하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 200여 회의 저장 예약이 발생하지 않도록 시그널을 차단하고 한 번만 저장
        for memo_edit in self.holding_registers_widget.memo_edits.values():
            old_state = memo_edit.blockSignals(True)
            memo_edit.clear()
            memo_edit.blockSignals(old_state)

        self.save_registers_to_file()
        logger.info("모든 홀딩 레지스터 메모 삭제 완료")

    def set_column_config_enabled(self, enabled):
        """컬럼 설정 입력 위젯들의 활성화 상태를 변경한다.

        Args:
            enabled (bool): True면 활성화, False면 비활성화.
        """
        for widget in getattr(self, "column_config_edits", []):
            widget.setEnabled(enabled)

    def restore_saved_state(self):
        """앱 시작 시 저장된 컬럼 설정·레지스터 값·메모를 복원한다."""
        config = None
        try:
            if os.path.exists(self.register_file):
                with open(self.register_file, 'r') as f:
                    data = json.load(f)
                saved = data.get("holding_columns")
                if saved and len(saved) >= 2:
                    config = [
                        (int(saved[0][0]), int(saved[0][1])),
                        (int(saved[1][0]), int(saved[1][1])),
                    ]
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            logger.error(f"저장된 컬럼 설정 로드 실패, 기본값 사용: {e}")
            config = None

        # 저장된 컬럼 설정이 현재 설정과 다르면 입력창과 위젯을 갱신
        if config and config != self.holding_columns:
            self.c1_start_edit.setText(str(config[0][0]))
            self.c1_count_edit.setText(str(config[0][1]))
            self.c2_start_edit.setText(str(config[1][0]))
            self.c2_count_edit.setText(str(config[1][1]))
            self.rebuild_holding_widget(config)

        # 저장소 크기를 컬럼 범위에 맞춘 뒤 값·메모 로드
        hr_size = max((start + count for start, count in self.holding_columns), default=200)
        self.init_modbus_store(hr_size)
        self.load_registers_from_file()

    def update_from_context(self):
        """Update UI from Modbus context"""
        if not self.server_running:
            return
        
        # Update from UI to context
        self.update_context_from_ui()
        
        # Update from context to UI (e.g., if another client modified values)
        self.update_ui_from_context()
    
    def update_ui_from_context(self):
        """Update UI with values from Modbus context"""
        try:
            register_count = 100
            
            # Update coils
            if hasattr(self, 'coils_widget'):
                coils_widget = self.coils_widget
                try:
                    coil_values = self.store.getValues(1, 0, register_count)
                    for addr in range(min(register_count, len(coil_values))):
                        if addr in coils_widget.checkboxes:
                            value = coil_values[addr]
                            coils_widget.checkboxes[addr].setChecked(value == 1)
                            coils_widget.values[addr] = value
                except Exception as e:
                    logger.warning(f"Error getting coil values: {e}")
            
            # Update discrete inputs
            if hasattr(self, 'discrete_inputs_widget'):
                discrete_widget = self.discrete_inputs_widget
                try:
                    di_values = self.store.getValues(2, 0, register_count)
                    for addr in range(min(register_count, len(di_values))):
                        if addr in discrete_widget.checkboxes:
                            value = di_values[addr]
                            discrete_widget.checkboxes[addr].setChecked(value == 1)
                            discrete_widget.values[addr] = value
                except Exception as e:
                    logger.warning(f"Error getting discrete input values: {e}")
            
            # Update holding registers - 컬럼별 절대 주소 범위를 순회
            if hasattr(self, 'holding_registers_widget'):
                holding_widget = self.holding_registers_widget
                try:
                    for start, count in self.holding_columns:
                        hr_values = self.store.getValues(3, start, count)
                        for i in range(min(count, len(hr_values))):
                            addr = start + i
                            if addr not in holding_widget.line_edits:
                                continue
                            # Skip update if this widget has focus (user is editing)
                            if holding_widget.line_edits[addr].hasFocus():
                                continue

                            value = hr_values[i]
                            # Format value as 4-digit hex string
                            hex_value = f"{value:04X}"

                            # Only update if value has actually changed
                            current_text = holding_widget.line_edits[addr].text()
                            if current_text != hex_value:
                                # Block signals to prevent feedback loop
                                old_state = holding_widget.line_edits[addr].blockSignals(True)
                                holding_widget.line_edits[addr].setText(hex_value)
                                holding_widget.line_edits[addr].blockSignals(old_state)
                                holding_widget.values[addr] = hex_value
                except Exception as e:
                    logger.warning(f"Error getting holding register values: {e}")
                
            # Update input registers
            if hasattr(self, 'input_registers_widget'):
                input_widget = self.input_registers_widget
                try:
                    ir_values = self.store.getValues(4, 0, register_count)
                    for addr in range(min(register_count, len(ir_values))):
                        if addr in input_widget.line_edits:
                            # Skip update if this widget has focus (user is editing)
                            if input_widget.line_edits[addr].hasFocus():
                                logger.debug(f"Skipping update for input register {addr} - user is editing")
                                continue
                                
                            value = ir_values[addr]
                            # Format value as 4-digit hex string
                            hex_value = f"{value:04X}"
                            
                            # Only update if value has actually changed
                            current_text = input_widget.line_edits[addr].text()
                            if current_text != hex_value:
                                # Block signals to prevent feedback loop
                                old_state = input_widget.line_edits[addr].blockSignals(True)
                                input_widget.line_edits[addr].setText(hex_value)
                                input_widget.line_edits[addr].blockSignals(old_state)
                                input_widget.values[addr] = hex_value
                except Exception as e:
                    logger.warning(f"Error getting input register values: {e}")
        except Exception as e:
            logger.error(f"Error updating UI from context: {e}")
    
    def update_context_from_ui(self):
        """Update Modbus context from UI values"""
        try:
            # Update coils - use the widget reference, not the group
            if hasattr(self, 'coils_widget'):
                coils_widget = self.coils_widget
                for addr, value in coils_widget.values.items():
                    # 체크박스 상태 확인 - 실제 UI에서 값 가져오기
                    if addr in coils_widget.checkboxes:
                        is_checked = coils_widget.checkboxes[addr].isChecked()
                        value = 1 if is_checked else 0
                        coils_widget.values[addr] = value  # 값 업데이트
                    
                    self.store.setValues(1, addr, [value])  # fc=1 for coils
                    # logger.info(f"UI sync - Coil {addr} set to {value}")
                    
                    # 디버깅: 스토어에서 값을 다시 읽어 확인
                    # read_value = self.store.getValues(1, addr, 1)[0]
                    # logger.info(f"Verification - Read coil {addr} from store: {read_value}")
            
            # Update holding registers - use the widget reference, not the group
            if hasattr(self, 'holding_registers_widget'):
                holding_widget = self.holding_registers_widget
                for addr, value in holding_widget.values.items():
                    try:
                        # Convert hex string to integer
                        if isinstance(value, str):
                            int_value = int(value, 16)
                        else:
                            int_value = value
                        self.store.setValues(3, addr, [int_value])  # fc=3 for holding registers
                        # logger.info(f"Updated holding register {addr} to {int_value} (hex: {value})")
                    except ValueError as e:
                        logger.error(f"Invalid hex value for register {addr}: {value}, error: {e}")
                        # Set to 0 if invalid
                        self.store.setValues(3, addr, [0])
        except Exception as e:
            logger.error(f"Error updating context from UI: {e}")
    @Slot(str, int, int)
    def on_register_value_changed(self, register_type, addr, value):
        """Handle register value changed signal"""
        try:
            # Map register type to function code
            if register_type == "coils":
                function_code = 1  # Read Coils
                self.store.setValues(function_code, addr, [value])
            elif register_type == "discrete_inputs":
                function_code = 2  # Read Discrete Inputs
                self.store.setValues(function_code, addr, [value])
            elif register_type == "holding_registers":
                function_code = 3  # Read Holding Registers
                self.store.setValues(function_code, addr, [value])
            elif register_type == "input_registers":
                function_code = 4  # Read Input Registers
                self.store.setValues(function_code, addr, [value])
            
            logger.info(f"Register value changed: {register_type}[{addr}] = {value}")
            # 레지스터 값이 변경될 때마다 파일에 저장
            self.save_registers_to_file()
        except Exception as e:
            logger.error(f"Error in on_register_value_changed: {e}")

    # --- 시퀀스 엔진 연동 어댑터 ---
    def read_register(self, reg_type: str, addr: int) -> int:
        """시퀀스 엔진용: 레지스터 타입/주소의 현재 값을 store 에서 읽는다.

        Args:
            reg_type: "coils"|"discrete_inputs"|"holding_registers"|"input_registers".
            addr: 레지스터 주소.

        Returns:
            정수 값(읽기 실패 시 0).
        """
        fc = {"coils": 1, "discrete_inputs": 2, "holding_registers": 3, "input_registers": 4}.get(reg_type)
        if fc is None:
            logger.error(f"read_register: 알 수 없는 레지스터 타입 {reg_type}")
            return 0
        try:
            return int(self.store.getValues(fc, addr, 1)[0])
        except (IndexError, ValueError, TypeError) as exc:
            logger.error(f"read_register 실패 ({reg_type}[{addr}]): {exc}")
            return 0

    def engine_write(self, reg_type: str, addr: int, value: int) -> None:
        """시퀀스 엔진용: 레지스터에 값을 쓴다(UI/저장과 동기화).

        on_register_value_changed 로 위임하여 외부 클라이언트 쓰기로 오탐되지
        않게 하고, 해당 위젯 표시도 즉시 갱신한다.
        """
        self.on_register_value_changed(reg_type, addr, value)
        widget = getattr(self, f"{reg_type}_widget", None)
        if widget is not None:
            try:
                widget.update_value(addr, value)
            except (KeyError, AttributeError) as exc:
                logger.debug(f"engine_write 위젯 갱신 생략 ({reg_type}[{addr}]): {exc}")

    @Slot()
    def open_sequence_window(self):
        """시퀀스 시뮬레이션 창을 연다(단일 인스턴스 재사용)."""
        from sequence.sequence_window import SequenceWindow

        if getattr(self, "_sequence_window", None) is None:
            self._sequence_window = SequenceWindow(self.read_register, self.engine_write, parent=self)
        self._sequence_window.show()
        self._sequence_window.raise_()
        self._sequence_window.activateWindow()

    def on_memo_changed(self, register_type, addr, memo_text):
        """메모 입력 필드 변경 처리"""
        if register_type == "holding_registers":
            # Schedule save
            self.schedule_save()
    
    def schedule_save(self):
        """Schedule a save operation"""
        if not self.save_pending:
            self.save_pending = True
            self.save_timer.start(2000)  # Save after 2 seconds of inactivity
            
    def save_registers_to_file(self):
        """레지스터 값을 JSON 파일로 저장"""
        # 저장 플래그 초기화
        self.save_pending = False
        
        try:
            register_data = {
                "coils": {},
                "discrete_inputs": {},
                "holding_registers": {},
                "input_registers": {},
                "holding_registers_memos": {},  # 홀딩 레지스터 메모 추가
                # 홀딩 레지스터 컬럼 설정 [(시작주소, 개수), ...] 저장
                "holding_columns": [list(col) for col in self.holding_columns],
            }
            
            # 코일 값 저장
            if hasattr(self, 'coils_widget'):
                for addr, value in self.coils_widget.values.items():
                    register_data["coils"][str(addr)] = value
            
            # 디스크릿 입력 값 저장
            if hasattr(self, 'discrete_inputs_widget'):
                for addr, value in self.discrete_inputs_widget.values.items():
                    register_data["discrete_inputs"][str(addr)] = value
            
            # 홀딩 레지스터 값 저장
            if hasattr(self, 'holding_registers_widget'):
                for addr, value in self.holding_registers_widget.values.items():
                    register_data["holding_registers"][str(addr)] = value
                
                # 홀딩 레지스터 메모 저장
                for addr, memo_edit in self.holding_registers_widget.memo_edits.items():
                    memo_text = memo_edit.text()
                    if memo_text:  # 메모가 있는 경우만 저장
                        register_data["holding_registers_memos"][str(addr)] = memo_text
            
            # 입력 레지스터 값 저장
            if hasattr(self, 'input_registers_widget'):
                for addr, value in self.input_registers_widget.values.items():
                    register_data["input_registers"][str(addr)] = value
            
            # JSON 파일로 저장
            with open(self.register_file, 'w') as f:
                json.dump(register_data, f, indent=4)
                
            logger.info(f"Register values saved to {self.register_file}")
        except Exception as e:
            logger.error(f"Error saving registers to file: {e}")
            
    def load_registers_from_file(self):
        """파일에서 레지스터 값 로드"""
        try:
            if not os.path.exists(self.register_file):
                logger.info(f"Register file {self.register_file} does not exist. Using default values.")
                return
                
            with open(self.register_file, 'r') as f:
                register_data = json.load(f)
            
            # 코일 값 로드
            if "coils" in register_data and hasattr(self, 'coils_widget'):
                for addr_str, value in register_data["coils"].items():
                    addr = int(addr_str)
                    if addr in self.coils_widget.checkboxes:
                        old_state = self.coils_widget.checkboxes[addr].blockSignals(True)
                        self.coils_widget.checkboxes[addr].setChecked(value == 1)
                        self.coils_widget.checkboxes[addr].blockSignals(old_state)
                        self.coils_widget.values[addr] = value
                        # Modbus 저장소에도 값 업데이트
                        self.store.setValues(1, addr, [value])
            
            # 디스크릿 입력 값 로드
            if "discrete_inputs" in register_data and hasattr(self, 'discrete_inputs_widget'):
                for addr_str, value in register_data["discrete_inputs"].items():
                    addr = int(addr_str)
                    if addr in self.discrete_inputs_widget.checkboxes:
                        old_state = self.discrete_inputs_widget.checkboxes[addr].blockSignals(True)
                        self.discrete_inputs_widget.checkboxes[addr].setChecked(value == 1)
                        self.discrete_inputs_widget.checkboxes[addr].blockSignals(old_state)
                        self.discrete_inputs_widget.values[addr] = value
                        # Modbus 저장소에도 값 업데이트
                        self.store.setValues(2, addr, [value])
            
            # 홀딩 레지스터 값 로드
            if "holding_registers" in register_data and hasattr(self, 'holding_registers_widget'):
                for addr_str, hex_value in register_data["holding_registers"].items():
                    addr = int(addr_str)
                    if addr in self.holding_registers_widget.line_edits:
                        # 텍스트 설정 전에 시그널 차단
                        old_state = self.holding_registers_widget.line_edits[addr].blockSignals(True)
                        self.holding_registers_widget.line_edits[addr].setText(hex_value)
                        self.holding_registers_widget.line_edits[addr].blockSignals(old_state)
                        self.holding_registers_widget.values[addr] = hex_value
                        
                        # Modbus 저장소에도 값 업데이트
                        try:
                            int_value = int(hex_value, 16)
                            self.store.setValues(3, addr, [int_value])
                        except ValueError:
                            logger.error(f"Invalid hex value for register {addr}: {hex_value}")
                # 홀딩 레지스터 메모 로드
                if "holding_registers_memos" in register_data:
                    for addr_str, memo_text in register_data["holding_registers_memos"].items():
                        addr = int(addr_str)
                        if addr in self.holding_registers_widget.memo_edits:
                            # 메모 텍스트가 문자열인지 확인하고 변환
                            if not isinstance(memo_text, str):
                                memo_text = str(memo_text)
                            self.holding_registers_widget.memo_edits[addr].setText(memo_text)
            
            # 입력 레지스터 값 로드
            if "input_registers" in register_data and hasattr(self, 'input_registers_widget'):
                for addr_str, hex_value in register_data["input_registers"].items():
                    addr = int(addr_str)
                    if addr in self.input_registers_widget.line_edits:
                        # 텍스트 설정 전에 시그널 차단
                        old_state = self.input_registers_widget.line_edits[addr].blockSignals(True)
                        self.input_registers_widget.line_edits[addr].setText(hex_value)
                        self.input_registers_widget.line_edits[addr].blockSignals(old_state)
                        self.input_registers_widget.values[addr] = hex_value
                        # Modbus 저장소에도 값 업데이트 (정수로 변환)
                        try:
                            int_value = int(hex_value, 16)
                            self.store.setValues(4, addr, [int_value])
                        except ValueError:
                            # 잘못된 16진수 값이면 0으로 설정
                            self.store.setValues(4, addr, [0])
            
            logger.info(f"Register values loaded from {self.register_file}")
        except Exception as e:
            logger.error(f"Error loading registers from file: {e}")
    
    def toggle_server(self):
        """Start or stop the server"""
        if self.server_running:
            self.stop_server()
            self.connect_button.setText("Connect")
            self.connect_button.setObjectName("connect_button")
            self.connect_button.setIcon(self.dynamic_icons["start"])
        else:
            # 설정 검증 실패 등으로 서버 시작이 시작되지 않으면 버튼 상태를 바꾸지 않는다.
            if self.start_server():
                self.connect_button.setText("Disconnect")
                self.connect_button.setObjectName("disconnect_button")
                self.connect_button.setIcon(self.dynamic_icons["stop"])

    def start_server(self):
        """Modbus 서버를 시작한다.

        Returns:
            bool: 서버 시작 절차를 정상적으로 시작했으면 True, 설정 오류나 예외로
                시작하지 못했으면 False.
        """
        if self.server_thread and self.server_thread.running:
            return True
        
        try:
            import traceback
            address = self.address_edit.text()
            port = int(self.port_edit.text())
            
            # 포트 범위 유효성 검사 (1-65535)
            if port <= 0 or port > 65535:
                raise ValueError(f"Invalid port number: {port}. Port must be between 1 and 65535.")
                
            # 502번 포트는 관리자 권한 필요 알림
            if port == 502 and os.name == 'nt':  # Windows에서
                logger.warning("Port 502 typically requires administrator privileges on Windows")
                # 관리자 권한 검사 (Windows)
                try:
                    import ctypes
                    is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
                    if not is_admin:
                        logger.warning("Not running as administrator - port 502 may fail to bind")
                        # 관리자 권한 없이 502 포트 사용 시 경고 메시지 표시
                        self.status_label.setText("⚠️ Warning: Port 502 may require administrator privileges")
                        self.status_label.setStyleSheet("color: #FFC107; font-weight: bold;") # 노란색 경고색
                except Exception as e:
                    logger.warning(f"Could not check admin status: {e}")
            
            # 홀딩 레지스터 컬럼 설정 읽기 및 검증
            columns, config_error = self.get_columns_from_ui()
            if config_error:
                QMessageBox.warning(self, "설정 오류", config_error)
                self.status_label.setText(f"설정 오류: {config_error}")
                self.status_label.setStyleSheet("color: #F44336; font-weight: bold;")
                return False

            # 설정이 변경되었으면 위젯을 재구성
            if columns != self.holding_columns:
                self.rebuild_holding_widget(columns)

            # 컬럼 범위를 모두 포함하도록 저장소 크기 계산
            hr_size = max(start + count for start, count in columns)

            # Reset the store to ensure we're starting fresh
            # This helps prevent issues with stale data or callbacks
            self.init_modbus_store(hr_size)

            # 저장된 레지스터 값 로드
            self.load_registers_from_file()

            # 서버 시작 시도 중임을 표시
            self.status_label.setText(f"Starting server at {address}:{port}...")
            self.status_label.setStyleSheet("color: #2196F3; font-weight: bold;")  # 파란색
            self.statusBar().showMessage("Starting server...")
            
            # 애플리케이션 이벤트 처리 업데이트를 위해 잠시 지연
            QApplication.processEvents()
            
            # Create and start server thread
            self.server_thread = ModbusServerThread(address, port, self.context, self.signals)
            self.server_thread.start()

            # Log the attempt
            logger.info(f"Attempting to start server at {address}:{port} with holding columns {self.holding_columns}")
            return True
        except Exception as e:
            logger.error(f"Failed to start server: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            
            # 오류 메시지 생성
            error_msg = str(e)
            if "Permission denied" in error_msg:
                if os.name == 'nt' and int(self.port_edit.text()) < 1024:
                    error_msg = f"Permission denied for port {self.port_edit.text()}. Ports below 1024 often require administrator privileges."
            elif "already in use" in error_msg:
                error_msg = f"Port {self.port_edit.text()} is already in use by another application."
                
            # 상태 라벨에 오류 표시
            self.status_label.setText(f"Error: {error_msg}")
            self.status_label.setStyleSheet("color: #F44336; font-weight: bold;")  # 빨간색
            
            # 상태바에도 오류 메시지 표시
            self.statusBar().showMessage(f"Server start failed: {error_msg}")
            
            # Show error on button
            self.connect_button.setText("Failed")
            
            # 서버 실행 상태 초기화
            self.server_running = False
            
            # Reset button after 3 seconds
            QTimer.singleShot(3000, lambda: {
                self.connect_button.setText("Connect"),
                self.connect_button.setIcon(self.dynamic_icons["start"]),
                self.connect_button.setObjectName("connect_button")
            })
            return False

    def stop_server(self):
        """서버를 정상 종료한다. 종료 후에는 다시 연결을 시도할 수 있다."""
        if self.server_thread and self.server_thread.isRunning():
            # 서버 스레드에 정상 종료 요청 (asyncio 루프에서 ServerAsyncStop 실행)
            self.server_thread.stop()

            # 혹시 스레드가 아직 살아 있으면 한 번 더 대기
            if self.server_thread.isRunning():
                self.server_thread.wait(2000)
                if self.server_thread.isRunning():
                    logger.warning("서버 스레드가 종료되지 않았습니다.")

        # 스레드 참조 정리 (다음 연결을 위해 새 스레드를 생성한다)
        self.server_thread = None
        self.server_running = False
        self.connect_button.setText("Connect")
        logger.info("Server stopped from UI")
    
    @Slot()
    def on_server_started(self):
        """Handle server started signal"""
        self.server_running = True
        self.connect_button.setText("Disconnect")
        self.connect_button.setObjectName("disconnect_button")
        self.connect_button.setIcon(self.stop_icon)
        self.connect_button.setStyleSheet("")
        
        # 서버 시작 후 주소, 포트 입력 비활성화 및 컬럼 설정 잠금
        self.address_edit.setReadOnly(True)
        self.port_edit.setReadOnly(True)
        self.set_column_config_enabled(False)
        
        # 상태 메시지 업데이트
        self.status_label.setText(f"Server running at {self.address_edit.text()}:{self.port_edit.text()}")
        self.status_label.setStyleSheet("color: #4CAF50;")
        
        logger.info(f"Server started at {self.address_edit.text()}:{self.port_edit.text()}")
    
    @Slot()
    def on_server_stopped(self):
        """Handle server stopped signal"""
        self.server_running = False
        self.connect_button.setText("Connect")
        self.connect_button.setObjectName("connect_button")
        # 서버 중지 시 시작 아이콘으로 변경
        self.connect_button.setIcon(self.start_icon)
        
        # 서버 중지 후 주소, 포트 입력 다시 활성화 및 컬럼 설정 잠금 해제
        self.address_edit.setReadOnly(False)
        self.port_edit.setReadOnly(False)
        self.set_column_config_enabled(True)

        # 상태바 초기화
        self.statusBar().clearMessage()

        self.status_label.setText("Server stopped.")
        logger.info("Server stopped.")
        
        # QStatusBar에서 모든 QLabel 위젯 제거
        status_bar_widgets = []
        for child in self.statusBar().children():
            if isinstance(child, QLabel):
                status_bar_widgets.append(child)
        
        # 수집된 위젯들을 제거
        for widget in status_bar_widgets:
            self.statusBar().removeWidget(widget)
            widget.deleteLater()  # 위젯 메모리 해제
        # 오프라인 아이콘 직접 생성 (외부 파일 의존성 제거)
        status_msg = QLabel()
        pixmap = QPixmap(12, 12)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(160, 160, 160))  # 회색 배경
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(0, 0, 12, 12)  # 원형 아이콘
        painter.end()
        status_msg.setPixmap(pixmap)
        self.statusBar().addWidget(status_msg)
        self.statusBar().showMessage("Server stopped")
        logger.info("Server stopped successfully")
        
    @Slot()
    def on_test_button_clicked(self):
        """테스트 버튼 클릭 이벤트 핸들러"""
        try:
            # 테스트 입력 필드에서 값 가져오기
            test_value = self.test_input.text().strip()
            
            if not test_value:
                QMessageBox.warning(self, "입력 오류", "테스트 값을 입력해주세요.")
                return
                
            # 16진수 문자열을 정수로 변환
            try:
                # 대문자로 변환하고 앞의 0 제거
                test_value = test_value.upper()
                # 4자리로 맞추기
                test_value = test_value.zfill(4)
                int_value = int(test_value, 16)
                
                # 값 범위 확인 (16비트 레지스터)
                if int_value < 0 or int_value > 65535:
                    QMessageBox.warning(self, "입력 오류", "값은 0에서 65535(0x0000-0xFFFF) 사이여야 합니다.")
                    return
                    
                # 테스트 값을 입력 필드에 표시
                self.test_input.setText(test_value)
                
                # 테스트 결과 메시지 표시
                QMessageBox.information(
                    self, 
                    "테스트 결과", 
                    f"입력값: {test_value} (16진수)\n" 
                    f"정수값: {int_value}\n"
                    f"2진수: {bin(int_value)[2:].zfill(16)}"
                )
                
                logger.info(f"테스트 입력: {test_value} (16진수), 정수값: {int_value}")
                
            except ValueError:
                QMessageBox.warning(self, "입력 오류", "올바른 16진수 값을 입력해주세요.")
                
        except Exception as e:
            logger.error(f"테스트 버튼 처리 중 오류 발생: {e}")
            QMessageBox.critical(self, "오류", f"처리 중 오류가 발생했습니다: {e}")
            
    @Slot(int, int, int)
    def on_client_write_detected(self, function_code, address, value):
        """Handle client write detection signal"""
        try:
            # Map function code to register type
            # Function codes for coils: 
            # 1 = Read Coils, 5 = Write Single Coil, 15 = Write Multiple Coils
            # Function codes for holding registers:
            # 3 = Read Holding Registers, 6 = Write Single Register, 16 = Write Multiple Registers
            
            # For coil operations
            if function_code == 5 or function_code == 15:  # Write Coil(s)
                register_type = "coils"
                widget = self.coils_widget
                if address in widget.checkboxes:
                    logger.info(f"Client wrote to coil {address}: {value}")
                    # Block signals temporarily to prevent feedback loop
                    old_state = widget.checkboxes[address].blockSignals(True)
                    widget.checkboxes[address].setChecked(value == 1)
                    widget.checkboxes[address].blockSignals(old_state)
                    widget.values[address] = value
                    
            # For holding register operations
            elif function_code == 6 or function_code == 16:  # Write Holding Register(s)
                register_type = "holding_registers"
                widget = self.holding_registers_widget

                # 컬럼별 절대 주소 방식이므로 클라이언트 주소가 곧 UI 주소이다.
                if address in widget.line_edits:
                    # Format value as 4-digit hex string
                    hex_value = f"{value:04X}"
                    logger.info(f"Client wrote to holding register {address}: {value} (hex: {hex_value})")
                    # Block signals temporarily to prevent feedback loop
                    old_state = widget.line_edits[address].blockSignals(True)
                    widget.line_edits[address].setText(hex_value)
                    widget.line_edits[address].blockSignals(old_state)
                    widget.values[address] = hex_value
                    # 외부 클라이언트 쓰기로 값이 바뀌었음을 행 하이라이트로 알린다.
                    if hasattr(widget, "flash_row"):
                        widget.flash_row(address)
                else:
                    logger.warning(f"No UI element found for holding register at address {address}")
                    
            QApplication.processEvents()
        except Exception as e:
            logger.error(f"Error handling client write: {e}", exc_info=True)


def main():
    """
Main function"""
    app = QApplication(sys.argv)
    
    # 애플리케이션 아이콘 생성 (외부 파일 의존성 제거)
    # 64x64 크기의 아이콘 생성
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    gradient = QLinearGradient(0, 0, 64, 64)
    gradient.setColorAt(0, QColor(30, 58, 95))  # 더 어두운 파란색
    gradient.setColorAt(1, QColor(44, 76, 124))  # 약간 더 밝은 파란색
    painter.setBrush(QBrush(gradient))
    painter.setPen(QPen(QColor(58, 94, 140), 2))
    painter.drawRoundedRect(4, 4, 56, 56, 10, 10)
    painter.setPen(QPen(QColor(255, 255, 255), 2))  # 흰색 글자
    painter.setFont(QFont("Arial", 32, QFont.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "M") # M for Modbus
    painter.end()
    
    # 생성된 아이콘 설정
    app_icon = QIcon(pixmap)
    app.setWindowIcon(app_icon)
    
    # Windows에서 작업 표시줄 아이콘을 설정하기 위한 추가 작업
    import ctypes
    if hasattr(ctypes, 'windll'):  # Windows 환경에서만 실행
        myappid = 'CMES.ModbusTCP.ServerSimulator.1.0'  # 고유 애플리케이션 ID
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    
    window = ModbusServerSimulator()
    window.setWindowIcon(app_icon)  # 창 아이콘 설정
    window.show()
    
    logger.info("애플리케이션 실행")
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        # Qt 애플리케이션 생성
        app = QApplication(sys.argv)
        app.setApplicationName("Modbus Server Simulator")
        
        # PyInstaller 번들링된 EXE에서 실행 중인 경우 처리
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            logger.info("PyInstaller 번들에서 실행 중입니다.")
            
            # 워킹 디렉토리 설정
            # 일부 환경에서는 워킹 디렉토리가 임시 폴더로 설정되어 문제 발생
            bundle_dir = getattr(sys, '_MEIPASS', os.path.abspath(os.path.dirname(__file__)))
            os.chdir(bundle_dir)
            logger.info(f"작업 디렉토리를 번들 디렉토리로 변경: {bundle_dir}")
        
        # 내장된 QSS 스타일 설정
        app.setStyleSheet(EMBEDDED_QSS_STYLE)
        logger.info("애플리케이션 수준에서 QSS 스타일 적용")
        
        # 애플리케이션 아이콘 생성 및 설정
        app_icon = QIcon()
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        gradient = QLinearGradient(0, 0, 64, 64)
        gradient.setColorAt(0, QColor(30, 58, 95))  # 더 어두운 파란색
        gradient.setColorAt(1, QColor(44, 76, 124))  # 약간 더 밝은 파란색
        painter.setBrush(QBrush(gradient))
        painter.setPen(QPen(QColor(58, 94, 140), 2))
        painter.drawRoundedRect(4, 4, 56, 56, 10, 10)
        painter.setPen(QPen(QColor(255, 255, 255), 2))  # 흰색 펜
        painter.setFont(QFont("Arial", 32, QFont.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "S")
        painter.end()
        app_icon.addPixmap(pixmap)
        app.setWindowIcon(app_icon)
        
        # 서버 창 생성 - 프로그램 시작 시 한 번만 창을 표시하도록 설정
        simulator = ModbusServerSimulator()
        
        # 명시적으로 메인 윈도우 아이콘 설정
        simulator.setWindowIcon(app_icon)
        app.setWindowIcon(app_icon)  # 전체 어플리케이션 아이콘도 설정
        
        # 창을 표시하고 이벤트 루프 시작
        simulator.show()
        
        # 이제 어플리케이션 실행 (이벤트 루프 시작)
        sys.exit(app.exec())
    except Exception as e:
        logger.error(f"애플리케이션 실행 중 오류 발생: {e}")
        DEBUG_MODE = True
        if DEBUG_MODE:
            import traceback
            traceback.print_exc()
        sys.exit(1)

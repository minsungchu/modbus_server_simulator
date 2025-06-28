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
import traceback
import json
import os
import threading
from threading import Thread

# PySide6 관련 임포트
from PySide6.QtCore import QObject, Signal, Slot, QTimer, Qt, QRegularExpression, QThread, QSize
from PySide6.QtGui import QIntValidator, QRegularExpressionValidator, QPixmap, QPainter, QColor, QLinearGradient, QBrush, QPen, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QLineEdit, QPushButton, QGroupBox, QCheckBox, QGridLayout,
    QScrollArea, QMessageBox, QComboBox, QGraphicsDropShadowEffect
)

# Pymodbus 관련 임포트
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext, ModbusSlaveContext
from pymodbus.device import ModbusDeviceIdentification

# pymodbus 3.0.0 버전에 맞게 import 문 수정
from pymodbus.server import StartTcpServer
from pymodbus.transaction import ModbusSocketFramer

# StopServer 함수가 없으므로 직접 구현
def StopServer():
    """Pymodbus 3.0.0에서는 StopServer 함수가 없으므로 직접 구현
    
    asyncio 이벤트 루프를 중지하여 서버를 종료합니다.
    """
    import asyncio
    loop = asyncio.get_event_loop()
    if loop.is_running():
        logger.info("이벤트 루프 중지 요청")
        loop.stop()

# 만약 ModbusSocketFramer가 없으면 None으로 설정 (버전 호환성)
try:
    ModbusSocketFramer
except NameError:
    logger.warning("ModbusSocketFramer를 찾을 수 없습니다. None으로 설정합니다.")
    ModbusSocketFramer = None

# 로깅 설정
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("ModbusServerSim")

# 파일 핸들러 추가
file_handler = logging.FileHandler('modbus_server.log')
file_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

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
    def __init__(self, signals, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.signals = signals
        self.last_write_source = None
        logger.info("커스텀 ModbusSlaveContext 초기화 완료")
        
    def getValues(self, fx, address, count=1):
        """Override getValues to log client read operations"""
        try:
            # Get the current call stack to determine if this is from UI or external client
            import traceback
            stack = traceback.extract_stack()
            caller = stack[-2].name if len(stack) >= 2 else "unknown"
            
            # 디버깅을 위한 추가 로그
            logger.debug(f"getValues called: fx={fx}, address={address}, count={count}, caller={caller}")
            
            # 함수 코드 매핑 (디버깅 용)
            function_code_map = {
                1: "Read Coils",
                2: "Read Discrete Inputs",
                3: "Read Holding Registers",
                4: "Read Input Registers"
            }
            fc_name = function_code_map.get(fx, f"Unknown({fx})")
            logger.debug(f"Function code: {fc_name}")
            
            # Call the parent method to get the values
            values = super().getValues(fx, address, count)
            
            # 값 로깅 (디버깅 용)
            logger.debug(f"Retrieved values: {values}")
            
            # Log external client reads (not from our own UI)
            if caller not in ["update_ui_from_context", "update_context_from_ui"]:
                logger.info(f"External client read detected: FC={fx}({fc_name}), Address={address}, Count={count}, Values={values}")
                
            return values
        except Exception as e:
            logger.error(f"Error in getValues: {e}")
            logger.error(f"Error traceback: {traceback.format_exc()}")
            # Return default values in case of error to avoid breaking client reads
            if fx in [1, 2]:  # Coils and Discrete Inputs
                return [0] * count
            else:  # Holding and Input Registers
                return [0] * count
    
    def setValues(self, fx, address, values):
        """Override setValues to detect external writes"""
        # Get the current call stack to determine if this is from UI or external client
        import traceback
        stack = traceback.extract_stack()
        caller = stack[-2].name if len(stack) >= 2 else "unknown"
        
        try:
            # Call the parent method to set the values
            super().setValues(fx, address, values)
            
            # Only emit signals for external client writes (not from our own UI)
            if caller not in ["on_register_value_changed", "update_context_from_ui"]:
                # This is likely an external client write
                for i, value in enumerate(values):
                    logger.info(f"External client write detected: FC={fx}, Address={address+i}, Value={value}")
                    # Emit signal to notify UI
                    self.signals.client_write_detected.emit(fx, address+i, value)
        except Exception as e:
            logger.error(f"Error in setValues: {e}")


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
        self.server = None  # 서버 객체
        # 스레드 종료 플래그
        self._stop_requested = False
        
        logger.info(f"ModbusServerThread 초기화: {address}:{port}")

    def run(self):
        try:
            self.running = True
            self._stop_requested = False
            logger.info(f"Starting Modbus server on {self.address}:{self.port}")
            
            # Create a custom identity for the server
            identity = ModbusDeviceIdentification()
            identity.VendorName = 'Modbus Server Simulator'
            identity.ProductCode = 'MODSIM'
            identity.VendorUrl = 'https://github.com/'
            identity.ProductName = 'Modbus Server Simulator'
            identity.ModelName = 'Simulator'
            identity.MajorMinorRevision = '1.0'
            
            # Signal that we're starting the server
            self._server_started = True
            self.signals.server_started.emit()
            
            # 다양한 pymodbus 버전을 지원하기 위한 서버 시작 방법
                        # pymodbus 3.0.0 버전에 맞게 서버 시작
            try:
                # StartTcpServer를 사용하여 서버 시작
                StartTcpServer(
                    context=self.context,
                    identity=identity,
                    address=(self.address, self.port),
                    framer=ModbusSocketFramer,
                    # 이 호출은 블록하고 이 스레드가 종료될 때까지 리턴하지 않음
                )
            except Exception as e:
                logger.error(f"Error starting Modbus server: {e}")
                raise
                
        except Exception as e:
            logger.error(f"Error starting Modbus server: {e}")
        finally:
            self.running = False
            self._server_started = False
            self.signals.server_stopped.emit()
            logger.info("Server stopped")

    def stop(self):
        # 서버 종료 방법
        if self.running and self._server_started:
            try:
                logger.info("Stopping Modbus server...")
                
                # 종료 플래그 설정
                self._stop_requested = True
                self.running = False
                self._server_started = False
                
                # pymodbus 3.0.0 버전에 맞게 서버 종료
                try:
                    # StopServer 함수 호출
                    StopServer()
                    logger.info("Server stopped using StopServer function")
                except Exception as e:
                    logger.warning(f"Error using StopServer: {e}")
                
                # 소켓 연결을 통해 서버 종료 시도 (백업 방법)
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1.0)
                try:
                    # 서버에 연결하여 종료 시그널 전송
                    sock.connect((self.address, self.port))
                    sock.send(b'\x00')
                except Exception as e:
                    logger.debug(f"Socket connection for shutdown: {e}")
                finally:
                    try:
                        sock.close()
                    except:
                        pass
                
                # 스레드 종료 처리
                # QThread의 wait 메서드를 사용하여 스레드가 종료되기를 기다림
                self.wait(1000)  # 1초 동안 기다림
                
                # 시그널 전송
                self.signals.server_stopped.emit()
                logger.info("Modbus server stopped successfully")
            except Exception as e:
                logger.error(f"Error stopping server: {e}")
                # 에러가 발생해도 시그널은 보내서 UI가 업데이트되도록 함
                self.signals.server_stopped.emit()


class RegisterWidget(QWidget):
    """레지스터 값을 표시하고 편집하기 위한 위젯
    
    코일, 디스크릿 입력, 홀딩 레지스터, 입력 레지스터의 값을 표시하고
    사용자가 값을 편집할 수 있도록 합니다. 또한 각 레지스터에 메모를 추가할 수 있습니다.
    """
    # 값 변경 시그널 추가
    value_changed = Signal(str, int, int)  # register_type, address, value
    # 메모 변경 시그널 추가
    memo_changed = Signal(str, int, str)  # register_type, address, memo_text
    
    def __init__(self, register_type, register_count=100, parent=None):
        super().__init__(parent)
        self.register_type = register_type
        # 홀딩 레지스터인 경우 200개로 설정
        if register_type == "holding_registers":
            self.register_count = 200
        else:
            self.register_count = register_count
        self.is_bit_type = register_type in ["coils", "discrete_inputs"]
        self.values = {}
        self.checkboxes = {}
        self.line_edits = {}
        self.memo_edits = {}  # 메모 텍스트 필드 저장용
        
        self.init_ui()
        
    def init_ui(self):
        # 홀딩 레지스터인 경우 두 컬럼으로 표시
        if self.register_type == "holding_registers":
            # 기존 레이아웃 제거 및 새 레이아웃 생성
            # 기존 레이아웃이 있으면 삭제
            if self.layout():
                QWidget().setLayout(self.layout())
                
            # 메인 레이아웃을 수평 레이아웃으로 생성
            main_layout = QHBoxLayout()
            
            # 첫 번째 컬럼 (0-99)
            left_widget = QWidget()
            left_layout = QGridLayout(left_widget)
            left_layout.setSpacing(5)
            
            # 두 번째 컬럼 (100-199)
            right_widget = QWidget()
            right_layout = QGridLayout(right_widget)
            right_layout.setSpacing(5)
            
            # 헤더 추가
            # 헤더 라벨에도 패딩 0 적용
            address_header = QLabel("Address")
            address_header.setStyleSheet("padding: 0px;")
            value_header = QLabel("Value (Hex)")
            value_header.setStyleSheet("padding: 0px;")
            memo_header = QLabel("Memo")
            memo_header.setStyleSheet("padding: 0px;")
            
            left_layout.addWidget(address_header, 0, 0)
            left_layout.addWidget(value_header, 0, 1)
            left_layout.addWidget(memo_header, 0, 2)
            
            address_header2 = QLabel("Address")
            address_header2.setStyleSheet("padding: 0px;")
            value_header2 = QLabel("Value (Hex)")
            value_header2.setStyleSheet("padding: 0px;")
            memo_header2 = QLabel("Memo")
            memo_header2.setStyleSheet("padding: 0px;")
            
            right_layout.addWidget(address_header2, 0, 0)
            right_layout.addWidget(value_header2, 0, 1)
            right_layout.addWidget(memo_header2, 0, 2)
            
            # 첫 번째 컬럼 레지스터 (0-99)
            for i in range(100):
                # 주소 레이블
                address_label = QLabel(str(i))
                address_label.setStyleSheet("padding: 0px;")
                left_layout.addWidget(address_label, i+1, 0)
                
                # 값 입력 필드
                line_edit = self.create_register_widget(i)
                self.line_edits[i] = line_edit
                left_layout.addWidget(line_edit, i+1, 1)
                self.values[i] = "0000"
                
                # 메모 필드
                memo_edit = QLineEdit()
                memo_edit.setPlaceholderText("메모 입력")
                memo_edit.setMinimumWidth(100)
                memo_edit.setStyleSheet("border: 1px solid #bec8d1; border-radius: 10px; padding: 0px;")
                # 메모 변경 시 시그널 연결
                memo_edit.textChanged.connect(lambda text, addr=i: self.on_memo_changed(addr, text))
                self.memo_edits[i] = memo_edit
                left_layout.addWidget(memo_edit, i+1, 2)
            
            # 두 번째 컬럼 레지스터 (100-199)
            for i in range(100, 200):
                # 주소 레이블
                address_label = QLabel(str(i))
                address_label.setStyleSheet("padding: 0px;")
                right_layout.addWidget(address_label, i-99, 0)  # i-99로 인덱스 조정
                
                # 값 입력 필드
                line_edit = self.create_register_widget(i)
                self.line_edits[i] = line_edit
                right_layout.addWidget(line_edit, i-99, 1)  # i-99로 인덱스 조정
                self.values[i] = "0000"
                
                # 메모 필드
                memo_edit = QLineEdit()
                memo_edit.setPlaceholderText("메모 입력")
                memo_edit.setMinimumWidth(100)
                memo_edit.setStyleSheet("border: 1px solid #bec8d1; border-radius: 10px; padding: 0px;")
                # 메모 변경 시 시그널 연결
                memo_edit.textChanged.connect(lambda text, addr=i: self.on_memo_changed(addr, text))
                self.memo_edits[i] = memo_edit
                right_layout.addWidget(memo_edit, i-99, 2)  # i-99로 인덱스 조정
            
            # 스크롤 영역 추가
            left_scroll = QScrollArea()
            left_scroll.setWidget(left_widget)
            left_scroll.setWidgetResizable(True)
            
            right_scroll = QScrollArea()
            right_scroll.setWidget(right_widget)
            right_scroll.setWidgetResizable(True)
            
            # 메인 레이아웃에 두 컬럼 추가
            main_layout.addWidget(left_scroll)
            main_layout.addWidget(right_scroll)
            
            # 시그널 연결
            for addr in range(200):
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
        line_edit.setStyleSheet("border: 1px solid #bec8d1; border-radius: 10px; padding: 0px;")
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
        try:
            text = self.line_edits[addr].text()
            # Try to parse as hex first
            if text.startswith("0x"):
                value = int(text, 16)
            else:
                # Try hex without prefix
                try:
                    value = int(text, 16)
                except ValueError:
                    # Fall back to decimal
                    value = int(text)
            
            # Ensure value is in valid range for 16-bit register
            value = max(0, min(value, 65535))
            self.values[addr] = value
            
            # Update the display with formatted hex
            self.line_edits[addr].setText(f"{value:X}")
            logger.info(f"Register changed: {self.register_type}[{addr}] = {value}")
            
            # 값 변경 시그널 발생
            self.value_changed.emit(self.register_type, addr, value)
        except ValueError:
            # Reset to previous value on error
            self.line_edits[addr].setText(f"{self.values[addr]:X}")
    
    def on_value_changed(self, addr, text):
        try:
            # Validate hex input
            if not text:
                value = 0
                text = "0000"
            else:
                # Convert to uppercase
                text = text.upper()
                # Remove any non-hex characters
                text = ''.join(c for c in text if c in '0123456789ABCDEF')
                # Add leading zeros if needed (but keep it at most 4 characters)
                if len(text) > 4:
                    text = text[-4:]  # Take only the last 4 characters if too long
                else:
                    text = text.zfill(4)  # Add leading zeros if needed
                
                # Convert to integer
                value = int(text, 16)
            
            # Ensure value is in valid range for 16-bit register
            value = max(0, min(value, 65535))
            
            # Store the formatted hex string
            self.values[addr] = text
            
            # Update the display with the properly formatted text
            # Block signals to prevent recursive calls
            old_state = self.line_edits[addr].blockSignals(True)
            self.line_edits[addr].setText(text)
            self.line_edits[addr].blockSignals(old_state)
            
            logger.info(f"Register changed: {self.register_type}[{addr}] = {text} (int: {value})")
            
            # 값 변경 시그널 발생
            self.value_changed.emit(self.register_type, addr, value)
        except ValueError as e:
            logger.error(f"Error in on_value_changed: {e}")
            # Reset to previous value on error
            if addr in self.values:
                self.line_edits[addr].setText(self.values[addr])
            else:
                self.line_edits[addr].setText("0000")
                self.values[addr] = "0000"
    
    def on_memo_changed(self, addr, text):
        """메모 입력 필드 변경 처리"""
        # 메모 변경 시그널 발생 (홀딩 레지스터만 해당)
        if self.register_type == "holding_registers":
            self.memo_changed.emit(self.register_type, addr, text)
            logger.debug(f"Memo changed: {self.register_type}[{addr}] = {text}")
    
    def update_value(self, addr, value):
        """Update a register value from the server"""
        if addr < 0 or addr >= self.register_count:
            return
        
        if self.is_bit_type:
            # For bit types (coils, discrete inputs)
            self.values[addr] = value
            self.checkboxes[addr].setChecked(bool(value))
        else:
            # For register types (holding registers, input registers)
            # Format as 4-digit hex string
            hex_value = f"{value:04X}"
            
            # Only update if the widget doesn't have focus (not being edited)
            if not self.line_edits[addr].hasFocus():
                # Block signals to prevent feedback loops
                old_state = self.line_edits[addr].blockSignals(True)
                self.line_edits[addr].setText(hex_value)
                self.line_edits[addr].blockSignals(old_state)
                
                # Store the hex string value
                self.values[addr] = hex_value
                
                logger.debug(f"Updated {self.register_type}[{addr}] to {hex_value} from server")


class ModbusServerSimulator(QMainWindow):
    """Modbus 서버 시뮬레이터 메인 애플리케이션 창
    
    사용자 인터페이스를 제공하고 Modbus 서버의 동작을 제어합니다.
    레지스터 값 표시, 서버 시작/중지, 설정 관리 등의 기능을 제공합니다.
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Modbus Server Simulator")
        self.resize(900, 600)
        
        # 최소 창 크기 설정 - UI 리사이징 시 글자가 가려지지 않도록 최소 크기 설정
        self.setMinimumSize(900, 600)
        
        # 애플리케이션 아이콘 설정
        self.create_server_icon()
        
        # 스타일시트 적용
        self.load_stylesheet()
        
        # 레지스터 값 저장 파일 경로 설정
        self.register_file = "modbus_registers.json"
        
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
        
        # Timer for polling data changes
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_from_context)
        self.timer.start(1000)  # Update every 1000ms (1초) - 업데이트 빈도 감소
    
    def create_server_icon(self):
        """서버 아이콘 생성 및 설정
        
        클라이언트와 구분되는 서버용 아이콘을 생성하고 애플리케이션에 설정합니다.
        """
        # 아이콘 이미지 생성 (동적으로 생성)
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 그라데이션 배경
        gradient = QLinearGradient(0, 0, 64, 64)
        gradient.setColorAt(0, QColor(30, 58, 95))  # 다크 블루
        gradient.setColorAt(1, QColor(44, 76, 124))  # 블루
        
        painter.setBrush(QBrush(gradient))
        painter.setPen(QPen(QColor(58, 94, 140), 2))  # 더 밝은 블루 테두리
        painter.drawRoundedRect(4, 4, 56, 56, 10, 10)
        
        # 'S' 글자 (Server)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.setFont(QFont("Arial", 32, QFont.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "S")
        
        painter.end()
        
        # 아이콘 설정
        icon = QIcon(pixmap)
        self.setWindowIcon(icon)
        
        logger.info("서버 아이콘 생성 및 설정 완료")
        
        # 아이콘 파일로 저장 (선택적)
        try:
            if not os.path.exists("resources"):
                os.makedirs("resources")
            pixmap.save("resources/server_icon.png")
            logger.info("서버 아이콘 파일 저장 완료: resources/server_icon.png")
        except Exception as e:
            logger.warning(f"아이콘 파일 저장 실패: {e}")
            # 아이콘 저장 실패는 중요한 오류가 아니므로 계속 진행
            
    def load_stylesheet(self):
        """서버 애플리케이션의 스타일시트 로드
        
        서버는 어두운 테마(다크 블루)를 사용하여 클라이언트와 시각적으로 구분됩니다.
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
        
    def init_modbus_store(self):
        """Initialize the Modbus data store"""
        # Create signals object first if not already created
        if not hasattr(self, 'signals'):
            self.signals = ModbusSignals()
            
        # Create data blocks with 100 registers each (0-99)
        # Modbus 레지스터는 0부터 시작하지만, 주소가 0부터 99까지인 경우 실제로는 100개의 레지스터가 필요함
        # 각 레지스터 타입에 대해 100개의 레지스터(0-99) 생성
        self.store = CustomModbusSlaveContext(
            signals=self.signals,
            di=ModbusSequentialDataBlock(0, [0] * 100),   # Discrete Inputs
            co=ModbusSequentialDataBlock(0, [0] * 100),   # Coils
            hr=ModbusSequentialDataBlock(0, [0] * 200),   # Holding Registers (0-199)
            ir=ModbusSequentialDataBlock(0, [0] * 100)    # Input Registers
        )
        self.context = ModbusServerContext(slaves=self.store, single=True)
        
        # 레지스터 초기화 확인
        logger.info("Modbus store initialized with:")
        logger.info(f"Coils: {self.store.getValues(1, 0, 100)}")
        logger.info(f"Discrete Inputs: {self.store.getValues(2, 0, 100)}")
        logger.info(f"Holding Registers: {self.store.getValues(3, 0, 100)}")
        logger.info(f"Input Registers: {self.store.getValues(4, 0, 100)}")
    
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
            print(f"Toggle {register_type}: {'visible' if is_checked else 'hidden'}")
    
    def init_ui(self):
        """Initialize the user interface"""
        # 전체 애플리케이션 창의 최소 크기 설정
        self.setMinimumSize(800, 900)  # 가로, 세로 최소 크기
        
        central_widget = QWidget()
        main_layout = QVBoxLayout(central_widget)
        
        # 타이틀 레이블
        title_label = QLabel("Modbus TCP Server Simulator")
        title_label.setObjectName("title_label")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title_label)
        self.apply_neumorphism_effect(title_label)
        
        # Connection settings
        conn_group = QGroupBox("")
        conn_group.setObjectName("connection_group")
        conn_layout = QHBoxLayout()
        
        # 그룹박스에 뉴모피즘 효과 추가
        self.apply_neumorphism_effect(conn_group)
        
        # Connection type
        conn_layout.addWidget(QLabel("Connection type:"))
        self.conn_type_combo = QComboBox()
        self.conn_type_combo.addItem("TCP")
        conn_layout.addWidget(self.conn_type_combo)
        
        # Port
        conn_layout.addWidget(QLabel("Port:"))
        self.port_edit = QLineEdit("502")
        self.port_edit.setValidator(QIntValidator(1, 65535))  # 포트 번호 유효성 검사 추가
        self.port_edit.setStyleSheet("border: 1px solid #bec8d1; border-radius: 10px;")
        conn_layout.addWidget(self.port_edit)
        
        # Server address
        conn_layout.addWidget(QLabel("Server Address:"))
        self.address_edit = QLineEdit("127.0.0.1")
        self.address_edit.setStyleSheet("border: 1px solid #bec8d1; border-radius: 10px;")
        conn_layout.addWidget(self.address_edit)
        
        # Connect/Disconnect button
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("connect_button")
        self.connect_button.clicked.connect(self.toggle_server)
        self.connect_button.setMinimumWidth(100)
        # 시작 아이콘 추가
        self.start_icon = QIcon("resources/start_icon.svg")
        self.stop_icon = QIcon("resources/stop_icon.svg")
        self.connect_button.setIcon(self.start_icon)
        self.connect_button.setIconSize(QSize(16, 16))
        conn_layout.addWidget(self.connect_button)
        
        conn_group.setLayout(conn_layout)
        main_layout.addWidget(conn_group)
        
        # Server options
        options_layout = QHBoxLayout()
        
        # Set server busy
        self.busy_checkbox = QCheckBox("Set server busy")
        self.busy_checkbox.setEnabled(False)  # Not implemented yet
        options_layout.addWidget(self.busy_checkbox)
        
        # Set server listen only
        self.listen_only_checkbox = QCheckBox("Set server listen only")
        self.listen_only_checkbox.setEnabled(False)  # Not implemented yet
        options_layout.addWidget(self.listen_only_checkbox)
        
        options_layout.addStretch()
        main_layout.addLayout(options_layout)
        
        # 레지스터 그룹 표시/숨김 기능을 위한 컨트롤 패널
        control_layout = QHBoxLayout()
        control_layout.setContentsMargins(10, 5, 10, 5)
        
        # 레지스터 그룹 표시/숨김 체크박스
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
        
        # 체크박스 영역 추가
        main_layout.addLayout(control_layout)
        
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
        
        main_layout.addLayout(self.registers_layout)
        
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
        
        # 아이콘이 있는 제목 생성
        title_layout = QHBoxLayout()
        if icon_path:
            icon_label = QLabel()
            icon_label.setPixmap(QIcon(icon_path).pixmap(16, 16))
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
        
        # 홀딩 레지스터에 테스트 입력 필드 추가
        if register_type == "holding_registers":
            test_layout = QHBoxLayout()
            test_layout.addWidget(QLabel("테스트 입력:"))
            
            # 테스트 입력 필드 생성
            self.test_input = QLineEdit()
            self.test_input.setPlaceholderText("테스트 값 입력 (16진수)")
            self.test_input.setMinimumWidth(80)  # 입력 필드 최소 너비 설정
            self.test_input.setStyleSheet("border: 1px solid #bec8d1; border-radius: 10px;")
            
            # 16진수 입력 검증기 설정
            hex_validator = QRegularExpressionValidator(QRegularExpression("[0-9A-Fa-f]{0,4}"))
            self.test_input.setValidator(hex_validator)
            
            # 테스트 버튼 생성
            test_button = QPushButton("테스트")
            test_button.setObjectName("test_button")
            test_button.setMinimumWidth(60)  # 버튼 최소 너비 설정
            test_button.clicked.connect(self.on_test_button_clicked)
            # 테스트 버튼에 아이콘 추가
            test_button.setIcon(QIcon("resources/holding_icon.svg"))
            test_button.setIconSize(QSize(16, 16))
            
            test_layout.addWidget(self.test_input)
            test_layout.addWidget(test_button)
            layout.addLayout(test_layout)
        
        # Create register widget
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
        
        # Add to layout
        layout.addWidget(scroll)
        group.setLayout(layout)
        
        # Store reference to widget
        setattr(self, f"{register_type}_widget", register_widget)
        
        return group
        
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
            
            # Update holding registers
            if hasattr(self, 'holding_registers_widget'):
                holding_widget = self.holding_registers_widget
                try:
                    hr_values = self.store.getValues(3, 0, register_count)
                    for addr in range(min(register_count, len(hr_values))):
                        if addr in holding_widget.line_edits:
                            # Skip update if this widget has focus (user is editing)
                            if holding_widget.line_edits[addr].hasFocus():
                                logger.debug(f"Skipping update for holding register {addr} - user is editing")
                                continue
                                
                            value = hr_values[addr]
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
                "holding_registers_memos": {}  # 홀딩 레지스터 메모 추가
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
            self.connect_button.setStyleSheet("")
        else:
            self.start_server()
            # 버튼 텍스트는 서버가 실제로 시작된 후에 변경됨
            
    def start_server(self):
        """Start the Modbus server"""
        if self.server_thread and self.server_thread.running:
            return
        
        try:
            address = self.address_edit.text()
            port = int(self.port_edit.text())
            
            # Reset the store to ensure we're starting fresh
            # This helps prevent issues with stale data or callbacks
            self.init_modbus_store()
            
            # 저장된 레지스터 값 로드
            self.load_registers_from_file()
            
            # Create and start server thread
            self.server_thread = ModbusServerThread(address, port, self.context, self.signals)
            self.server_thread.start()
            
            # Log the attempt
            logger.info(f"Attempting to start server at {address}:{port}")
            
        except Exception as e:
            logger.error(f"Failed to start server: {e}")
            # Show error in UI
            self.connect_button.setText("Error")
            # Reset after 2 seconds
            QTimer.singleShot(2000, lambda: self.connect_button.setText("Connect"))
    
    def stop_server(self):
        """Stop the Modbus server"""
        if self.server_thread and self.server_thread.running:
            # Call stop method to trigger server shutdown
            self.server_thread.stop()
            
            # Give the server a moment to shut down
            for _ in range(20):  # Try for up to 2 seconds
                if not self.server_thread.running:
                    break
                time.sleep(0.1)
            
            # Force thread termination if it's still running
            import sys
            if self.server_thread and self.server_thread.running and sys.platform == 'win32':
                try:
                    import ctypes
                    if hasattr(self.server_thread, '_thread_id'):
                        thread_id = self.server_thread._thread_id
                    else:
                        thread_id = None
                    if thread_id is not None:
                        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(thread_id, 
                              ctypes.py_object(SystemExit))
                        if res > 1:
                            ctypes.pythonapi.PyThreadState_SetAsyncExc(thread_id, 0)
                            logger.error('Failed to terminate thread properly')
                except Exception as e:
                    logger.error(f"Error forcefully stopping thread: {e}")
            
            # Clean up thread reference
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
        self.connect_button.setStyleSheet("")
        # 서버 실행 중일 때는 중지 아이콘으로 변경
        self.connect_button.setIcon(self.stop_icon)
        # 상태 표시줄에 온라인 아이콘 추가
        status_msg = QLabel()
        status_msg.setPixmap(QIcon("resources/online_icon.svg").pixmap(12, 12))
        self.statusBar().addWidget(status_msg)
        self.statusBar().showMessage("Server running")
        logger.info("Server started successfully")
    
    @Slot()
    def on_server_stopped(self):
        """Handle server stopped signal"""
        self.server_running = False
        self.connect_button.setText("Connect")
        self.connect_button.setObjectName("connect_button")
        self.connect_button.setStyleSheet("")
        # 서버 중지 시 시작 아이콘으로 변경
        self.connect_button.setIcon(self.start_icon)
        # 상태바 초기화
        self.statusBar().clearMessage()
        
        # QStatusBar에서 모든 QLabel 위젯 제거
        status_bar_widgets = []
        for child in self.statusBar().children():
            if isinstance(child, QLabel):
                status_bar_widgets.append(child)
        
        # 수집된 위젯들을 제거
        for widget in status_bar_widgets:
            self.statusBar().removeWidget(widget)
            widget.deleteLater()  # 위젯 메모리 해제
        # 오프라인 아이콘 추가
        status_msg = QLabel()
        status_msg.setPixmap(QIcon("resources/offline_icon.svg").pixmap(12, 12))
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
                if address in widget.line_edits:
                    # Format value as 4-digit hex string
                    hex_value = f"{value:04X}"
                    logger.info(f"Client wrote to holding register {address}: {value} (hex: {hex_value})")
                    # Block signals temporarily to prevent feedback loop
                    old_state = widget.line_edits[address].blockSignals(True)
                    widget.line_edits[address].setText(hex_value)
                    widget.line_edits[address].blockSignals(old_state)
                    widget.values[address] = hex_value
                    
            QApplication.processEvents()
        except Exception as e:
            logger.error(f"Error handling client write: {e}")


def main():
    """
Main function"""
    app = QApplication(sys.argv)
    
    # 애플리케이션 아이콘 설정
    app_icon = QIcon("resources/app_icon.svg")
    
    window = ModbusServerSimulator()
    window.show()
    
    logger.info("애플리케이션 실행")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

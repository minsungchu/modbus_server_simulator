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
from datetime import datetime
import socket
from contextlib import closing


# 내장된 스타일시트 (resources/style.qss 파일 내용을 직접 포함)
EMBEDDED_QSS_STYLE = """/* Neumorphism Style Sheet for Modbus TCP Server Simulator */

/* 전체 애플리케이션 스타일 */
QWidget {
    background-color: #e0e5ec;
    color: #333;
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 10pt;
}

/* 타이틀 레이블 스타일 개선 */
#title_label {
    color: #2980b9;
    font-size: 18pt;
    font-weight: bold;
    padding: 15px;
    border-bottom: 2px solid #3498db;
    margin-bottom: 5px;
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f0f5fa, stop:1 #e0e5ec);
    border-radius: 10px;
}

/* 그룹박스 스타일 */
QGroupBox {
    border: 1px solid #bec8d1;
    border-radius: 15px;
    margin-top: 1.5em;
    padding: 15px;
    background-color: #f0f5fa;
    /* 뉴모피즘 효과는 코드에서 QGraphicsDropShadowEffect로 적용됨 */
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top center;
    padding: 0 12px;
    color: #2980b9;
    font-weight: bold;
    font-size: 12pt;
    background-color: #f0f5fa;
    border: 1px solid #bec8d1;
    border-radius: 8px;
}

/* 레지스터 그룹 스타일 */
#coils_group {
    background-color: #fff8f0;
    border-top: 3px solid #ff8c00;
}

#discrete_inputs_group {
    background-color: #ebf5fb;
    border-top: 3px solid #3498db;
}

#holding_registers_group {
    background-color: #f4ecf7;
    border-top: 3px solid #9b59b6;
}

#input_registers_group {
    background-color: #eafaf1;
    border-top: 3px solid #2ecc71;
}

/* 연결 설정 그룹 스타일 */
#connection_group {
    background-color: #f5f7fa;
    border-top: 3px solid #34495e;
    padding: 3px;
}

/* 버튼 스타일 */
QPushButton {
    background-color: #f0f5fa;
    border: none;
    padding: 10px 20px;
    border-radius: 12px;
    color: #3498db;
    font-weight: bold;
}

QPushButton:hover {
    background-color: #e5eef7;
    color: #2980b9;
}

QPushButton:pressed {
    background-color: #e0e5ec;
    padding: 11px 21px 9px 19px; /* 눌렸을 때 약간 이동하는 효과 */
}

/* 입력 필드 스타일 */
QLineEdit, QSpinBox {
    background-color: #f5f7fa;
    border: 1px solid #bec8d1;
    border-radius: 10px;
    padding: 8px;
    color: #2d3436;
}

/* 체크박스 스타일 */
QCheckBox {
    spacing: 10px;
    color: #2c3e50;
    font-weight: 500;
}

/* 라벨 스타일 */
QLabel {
    color: #2c3e50;
    font-weight: 500;
    padding: 5px;
}
"""

# PySide6 관련 임포트
from PySide6.QtCore import QObject, Signal, Slot, QTimer, Qt, QRegularExpression, QThread, QSize, QPoint, QRect
from PySide6.QtGui import QIntValidator, QRegularExpressionValidator, QPixmap, QPainter, QColor, QLinearGradient, QBrush, QPen, QFont, QIcon, QPolygon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QLineEdit, QPushButton, QGroupBox, QCheckBox, QGridLayout,
    QScrollArea, QMessageBox, QComboBox, QGraphicsDropShadowEffect
)

# Pymodbus 관련 임포트
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext, ModbusSlaveContext
from pymodbus.device import ModbusDeviceIdentification

# pymodbus 버전 호환성을 위한 import 처리
from pymodbus.server import StartTcpServer

# pymodbus 버전에 따른 호환성 처리
try:
    from pymodbus.framer.socket_framer import ModbusSocketFramer  # 최신 버전
except ImportError:
    try:
        from pymodbus.transaction import ModbusSocketFramer  # 구버전
    except ImportError:
        ModbusSocketFramer = None  # 모듈이 없으면 None으로 설정

# StopServer 함수가 없으므로 직접 구현
def StopServer():
    """Pymodbus 3.0.0에서는 StopServer 함수가 없으므로 직접 구현
    
    asyncio 이벤트 루프를 중지하여 서버를 종료합니다.
    """
    import asyncio
    loop = asyncio.get_event_loop()
    if loop.is_running():
        logger.info("이벤트 루프 중지 요청")
        print("이벤트 루프 중지 요청")
        loop.stop()

# 만약 ModbusSocketFramer가 없으면 None으로 설정 (버전 호환성)
try:
    ModbusSocketFramer
except NameError:
    logger.warning("ModbusSocketFramer를 찾을 수 없습니다. None으로 설정합니다.")
    print("ModbusSocketFramer를 찾을 수 없습니다. None으로 설정합니다.")
    ModbusSocketFramer = None

# 로깅 설정
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.DEBUG)
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
    def __init__(self, signals=None, *args, hr_offset=0, **kwargs):
        super().__init__(*args, **kwargs)
        self.signals = signals if signals is not None else ModbusSignals()
        self.last_write_source = None
        self.hr_offset = hr_offset  # 홀딩 레지스터 오프셋 값 설정
        logger.info(f"커스텀 ModbusSlaveContext 초기화 완료 - HR 오프셋: {self.hr_offset}")
        print(f"커스텀 ModbusSlaveContext 초기화 완료 - HR 오프셋: {self.hr_offset}")
        
    def getValues(self, fx, address, count=1):
        """Override getValues to log client read operations and apply offset for holding registers"""
        try:
            # Get the current call stack to determine if this is from UI or external client
            import traceback
            stack = traceback.extract_stack()
            caller = stack[-2].name if len(stack) >= 2 else "unknown"
            
            # 디버깅을 위한 추가 로그
            logger.debug(f"getValues called: fx={fx}, address={address}, count={count}, caller={caller}")
            # print(f"getValues called: fx={fx}, address={address}, count={count}, caller={caller}")
            
            # 함수 코드 매핑 (디버깅 용)
            function_code_map = {
                1: "Read Coils",
                2: "Read Discrete Inputs",
                3: "Read Holding Registers",
                4: "Read Input Registers"
            }
            fc_name = function_code_map.get(fx, f"Unknown({fx})")
            logger.debug(f"Function code: {fc_name}")
            
            # 홀딩 레지스터(fx=3)인 경우 오프셋 적용
            actual_address = address
            logger.info(f"[getValues] 함수 코드: {fx}, 요청 주소: {address}, HR 오프셋: {self.hr_offset}")
            
            # Get the current call stack to determine if this is from UI or external client
            stack = traceback.extract_stack()
            caller = stack[-2].name if len(stack) >= 2 else "unknown"
            is_ui_request = caller in ["update_ui_from_context", "update_context_from_ui"]
            
            # 홀딩 레지스터 관련 함수 코드(3, 6, 16)인 경우 오프셋 적용
            # fx=3: Read Holding Registers, fx=6: Write Single Register, fx=16: Write Multiple Registers
            if fx in [3, 6, 16]:  # 홀딩 레지스터 관련 함수 코드인 경우
                if self.hr_offset > 0 and not is_ui_request:  # 오프셋이 있고 UI에서의 요청이 아닌 경우에만 적용
                    # 클라이언트가 요청한 주소에서 오프셋을 빼서 실제 데이터 저장소 주소를 계산
                    # 예: 클라이언트가 10을 요청하면 실제로는 0번 데이터를 반환해야 함 (10-10=0)
                    
                    # 클라이언트 주소가 오프셋보다 작은 경우 유효하지 않은 주소
                    if address < self.hr_offset:
                        logger.warning(f"[getValues] 유효하지 않은 주소 요청: 클라이언트 주소={address}, 오프셋={self.hr_offset}, 0 값 반환")
                        print(f"[DEBUG] 유효하지 않은 주소 요청: 클라이언트 주소={address}, 오프셋={self.hr_offset}, 0 값 반환")
                        return [0] * count
                        
                    actual_address = address - self.hr_offset
                    logger.info(f"[getValues] 홀딩 레지스터 오프셋 적용: 클라이언트 요청 주소={address}, 실제 주소={actual_address}, 오프셋={self.hr_offset}")
                    print(f"[DEBUG] 홀딩 레지스터 오프셋 적용: 클라이언트 요청 주소={address}, 실제 주소={actual_address}, 오프셋={self.hr_offset}")
                elif self.hr_offset > 0 and is_ui_request:
                    # UI에서의 요청은 오프셋 검증을 건너뛰고 주소 그대로 사용
                    # logger.info(f"[getValues] UI 요청 - 오프셋 검증 건너뛰기: 주소={address}, 오프셋={self.hr_offset}")
                    # print(f"[DEBUG] UI 요청 - 오프셋 검증 건너뛰기: 주소={address}, 오프셋={self.hr_offset}")
                    actual_address = address
                    
                    # 유효한 주소 범위를 벗어나는지 확인
                    # 동적으로 할당된 데이터 저장소 크기를 고려하여 검증
                    hr_size = 200
                    if hasattr(self, '_blocks') and hasattr(self._blocks[3], 'values'):
                        hr_size = len(self._blocks[3].values)
                    
                    if actual_address >= hr_size:  # 동적으로 할당된 홀딩 레지스터 범위 검증
                        logger.warning(f"[getValues] 오프셋 적용 후 유효하지 않은 주소: {actual_address}, 데이터 저장소 크기: {hr_size}, 0 값 반환")
                        print(f"[getValues] 오프셋 적용 후 유효하지 않은 주소: {actual_address}, 데이터 저장소 크기: {hr_size}, 0 값 반환")
                        return [0] * count
                else:
                    # logger.info(f"[getValues] 홀딩 레지스터지만 오프셋이 0이므로 적용하지 않음")
                    # print(f"[getValues] 홀딩 레지스터지만 오프셋이 0이므로 적용하지 않음")
                    pass
            
            # Call the parent method to get the values with adjusted address
            values = super().getValues(fx, actual_address, count)
            
            # 값 로깅 (디버깅 용)
            logger.debug(f"Retrieved values: {values}")
            # print(f"Retrieved values: {values}")
            
            # Log external client reads (not from our own UI)
            if caller not in ["update_ui_from_context", "update_context_from_ui"]:
                if fx == 3 and self.hr_offset > 0:
                    logger.info(f"External client read detected: FC={fx}({fc_name}), Client Address={address}, Actual Address={actual_address}, Count={count}, Values={values}")
                    print(f"[DEBUG] External client read detected: FC={fx}({fc_name}), Client Address={address}, Actual Address={actual_address}, Count={count}, Values={values}, HR Offset={self.hr_offset}")
                    
                    # 추가 디버깅: 내부 데이터 저장소 값 확인
                    if hasattr(self, '_blocks') and hasattr(self._blocks[3], 'values'):
                        internal_values = [self._blocks[3].values.get(i, 0) for i in range(10)]
                        print(f"[DEBUG] Internal data store first 10 values: {internal_values}")
                else:
                    logger.info(f"External client read detected: FC={fx}({fc_name}), Address={address}, Count={count}, Values={values}")
                    print(f"External client read detected: FC={fx}({fc_name}), Address={address}, Count={count}, Values={values}")
                
            return values
        except Exception as e:
            logger.error(f"Error in getValues: {e}")
            print(f"Error in getValues: {e}")
            logger.error(f"Error traceback: {traceback.format_exc()}")
            print(f"Error traceback: {traceback.format_exc()}")
            # Return default values in case of error to avoid breaking client reads
            if fx in [1, 2]:  # Coils and Discrete Inputs
                return [0] * count
            else:  # Holding and Input Registers
                return [0] * count
    
    def setValues(self, fx, address, values):
        """Override setValues to detect external writes and apply offset for holding registers"""
        # Get the current call stack to determine if this is from UI or external client
        import traceback
        stack = traceback.extract_stack()
        caller = stack[-2].name if len(stack) >= 2 else "unknown"
        
        try:
            # 기본적으로 요청 주소를 그대로 사용
            actual_address = address
            
            # 디버깅을 위한 추가 로그 (상세)
            logger.info(f"[setValues 시작] 함수 코드: {fx}, 요청 주소: {address}, HR 오프셋: {self.hr_offset}, 값: {values}, 호출자: {caller}")
            # print(f"[DEBUG] [setValues 시작] 함수 코드: {fx}, 요청 주소: {address}, HR 오프셋: {self.hr_offset}, 값: {values}, 호출자: {caller}")
            
            # UI 요청인지 확인 - 좀 더 구체적인 조건 정의
            is_ui_request = caller in ["update_ui_from_context", "update_context_from_ui", "on_register_value_changed"]
            logger.info(f"[setValues] UI 요청 여부: {is_ui_request}, 호출자: {caller}")
            # print(f"[DEBUG] [setValues] UI 요청 여부: {is_ui_request}, 호출자: {caller}")
            
            # 함수 코드 매핑 (디버깅 용)
            function_code_map = {
                5: "Write Single Coil",
                6: "Write Single Register",
                15: "Write Multiple Coils",
                16: "Write Multiple Registers"
            }
            fc_name = function_code_map.get(fx, f"Unknown({fx})")
            logger.info(f"[setValues] 함수 코드: {fx} ({fc_name})")
            # print(f"[DEBUG] [setValues] 함수 코드: {fx} ({fc_name})")
            
            # 내부 데이터 저장소 접근 전/후 값 확인 (디버깅용)
            if hasattr(self, '_blocks') and hasattr(self._blocks[fx], 'values'):
                old_values = {}
                if address < len(self._blocks[fx].values):
                    for i in range(len(values)):
                        if address + i < len(self._blocks[fx].values):
                            old_values[address + i] = self._blocks[fx].values.get(address + i, 0)
                logger.info(f"[setValues] 변경 전 데이터 저장소 값: {old_values}")
                # print(f"[DEBUG] [setValues] 변경 전 데이터 저장소 값: {old_values}")
            
            # 홀딩 레지스터 관련 함수 코드(3, 6, 16)인 경우 오프셋 적용
            # fx=3: Read Holding Registers, fx=6: Write Single Register, fx=16: Write Multiple Registers
            if fx in [3, 6, 16]:  # 홀딩 레지스터 관련 함수 코드인 경우
                if self.hr_offset > 0 and not is_ui_request:  # 오프셋이 있고 UI에서의 요청이 아닌 경우에만 적용
                    # 클라이언트가 요청한 주소에서 오프셋을 빼서 실제 데이터 저장소 주소를 계산
                    # 예: 클라이언트가 10을 요청하면 실제로는 0번 데이터를 저장해야 함 (10-10=0)
                    
                    # 클라이언트 주소가 오프셋보다 작은 경우 유효하지 않은 주소
                    if address < self.hr_offset:
                        logger.warning(f"[setValues] 유효하지 않은 주소 요청: 클라이언트 주소={address}, 오프셋={self.hr_offset}, 쓰기 무시")
                        print(f"[DEBUG] [setValues] 유효하지 않은 주소 요청: 클라이언트 주소={address}, 오프셋={self.hr_offset}, 쓰기 무시")
                        return
                        
                    actual_address = address - self.hr_offset
                    logger.info(f"[setValues] 홀딩 레지스터 오프셋 적용: 클라이언트 요청 주소={address}, 실제 주소={actual_address}, 오프셋={self.hr_offset}")
                    # print(f"[DEBUG] [setValues] 홀딩 레지스터 오프셋 적용: 클라이언트 요청 주소={address}, 실제 주소={actual_address}, 오프셋={self.hr_offset}")
                elif self.hr_offset > 0 and is_ui_request:
                    # UI에서의 요청은 오프셋 검증을 건너뛰고 주소 그대로 사용
                    logger.info(f"[setValues] UI 요청 - 오프셋 검증 건너뛰기: 주소={address}, 오프셋={self.hr_offset}")
                    # print(f"[DEBUG] [setValues] UI 요청 - 오프셋 검증 건너뛰기: 주소={address}, 오프셋={self.hr_offset}")
                    actual_address = address
                    
                    # 유효한 주소 범위를 벗어나는지 확인
                    # 동적으로 할당된 데이터 저장소 크기를 고려하여 검증
                    hr_size = 200
                    if hasattr(self, '_blocks') and hasattr(self._blocks[3], 'values'):
                        hr_size = len(self._blocks[3].values)
                    
                    if actual_address >= hr_size:  # 동적으로 할당된 홀딩 레지스터 범위 검증
                        logger.warning(f"[setValues] 오프셋 적용 후 유효하지 않은 주소: {actual_address}, 데이터 저장소 크기: {hr_size}, 쓰기 무시")
                        # print(f"[DEBUG] [setValues] 오프셋 적용 후 유효하지 않은 주소: {actual_address}, 데이터 저장소 크기: {hr_size}, 쓰기 무시")
                        return
                else:
                    logger.info(f"[setValues] 홀딩 레지스터지만 오프셋이 0이므로 적용하지 않음")
                    # print(f"[DEBUG] [setValues] 홀딩 레지스터지만 오프셋이 0이므로 적용하지 않음")
            
            # Call the parent method to set the values with adjusted address
            logger.info(f"[setValues] 실제 값 저장 전 - 함수 코드: {fx}, 계산된 주소: {actual_address}, 값: {values}")
            # print(f"[DEBUG] [setValues] 실제 값 저장 전 - 함수 코드: {fx}, 계산된 주소: {actual_address}, 값: {values}")
            
            super().setValues(fx, actual_address, values)
            
            # 값 저장 후 디버깅 - 실제로 값이 저장되었는지 확인
            if hasattr(self, '_blocks') and hasattr(self._blocks[fx], 'values'):
                new_values = {}
                for i in range(len(values)):
                    if actual_address + i < len(self._blocks[fx].values):
                        new_values[actual_address + i] = self._blocks[fx].values.get(actual_address + i, 0)
                logger.info(f"[setValues] 변경 후 데이터 저장소 값: {new_values}")
                print(f"[DEBUG] [setValues] 변경 후 데이터 저장소 값: {new_values}")
            
            # Only emit signals for external client writes (not from our own UI)
            if caller not in ["on_register_value_changed", "update_context_from_ui"]:
                # This is likely an external client write
                for i, value in enumerate(values):
                    if fx in [3, 6, 16] and self.hr_offset > 0:  # 홀딩 레지스터 관련 함수 코드
                        # 외부 클라이언트 쓰기 감지 시 오프셋 적용된 주소 정보 로깅
                        logger.info(f"External client write detected: FC={fx}, Client Address={address+i}, Actual Address={actual_address+i}, Value={value}, Offset={self.hr_offset}")
                        print(f"[DEBUG] External client write detected: FC={fx}, Client Address={address+i}, Actual Address={actual_address+i}, Value={value}, Offset={self.hr_offset}")
                        # UI에 클라이언트 주소 그대로 전달 (오프셋이 적용된 주소)
                        self.signals.client_write_detected.emit(fx, address+i, value)
                    else:
                        logger.info(f"External client write detected: FC={fx}, Address={address+i}, Value={value}")
                        print(f"[DEBUG] External client write detected: FC={fx}, Address={address+i}, Value={value}")
                        # Emit signal to notify UI
                        self.signals.client_write_detected.emit(fx, address+i, value)
        except Exception as e:
            logger.error(f"Error in setValues: {e}")
            logger.error(f"Error traceback: {traceback.format_exc()}")
            print(f"[DEBUG] Error in setValues: {e}")
            print(f"[DEBUG] Error traceback: {traceback.format_exc()}")


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
        print(f"ModbusServerThread 초기화: {address}:{port}")

    def run(self):
        import traceback
        try:
            self.running = True
            self._stop_requested = False
            logger.info(f"Starting Modbus server on {self.address}:{self.port}")
            print(f"Starting Modbus server on {self.address}:{self.port}")
            
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
            
            # 먼저 포트가 이미 사용 중인지 확인
            try:
                import socket
                test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                test_socket.settimeout(1)
                result = test_socket.connect_ex((self.address, self.port))
                test_socket.close()
                if result == 0:
                    logger.error(f"Port {self.port} is already in use!")
                    print(f"Port {self.port} is already in use!")
                    # 포트 사용 중 오류 메시지 전달을 위해 예외 발생
                    raise Exception(f"Port {self.port} is already in use. Please select a different port.")
            except Exception as e:
                if "already in use" in str(e):
                    # 이미 발생한 예외 재사용
                    logger.error(f"{str(e)}")
                    raise
                # 소켓 테스트 중 다른 오류가 발생했다면 무시하고 계속 진행
                logger.warning(f"Socket test failed: {e}, continuing anyway")
            
            # 다양한 pymodbus 버전을 지원하기 위한 서버 시작 방법
            # 상세한 예외 처리를 위해 다양한 방법 시도
            try:
                # 1. pymodbus 3.0.0 버전에 맞게 서버 시작 시도
                logger.info("Attempting to start server with pymodbus 3.0.0 compatible method")
                # pymodbus 3.x에서 framer는 문자열 또는 클래스가 아닌 프레이머 이름을 사용해야 함
                StartTcpServer(
                    context=self.context,
                    identity=identity,
                    address=(self.address, self.port),
                    framer="socket",  # ModbusSocketFramer 대신 "socket" 문자열 사용
                    # 이 호출은 블록하고 이 스레드가 종료될 때까지 리턴하지 않음
                )
            except Exception as e:
                logger.error(f"Error using primary server start method: {e}")
                logger.error(f"Exception type: {type(e).__name__}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                print(f"Error using primary server start method: {e}")
                
                # 2. 대체 시작 방법 시도 (특정 오류가 발생한 경우에만)
                if "argument after ** must be a mapping" in str(e) or \
                   "got multiple values for argument" in str(e) or \
                   "__init__() got an unexpected keyword argument" in str(e):
                    try:
                        logger.info("Attempting alternative server start method")
                        print("Attempting alternative server start method")
                        # 인자 구조를 다르게 시도
                        StartTcpServer(
                            context=self.context,
                            address=(self.address, self.port),
                            framer="socket",  # ModbusSocketFramer 대신 "socket" 문자열 사용
                            identity=identity
                        )
                    except Exception as alt_e:
                        logger.error(f"Error using alternative server start method: {alt_e}")
                        logger.error(f"Traceback: {traceback.format_exc()}")
                        print(f"Error using alternative server start method: {alt_e}")
                        raise
                else:
                    # 특정 오류가 아닌 경우 원래 예외를 다시 발생시킴
                    raise
                
        except Exception as e:
            logger.error(f"Error starting Modbus server: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            print(f"Error starting Modbus server: {e}")
            # 오류 발생 시 시그널 전송 (UI 업데이트를 위해)
            if hasattr(self, 'signals'):
                try:
                    # 에러 정보를 포함한 커스텀 시그널을 만들고 사용할 수도 있음
                    self.signals.server_stopped.emit()
                except Exception as signal_e:
                    logger.error(f"Error emitting signal: {signal_e}")
        finally:
            self.running = False
            self._server_started = False
            self.signals.server_stopped.emit()
            logger.info("Server stopped")
            print("Server stopped")

    def stop(self):
        # 서버 종료 방법
        if self.running and self._server_started:
            try:
                logger.info("Stopping Modbus server...")
                print("Stopping Modbus server...")
                
                # 종료 플래그 설정
                self._stop_requested = True
                self.running = False
                self._server_started = False
                
                # pymodbus 3.0.0 버전에 맞게 서버 종료
                try:
                    # StopServer 함수 호출
                    StopServer()
                    logger.info("Server stopped using StopServer function")
                    print("Server stopped using StopServer function")
                except Exception as e:
                    logger.warning(f"Error using StopServer: {e}")
                    print(f"Error using StopServer: {e}")
                
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
                    print(f"Socket connection for shutdown: {e}")
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
                print("Modbus server stopped successfully")
            except Exception as e:
                logger.error(f"Error stopping server: {e}")
                print(f"Error stopping server: {e}")
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
        self.address_labels = {}  # 주소 라벨 저장용
        self.offset = 0  # 홀딩 레지스터 주소 오프셋 초기값
        
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
                address_label = QLabel(str(i + self.offset))
                address_label.setStyleSheet("padding: 0px;")
                left_layout.addWidget(address_label, i+1, 0)
                self.address_labels[i] = address_label
                
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
                address_label = QLabel(str(i + self.offset))
                address_label.setStyleSheet("padding: 0px;")
                right_layout.addWidget(address_label, i-99, 0)  # i-99로 인덱스 조정
                self.address_labels[i] = address_label
                
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
        print(f"Raw checkbox state: {state}, type: {type(state)}, Qt.Checked: {Qt.CheckState.Checked}")
        
        # 체크박스 상태 확인 - isChecked() 사용
        is_checked = self.checkboxes[addr].isChecked()
        value = 1 if is_checked else 0
        
        self.values[addr] = value
        logger.info(f"Bit changed: {self.register_type}[{addr}] = {value}, checkbox is checked: {is_checked}")
        print(f"Bit changed: {self.register_type}[{addr}] = {value}, checkbox is checked: {is_checked}")
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
            print(f"Register changed: {self.register_type}[{addr}] = {value}")
            
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
            print(f"Register changed: {self.register_type}[{addr}] = {text} (int: {value})")
            
            # 값 변경 시그널 발생
            self.value_changed.emit(self.register_type, addr, value)
        except ValueError as e:
            logger.error(f"Error in on_value_changed: {e}")
            print(f"Error in on_value_changed: {e}")
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
            print(f"Memo changed: {self.register_type}[{addr}] = {text}")
    
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
                print(f"Updated {self.register_type}[{addr}] to {hex_value} from server")
                
    def set_address_offset(self, offset):
        """홀딩 레지스터 주소 오프셋을 설정하고 주소 라벨을 업데이트합니다.
        
        Args:
            offset (int): 적용할 주소 오프셋 값
        """
        if self.register_type != "holding_registers":
            return  # 홀딩 레지스터가 아니면 아무 작업도 하지 않음
            
        self.offset = offset
        
        # 모든 주소 라벨 업데이트
        for addr, label in self.address_labels.items():
            label.setText(str(addr + offset))
            
        logger.info(f"홀딩 레지스터 주소 오프셋 {offset} 적용 완료")
        print(f"홀딩 레지스터 주소 오프셋 {offset} 적용 완료")


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
        
        # 버튼 아이콘 생성
        self.create_button_icons()
        
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
        print("서버 아이콘 생성 및 설정 완료")

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
        
        print("버튼 아이콘 생성 완료")
            
    def load_stylesheet(self):
        """서버 애플리케이션의 스타일시트 로드
        
        내장된 스타일시트를 직접 적용하여 외부 파일 의존성을 제거합니다.
        이는 원래 resources/style.qss 파일에 있던 내용이 직접 코드에 포함된 것입니다.
        """
        try:
            # 내장된 QSS 스타일 정의 (원래 style.qss 파일에서 가져온 내용)
            self.setStyleSheet(EMBEDDED_QSS_STYLE)
            logger.info("내장된 스타일시트 적용 성공")
            print("내장된 스타일시트 적용 성공")
                
        except Exception as e:
            logger.error(f"스타일시트 로드 중 오류 발생: {e}")
            print(f"스타일시트 로드 중 오류 발생: {e}")
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
        
    def init_modbus_store(self, hr_offset=0):
        # 홀딩 레지스터 주소 오프셋 설정
        self.hr_offset = hr_offset
        
        # 홀딩 레지스터 주소 오프셋 적용 로그
        print(f"[DEBUG] 홀딩 레지스터 주소 오프셋 {hr_offset} 적용 완료")
        
        # Create signals object first if not already created
        if not hasattr(self, 'signals'):
            self.signals = ModbusSignals()
        
        # 홀딩 레지스터 오프셋이 있는 경우, 데이터 저장소 크기를 조정
        # 기본 크기 200개(0-199) + 오프셋에 따른 추가 공간
        hr_size = 200
        if hr_offset > 0:
            # 오프셋이 있는 경우, 오프셋 + 기본 크기(200)를 할당
            # 예: 오프셋이 10이면, 0-209까지 총 210개 필요
            hr_size = hr_offset + 200
            logger.info(f"홀딩 레지스터 오프셋 {hr_offset} 적용, 저장소 크기: {hr_size}")
            print(f"홀딩 레지스터 오프셋 {hr_offset} 적용, 저장소 크기: {hr_size}")
            
        # Create data blocks with adjusted sizes
        # Modbus 레지스터는 0부터 시작하지만, 주소가 0부터 99까지인 경우 실제로는 100개의 레지스터가 필요함
        self.store = CustomModbusSlaveContext(
            signals=self.signals,
            di=ModbusSequentialDataBlock(0, [0] * 100),   # Discrete Inputs
            co=ModbusSequentialDataBlock(0, [0] * 100),   # Coils
            hr=ModbusSequentialDataBlock(0, [0] * hr_size),   # Holding Registers (0-199 + offset)
            ir=ModbusSequentialDataBlock(0, [0] * 100),   # Input Registers
            hr_offset=hr_offset                           # Holding Register Offset
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
                    print("스타일시트가 성공적으로 적용되었습니다.")
            else:
                logger.warning(f"스타일시트 파일을 찾을 수 없습니다: {style_path}")
                print(f"스타일시트 파일을 찾을 수 없습니다: {style_path}")
        except Exception as e:
            logger.error(f"스타일시트 적용 중 오류 발생: {e}")
            print(f"스타일시트 적용 중 오류 발생: {e}")
            
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
        conn_layout.addWidget(QLabel("Type:"))
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
        
        # Holding Register Offset
        conn_layout.addWidget(QLabel("HR Offset:"))
        self.hr_offset_edit = QLineEdit("0")
        self.hr_offset_edit.setValidator(QIntValidator(0, 9999))  # 오프셋 유효성 검사 추가
        self.hr_offset_edit.setStyleSheet("border: 1px solid #bec8d1; border-radius: 10px;")
        self.hr_offset_edit.setToolTip("Holding Register 주소 오프셋 (연결 후에는 변경 불가)")
        self.hr_offset_edit.setFixedWidth(80)  # 너비 고정
        conn_layout.addWidget(self.hr_offset_edit)
        
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
        
        # 상태 라벨 추가
        self.status_label = QLabel("Server not running")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setObjectName("status_label")
        self.status_label.setStyleSheet("color: #757575; font-weight: bold;")
        main_layout.addWidget(self.status_label)
        
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
            test_button.setIcon(self.dynamic_icons["holding"])
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
                    print(f"Error getting discrete input values: {e}")
            
            # Update holding registers
            if hasattr(self, 'holding_registers_widget'):
                holding_widget = self.holding_registers_widget
                try:
                    hr_register_count = 200
                    hr_values = self.store.getValues(3, 0, hr_register_count)
                    for addr in range(min(hr_register_count, len(hr_values))):
                        if addr in holding_widget.line_edits:
                            # Skip update if this widget has focus (user is editing)
                            if holding_widget.line_edits[addr].hasFocus():
                                logger.debug(f"Skipping update for holding register {addr} - user is editing")
                                print(f"Skipping update for holding register {addr} - user is editing")
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
                                print(f"Skipping update for input register {addr} - user is editing")
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
                    print(f"Error getting input register values: {e}")
        except Exception as e:
            logger.error(f"Error updating UI from context: {e}")
            print(f"Error updating UI from context: {e}")
    
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
                        print(f"Invalid hex value for register {addr}: {value}, error: {e}")
                        # Set to 0 if invalid
                        self.store.setValues(3, addr, [0])
        except Exception as e:
            logger.error(f"Error updating context from UI: {e}")
            print(f"Error updating context from UI: {e}")
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
            print(f"Register value changed: {register_type}[{addr}] = {value}")
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
            print(f"Register values saved to {self.register_file}")
        except Exception as e:
            logger.error(f"Error saving registers to file: {e}")
            print(f"Error saving registers to file: {e}")
            
    def load_registers_from_file(self):
        """파일에서 레지스터 값 로드"""
        try:
            if not os.path.exists(self.register_file):
                logger.info(f"Register file {self.register_file} does not exist. Using default values.")
                print(f"Register file {self.register_file} does not exist. Using default values.")
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
                            print(f"Invalid hex value for register {addr}: {hex_value}")                
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
            print(f"Register values loaded from {self.register_file}")
        except Exception as e:
            logger.error(f"Error loading registers from file: {e}")
            print(f"Error loading registers from file: {e}")
    
    def toggle_server(self):
        """Start or stop the server"""
        if self.server_running:
            self.stop_server()
            self.connect_button.setText("Connect")
            self.connect_button.setObjectName("connect_button")
            self.connect_button.setIcon(self.dynamic_icons["start"])
        else:
            self.start_server()
            # 버튼 텍스트는 서버가 실제로 시작된 후에 변경됨
            self.connect_button.setText("Disconnect")
            self.connect_button.setObjectName("disconnect_button")
            self.connect_button.setIcon(self.dynamic_icons["stop"])
            
    def start_server(self):
        """Start the Modbus server"""
        if self.server_thread and self.server_thread.running:
            return
        
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
                print("Port 502 typically requires administrator privileges on Windows")
                # 관리자 권한 검사 (Windows)
                try:
                    import ctypes
                    is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
                    if not is_admin:
                        logger.warning("Not running as administrator - port 502 may fail to bind")
                        print("Not running as administrator - port 502 may fail to bind")
                        # 관리자 권한 없이 502 포트 사용 시 경고 메시지 표시
                        self.status_label.setText("⚠️ Warning: Port 502 may require administrator privileges")
                        self.status_label.setStyleSheet("color: #FFC107; font-weight: bold;") # 노란색 경고색
                except Exception as e:
                    logger.warning(f"Could not check admin status: {e}")
            
            # 홀딩 레지스터 오프셋 값 가져오기
            try:
                hr_offset = int(self.hr_offset_edit.text())
            except ValueError:
                hr_offset = 0
                self.hr_offset_edit.setText("0")
                logger.warning("오프셋 값이 유효하지 않아 0으로 초기화합니다.")
                print("오프셋 값이 유효하지 않아 0으로 초기화합니다.")
            
            # Reset the store to ensure we're starting fresh
            # This helps prevent issues with stale data or callbacks
            # Pass the holding register offset to the store
            self.init_modbus_store(hr_offset)
            
            # 저장된 레지스터 값 로드
            self.load_registers_from_file()
            
            # 홀딩 레지스터 오프셋 적용
            if hasattr(self, 'holding_registers_widget'):
                self.holding_registers_widget.set_address_offset(hr_offset)
            
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
            logger.info(f"Attempting to start server at {address}:{port} with HR offset {hr_offset}")
            print(f"Attempting to start server at {address}:{port} with HR offset {hr_offset}")
        except Exception as e:
            logger.error(f"Failed to start server: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            print(f"Failed to start server: {e}")
            
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
            
            # QThread 종료 예외 방지를 위해 wait() 호출
            try:
                if self.server_thread and self.server_thread.isRunning():
                    logger.info("Waiting for server thread to finish...")
                    self.server_thread.wait(2000)  # 최대 2초 대기
                    if self.server_thread.isRunning():
                        logger.warning("Thread still running after wait period")
            except Exception as e:
                logger.error(f"Error waiting for thread: {e}")
            
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
        self.connect_button.setIcon(self.stop_icon)
        self.connect_button.setStyleSheet("")
        
        # 서버 시작 후 주소, 포트, 오프셋 입력 비활성화
        self.address_edit.setReadOnly(True)
        self.port_edit.setReadOnly(True)
        self.hr_offset_edit.setReadOnly(True)
        
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
        
        # 서버 중지 후 주소, 포트, 오프셋 입력 다시 활성화
        self.address_edit.setReadOnly(False)
        self.port_edit.setReadOnly(False)
        self.hr_offset_edit.setReadOnly(False)
        
        # 상태바 초기화
        self.statusBar().clearMessage()
        
        # 오프셋 값이 있는 경우 상태 표시
        try:
            hr_offset = int(self.hr_offset_edit.text())
            if hr_offset > 0:
                self.status_label.setText(f"Server stopped. HR offset: {hr_offset}")
                logger.info(f"Server stopped with HR offset: {hr_offset}")
            else:
                self.status_label.setText("Server stopped.")
                logger.info("Server stopped.")
        except ValueError:
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
                
                # Apply offset adjustment for holding registers
                actual_address = address
                if hasattr(self, 'hr_offset') and self.hr_offset > 0:
                    # Check if address is valid after offset adjustment
                    if address >= self.hr_offset:
                        actual_address = address - self.hr_offset
                        logger.info(f"Applying offset {self.hr_offset}: client address {address} -> UI address {actual_address}")
                    else:
                        logger.warning(f"Client address {address} is less than offset {self.hr_offset}, ignoring")
                        return
                
                if actual_address in widget.line_edits:
                    # Format value as 4-digit hex string
                    hex_value = f"{value:04X}"
                    logger.info(f"Client wrote to holding register {address} (UI addr: {actual_address}): {value} (hex: {hex_value})")
                    # Block signals temporarily to prevent feedback loop
                    old_state = widget.line_edits[actual_address].blockSignals(True)
                    widget.line_edits[actual_address].setText(hex_value)
                    widget.line_edits[actual_address].blockSignals(old_state)
                    widget.values[actual_address] = hex_value
                else:
                    logger.warning(f"No UI element found for holding register at address {actual_address} (client address: {address})")
                    
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
            print("PyInstaller 번들에서 실행 중입니다.")
            
            # 워킹 디렉토리 설정
            # 일부 환경에서는 워킹 디렉토리가 임시 폴더로 설정되어 문제 발생
            bundle_dir = getattr(sys, '_MEIPASS', os.path.abspath(os.path.dirname(__file__)))
            os.chdir(bundle_dir)
            logger.info(f"작업 디렉토리를 번들 디렉토리로 변경: {bundle_dir}")
            print(f"작업 디렉토리를 번들 디렉토리로 변경: {bundle_dir}")
        
        # 내장된 QSS 스타일 설정
        app.setStyleSheet(EMBEDDED_QSS_STYLE)
        logger.info("애플리케이션 수준에서 QSS 스타일 적용")
        print("애플리케이션 수준에서 QSS 스타일 적용")
        
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
        print(f"애플리케이션 실행 중 오류 발생: {e}")
        DEBUG_MODE = True
        if DEBUG_MODE:
            import traceback
            traceback.print_exc()
        sys.exit(1)

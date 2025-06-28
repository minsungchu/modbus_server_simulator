#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Modbus TCP 클라이언트 시뮬레이터

이 프로그램은 Modbus TCP 프로토콜을 사용하여 서버에 연결하고 통신하는 클라이언트를 시뮬레이션합니다.
주요 기능:
- Modbus TCP 서버 연결 및 연결 해제
- 코일, 디스크릿 입력, 홀딩 레지스터, 입력 레지스터 값 읽기
- 코일, 홀딩 레지스터 값 쓰기
- 레지스터 값 저장 및 불러오기
- 레지스터 값 자동 업데이트
"""

import sys
import os
import logging
import json
import time
import traceback
from datetime import datetime

# PySide6 관련 임포트
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QLineEdit, QPushButton, QGroupBox, QCheckBox, 
    QGridLayout, QScrollArea, QMessageBox, QComboBox, QStatusBar, QTextEdit
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject, Slot, QRegularExpression, QPoint, QSize
from PySide6.QtGui import QIcon, QIntValidator, QRegularExpressionValidator, QPixmap, QPainter, QColor, QLinearGradient, QBrush, QPen, QFont, QPolygon

# Pymodbus 관련 임포트
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException, ConnectionException

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ModbusClientSim")

# 파일 핸들러 추가
file_handler = logging.FileHandler('modbus_client.log')
file_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# 콘솔 핸들러 추가 (더 자세한 로그 출력)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

logger.info("Modbus TCP 클라이언트 시뮬레이터 로깅 시스템 초기화 완료")


class ClientSignals(QObject):
    """Modbus 클라이언트 시그널 클래스
    
    클라이언트의 상태 변경 및 데이터 수신 시 시그널을 발생시켜 UI 업데이트를 처리합니다.
    연결 상태, 에러 발생, 데이터 수신 등의 이벤트를 처리합니다.
    """
    connected = Signal()  # 연결 성공 시그널
    disconnected = Signal()  # 연결 해제 시그널
    error = Signal(str)  # 에러 발생 시그널
    data_received = Signal(str, int, int)  # 데이터 수신 시그널 (register_type, address, value)
    status_update = Signal(str)  # 상태 업데이트 시그널


class ModbusClientSimulator(QMainWindow):
    """Modbus TCP 클라이언트 시뮬레이터 메인 클래스
    
    사용자 인터페이스를 제공하고 Modbus TCP 클라이언트의 동작을 제어합니다.
    서버 연결, 레지스터 값 읽기/쓰기, 자동 업데이트 등의 기능을 제공합니다.
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Modbus TCP Client Simulator")
        
        # 클라이언트 객체 초기화
        self.client = None
        self.connected = False
        
        # 시그널 객체 초기화
        self.signals = ClientSignals()
        self.signals.connected.connect(self.on_connected)
        self.signals.disconnected.connect(self.on_disconnected)
        self.signals.error.connect(self.on_error)
        self.signals.status_update.connect(self.update_status)
        
        # 레지스터 값 저장용 딕셔너리
        self.register_values = {
            "coils": {},
            "discrete_inputs": {},
            "holding_registers": {},
            "input_registers": {}
        }
        
        # 메모 저장용 딕셔너리
        self.register_memos = {
            "holding_registers": {}
        }
        
        # 레지스터 값 저장 파일 경로 설정
        self.register_file = "modbus_client_registers.json"
        
        # 자동 저장 타이머 설정
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save_registers_to_file)
        self.save_pending = False
        
        # 레지스터 그룹 및 위젯 저장용 딕셔너리
        self.register_groups = {}
        self.register_widgets = {}
        
        # 타이머 초기화 (주기적 데이터 갱신용)
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_registers)
        
        # UI 초기화
        self.init_ui()
        
        logger.info("Modbus TCP Client Simulator initialized")
        
    def apply_client_style(self, widget):
        """
        클라이언트 애플리케이션의 스타일시트 적용
        
        클라이언트는 밝은 테마(라이트 그린)를 사용하여 서버와 시각적으로 구분됩니다.
        """
        self.setStyleSheet("""
            QMainWindow {
                background-color: #e8f5e9;
            }
            QPushButton {
                background-color: #81c784;
                border: none;
                border-radius: 10px;
                padding: 10px;
                color: #1b5e20;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #66bb6a;
            }
            QPushButton:pressed {
                background-color: #4caf50;
            }
            QLineEdit, QSpinBox {
                background-color: #c8e6c9;
                border: none;
                border-radius: 10px;
                padding: 5px;
                color: #1b5e20;
            }
            QCheckBox {
                color: #1b5e20;
            }
            QGroupBox {
                border: 1px solid #a5d6a7;
                border-radius: 10px;
                margin-top: 10px;
                padding-top: 10px;
                color: #1b5e20;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 5px;
                color: #1b5e20;
                font-weight: bold;
            }
            QLabel {
                color: #1b5e20;
            }
            QComboBox {
                background-color: #c8e6c9;
                border-radius: 10px;
                padding: 5px;
                color: #1b5e20;
            }
            QComboBox QAbstractItemView {
                background-color: #c8e6c9;
                color: #1b5e20;
            }
        """)
        
    def init_ui(self):
        """사용자 인터페이스 초기화"""
        # 중앙 위젯 설정
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # 애플리케이션 아이콘 설정
        self.create_client_icon()
        
        # 타이틀 라벨
        title_label = QLabel("Modbus TCP Client Simulator")
        title_label.setObjectName("title_label")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setFont(QFont("Arial", 14, QFont.Bold))
        main_layout.addWidget(title_label)
        
        # 연결 설정 그룹
        self.init_connection_settings(main_layout)
        
        # 레지스터 그룹 초기화
        self.init_register_groups(main_layout)
        
        # 상태바 초기화
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")
        
        # 스타일 적용
        self.apply_client_style(central_widget)
        
        # 창 크기 설정
        self.resize(1000, 800)
        
    def create_client_icon(self):
        """
        클라이언트 아이콘 생성 및 설정
        
        서버와 구분되는 클라이언트용 아이콘을 생성하고 애플리케이션에 설정합니다.
        """
        # 아이콘 생성
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 라이트 그린 배경의 둥근 사각형
        painter.setBrush(QColor("#c8e6c9"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(0, 0, 64, 64, 15, 15)
        
        # 텍스트 설정
        painter.setPen(QColor("white"))
        font = QFont()
        font.setPointSize(30)
        font.setBold(True)
        painter.setFont(font)
        
        # 가운데에 'C' 텍스트 그리기
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "C")
        
        painter.end()
        
        # 아이콘 설정
        icon = QIcon(pixmap)
        self.setWindowIcon(icon)
        
        logger.info("클라이언트 아이콘 생성 및 설정 완료")
        
        # 아이콘 파일로 저장 (선택적)
        try:
            if not os.path.exists("resources"):
                os.makedirs("resources")
            pixmap.save("resources/client_icon.png")
            logger.info("클라이언트 아이콘 파일 저장 완료: resources/client_icon.png")
            
            # 드롭다운 화살표 아이콘 생성 및 저장
            self.create_dropdown_arrow_icon()
        except Exception as e:
            logger.warning(f"아이콘 파일 저장 실패: {e}")
            # 아이콘 저장 실패는 중요한 오류가 아니므로 계속 진행
            
    def create_dropdown_arrow_icon(self):
        """
        드롭다운 화살표 아이콘 생성 및 저장
        
        콤보박스에 사용할 드롭다운 화살표 아이콘을 생성하고 저장합니다.
        """
        try:
            # 아이콘 생성
            pixmap = QPixmap(12, 12)
            pixmap.fill(Qt.transparent)
            
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            
            # 화살표 그리기
            painter.setPen(QPen(QColor("#1b5e20"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(QColor("#1b5e20"))
            
            # 아래쪽을 가리키는 삼각형 그리기
            points = [QPoint(2, 4), QPoint(10, 4), QPoint(6, 8)]
            painter.drawPolygon(QPolygon(points))
            
            painter.end()
            
            # 아이콘 파일로 저장
            pixmap.save("resources/dropdown_arrow.png")
            logger.info("드롭다운 화살표 아이콘 파일 저장 완료: resources/dropdown_arrow.png")
        except Exception as e:
            logger.warning(f"드롭다운 화살표 아이콘 파일 저장 실패: {e}")
            # 아이콘 저장 실패는 중요한 오류가 아니므로 계속 진행
            
    def create_connection_button_icons(self):
        """
        연결 버튼용 아이콘 생성
        
        연결 및 연결 해제 상태에 사용할 아이콘을 생성합니다.
        """
        try:
            # 연결 아이콘 생성 (초록색 플러스)
            connect_pixmap = QPixmap(16, 16)
            connect_pixmap.fill(Qt.transparent)
            
            connect_painter = QPainter(connect_pixmap)
            connect_painter.setRenderHint(QPainter.Antialiasing)
            
            # 플러스 기호 그리기
            connect_painter.setPen(QPen(QColor("#1b5e20"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            connect_painter.drawLine(8, 4, 8, 12)  # 세로선
            connect_painter.drawLine(4, 8, 12, 8)  # 가로선
            
            connect_painter.end()
            
            # 연결 해제 아이콘 생성 (빨간색 X)
            disconnect_pixmap = QPixmap(16, 16)
            disconnect_pixmap.fill(Qt.transparent)
            
            disconnect_painter = QPainter(disconnect_pixmap)
            disconnect_painter.setRenderHint(QPainter.Antialiasing)
            
            # X 기호 그리기
            disconnect_painter.setPen(QPen(QColor("white"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            disconnect_painter.drawLine(4, 4, 12, 12)  # 왼쪽 위에서 오른쪽 아래로
            disconnect_painter.drawLine(4, 12, 12, 4)  # 왼쪽 아래에서 오른쪽 위으로
            
            disconnect_painter.end()
            
            # 아이콘 저장
            if not os.path.exists("resources"):
                os.makedirs("resources")
                
            connect_pixmap.save("resources/connect_icon.png")
            disconnect_pixmap.save("resources/disconnect_icon.png")
            
            # 아이콘 설정
            self.connect_icon = QIcon("resources/connect_icon.png")
            self.disconnect_icon = QIcon("resources/disconnect_icon.png")
            
            logger.info("연결 버튼 아이콘 생성 및 저장 완료")
        except Exception as e:
            logger.warning(f"연결 버튼 아이콘 생성 실패: {e}")
            # 아이콘 생성 실패는 중요한 오류가 아니므로 계속 진행
        
    def init_connection_settings(self, parent_layout):
        """연결 설정 UI 초기화"""
        # 연결 설정 그룹
        connection_group = QGroupBox("Connection Settings")
        connection_layout = QGridLayout(connection_group)
        
        # 호스트 입력
        connection_layout.addWidget(QLabel("Host:"), 0, 0)
        self.host_input = QLineEdit("localhost")
        connection_layout.addWidget(self.host_input, 0, 1)
        
        # 포트 입력
        connection_layout.addWidget(QLabel("Port:"), 0, 2)
        self.port_input = QLineEdit("502")
        self.port_input.setValidator(QIntValidator(1, 65535))
        connection_layout.addWidget(self.port_input, 0, 3)
        
        # 연결/연결 해제 토글 버튼
        self.connection_button = QPushButton("Connect")
        self.connection_button.setObjectName("connection_button")
        self.connection_button.setCheckable(True)  # 토글 가능하도록 설정
        self.connection_button.clicked.connect(self.toggle_connection)
        
        # 버튼 아이콘 생성 및 설정
        self.create_connection_button_icons()
        
        # 초기 상태는 연결되지 않은 상태
        self.connection_button.setChecked(False)
        self.connection_button.setIcon(self.connect_icon)
        self.connection_button.setIconSize(QSize(16, 16))
        
        # 버튼 텍스트와 아이콘 함께 표시
        self.connection_button.setStyleSheet("""
            QPushButton {
                background-color: #c8e6c9;
                color: #1b5e20;
                border-radius: 10px;
                padding: 5px 10px;
                font-weight: bold;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #a5d6a7;
            }
            QPushButton:pressed {
                background-color: #81c784;
            }
            QPushButton:checked {
                background-color: #ef5350;
                color: white;
            }
            QPushButton:checked:hover {
                background-color: #e53935;
            }
        """)
        
        connection_layout.addWidget(self.connection_button, 0, 4, 1, 2)  # 두 칸 차지
        
        # Range Read 그룹
        range_group = QGroupBox("Range Read")
        range_layout = QGridLayout(range_group)
        
        # 첫번째 줄 - 레지스터 타입
        range_layout.addWidget(QLabel("Register Type:"), 0, 0)
        self.range_reg_type = QComboBox()
        self.range_reg_type.addItems(["coils", "discrete_inputs", "holding_registers", "input_registers"])
        # 디폴트로 holding_registers 선택
        self.range_reg_type.setCurrentText("holding_registers")
        # 콤보박스 스타일 개선
        self.range_reg_type.setStyleSheet("""
            QComboBox {
                background-color: #c8e6c9;
                border-radius: 10px;
                padding: 5px;
                color: #1b5e20;
                min-width: 150px;
                font-weight: bold;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 20px;
                border-left-width: 1px;
                border-left-color: #81c784;
                border-left-style: solid;
                border-top-right-radius: 10px;
                border-bottom-right-radius: 10px;
            }
            QComboBox::down-arrow {
                image: url(resources/dropdown_arrow.png);
                width: 12px;
                height: 12px;
            }
            QComboBox QAbstractItemView {
                background-color: #c8e6c9;
                color: #1b5e20;
                selection-background-color: #81c784;
                selection-color: #1b5e20;
                border-radius: 5px;
            }
        """)
        range_layout.addWidget(self.range_reg_type, 0, 1)
        
        # 첫번째 줄 - 시작 주소
        range_layout.addWidget(QLabel("Start Address:"), 0, 2)
        self.range_start_addr = QLineEdit("0")
        self.range_start_addr.setValidator(QIntValidator(0, 65535))
        range_layout.addWidget(self.range_start_addr, 0, 3)
        
        # 첫번째 줄 - 개수
        range_layout.addWidget(QLabel("Count:"), 0, 4)
        self.range_count = QLineEdit("10")
        self.range_count.setValidator(QIntValidator(1, 125))
        range_layout.addWidget(self.range_count, 0, 5)
        
        # 첫번째 줄 - 읽기 버튼
        self.range_read_button = QPushButton("Read Range")
        self.range_read_button.clicked.connect(self.read_register_range)
        self.range_read_button.setEnabled(False)  # 연결 전에는 비활성화
        range_layout.addWidget(self.range_read_button, 0, 6)
        
        # 두번째 줄 - 결과 표시 영역 (한 줄로 간결하게)
        self.range_result_label = QLabel("결과가 여기에 표시됩니다")
        self.range_result_label.setStyleSheet("background-color: #f0f0f0; padding: 3px; border-radius: 5px;")
        range_layout.addWidget(self.range_result_label, 1, 0, 1, 7)
        
        parent_layout.addWidget(connection_group)
        parent_layout.addWidget(range_group)
        
    def toggle_connection(self):
        """
        연결/연결 해제 토글
        
        버튼의 체크 상태에 따라 서버에 연결하거나 연결을 해제합니다.
        """
        if self.connection_button.isChecked():
            # 버튼이 체크되어 있으면 연결 시도
            self.connect_to_server()
        else:
            # 버튼이 체크 해제되어 있으면 연결 해제 시도
            self.disconnect_from_server()
    
    def connect_to_server(self):
        """서버에 연결"""
        try:
            host = self.host_input.text()
            port = int(self.port_input.text())
            
            logger.info(f"서버에 연결 시도: {host}:{port}")
            
            # 클라이언트 객체 생성
            self.client = ModbusTcpClient(host, port)
            
            # 연결 시도
            if self.client.connect():
                self.connected = True
                self.signals.connected.emit()
                self.signals.status_update.emit(f"서버에 연결됨: {host}:{port}")
                logger.info(f"서버 연결 성공: {host}:{port}")
                
                # 버튼 상태 변경
                self.connection_button.setText("Disconnect")
                self.connection_button.setIcon(self.disconnect_icon)
                self.connection_button.setChecked(True)
            else:
                self.signals.error.emit("서버에 연결할 수 없습니다.")
                logger.error(f"서버 연결 실패: {host}:{port}")
                
                # 연결 실패 시 버튼 상태 복원
                self.connection_button.setChecked(False)
                self.connection_button.setText("Connect")
                self.connection_button.setIcon(self.connect_icon)
        except Exception as e:
            self.signals.error.emit(f"연결 오류: {str(e)}")
            logger.error(f"연결 오류: {str(e)}")
            
            # 오류 발생 시 버튼 상태 복원
            self.connection_button.setChecked(False)
            self.connection_button.setText("Connect")
            self.connection_button.setIcon(self.connect_icon)
    
    def disconnect_from_server(self):
        """서버에서 연결 해제"""
        if self.client and self.connected:
            try:
                self.client.close()
                logger.info("서버에서 연결 해제")
            except Exception as e:
                logger.error(f"연결 해제 오류: {str(e)}")
            finally:
                self.connected = False
                self.client = None
                self.signals.disconnected.emit()
                self.signals.status_update.emit("서버에서 연결 해제됨")
                
                # 버튼 상태 변경
                self.connection_button.setText("Connect")
                self.connection_button.setIcon(self.connect_icon)
                self.connection_button.setChecked(False)
                
    def on_connected(self):
        """연결 성공 시 호출되는 슬롯"""
        # 연결 버튼 상태 업데이트
        self.connection_button.setText("Disconnect")
        self.connection_button.setIcon(self.disconnect_icon)
        self.connection_button.setChecked(True)
        self.range_read_button.setEnabled(True)  # 범위 읽기 버튼 활성화
        
        # 초기 데이터 읽기
        self.update_registers()
        
    def on_disconnected(self):
        """연결 해제 시 호출되는 슬롯"""
        # 연결 버튼 상태 업데이트
        self.connection_button.setText("Connect")
        self.connection_button.setIcon(self.connect_icon)
        self.connection_button.setChecked(False)
        self.range_read_button.setEnabled(False)  # 범위 읽기 버튼 비활성화
        
    def on_error(self, error_msg):
        """에러 발생 시 호출되는 슬롯"""
        QMessageBox.critical(self, "Error", error_msg)
        self.signals.status_update.emit(f"Error: {error_msg}")
        
    def update_status(self, status_msg):
        """상태바 업데이트"""
        self.status_bar.showMessage(status_msg)
        
    def on_memo_changed(self, register_type, addr, text):
        """메모 변경 처리"""
        logger.info(f"Memo changed for {register_type}[{addr}]: {text}")
        
        # 현재는 holding_registers만 메모 지원
        if register_type == "holding_registers":
            # 메모 저장
            if addr not in self.register_memos[register_type]:
                self.register_memos[register_type][addr] = {}
            self.register_memos[register_type][addr] = text
            
            # 저장 예약
            self.schedule_save()
            
    def schedule_save(self):
        """변경 사항 저장 예약"""
        if not self.save_pending:
            self.save_pending = True
            self.save_timer.start(2000)  # 2초 후 저장
            
    def save_registers_to_file(self):
        """레지스터 값과 메모를 JSON 파일로 저장"""
        try:
            # 저장할 데이터 구성
            data = {
                "values": self.register_values,
                "memos": self.register_memos
            }
            
            # JSON 파일로 저장
            with open(self.register_file, "w") as f:
                json.dump(data, f, indent=4)
                
            logger.info(f"Registers and memos saved to {self.register_file}")
            self.save_pending = False
        except Exception as e:
            logger.error(f"Error saving registers to file: {e}")
            
    def load_registers_from_file(self):
        """레지스터 값과 메모를 JSON 파일에서 로드"""
        try:
            if os.path.exists(self.register_file):
                with open(self.register_file, "r") as f:
                    data = json.load(f)
                    
                # 값과 메모 로드
                if "values" in data:
                    self.register_values = data["values"]
                if "memos" in data:
                    self.register_memos = data["memos"]
                    
                logger.info(f"Registers and memos loaded from {self.register_file}")
                
                # UI 업데이트
                self.update_ui_from_loaded_data()
        except Exception as e:
            logger.error(f"Error loading registers from file: {e}")
            
    def update_ui_from_loaded_data(self):
        """로드된 데이터로 UI 업데이트"""
        # 홀딩 레지스터 메모 업데이트
        if "holding_registers" in self.register_widgets and "holding_registers" in self.register_memos:
            widget = self.register_widgets["holding_registers"]
            for addr, memo in self.register_memos["holding_registers"].items():
                addr_int = int(addr) if isinstance(addr, str) else addr
                if addr_int in widget.memo_edits:
                    widget.memo_edits[addr_int].setText(memo)
        
    def toggle_auto_update(self):
        """자동 업데이트 시작/중지 토글"""
        if self.update_timer.isActive():
            self.update_timer.stop()
            self.update_button.setText("Start Auto Update")
            logger.info("Auto update stopped")
            self.signals.status_update.emit("Auto update stopped")
        else:
            try:
                interval = int(self.update_interval_input.text())
                self.update_timer.start(interval)
                self.update_button.setText("Stop Auto Update")
                logger.info(f"Auto update started with interval {interval}ms")
                self.signals.status_update.emit(f"Auto update started ({interval}ms)")
            except ValueError:
                self.signals.error.emit("Invalid update interval")
                logger.error("Invalid update interval")
                
    def init_register_groups(self, parent_layout):
        """레지스터 그룹 UI 초기화"""
        # 레지스터 그룹 컨테이너
        register_container = QWidget()
        register_layout = QVBoxLayout(register_container)
        
        # 레지스터 타입 체크박스 그룹
        checkbox_group = QGroupBox("Register Types")
        checkbox_layout = QHBoxLayout(checkbox_group)
        
        # 레지스터 타입 체크박스 생성
        self.register_checkboxes = {}
        register_types = [
            ("coils", "Coils (Read/Write)"),
            ("discrete_inputs", "Discrete Inputs (Read-only)"),
            ("holding_registers", "Holding Registers (Read/Write)"),
            ("input_registers", "Input Registers (Read-only)")
        ]
        
        for reg_type, reg_label in register_types:
            checkbox = QCheckBox(reg_label)
            checkbox.setChecked(reg_type == "holding_registers")  # 기본적으로 홀딩 레지스터만 표시
            checkbox.stateChanged.connect(lambda state, rt=reg_type: self.toggle_register_group(rt, state))
            checkbox_layout.addWidget(checkbox)
            self.register_checkboxes[reg_type] = checkbox
        
        register_layout.addWidget(checkbox_group)
        
        # 각 레지스터 타입에 대한 그룹 생성
        self.create_register_group("coils", "Coils", 0, 99, True, register_layout)
        self.create_register_group("discrete_inputs", "Discrete Inputs", 0, 99, False, register_layout)
        self.create_register_group("holding_registers", "Holding Registers", 0, 199, True, register_layout)
        self.create_register_group("input_registers", "Input Registers", 0, 99, False, register_layout)
        
        # 초기 가시성 설정
        for reg_type in self.register_groups:
            self.register_groups[reg_type].setVisible(reg_type == "holding_registers")
        
        parent_layout.addWidget(register_container)
        
    def create_register_group(self, reg_type, title, start_addr, end_addr, writable, parent_layout):
        """레지스터 그룹 생성"""
        group = QGroupBox(title)
        group_layout = QVBoxLayout(group)
        
        # 레지스터 위젯 생성
        register_widget = RegisterWidget(reg_type, start_addr, end_addr, writable, self)
        self.register_widgets[reg_type] = register_widget
        
        # 메모 변경 시그널 연결
        register_widget.memo_changed.connect(self.on_memo_changed)
        
        group_layout.addWidget(register_widget)
        
        # 그룹 저장 및 추가
        self.register_groups[reg_type] = group
        parent_layout.addWidget(group)
        
    def toggle_register_group(self, reg_type, state):
        """레지스터 그룹 표시 여부 토글"""
        if reg_type in self.register_groups:
            self.register_groups[reg_type].setVisible(state == Qt.Checked)
            
    def update_registers(self):
        """레지스터 값 업데이트 (서버에서 읽기)"""
        if not self.client or not self.connected:
            return
            
        try:
            # 현재 표시되는 레지스터 그룹만 업데이트
            for reg_type, checkbox in self.register_checkboxes.items():
                if checkbox.isChecked():
                    self.read_register_values(reg_type)
                    
            self.signals.status_update.emit("Registers updated successfully")
        except Exception as e:
            self.signals.error.emit(f"Failed to update registers: {str(e)}")
            logger.error(f"Register update error: {str(e)}")
            logger.debug(traceback.format_exc())
            
    def read_register_values(self, reg_type):
        """특정 타입의 레지스터 값 읽기"""
        widget = self.register_widgets.get(reg_type)
        if not widget:
            return
            
        start_addr = widget.start_addr
        count = widget.end_addr - start_addr + 1
        
        # 레지스터 범위가 너무 크면 나눠서 읽기
        max_registers_per_read = 125  # Modbus 프로토콜 제한
        
        try:
            if reg_type == "coils":
                # 코일은 최대 2000개까지 한번에 읽을 수 있음
                result = self.client.read_coils(start_addr, count)
                if result.isError():
                    raise Exception(f"Modbus error: {result}")
                widget.update_values(result.bits)
                
            elif reg_type == "discrete_inputs":
                result = self.client.read_discrete_inputs(start_addr, count)
                if result.isError():
                    raise Exception(f"Modbus error: {result}")
                widget.update_values(result.bits)
                
            elif reg_type == "holding_registers":
                # 레지스터 수가 많으면 나눠서 읽기
                if count > max_registers_per_read:
                    all_registers = []
                    for i in range(0, count, max_registers_per_read):
                        batch_count = min(max_registers_per_read, count - i)
                        batch_start = start_addr + i
                        result = self.client.read_holding_registers(batch_start, batch_count)
                        if result.isError():
                            raise Exception(f"Modbus error: {result}")
                        all_registers.extend(result.registers)
                    widget.update_values(all_registers)
                else:
                    result = self.client.read_holding_registers(start_addr, count)
                    if result.isError():
                        raise Exception(f"Modbus error: {result}")
                    widget.update_values(result.registers)
                
            elif reg_type == "input_registers":
                # 레지스터 수가 많으면 나눠서 읽기
                if count > max_registers_per_read:
                    all_registers = []
                    for i in range(0, count, max_registers_per_read):
                        batch_count = min(max_registers_per_read, count - i)
                        batch_start = start_addr + i
                        result = self.client.read_input_registers(batch_start, batch_count)
                        if result.isError():
                            raise Exception(f"Modbus error: {result}")
                        all_registers.extend(result.registers)
                    widget.update_values(all_registers)
                else:
                    result = self.client.read_input_registers(start_addr, count)
                    if result.isError():
                        raise Exception(f"Modbus error: {result}")
                    widget.update_values(result.registers)
                
            logger.info(f"Read {reg_type} values from server successfully")
        except Exception as e:
            self.signals.error.emit(f"Failed to read {reg_type}: {str(e)}")
            logger.error(f"Error reading {reg_type}: {str(e)}")
            logger.debug(traceback.format_exc())
            
    def read_register(self, reg_type, address):
        """특정 주소의 레지스터 값 읽기"""
        if not self.client or not self.connected:
            self.signals.error.emit("Not connected to server")
            return None
            
        try:
            if reg_type == "coils":
                result = self.client.read_coils(address, 1)
                if result.isError():
                    raise Exception(f"Modbus error: {result}")
                value = result.bits[0]
                logger.info(f"Read coil {address} = {value}")
                return value
                
            elif reg_type == "discrete_inputs":
                result = self.client.read_discrete_inputs(address, 1)
                if result.isError():
                    raise Exception(f"Modbus error: {result}")
                value = result.bits[0]
                logger.info(f"Read discrete input {address} = {value}")
                return value
                
            elif reg_type == "holding_registers":
                result = self.client.read_holding_registers(address, 1)
                if result.isError():
                    raise Exception(f"Modbus error: {result}")
                value = result.registers[0]
                logger.info(f"Read holding register {address} = {value}")
                return value
                
            elif reg_type == "input_registers":
                result = self.client.read_input_registers(address, 1)
                if result.isError():
                    raise Exception(f"Modbus error: {result}")
                value = result.registers[0]
                logger.info(f"Read input register {address} = {value}")
                return value
                
            else:
                self.signals.error.emit(f"Unknown register type: {reg_type}")
                return None
                
        except Exception as e:
            self.signals.error.emit(f"Failed to read {reg_type} at {address}: {str(e)}")
            logger.error(f"Error reading {reg_type} at {address}: {str(e)}")
            logger.debug(traceback.format_exc())
            return None
            
    def write_register_value(self, reg_type, address, value):
        """레지스터 값 쓰기"""
        if not self.client or not self.connected:
            self.signals.error.emit("Not connected to server")
            return False
            
        try:
            if reg_type == "coils":
                result = self.client.write_coil(address, bool(value))
                if result.isError():
                    raise Exception(f"Modbus error: {result}")
                logger.info(f"Wrote coil {address} = {value}")
                return True
                
            elif reg_type == "holding_registers":
                result = self.client.write_register(address, value)
                if result.isError():
                    raise Exception(f"Modbus error: {result}")
                logger.info(f"Wrote holding register {address} = {value}")
                return True
                
            else:
                self.signals.error.emit(f"Cannot write to read-only register type: {reg_type}")
                return False
                
        except Exception as e:
            self.signals.error.emit(f"Failed to write to {reg_type}: {str(e)}")
            logger.error(f"Error writing to {reg_type}: {str(e)}")
            logger.debug(traceback.format_exc())
            return False
            
    def read_register_range(self):
        """특정 범위의 레지스터 값 읽기"""
        if not self.client or not self.connected:
            self.signals.error.emit("Not connected to server")
            return
            
        try:
            # 입력값 가져오기
            reg_type = self.range_reg_type.currentText()
            start_addr = int(self.range_start_addr.text())
            count = int(self.range_count.text())
            
            # 최대 개수 제한
            max_count = 125  # Modbus 프로토콜 제한
            if count > max_count:
                self.signals.error.emit(f"Count exceeds maximum allowed ({max_count})")
                return
                
            # 레지스터 타입에 따라 읽기
            if reg_type == "coils":
                result = self.client.read_coils(start_addr, count)
                if result.isError():
                    raise Exception(f"Modbus error: {result}")
                values = result.bits
                is_bit = True
                
            elif reg_type == "discrete_inputs":
                result = self.client.read_discrete_inputs(start_addr, count)
                if result.isError():
                    raise Exception(f"Modbus error: {result}")
                values = result.bits
                is_bit = True
                
            elif reg_type == "holding_registers":
                result = self.client.read_holding_registers(start_addr, count)
                if result.isError():
                    raise Exception(f"Modbus error: {result}")
                values = result.registers
                is_bit = False
                
            elif reg_type == "input_registers":
                result = self.client.read_input_registers(start_addr, count)
                if result.isError():
                    raise Exception(f"Modbus error: {result}")
                values = result.registers
                is_bit = False
            
            # 해당 레지스터 위젯 업데이트 (중요: 위젯이 존재하는 경우에만)
            updated_count = 0
            if reg_type in self.register_widgets:
                widget = self.register_widgets[reg_type]
                # 위젯의 범위를 확인하여 가능한 경우에만 업데이트
                widget_start = widget.start_addr
                widget_end = widget.end_addr
                
                # 읽은 값을 위젯에 적용할 배열 생성
                update_values = [0] * (widget_end - widget_start + 1)
                
                # 읽은 값을 위젯 범위에 맞게 적용
                for i, value in enumerate(values):
                    addr = start_addr + i
                    if widget_start <= addr <= widget_end:
                        # 위젯 범위 내에 있는 값만 업데이트
                        update_values[addr - widget_start] = value
                        updated_count += 1
                        
                # 위젯 업데이트
                widget.update_values(update_values)
            
            # 결과 표시 (한 줄로 간결하게)
            if updated_count > 0:
                self.range_result_label.setText(f"Read {len(values)} {reg_type} values, updated {updated_count} widgets")
            else:
                self.range_result_label.setText(f"Read {len(values)} {reg_type} values from {start_addr} to {start_addr + count - 1}")
                    
            self.signals.status_update.emit(f"Read {len(values)} {reg_type} values successfully")
            logger.info(f"Read {len(values)} {reg_type} values from {start_addr} to {start_addr + count - 1}")
            
        except Exception as e:
            self.signals.error.emit(f"Failed to read register range: {str(e)}")
            logger.error(f"Error reading register range: {str(e)}")
            logger.debug(traceback.format_exc())
            self.range_result_label.setText(f"Error: {str(e)}")



class RegisterWidget(QWidget):
    """레지스터 값을 표시하고 제어하는 위젯"""
    # 메모 변경 시그널 정의
    memo_changed = Signal(str, int, str)  # register_type, address, memo_text
    
    def __init__(self, register_type, start_addr, end_addr, writable, parent=None):
        super().__init__(parent)
        self.register_type = register_type
        self.start_addr = start_addr
        self.end_addr = end_addr
        self.writable = writable
        self.parent = parent
        
        # 비트 타입 레지스터 여부 (코일, 디스크릿 인풋)
        self.is_bit_type = register_type in ["coils", "discrete_inputs"]
        
        # 위젯 저장용 딕셔너리
        self.widgets = {}
        self.values = {}
        self.memo_edits = {}
        
        # UI 초기화
        self.init_ui()
        
    def init_ui(self):
        """사용자 인터페이스 초기화"""
        # 홀딩 레지스터인 경우 두 커럼으로 표시
        if self.register_type == "holding_registers":
            # 기존 레이아웃이 있으면 삭제
            if self.layout():
                QWidget().setLayout(self.layout())
                
            # 메인 레이아웃을 수평 레이아웃으로 생성
            main_layout = QHBoxLayout()
            
            # 첫 번째 커럼 (0-99)
            left_widget = QWidget()
            left_layout = QGridLayout(left_widget)
            left_layout.setSpacing(2)  # 간격 축소
            left_layout.setContentsMargins(3, 3, 3, 3)  # 여백 축소
            
            # 두 번째 커럼 (100-199)
            right_widget = QWidget()
            right_layout = QGridLayout(right_widget)
            right_layout.setSpacing(2)  # 간격 축소
            right_layout.setContentsMargins(3, 3, 3, 3)  # 여백 축소
            
            # 헤더 추가
            left_layout.addWidget(QLabel("Address"), 0, 0)
            left_layout.addWidget(QLabel("Value (Hex)"), 0, 1)
            left_layout.addWidget(QLabel("Memo"), 0, 2)
            
            right_layout.addWidget(QLabel("Address"), 0, 0)
            right_layout.addWidget(QLabel("Value (Hex)"), 0, 1)
            right_layout.addWidget(QLabel("Memo"), 0, 2)
            
            # 첫 번째 커럼 레지스터 (0-99)
            for i in range(100):
                # 주소 레이블 (편집 가능)
                addr_edit = QLineEdit(str(i))
                addr_edit.setMaximumWidth(60)
                addr_edit.setValidator(QIntValidator(0, 65535))
                addr_edit.setReadOnly(False)  # 편집 가능하도록 설정
                left_layout.addWidget(addr_edit, i+1, 0)
                
                # 값 입력 필드
                widget = self.create_register_widget(i)
                self.widgets[i] = widget
                left_layout.addWidget(widget, i+1, 1)
                self.values[i] = 0
                
                # 메모 필드
                memo_edit = QLineEdit()
                memo_edit.setPlaceholderText("메모 입력")
                memo_edit.setMinimumWidth(90)  # 최소 너비 축소
                memo_edit.setStyleSheet("border: 1px solid #bec8d1; border-radius: 8px; padding: 1px;")
                # 메모 필드 텍스트 변경 시 이벤트 연결
                memo_edit.textChanged.connect(lambda text, addr=i: self.on_memo_changed(addr, text))
                self.memo_edits[i] = memo_edit
                left_layout.addWidget(memo_edit, i+1, 2)
            
            # 두 번째 커럼 레지스터 (100-199)
            for i in range(100, 200):
                # 주소 레이블 (편집 가능)
                addr_edit = QLineEdit(str(i))
                addr_edit.setMaximumWidth(60)
                addr_edit.setValidator(QIntValidator(0, 65535))
                addr_edit.setReadOnly(False)  # 편집 가능하도록 설정
                right_layout.addWidget(addr_edit, i-99, 0)  # i-99로 인덱스 조정
                
                # 값 입력 필드
                widget = self.create_register_widget(i)
                self.widgets[i] = widget
                right_layout.addWidget(widget, i-99, 1)  # i-99로 인덱스 조정
                self.values[i] = 0
                
                # 메모 필드
                memo_edit = QLineEdit()
                memo_edit.setPlaceholderText("메모 입력")
                memo_edit.setMinimumWidth(90)  # 최소 너비 축소
                memo_edit.setStyleSheet("border: 1px solid #bec8d1; border-radius: 8px; padding: 1px;")
                # 메모 필드 텍스트 변경 시 이벤트 연결
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
            
            # 메인 레이아웃에 두 커럼 추가
            main_layout.addWidget(left_scroll)
            main_layout.addWidget(right_scroll)
            
            self.setLayout(main_layout)
            return
        
        # 비트 타입 또는 다른 레지스터 타입인 경우 기존 레이아웃 사용
        # 기존 레이아웃이 있으면 삭제
        if self.layout():
            QWidget().setLayout(self.layout())
            
        # 새 레이아웃 생성
        layout = QGridLayout()
        layout.setSpacing(5)
        
        # 헤더
        layout.addWidget(QLabel("Address"), 0, 0)
        if self.is_bit_type:
            layout.addWidget(QLabel("Value"), 0, 1)
        else:
            layout.addWidget(QLabel("Value (Hex)"), 0, 1)
        layout.addWidget(QLabel("Memo"), 0, 2)
        
        # 레지스터 컨트롤 생성
        count = self.end_addr - self.start_addr + 1
        for i in range(count):
            addr = self.start_addr + i
            
            # 주소 레이블 (편집 가능)
            addr_edit = QLineEdit(str(addr))
            addr_edit.setMaximumWidth(60)
            addr_edit.setValidator(QIntValidator(0, 65535))
            addr_edit.setReadOnly(False)  # 편집 가능하도록 설정
            layout.addWidget(addr_edit, i+1, 0)
            
            # 값 입력 필드
            widget = self.create_register_widget(addr)
            self.widgets[addr] = widget
            layout.addWidget(widget, i+1, 1)
            self.values[addr] = 0 if self.is_bit_type else 0
            
            # 메모 필드
            memo_edit = QLineEdit()
            memo_edit.setPlaceholderText("메모 입력")
            memo_edit.setMinimumWidth(90)  # 최소 너비 축소
            memo_edit.setStyleSheet("border: 1px solid #bec8d1; border-radius: 8px; padding: 1px;")
            # 메모 필드 텍스트 변경 시 이벤트 연결
            memo_edit.textChanged.connect(lambda text, a=addr: self.on_memo_changed(a, text))
            self.memo_edits[addr] = memo_edit
            layout.addWidget(memo_edit, i+1, 2)
        
        # 스크롤 영역 생성
        scroll = QScrollArea()
        container = QWidget()
        container.setLayout(layout)
        scroll.setWidget(container)
        scroll.setWidgetResizable(True)
        
        # 전체 레이아웃 설정
        main_layout = QVBoxLayout()
        main_layout.addWidget(scroll)
        self.setLayout(main_layout)
        
    def create_register_widget(self, address):
        """레지스터 타입에 따른 위젯 생성"""
        # 위젯 컨테이너 생성
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        
        if self.is_bit_type:  # 코일 또는 디스크릿 인풋
            # 값 표시를 위한 체크박스
            checkbox = QCheckBox()
            checkbox.setEnabled(False)  # 기본적으로 비활성화 (읽기/쓰기 버튼으로 제어)
            layout.addWidget(checkbox)
            
            # 버튼 그룹
            button_container = QWidget()
            button_layout = QHBoxLayout(button_container)
            button_layout.setContentsMargins(0, 0, 0, 0)
            button_layout.setSpacing(2)
            
            # 읽기 버튼
            read_btn = QPushButton("R")
            read_btn.setFixedSize(25, 25)
            read_btn.clicked.connect(lambda checked=False, addr=address: self.read_bit(addr))
            button_layout.addWidget(read_btn)
            
            # 쓰기 버튼 (코일만 활성화)
            write_btn = QPushButton("W")
            write_btn.setFixedSize(25, 25)
            write_btn.setEnabled(self.writable)
            if self.writable:  # 코일은 쓰기 가능
                write_btn.clicked.connect(lambda checked=False, addr=address, cb=checkbox: self.write_bit(addr, cb))
            button_layout.addWidget(write_btn)
            
            layout.addWidget(button_container)
            
            return container
        else:  # 홀딩 레지스터 또는 입력 레지스터
            # 값 표시를 위한 텍스트 필드
            line_edit = QLineEdit("0000")
            line_edit.setFixedWidth(60)  # 4글자에 맞는 고정 너비로 설정
            line_edit.setEnabled(True)  # 편집 가능하도록 활성화
            line_edit.setAlignment(Qt.AlignCenter)  # 가운데 정렬 설정
            
            # 16진수 유효성 검사
            hex_validator = QRegularExpressionValidator(QRegularExpression("[0-9A-Fa-f]{1,4}"))
            line_edit.setValidator(hex_validator)
            line_edit.setStyleSheet("border: 1px solid #bec8d1; border-radius: 10px; padding: 3px;")
            layout.addWidget(line_edit)            
            # 버튼 그룹
            button_container = QWidget()
            button_layout = QHBoxLayout(button_container)
            button_layout.setContentsMargins(0, 0, 0, 0)
            button_layout.setSpacing(1)  # 간격 축소
            
            # 읽기 버튼
            read_btn = QPushButton("R")
            read_btn.setStyleSheet("border: 1px solid #bec8d1; border-radius: 8px; padding: 0px;")
            read_btn.setFixedSize(30, 22)  # 버튼 크기 축소
            read_btn.clicked.connect(lambda checked=False, addr=address, le=line_edit: self.read_register(addr, le))
            button_layout.addWidget(read_btn)
            
            # 쓰기 버튼 (홀딩 레지스터만 활성화)
            write_btn = QPushButton("W")
            write_btn.setStyleSheet("border: 1px solid #bec8d1; border-radius: 8px; padding: 0px;")
            write_btn.setFixedSize(30, 22)  # 버튼 크기 축소
            write_btn.setEnabled(self.writable)
            if self.writable:  # 홀딩 레지스터는 쓰기 가능
                write_btn.clicked.connect(lambda checked=False, addr=address, le=line_edit: self.write_register(addr, le))
            button_layout.addWidget(write_btn)
            
            layout.addWidget(button_container)
            
            return container
            
    def update_values(self, values):
        """서버에서 읽어온 값으로 위젯 업데이트"""
        for i, value in enumerate(values):
            addr = self.start_addr + i
            if addr > self.end_addr:
                break
                
            if addr in self.widgets:
                widget = self.widgets[addr]
                
                if self.is_bit_type:
                    # 체크박스 업데이트 (비트 타입)
                    # 위젯은 컨테이너이므로 첫 번째 자식 위젯(체크박스)을 찾아야 함
                    checkbox = widget.findChild(QCheckBox)
                    if checkbox:
                        checkbox.blockSignals(True)
                        checkbox.setChecked(bool(value))
                        checkbox.blockSignals(False)
                        self.values[addr] = bool(value)
                else:
                    # 라인 에디트 업데이트 (레지스터 타입)
                    # 위젯은 컨테이너이므로 첫 번째 자식 위젯(라인에디트)을 찾아야 함
                    line_edit = widget.findChild(QLineEdit)
                    if line_edit:
                        line_edit.blockSignals(True)
                        line_edit.setText(f"{value:04X}")
                        line_edit.blockSignals(False)
                        self.values[addr] = value
                    
    def read_bit(self, address):
        """특정 비트 값 읽기"""
        if not self.parent:
            return
            
        # 부모 클래스에 읽기 요청
        value = self.parent.read_register(self.register_type, address)
        if value is not None:
            # 체크박스 업데이트
            widget = self.widgets[address]
            checkbox = widget.layout().itemAt(0).widget()
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(value))
            checkbox.blockSignals(False)
            self.values[address] = bool(value)
            
    def write_bit(self, address, checkbox):
        """특정 비트 값 쓰기"""
        if not self.writable or not self.parent:
            return
            
        # 체크박스 상태 가져오기
        checkbox = self.widgets[address].layout().itemAt(0).widget()
        value = not checkbox.isChecked()  # 현재 상태의 반대로 설정
        
        # 부모 클래스에 쓰기 요청
        if self.parent.write_register_value(self.register_type, address, value):
            # 성공적으로 쓰기가 완료되면 체크박스 업데이트
            checkbox.blockSignals(True)
            checkbox.setChecked(value)
            checkbox.blockSignals(False)
            self.values[address] = value
            
    def read_register(self, address, line_edit):
        """특정 레지스터 값 읽기"""
        if not self.parent:
            return
            
        # 부모 클래스에 읽기 요청
        value = self.parent.read_register(self.register_type, address)
        if value is not None:
            # 라인 에디트 업데이트
            line_edit = self.widgets[address].layout().itemAt(0).widget()
            line_edit.blockSignals(True)
            line_edit.setText(f"{value:04X}")
            line_edit.blockSignals(False)
            self.values[address] = value
            
    def on_memo_changed(self, address, text):
        """메모 필드 텍스트 변경 처리"""
        # 메모 변경 시그널 발생
        self.memo_changed.emit(self.register_type, address, text)
            
    def write_register(self, address, line_edit):
        """특정 레지스터 값 쓰기"""
        if not self.writable or not self.parent:
            return
            
        # 라인 에디트에서 값 가져오기
        line_edit = self.widgets[address].layout().itemAt(0).widget()
        try:
            # 16진수 문자열을 정수로 변환
            value = int(line_edit.text(), 16)
            
            # 부모 클래스에 쓰기 요청
            if self.parent.write_register_value(self.register_type, address, value):
                # 성공적으로 쓰기가 완료되면 값 업데이트
                self.values[address] = value
        except ValueError:
            # 유효하지 않은 값이면 이전 값으로 복원
            line_edit.setText(f"{self.values.get(address, 0):04X}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Qt 6에서는 기본적으로 고해상도 픽스맵을 사용하므로 속성 설정 제거
    
    # 애플리케이션 아이콘 설정
    app_icon = QIcon("icon.png") if os.path.exists("icon.png") else None
    if app_icon:
        app.setWindowIcon(app_icon)
    
    # 폰트 설정
    font = app.font()
    font.setPointSize(10)
    app.setFont(font)
    
    # 애플리케이션 시작
    window = ModbusClientSimulator()
    window.show()
    
    sys.exit(app.exec())

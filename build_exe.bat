@echo off
echo Modbus TCP 서버 시뮬레이터 EXE 빌드 시작...
echo.

REM 기존 빌드 폴더 정리
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM PyInstaller를 사용하여 EXE 파일 생성
echo PyInstaller로 EXE 빌드 시작...
pyinstaller --onefile --noconsole --name modbus_tcp_server_sim --hidden-import pymodbus --hidden-import pymodbus.server --hidden-import pymodbus.transaction --hidden-import pymodbus.datastore --hidden-import pymodbus.device modbus_server_sim.py

REM 성공 메시지 출력
echo.
echo 빌드가 완료되었습니다.
echo EXE 파일 위치: dist\modbus_tcp_server_sim.exe
echo.
pause

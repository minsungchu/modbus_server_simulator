@echo off
echo Starting Modbus TCP Client build...

REM Check if PyInstaller is installed
pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo PyInstaller is not installed. Starting installation...
    pip install pyinstaller
    if %errorlevel% neq 0 (
        echo PyInstaller installation failed! Exiting program.
        pause
        exit /b 1
    )
    echo PyInstaller installation completed!
)

REM Clean previous builds
if exist build\modbus_tcp_client rmdir /s /q build\modbus_tcp_client
if exist dist\modbus_tcp_client rmdir /s /q dist\modbus_tcp_client
if exist dist\modbus_tcp_client.exe del /f /q dist\modbus_tcp_client.exe

REM Create a simple ASCII icon file for PyInstaller
echo Creating simple icon for build...

REM Create a simple colored icon using Python Pillow library
echo import os > temp_create_ico.py
echo try: >> temp_create_ico.py
echo     from PIL import Image, ImageDraw, ImageFont >> temp_create_ico.py
echo     img = Image.new('RGBA', (64, 64), color=(0, 0, 0, 0)) >> temp_create_ico.py
echo     d = ImageDraw.Draw(img) >> temp_create_ico.py
echo     d.rectangle([(0, 0), (64, 64)], fill=(200, 230, 201)) >> temp_create_ico.py
echo     # Try to create text, but continue even if font creation fails >> temp_create_ico.py
echo     try: >> temp_create_ico.py
echo         d.text((32, 32), "C", fill=(255, 255, 255), anchor="mm") >> temp_create_ico.py
echo     except Exception as e: >> temp_create_ico.py
echo         print(f"Could not add text to icon: {e}") >> temp_create_ico.py
echo     img.save("temp_client_icon.png") >> temp_create_ico.py
echo     print("Icon created successfully") >> temp_create_ico.py
echo except ImportError: >> temp_create_ico.py
echo     print("Pillow not installed, creating fallback icon") >> temp_create_ico.py
echo     # Create a simple icon with plain Python >> temp_create_ico.py
echo     with open('temp_client_icon.png', 'wb') as f: >> temp_create_ico.py
echo         # Minimal valid PNG header and data >> temp_create_ico.py
echo         f.write(bytes.fromhex('89504e470d0a1a0a0000000d49484452000000100000001008060000001ff3ff610000001c4944415478da63fcffff3f03b9807160d4a051034c0d503a0000ffff34a10743'))  >> temp_create_ico.py
echo except Exception as e: >> temp_create_ico.py
echo     print(f"Failed to create icon: {e}") >> temp_create_ico.py

python temp_create_ico.py
if not exist temp_client_icon.png (
    echo Failed to create temporary icon
    set ICON_PATH=
) else (
    set ICON_PATH=temp_client_icon.png
    echo Temporary icon created successfully
)

echo Building client application with version info...
pyinstaller --noconfirm --onefile --windowed ^
    --add-data "%ICON_PATH%;." ^
    --icon=%ICON_PATH% ^
    --version-file=modbus_tcp_client_version.txt ^
    --name "modbus_tcp_client" ^
    --hidden-import=pymodbus.client ^
    --hidden-import=pymodbus.exceptions ^
    modbus_tcp_client.py

if %errorlevel% neq 0 (
    echo Error during build! Please check the log.
    pause
    exit /b 1
)

echo Build completed!
echo Executable path: dist\modbus_tcp_client\modbus_tcp_client.exe

REM Create desktop shortcut
echo Creating desktop shortcut...
set SCRIPT="%TEMP%\create_shortcut.vbs"
echo Set oWS = WScript.CreateObject("WScript.Shell") > %SCRIPT%
echo sLinkFile = "%USERPROFILE%\Desktop\Modbus TCP Client.lnk" >> %SCRIPT%
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> %SCRIPT%
echo oLink.TargetPath = "%CD%\dist\modbus_tcp_client.exe" >> %SCRIPT%
echo oLink.WorkingDirectory = "%CD%\dist\" >> %SCRIPT%
echo oLink.IconLocation = "%CD%\%ICON_PATH%" >> %SCRIPT%
echo oLink.Save >> %SCRIPT%
cscript /nologo %SCRIPT%
del %SCRIPT%

echo Build and shortcut creation completed.
echo You can now run dist\modbus_tcp_client\modbus_tcp_client.exe or use the desktop shortcut.
pause

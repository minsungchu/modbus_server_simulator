@echo off
echo Starting Modbus TCP Server build...

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

REM Check if resources folder exists
if not exist resources (
    echo Resources folder does not exist!
    echo The resources folder containing icon files is required.
    pause
    exit /b 1
)

REM Clean previous builds
if exist build\modbus_tcp_server rmdir /s /q build\modbus_tcp_server
if exist dist\modbus_tcp_server rmdir /s /q dist\modbus_tcp_server

REM Get the ICO file for the application
set ICON_PATH=resources\app_icon.ico

REM If ICO doesn't exist but SVG does, try to convert
if not exist %ICON_PATH% (
    if exist resources\app_icon.svg (
        echo App icon ICO not found, but SVG exists. Attempting conversion...
        
        REM Check icons folder
        if not exist icons mkdir icons
        
        REM Convert SVG to ICO using ImageMagick if available
        where magick >nul 2>&1
        if %errorlevel% equ 0 (
            echo Using ImageMagick to convert SVG to ICO.
            magick convert -background none resources\app_icon.svg -define icon:auto-resize=256,128,64,48,32,16 resources\app_icon.ico
            if exist resources\app_icon.ico (
                echo Conversion successful!
                set ICON_PATH=resources\app_icon.ico
            )
        ) else (
            echo ImageMagick not installed, using SVG icon directly.
            echo For better results, installing ImageMagick is recommended.
            set ICON_PATH=resources\app_icon.svg
        )
    ) else (
        echo Warning: Neither app_icon.ico nor app_icon.svg found!
        echo Using default icon.
        set ICON_PATH=
    )
)

echo Building server application with size optimization and version info...
pyinstaller --noconfirm --onefile --windowed ^
    --icon=%ICON_PATH% ^
    --version-file=modbus_tcp_server_version.txt ^
    --name "modbus_tcp_server" ^
    --add-data "resources\default_sequence.json;resources" ^
    --hidden-import=pymodbus.server ^
    --hidden-import=pymodbus.transaction ^
    --hidden-import=pymodbus.datastore ^
    --exclude-module=matplotlib ^
    --exclude-module=notebook ^
    --exclude-module=pandas ^
    --exclude-module=scipy ^
    --exclude-module=PIL.ImageDraw2 ^
    --exclude-module=PIL.ImageQt ^
    --exclude-module=PIL.ImageShow ^
    --exclude-module=PyQt5 ^
    --exclude-module=PyQt6 ^
    --exclude-module=tk ^
    --exclude-module=tcl ^
    --exclude-module=_tkinter ^
    --exclude-module=sqlalchemy ^
    --exclude-module=PySide2 ^
    --clean ^
    --log-level=INFO ^
    --noupx ^
    modbus_tcp_server.py

if %errorlevel% neq 0 (
    echo Error during build! Please check the log.
    pause
    exit /b 1
)

echo Build completed!
echo Executable path: dist\modbus_tcp_server\modbus_tcp_server.exe

echo Creating desktop shortcut...
set SCRIPT="%TEMP%\create_shortcut.vbs"
echo Set oWS = WScript.CreateObject("WScript.Shell") > %SCRIPT%
echo sLinkFile = "%USERPROFILE%\Desktop\Modbus TCP Server.lnk" >> %SCRIPT%
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> %SCRIPT%
echo oLink.TargetPath = "%CD%\dist\\modbus_tcp_server.exe" >> %SCRIPT%
echo oLink.WorkingDirectory = "%CD%\dist\" >> %SCRIPT%
echo oLink.Save >> %SCRIPT%
cscript /nologo %SCRIPT%
del %SCRIPT%

echo Build and shortcut creation completed.
echo You can now run dist\modbus_tcp_server\modbus_tcp_server.exe or use the desktop shortcut.
pause

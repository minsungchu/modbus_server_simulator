#!/usr/bin/env python3
"""크로스플랫폼 PyInstaller 빌드 스크립트 (Windows / Linux 공용).

사용법:
    python build.py server   # 서버 실행파일만 빌드
    python build.py client   # 클라이언트 실행파일만 빌드
    python build.py all      # 둘 다 (기본값)

결과물은 ``dist/`` 에 생성된다. add-data 구분자(';' / ':')와 Windows 전용
옵션(--version-file, --icon=.ico)은 실행 OS 에 맞춰 자동 처리한다. 기존
``*_build.bat`` 의 플래그(숨은 import / 제외 모듈 / 리소스 동봉)를 그대로 옮겼다.
"""

from __future__ import annotations

import os
import sys

import PyInstaller.__main__

# Windows 는 add-data 구분자가 ';', 그 외(Linux/macOS)는 ':'.
SEP = ";" if os.name == "nt" else ":"
IS_WIN = os.name == "nt"

# 번들 크기를 줄이기 위해 제외하는 모듈(런타임에 쓰지 않음).
EXCLUDES = [
    "matplotlib", "notebook", "pandas", "scipy",
    "PIL.ImageDraw2", "PIL.ImageQt", "PIL.ImageShow",
    "PyQt5", "PyQt6", "PySide2",
    "tk", "tcl", "_tkinter", "sqlalchemy",
]

# 공통 옵션: 단일 실행파일 + GUI(windowed) + 버전/리소스 동봉.
# pyproject.toml 은 appversion.get_version() 이 런타임에 읽고,
# resources/ 는 아이콘·스타일·기본 시퀀스(default_sequence.json)에 쓰인다.
COMMON = [
    "--noconfirm", "--onefile", "--windowed", "--clean", "--noupx",
    "--add-data", f"pyproject.toml{SEP}.",
    "--add-data", f"resources{SEP}resources",
]


def _excludes() -> list[str]:
    args: list[str] = []
    for mod in EXCLUDES:
        args += ["--exclude-module", mod]
    return args


def _win_extras(version_file: str) -> list[str]:
    """Windows 전용 옵션(버전 정보 + 아이콘)을 있는 것만 추가한다."""
    extras: list[str] = []
    if os.path.exists(version_file):
        extras += ["--version-file", version_file]
    if os.path.exists("resources/app_icon.ico"):
        extras += ["--icon", "resources/app_icon.ico"]
    return extras


def build_server() -> None:
    """Modbus TCP 서버 시뮬레이터 실행파일을 빌드한다."""
    args = [
        "modbus_tcp_server.py",
        "--name", "modbus_tcp_server",
        "--hidden-import", "pymodbus.server",
        "--hidden-import", "pymodbus.transaction",
        "--hidden-import", "pymodbus.datastore",
        *COMMON, *_excludes(),
    ]
    if IS_WIN:
        args += _win_extras("modbus_tcp_server_version.txt")
    print(">>> building server:", " ".join(args))
    PyInstaller.__main__.run(args)


def build_client() -> None:
    """Modbus TCP 클라이언트 실행파일을 빌드한다."""
    args = [
        "modbus_tcp_client.py",
        "--name", "modbus_tcp_client",
        "--hidden-import", "pymodbus.client",
        "--hidden-import", "pymodbus.exceptions",
        *COMMON, *_excludes(),
    ]
    if IS_WIN:
        args += _win_extras("modbus_tcp_client_version.txt")
    print(">>> building client:", " ".join(args))
    PyInstaller.__main__.run(args)


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target not in ("server", "client", "all"):
        print(f"알 수 없는 대상: {target!r} (server | client | all)")
        sys.exit(2)
    if target in ("server", "all"):
        build_server()
    if target in ("client", "all"):
        build_client()


if __name__ == "__main__":
    main()

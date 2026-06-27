"""애플리케이션 버전 조회 유틸리티.

버전의 단일 소스는 pyproject.toml 의 [project].version 이다.
이 모듈은 실행 시점에 pyproject.toml 을 읽어 버전을 제공하며,
파일을 찾지 못하는 환경(예: 일부 PyInstaller 번들)에서는 FALLBACK_VERSION 을 사용한다.
"""

import os
import sys

# pyproject.toml 의 [project].version 과 동일하게 유지할 것.
FALLBACK_VERSION = "1.0.0"


def get_version() -> str:
    """pyproject.toml 의 [project].version 을 반환한다(실패 시 FALLBACK_VERSION).

    Returns:
        str: 애플리케이션 버전 문자열.
    """
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    pyproject_path = os.path.join(base, "pyproject.toml")
    try:
        import tomllib  # Python 3.11+
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        version = data.get("project", {}).get("version")
        return version if version else FALLBACK_VERSION
    except (OSError, ValueError, ModuleNotFoundError):
        return FALLBACK_VERSION


APP_VERSION = get_version()

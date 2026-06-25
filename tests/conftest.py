"""pytest 공용 픽스처.

Qt 시그널/슬롯과 위젯 테스트를 위해 세션 전체에서 단 하나의 QApplication 을
공유한다. 엔진(QObject) 테스트와 GUI(QWidget) 테스트가 같은 앱을 쓰도록 하여,
QCoreApplication 만 있는 상태에서 QWidget 을 만들다 크래시하는 문제를 막는다.
디스플레이가 없는 환경에서도 동작하도록 offscreen 플랫폼을 기본값으로 설정한다.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402  (환경변수 설정 후 임포트)


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """세션 전체에서 공유하는 QApplication 을 반환한다."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

"""pytest 공용 픽스처.

QObject 시그널/슬롯이 동작하려면 QCoreApplication 인스턴스가 하나 필요하다.
GUI(QWidget) 없이도 엔진(QObject) 테스트가 가능하도록 코어 앱만 띄운다.
"""

import pytest
from PySide6.QtCore import QCoreApplication


@pytest.fixture(scope="session")
def qapp() -> QCoreApplication:
    """세션 전체에서 공유하는 QCoreApplication 을 반환한다."""
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app

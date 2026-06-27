# Modbus TCP Simulator

PySide6 기반 Modbus TCP **서버 / 클라이언트** 시뮬레이터.

## 요구 사항

- [uv](https://docs.astral.sh/uv/) (0.5+ 권장)
- Python 3.11+ (uv 가 `.python-version` 에 맞춰 자동 설치/선택)

## 설치 (의존성 동기화)

```bash
uv sync
```

`uv sync` 는 `.venv` 를 생성하고 `pyproject.toml` 의 모든 의존성을 설치한다.
(`uv run` 으로 처음 실행할 때도 자동으로 동기화된다.)

## 실행

```bash
# 서버 시뮬레이터
uv run modbus_tcp_server.py

# 클라이언트 시뮬레이터
uv run modbus_tcp_client.py
```

## 실행파일 빌드 (Windows / Linux 공용)

크로스플랫폼 빌드 스크립트 `build.py` 가 OS 차이(데이터 구분자, Windows 전용
버전/아이콘 옵션)를 자동 처리한다.

```bash
uv run python build.py all      # 서버+클라이언트  (server | client | all)
```

결과물은 `dist/` 에 생성된다 (`modbus_tcp_server[.exe]`, `modbus_tcp_client[.exe]`).

> Linux 실행 시 Qt 런타임 라이브러리가 필요할 수 있다:
> `sudo apt-get install -y libegl1 libxcb-cursor0 libxkbcommon0`

## 배포 / 릴리스 (GitHub Actions)

`.github/workflows/release.yml` 가 Windows·Ubuntu 실행파일을 자동 빌드한다.

- **푸시 / PR**: 테스트(offscreen) + 빌드까지 수행하고 아티팩트를 업로드한다.
- **태그 `v*` 푸시**: 위 빌드 결과를 **GitHub Release** 에 자동 첨부한다.
- 릴리스 빌드 대상은 **서버만**이다(클라이언트는 CI 빌드에서 제외). 클라이언트가
  필요하면 로컬에서 `python build.py client` 로 빌드한다.

```bash
# 새 버전 릴리스
git tag v1.0.0
git push origin v1.0.0
```

릴리스에는 `modbus_tcp_server-{windows,linux}-x64` 실행파일이 첨부된다.

## 의존성

| 패키지     | 용도                     |
|-----------|--------------------------|
| pymodbus  | Modbus TCP 서버/클라이언트 |
| PySide6   | Qt6 GUI                  |
| pyinstaller (dev) | 단일 exe 빌드      |

# CLAUDE.md — 프로젝트 가이드

Modbus TCP 서버/클라이언트 시뮬레이터 (PySide6 GUI, 크로스플랫폼).

## 새 버전 릴리스 절차

릴리스는 **태그 `v*` 푸시**로 자동화되어 있다(`.github/workflows/release.yml`).
태그를 밀면 test → build(Windows/Ubuntu) → 설치파일 생성 → GitHub Release 첨부가
순서대로 돈다. **CI 릴리스 대상은 서버만**이다(클라이언트는 제외).

### 1) 버전 문자열 올리기 (4곳, 같은 값으로)

버전 단일 소스는 `pyproject.toml` 이지만, 실제로 함께 맞춰야 하는 곳:

- `pyproject.toml` → `[project].version`
- `appversion.py` → `FALLBACK_VERSION` (번들에서 pyproject 를 못 읽을 때 대비)
- `modbus_tcp_server_version.txt` → `filevers`, `prodvers`, `FileVersion`, `ProductVersion`
- (클라이언트도 빌드한다면) `modbus_tcp_client_version.txt` 동일하게

### 2) 커밋 → 태그 → 푸시

```bash
git add -A && git commit -m "chore: bump version to X.Y.Z"
git tag vX.Y.Z
git push origin <branch>        # 코드
git push origin vX.Y.Z          # 태그 → 릴리스 트리거
```

### 3) 같은 태그로 릴리스를 다시 만들어야 할 때(재생성)

기존 릴리스/태그를 지우고 현재 커밋에 다시 태깅한다. (릴리스 삭제는 GitHub API,
인증은 `git credential` 의 토큰 사용 — 토큰을 로그에 출력하지 말 것.)

```bash
REPO="minsungchu/modbus_server_simulator"
TOKEN=$(printf "protocol=https\nhost=github.com\n\n" | git credential fill | sed -n 's/^password=//p')
RID=$(curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/releases/tags/vX.Y.Z" | sed -n 's/^  "id": \([0-9]*\),/\1/p' | head -1)
[ -n "$RID" ] && curl -s -X DELETE -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/releases/$RID"
git push origin :refs/tags/vX.Y.Z && git tag -d vX.Y.Z
git tag -a vX.Y.Z -m "Release vX.Y.Z" && git push origin vX.Y.Z
```

### 4) 빌드/릴리스 상태 확인 (gh CLI 없음 → API)

```bash
curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/actions/runs?per_page=5"
curl -s -H "Authorization: token $TOKEN" "https://api.github.com/repos/$REPO/releases/tags/vX.Y.Z"
```

## 빌드 / 패키징 메모

- 로컬 빌드: `python build.py {server|client|all}` → `dist/` (OS 차이 자동 처리).
- 설치파일: Windows `packaging/windows/installer.iss`(Inno Setup), Ubuntu
  `packaging/linux/build_deb.sh`(.deb, `Depends` 에 Qt 런타임 라이브러리 명시).
- Windows 인스톨러는 **같은 AppId 의 이전 버전을 무인 제거 후 설치**한다. AppId 를
  바꾸면 이 동작이 깨지니 유지할 것.
- `.deb` 의 `Depends` 가 `libegl1, libxcb-cursor0 …` 를 자동 설치한다.

## 런타임/리소스 규약 (깨기 쉬움)

- **리소스 읽기**는 항상 `getattr(sys, "_MEIPASS", <소스경로>)` + `resources/` 로.
  PyInstaller 번들과 소스 실행 모두 동작해야 한다.
- **런타임 쓰기 파일**(로그/레지스터/시퀀스)은 상대경로다. frozen 실행 시
  `modbus_tcp_server.py` 의 `_ensure_writable_data_dir()` 가 CWD 를
  `%LOCALAPPDATA%\ModbusTcpServer`(Linux: `~/.local/share/ModbusTcpServer`) 로
  옮긴다 — Program Files 같은 읽기전용 설치 위치의 PermissionError 방지. 새 런타임
  파일을 추가할 때도 이 규약(상대경로) 안에서 쓰면 된다.
- 배포 기본 시퀀스: `resources/default_sequence.json`(번들). 신규 설치 시
  `sequences/DEFAULT.json` 으로 시드되고 기본 지정된다. 기본 다이어그램을 갱신하려면
  최신 `sequences/<name>.json` → `resources/default_sequence.json` 으로 복사.
- `DEFAULT` 세트는 이름 변경/삭제 불가(관리 창에서 보호). 복사/내보내기는 허용.

## 코드 규약

- Python 3.11+. type hints + Google 스타일 docstring.
- `print()` 금지 → `logging`. 시크릿 하드코딩 금지.
- 커밋: Conventional Commits. GUI 테스트는 `QT_QPA_PLATFORM=offscreen` + 세션 공용
  QApplication. `python -m pytest tests/ -q` 로 확인.

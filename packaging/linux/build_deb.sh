#!/usr/bin/env bash
# Modbus TCP Server Simulator 의 Debian(.deb) 패키지를 만든다.
# 사용법:  bash packaging/linux/build_deb.sh <version>
# 사전 조건: dist/modbus_tcp_server (build.py 로 빌드된 실행파일)가 존재할 것.
set -euo pipefail

VERSION="${1:?사용법: build_deb.sh <version>}"
BIN="dist/modbus_tcp_server"
[ -f "$BIN" ] || { echo "실행파일이 없습니다: $BIN (먼저 python build.py server)"; exit 1; }

PKG="modbus-tcp-server_${VERSION}_amd64"
ROOT="packaging/linux/${PKG}"
rm -rf "$ROOT"
mkdir -p "$ROOT/DEBIAN" \
         "$ROOT/usr/bin" \
         "$ROOT/usr/share/applications" \
         "$ROOT/usr/share/icons/hicolor/256x256/apps"

# 실행파일 설치
install -m755 "$BIN" "$ROOT/usr/bin/modbus-tcp-server"

# 아이콘(있으면). app_icon.ico → png 변환 시도, 실패하면 server_icon.png 폴백.
ICON_DST="$ROOT/usr/share/icons/hicolor/256x256/apps/modbus-tcp-server.png"
if command -v convert >/dev/null 2>&1 && [ -f resources/app_icon.ico ]; then
    convert "resources/app_icon.ico[0]" -resize 256x256 "$ICON_DST" 2>/dev/null \
        || cp resources/server_icon.png "$ICON_DST" 2>/dev/null || true
else
    cp resources/server_icon.png "$ICON_DST" 2>/dev/null || true
fi

# 데스크톱 항목(앱 메뉴 등록)
cat > "$ROOT/usr/share/applications/modbus-tcp-server.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Modbus TCP Server Simulator
Comment=노드 그래프 기반 Modbus TCP 서버/시퀀스 시뮬레이터
Exec=/usr/bin/modbus-tcp-server
Icon=modbus-tcp-server
Terminal=false
Categories=Utility;Development;
EOF

# 패키지 메타데이터. Depends 에 Qt 런타임 라이브러리를 넣어 apt 가 자동 설치하게 한다.
cat > "$ROOT/DEBIAN/control" <<EOF
Package: modbus-tcp-server
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: amd64
Depends: libegl1, libxcb-cursor0, libxkbcommon0, libdbus-1-3, libxcb-xinerama0, libfontconfig1
Maintainer: CMES <noreply@cmes-ai.com>
Description: Modbus TCP Server Simulator (PySide6 GUI)
 노드 그래프로 신호 전송/대기/분기 시퀀스를 편집·실행하는 Modbus TCP 서버 시뮬레이터.
EOF

dpkg-deb --build --root-owner-group "$ROOT"
OUT="packaging/linux/modbus_tcp_server-${VERSION}-linux-x64.deb"
mv "${ROOT}.deb" "$OUT"
echo "생성됨: $OUT"

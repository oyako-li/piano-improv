#!/usr/bin/env bash
# ピアノの音源（Salamander Grand Piano の SF2 版, FreePats）を assets/ に入れる。
# 音源: Alexander Holm, CC BY 3.0 — https://freepats.zenvoid.org/Piano/acoustic-grand-piano.html
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="SalamanderGrandPiano-SF2-V3+20200602"
URL="https://freepats.zenvoid.org/Piano/SalamanderGrandPiano/${NAME}.tar.xz"
SF2="assets/${NAME}/SalamanderGrandPiano-V3+20200602.sf2"
SHA256="712d0e681efbe5203a8014e9b3e84168f1908c82f2f6fb13bd2c77d6d72c70b7"

if [ -f "$SF2" ]; then
  echo "もう入っています: $SF2"
  exit 0
fi
mkdir -p assets
echo "ダウンロード中（約 300MB、展開すると約 1.2GB）: $URL"
curl -fL --progress-bar "$URL" -o "assets/${NAME}.tar.xz"
tar -xJf "assets/${NAME}.tar.xz" -C assets
rm "assets/${NAME}.tar.xz"
if command -v sha256sum >/dev/null; then
  echo "${SHA256}  ${SF2}" | sha256sum -c -
fi
echo "入れました: $SF2"

#!/usr/bin/env bash
# Rise – copia i file di Rise dentro un sistema (cartella chroot).
# Uso: build/install-system.sh <chroot> <cartella pacchetti npm estratti>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CH="$1"; NPMX="$2"
install -d "$CH/usr/local/bin" "$CH/usr/local/lib/rise" "$CH/opt/rise" "$CH/usr/share/themes"
for f in "$ROOT"/system/bin/*; do [ -f "$f" ] && install -m 755 "$f" "$CH/usr/local/bin/"; done
rm -rf "$CH/usr/local/lib/rise"; cp -r "$ROOT/system/lib/rise" "$CH/usr/local/lib/rise"
find "$CH/usr/local/lib/rise" -name __pycache__ -prune -exec rm -rf {} +
cp -r "$ROOT/system/etc/." "$CH/etc/"
cp -r "$ROOT/system/share/themes/." "$CH/usr/share/themes/"
install -d "$CH/usr/share/plymouth/themes"
cp -r "$ROOT/system/share/plymouth/themes/." "$CH/usr/share/plymouth/themes/"
python3 "$ROOT/build/offline-desktop.py" "$ROOT/desktop/index.html" "$NPMX" "$CH/opt/rise"
cp "$ROOT/LICENSE" "$CH/opt/rise/LICENZA-rise.txt"
cp "$ROOT/NOTICE.md" "$CH/opt/rise/CREDITI.md"
echo "File di Rise installati in $CH"

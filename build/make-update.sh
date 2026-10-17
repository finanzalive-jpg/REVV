#!/usr/bin/env bash
# Rise – prepara il pacchetto di aggiornamento (per i computer con Rise già installato).
# Uso: build/make-update.sh <versione> <cartella di uscita> [url di download del pacchetto]
# Risultato: rise-update.tar.gz (root/… + packages.txt) e rise-update.json (versione, impronta SHA-256)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$1"; OUT="$2"
URL="${3:-https://github.com/finanzalive-jpg/RISE/releases/download/updates/rise-update.tar.gz}"
mkdir -p "$OUT"; OUT="$(cd "$OUT" && pwd)"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
"$ROOT/build/npm-libs.sh" "$WORK/npm"
STAGE="$WORK/pkg/root"; mkdir -p "$STAGE"
"$ROOT/build/install-system.sh" "$STAGE" "$WORK/npm/x" >/dev/null
# l'installazione personalizza questi file: l'aggiornamento non li tocca
rm -f "$STAGE/etc/greetd/config.toml" "$STAGE/etc/casper.conf"
cp "$ROOT/build/packages.txt" "$WORK/pkg/packages.txt"
BUILD_ID="$(git -C "$ROOT" log -1 --format=%ct)"
COMMIT="$(git -C "$ROOT" rev-parse --short HEAD)"
DATE="$(git -C "$ROOT" log -1 --format=%cI)"
tar -C "$WORK/pkg" --owner=0 --group=0 --numeric-owner -czf "$OUT/rise-update.tar.gz" root packages.txt
SHA="$(sha256sum "$OUT/rise-update.tar.gz" | cut -d' ' -f1)"
SIZE="$(stat -c %s "$OUT/rise-update.tar.gz")"
git -C "$ROOT" log -15 --no-merges --format='%s' > "$WORK/notes.txt"
BUILD_ID="$BUILD_ID" VERSION="$VERSION" COMMIT="$COMMIT" DATE="$DATE" URL="$URL" SHA="$SHA" SIZE="$SIZE" \
python3 - "$OUT/rise-update.json" "$WORK/notes.txt" <<'PY'
import json, os, sys
e = os.environ
json.dump({'build': int(e['BUILD_ID']), 'version': e['VERSION'], 'commit': e['COMMIT'], 'date': e['DATE'],
           'url': e['URL'], 'sha256': e['SHA'], 'size': int(e['SIZE']),
           'notes': [l.strip() for l in open(sys.argv[2], errors='replace') if l.strip()][:15]},
          open(sys.argv[1], 'w'), ensure_ascii=False, indent=1)
PY
echo "Pacchetto di aggiornamento: $OUT/rise-update.tar.gz ($SIZE byte, build $BUILD_ID)"

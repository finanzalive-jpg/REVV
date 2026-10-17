#!/usr/bin/env bash
# Rise – caratteri e lettori di file per il desktop offline (pacchetti npm estratti in <cartella>/x)
set -euo pipefail
NPM="$1"; mkdir -p "$NPM"
[ -n "${NPM_CACHE:-}" ] && [ -d "$NPM_CACHE" ] && cp "$NPM_CACHE"/*.tgz "$NPM/" 2>/dev/null || true
( cd "$NPM" && for p in @fontsource/onest@5 @fontsource/unbounded@5 @fontsource/jetbrains-mono@5 @fontsource/source-serif-4@5 pdfjs-dist@3.11.174 mammoth@1.6.0 xlsx@0.18.5; do n="$(echo "$p" | sed 's/^@//; s/\//-/; s/@.*//')"; ls "$n"-*.tgz >/dev/null 2>&1 || npm pack -q "$p" >/dev/null; done
  for f in *.tgz; do mkdir -p "x/${f%.tgz}"; tar xzf "$f" -C "x/${f%.tgz}"; done )

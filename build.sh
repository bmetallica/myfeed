#!/usr/bin/env bash
# =============================================================================
# build.sh – Pakete für die MyFeed Browser-Extension bauen
#
# Erzeugt:
#   dist/myfeed-chrome.zip   → Für Chrome (als "Entpackte Extension" laden)
#   dist/myfeed-firefox.xpi  → Für Firefox (direkte Installation)
#
# Aufruf: bash build.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_DIR="$SCRIPT_DIR/extension"
DIST_DIR="$SCRIPT_DIR/dist"

# ── 1. Icons generieren ───────────────────────────────────────────────────────
echo "▶  Icons generieren …"
python3 "$SCRIPT_DIR/make_icons.py"

# ── 2. dist/-Verzeichnis vorbereiten ─────────────────────────────────────────
mkdir -p "$DIST_DIR"
rm -f "$DIST_DIR/myfeed-chrome.zip" "$DIST_DIR/myfeed-firefox.xpi"

# ── 3. Zu packende Dateien definieren ────────────────────────────────────────
FILES=(
  manifest.json
  background.js
  content_cookie_bridge.js
  options.html
  options.js
  icons/icon16.png
  icons/icon48.png
  icons/icon128.png
)

# ── 4. Chrome-Paket (ZIP) ────────────────────────────────────────────────────
# Chrome lädt die Extension als "entpackten Ordner" oder via Web Store.
# Für die lokale Entwicklung reicht das ZIP als Referenz; der Ordner selbst
# wird in chrome://extensions → "Entpackte Extension laden" ausgewählt.
echo "▶  Chrome-Paket bauen: dist/myfeed-chrome.zip"
(
  cd "$EXT_DIR"
  zip -q -r "$DIST_DIR/myfeed-chrome.zip" "${FILES[@]}"
)
echo "   ✓ $(du -sh "$DIST_DIR/myfeed-chrome.zip" | cut -f1) – dist/myfeed-chrome.zip"

# ── 5. Firefox-Paket (XPI) ───────────────────────────────────────────────────
# XPI ist strukturell identisch mit ZIP; Firefox erkennt es am Dateinamen.
# Für temporäre Installation (about:debugging) oder Firefox Developer Edition
# wird keine Signierung benötigt.
echo "▶  Firefox-Paket bauen: dist/myfeed-firefox.xpi"
(
  cd "$EXT_DIR"
  zip -q -r "$DIST_DIR/myfeed-firefox.xpi" "${FILES[@]}"
)
echo "   ✓ $(du -sh "$DIST_DIR/myfeed-firefox.xpi" | cut -f1) – dist/myfeed-firefox.xpi"

# ── 6. Zusammenfassung ────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  Pakete fertig in: $DIST_DIR"
echo ""
echo "  CHROME – Installation:"
echo "  1. chrome://extensions öffnen"
echo "  2. 'Entwicklermodus' (oben rechts) aktivieren"
echo "  3. 'Entpackte Erweiterung laden' → Ordner wählen:"
echo "     $EXT_DIR"
echo ""
echo "  FIREFOX – Temporäre Installation (kein Signing nötig):"
echo "  1. about:debugging#/runtime/this-firefox öffnen"
echo "  2. 'Temporäres Add-on laden' → Datei wählen:"
echo "     $DIST_DIR/myfeed-firefox.xpi"
echo ""
echo "  FIREFOX – Permanente Installation (Developer Edition):"
echo "  1. about:addons öffnen → Zahnrad → 'Add-on aus Datei installieren'"
echo "     $DIST_DIR/myfeed-firefox.xpi"
echo "     (Funktioniert ohne Signing in Firefox Developer Edition / Nightly)"
echo "═══════════════════════════════════════════════════════"

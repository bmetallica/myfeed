#!/bin/bash
# ============================================================
# start.sh – Startet Xvfb, x11vnc, noVNC und dann den Scraper
# ============================================================
set -e

echo "[MyFeed] ─── Starte virtuellen X11-Display (Xvfb :99) ───"
# Stale Lock-Dateien aus vorherigen Runs entfernen (Container-Restart ohne sauberes Beenden)
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
rm -f /chrome-profile/SingletonLock /chrome-profile/SingletonCookie /chrome-profile/SingletonSocket
Xvfb :99 -screen 0 1280x900x24 -ac +extension GLX +render -noreset &
sleep 3

export DISPLAY=:99

echo "[MyFeed] ─── Starte VNC-Server (x11vnc) ───"
x11vnc -display :99 -nopw -listen localhost -xkb -forever -shared -bg -quiet 2>/dev/null
sleep 1

echo "[MyFeed] ─── Starte noVNC-Proxy auf Port 6080 ───"
websockify --web=/usr/share/novnc/ 6080 localhost:5900 &
sleep 1

echo "[MyFeed] ════════════════════════════════════════════════"
echo "[MyFeed]  noVNC bereit: http://<SERVER-IP>:6080/vnc.html"
echo "[MyFeed]  Im Browserfenster einmalig bei Google anmelden."
echo "[MyFeed] ════════════════════════════════════════════════"

exec python /app/scraper.py

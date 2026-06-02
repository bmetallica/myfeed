"""
scraper.py – MyFeed Android Chrome Scraper (noVNC Edition)
============================================================
Läuft im Container mit Xvfb + noVNC. Öffnet Chromium mit persistentem
Profil (Docker-Volume /chrome-profile).

Ersteinrichtung:
  1. noVNC aufrufen: http://<SERVER-IP>:6080/vnc.html
  2. Im Browserfenster bei Google anmelden und Chrome-Sync aktivieren
  3. Danach scrapet der Container automatisch alle SCRAPER_INTERVAL_SECS Sekunden

Konfiguration via Umgebungsvariablen:
  ENABLE_ANDROID_SCRAPER   true|false (Standard: false)
  GATEWAY_URL              http://gateway-api:8000
  API_BEARER_TOKEN         Bearer-Token aus .env
  SCRAPER_INTERVAL_SECS    Pause zwischen Durchläufen (Standard: 300)
  CHROME_PROFILE_DIR       Pfad zum persistenten Chromium-Profil (Standard: /chrome-profile)
"""

import os
import time
import logging
import requests
from datetime import datetime, timezone

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
log = logging.getLogger("android-scraper")

# ── Konfiguration ─────────────────────────────────────────────────────────────
ENABLE        = os.environ.get("ENABLE_ANDROID_SCRAPER", "false").lower() == "true"
GATEWAY_URL   = os.environ.get("GATEWAY_URL", "http://gateway-api:8000").rstrip("/")
TOKEN         = os.environ.get("API_BEARER_TOKEN", "")
INTERVAL_SECS = int(os.environ.get("SCRAPER_INTERVAL_SECS", "300"))
PROFILE_DIR   = os.environ.get("CHROME_PROFILE_DIR", "/chrome-profile")

ACTIVITY_URL = "https://myactivity.google.com/myactivity?hl=de"

_AUTH = {"Authorization": f"Bearer {TOKEN}"}

# JavaScript: Externe Links aus der MyActivity-Seite extrahieren
_EXTRACT_JS = r"""
() => {
    const results = [];
    const seen    = new Set();
    const skip    = ["google.com","google.de","gstatic.com","googleapis.com",
                     "ggpht.com","googleusercontent.com"];

    const MONTHS = {
        'januar':1,'februar':2,'m\u00e4rz':3,'april':4,'mai':5,'juni':6,
        'juli':7,'august':8,'september':9,'oktober':10,'november':11,'dezember':12
    };

    function parseDateDE(text) {
        if (!text) return null;
        const m = (text + '').toLowerCase().match(
            /(\d{1,2})\.\s*(januar|februar|m\u00e4rz|april|mai|juni|juli|august|september|oktober|november|dezember)\s*(\d{4})/
        );
        if (!m) return null;
        return m[3] + '-' + String(MONTHS[m[2]]).padStart(2,'0') + '-' + String(m[1]).padStart(2,'0');
    }

    // Sucht einen reinen "HH:MM"-Textknoten irgendwo im Teilbaum (TreeWalker)
    // MyActivity schreibt "12:09 •" (mit Bullet), deshalb: /^HH:MM(\s*•)?$/
    function findTimeInSubtree(root) {
        try {
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
            let tn;
            while ((tn = walker.nextNode())) {
                const m = tn.textContent.trim().match(/^(\d{1,2}:\d{2})(\s*[•·])?$/);
                if (m) return m[1];
            }
        } catch(e) {}
        return null;
    }

    // Löst relative deutsche Datumsangaben auf.
    // WICHTIG: Datums-Header kann mit Zusatztext zusammenkleben, z.B. "HeuteEinige
    // Aktivitäten werden eventuell..." → startsWith statt exakter Vergleich.
    function resolveDateDE(text) {
        if (!text) return null;
        const t = text.trim().toLowerCase();
        const now = new Date();
        if (t.startsWith('heute')) {
            return now.toISOString().slice(0, 10);
        }
        if (t.startsWith('gestern')) {
            const y = new Date(now); y.setDate(y.getDate() - 1);
            return y.toISOString().slice(0, 10);
        }
        // Ausgeschriebenes Datum nur in kurzen Texten suchen (Datums-Header sind kurz)
        if (t.length < 80) return parseDateDE(text);
        return null;
    }

    // Sucht ein deutsches Datum in vorherigen Geschwistern der Vorfahren.
    // Kein Längen-Limit hier – resolveDateDE entscheidet intern.
    function findDateAbove(el) {
        let n = el;
        while (n && n !== document.body) {
            let sib = n.previousElementSibling;
            while (sib) {
                const d = resolveDateDE(sib.textContent.trim());
                if (d) return d;
                sib = sib.previousElementSibling;
            }
            n = n.parentElement;
        }
        return null;
    }

    function findTs(el) {
        // 1. <time datetime="..."> im Vorfahren-Baum
        let n = el;
        for (let i = 0; i < 10; i++) {
            if (!n) break;
            if (n.querySelector) {
                const te = n.querySelector('time[datetime]');
                if (te) return te.getAttribute('datetime');
            }
            n = n.parentElement;
        }
        // 2. Zeit via TreeWalker im Teilbaum + Datum via Geschwister-Scan
        let timeStr = null;
        n = el.parentElement;
        for (let i = 0; i < 8; i++) {
            if (!n || n === document.body) break;
            const t = findTimeInSubtree(n);
            if (t) {
                const [h, mi] = t.split(':');
                timeStr = 'T' + h.padStart(2,'0') + ':' + mi + ':00';
                break;
            }
            n = n.parentElement;
        }
        const date = findDateAbove(el);
        return date ? date + (timeStr || 'T00:00:00') : null;
    }

    // Bereinigt den Titel: entfernt das Google-Prefix
    // "Bild des Aktivitätselements \u201e[ECHTER TITEL] aufgerufen".
    function cleanTitle(a) {
        const al = a.getAttribute('aria-label') || '';
        const idx = al.indexOf('\u201e');
        if (idx >= 0) {
            const raw = al.slice(idx + 1)
                .replace(/\s+aufgerufen["\u201c\u201d\u0022]\.?\s*$/, '')
                .trim();
            if (raw.length >= 4) return raw;
        }
        return (a.getAttribute('data-title') || a.title || a.textContent).trim();
    }

    document.querySelectorAll('a[href]').forEach(a => {
        const url = a.href;
        if (!url || !url.startsWith('http')) return;
        try {
            const parsed = new URL(url);
            const host   = parsed.hostname;
            // Ausnahmen: Google-Suche (/search mit beliebigen Parametern),
            // Play Store (beliebige play.google.com-URLs) und Gemini/AI-Modus
            const isGoogleSearch = parsed.pathname === '/search';
            const isPlayStore    = host === 'play.google.com';
            const isGemini       = host === 'gemini.google.com';
            if (!isGoogleSearch && !isPlayStore && !isGemini &&
                skip.some(d => host === d || host.endsWith('.' + d))) return;
        } catch { return; }

        const title = cleanTitle(a);
        if (!title || title.length < 4) return;
        const ts  = findTs(a);
        const key = url + '|' + (ts || '');   // Selbe URL zu verschiedenen Zeiten = verschiedene Einträge
        if (seen.has(key)) return;
        seen.add(key);
        results.push({ title, url, ts });
    });

    return results;   // Kein slice – alle sichtbaren Einträge zurückgeben
}
"""


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def send_to_gateway(entry: dict) -> bool:
    """Sendet einen Verlaufseintrag ans Gateway."""
    # Echten Besuchszeitpunkt aus History bevorzugen; Fallback: jetzt (UTC)
    ts = entry.get("ts") or datetime.now(timezone.utc).isoformat()
    try:
        r = requests.post(
            f"{GATEWAY_URL}/api/v1/context",
            headers={**_AUTH, "Content-Type": "application/json"},
            json={
                "source":    "google_activity",
                "title":     entry["title"],
                "url":       entry["url"],
                "timestamp": ts,
            },
            timeout=10,
        )
        return r.status_code in (200, 201)
    except requests.RequestException as exc:
        log.error("Gateway-Fehler: %s", exc)
        return False


def scrape_activity(ctx) -> list[dict] | None:
    """
    Öffnet pro Zyklus einen frischen Tab, damit MyActivity nicht aus dem
    Browser-Cache geladen wird. Der Tab wird nach dem Scrape immer geschlossen.
    Session-Cookies bleiben im persistenten Context erhalten.

    Returns:
        None  → Session abgelaufen, Login via noVNC erforderlich
        []    → angemeldet, aber keine Einträge gefunden
        [...]  → Liste der Einträge
    """
    page = ctx.new_page()
    try:
        try:
            page.goto(ACTIVITY_URL, wait_until="networkidle", timeout=45_000)
        except Exception as exc:
            log.warning("Laden-Timeout (fahre fort): %s", exc)

        # Kurz warten bis dynamische UI-Elemente (inkl. "Weitere laden") fertig gerendert
        time.sleep(3)

        # Prüfen ob Session noch gültig
        url = page.url
        if "accounts.google.com" in url or "signin" in url.lower():
            log.warning("Session abgelaufen – bitte via noVNC neu anmelden.")
            return None

        # Inkrementelles Scrapen: MyActivity hat pro Tagesabschnitt einen
        # "Weitere laden"-Button, der mehrfach geklickt werden muss.
        # Stopp-Bedingung: Button verschwindet (= alle heutigen Einträge geladen).
        # ACHTUNG: "Gestern"-Daten sind oft schon im initialen DOM → dieser
        # Text taugt NICHT als Stopp-Signal.
        #
        # Playwright get_by_text() versagt bei diesem Button (Shadow-DOM / NBSP).
        # Stattdessen: JS-basiertes Klicken + Diagnose per innerText-Dump.
        accumulated: dict[str, dict] = {}   # URL → Eintrag (dedup)

        _JS_CLICK_MORE = """
        () => {
            // Der "Weitere laden"-Button hat zwei Zustände im selben DIV:
            //   "Wird geladen…Weitere laden" (CSS: Spinner sichtbar, Text unsichtbar)
            //   "Weitere laden"              (CSS: Spinner unsichtbar, Text sichtbar)
            // textContent enthält immer beide, daher: endsWith('Weitere laden')
            const normalize = t => (t || '').replace(/\\u00a0/g,' ').trim();
            const seen = new Set();
            // Tiefste passende Elemente bevorzugen (spezifischster Klick)
            let best = null, bestDepth = -1;
            function search(root, depth) {
                for (const el of root.querySelectorAll('div,button,span,a')) {
                    const tc = normalize(el.textContent);
                    if (tc.endsWith('Weitere laden') && el.offsetParent !== null) {
                        if (depth > bestDepth) { best = el; bestDepth = depth; }
                    }
                    if (el.shadowRoot && !seen.has(el)) {
                        seen.add(el);
                        search(el.shadowRoot, depth + 1);
                    }
                }
            }
            search(document, 0);
            if (best) {
                best.scrollIntoView({block:'center'});
                best.click();
                return 'clicked:depth=' + bestDepth + ':tag=' + best.tagName;
            }
            // Debug: welche Texte mit 'Weitere' existieren?
            const hints = [];
            for (const el of document.querySelectorAll('div,button,span')) {
                const t = normalize(el.textContent);
                if (t.includes('Weitere') && el.offsetParent !== null) hints.push(t.slice(0,50));
            }
            return hints.length ? 'notfound:' + hints.slice(0,3).join('|') : 'absent';
        }
        """

        def _collect():
            for e in (page.evaluate(_EXTRACT_JS) or []):
                if e.get("url") and e.get("title"):
                    key = e["url"] + "|" + (e.get("ts") or "")
                    accumulated[key] = e

        _collect()   # Initiale Einträge sichern

        load_more_clicks = 0
        for step in range(100):   # Sicherheitsgrenze; normalerweise früher Stopp
            result = page.evaluate(_JS_CLICK_MORE)
            if result and result.startswith('clicked:'):
                load_more_clicks += 1
                time.sleep(2.0)   # auf Nachladen warten
                prev_count = len(accumulated)
                _collect()
                if len(accumulated) == prev_count:
                    # Keine neuen Einträge → Heute vollständig geladen
                    break
            elif result and result.startswith('notfound:'):
                log.warning("'Weitere laden' DOM vorhanden aber nicht klickbar: %s", result)
                break
            else:
                # 'absent' → kein Button mehr → alle Einträge für diesen Tag geladen
                # Einmal ans Ende scrollen, damit auch Folge-Abschnitte sichtbar
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(1.5)
                _collect()
                break

        entries = list(accumulated.values())
        log.info(
            "Extrahiert: %d Verlaufseinträge (%d × 'Weitere laden' geklickt).",
            len(entries), load_more_clicks,
        )

        # Ein-maliger DOM-Debug-Dump: hilft die Timestamp-Struktur zu verstehen
        import json as _json
        _debug = "/tmp/debug_ts.json"
        if not os.path.exists(_debug) and entries:
            try:
                dom_info = page.evaluate("""
                () => {
                    // Echten History-Eintrag suchen (aria-label mit 'Aktivitätselements' + 'aufgerufen')
                    const link = Array.from(document.querySelectorAll('a[href]')).find(a => {
                        const al = a.getAttribute('aria-label') || '';
                        return al.includes('Aktivit\u00e4tselements') && al.includes('aufgerufen');
                    });
                    if (!link) return { error: 'Kein History-Link gefunden' };
                    const chain = [];
                    let n = link;
                    for (let i = 0; i < 14 && n && n !== document.body; i++, n = n.parentElement) {
                        chain.push({
                            depth: i,
                            tag: n.tagName,
                            ariaLabel: (n.getAttribute && n.getAttribute('aria-label') || '').slice(0, 100),
                            childTagsAndText: n.children
                                ? Array.from(n.children).slice(0, 6).map(c =>
                                    c.tagName + '|' + c.textContent.trim().slice(0, 30))
                                : [],
                            prevSibText: (n.previousElementSibling
                                && n.previousElementSibling.textContent.trim().slice(0, 80)) || null,
                            hasTimeDatetime: n.querySelector ? !!n.querySelector('time[datetime]') : false,
                            textNodeSnippets: (() => {
                                try {
                                    const w = document.createTreeWalker(n, NodeFilter.SHOW_TEXT, null, false);
                                    const snips = []; let tn;
                                    while ((tn = w.nextNode()) && snips.length < 10)
                                        if (tn.textContent.trim()) snips.push(tn.textContent.trim().slice(0, 30));
                                    return snips;
                                } catch { return []; }
                            })()
                        });
                    }
                    return { href: link.href, ariaLabel: link.getAttribute('aria-label'), chain };
                }
                """)
                with open(_debug, 'w', encoding='utf-8') as _f:
                    _json.dump(dom_info, _f, indent=2, ensure_ascii=False)
                log.info("DOM-Debug-Dump gespeichert: %s  (einmalig)", _debug)
            except Exception as _e:
                log.debug("Debug-Dump fehlgeschlagen: %s", _e)

        return entries

    except Exception as exc:
        log.error("Scrape-Fehler: %s", exc)
        return []
    finally:
        page.close()  # Tab schließen – nächster Zyklus bekommt frische Seite


# ── Hauptschleife ─────────────────────────────────────────────────────────────

def run() -> None:
    if not ENABLE:
        log.info(
            "ENABLE_ANDROID_SCRAPER=false – Scraper deaktiviert. "
            "Container verbleibt im Standby."
        )
        while True:
            time.sleep(86400)
        return

    if not TOKEN:
        log.error("API_BEARER_TOKEN nicht gesetzt – Scraper kann nicht starten.")
        return

    from playwright.sync_api import sync_playwright

    os.makedirs(PROFILE_DIR, exist_ok=True)
    log.info(
        "Chromium-Profil: %s | Gateway: %s | Intervall: %ds",
        PROFILE_DIR, GATEWAY_URL, INTERVAL_SECS,
    )

    seen: set[str] = set()

    with sync_playwright() as p:
        log.info("Öffne Chromium mit persistentem Profil ...")
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1280,900",
            ],
            viewport={"width": 1280, "height": 900},
        )

        login_warned = False

        while True:
            try:
                entries = scrape_activity(ctx)

                if entries is None:
                    if not login_warned:
                        log.warning(
                            "Nicht angemeldet! Bitte noVNC öffnen und bei Google einloggen:\n"
                            "  → http://<SERVER-IP>:6080/vnc.html\n"
                            "  (Danach wird automatisch weitergemacht)"
                        )
                        login_warned = True
                    time.sleep(30)
                    continue

                login_warned = False

                ts_found = sum(1 for e in entries if e.get("ts"))
                if ts_found == 0:
                    log.warning(
                        "Keine Timestamps aus MyActivity-DOM extrahierbar "
                        "(%d Einträge) – Importzeit wird als Fallback genutzt.",
                        len(entries),
                    )
                else:
                    log.info("Timestamps aus Seite: %d/%d Einträge", ts_found, len(entries))

                new_count = 0
                for entry in entries:
                    url = entry.get("url", "")
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    if send_to_gateway(entry):
                        new_count += 1

                log.info(
                    "Durchlauf abgeschlossen: %d neue Einträge gesendet. "
                    "Nächster Durchlauf in %ds.",
                    new_count, INTERVAL_SECS,
                )
                time.sleep(INTERVAL_SECS)

            except Exception as exc:
                log.error("Unerwarteter Fehler: %s – Warte 30s.", exc)
                time.sleep(30)


if __name__ == "__main__":
    run()

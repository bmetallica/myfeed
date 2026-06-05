# MyFeed – Systemdokumentation

> **Personalisiertes KI-Newsfeed-System** – Selbstgehostetes Kontext-Erfassungssystem auf Basis von FastAPI, PostgreSQL/pgvector, Browser-Extension (Chrome & Firefox MV3) und VSCode-Extension.

---

## Inhaltsverzeichnis

1. [Systemübersicht](#1-systemübersicht)
2. [Architektur](#2-architektur)
3. [Verzeichnisstruktur](#3-verzeichnisstruktur)
4. [Schnellstart](#4-schnellstart)
5. [Konfiguration (.env)](#5-konfiguration-env)
6. [Docker-Setup](#6-docker-setup)
7. [Datenbank](#7-datenbank)
8. [API Gateway (FastAPI)](#8-api-gateway-fastapi)
9. [Browser-Extension](#9-browser-extension)
10. [VSCode-Extension](#10-vscode-extension)
11. [Android-Scraper](#11-android-scraper)
12. [Sicherheit](#12-sicherheit)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Systemübersicht

MyFeed ist ein **selbstgehostetes Kontext-Erfassungssystem**, das Browsing-Aktivitäten, IDE-Nutzung und andere Datenquellen als strukturierte Ereignisse speichert. Diese Ereignisse bilden die Rohdatenbasis für einen personalisierten KI-Newsfeed.

```
┌─────────────────────┐
│  Browser-Extension  │  Chrome & Firefox MV3
│  (Dwell-Timer,      │  source: browser_chrome / browser_history / search_*
│   Sucherfassung,    │
│   History-Sync)     │
└──────────┬──────────┘
           │
┌──────────┴──────────┐
│  VSCode-Extension   │  TypeScript, VSCode API
│  (Datei-Dwell,      │  source: vscode
│   Workspace-Info)   │
└──────────┬──────────┘
           │  POST /api/v1/context
           │  Bearer Token Auth
           ▼
┌─────────────────────┐         ┌─────────────────────┐
│  FastAPI Gateway    │────────►│  PostgreSQL         │
│  (gateway-api)      │  INSERT │  + pgvector         │
└──────────┬──────────┘         └─────────────────────┘
           │
           ▼
┌─────────────────────┐
│  Ollama (extern)    │  Tag-Generierung + Re-Ranking
└──────────┬──────────┘
           │
           ▼
┌──────────────────────────────┐
│  DuckDuckGo / SearXNG        │  News-Suche
└──────────┬───────────────────┘
           │
           ▼
       RSS-Feed  +  Admin-Dashboard (Port 7999)
```

---

## 2. Architektur

### Prinzipien

| Prinzip | Umsetzung |
|---|---|
| **Entkopplung** | Ingest und Vektorisierung laufen in getrennten Prozessen |
| **Einfachheit** | Ein `docker compose up -d` startet das gesamte System |
| **Sicherheit** | Jeder API-Request erfordert Bearer-Token; DB nicht nach außen exponiert |
| **Erweiterbarkeit** | Beliebige HTTP-Clients können den gleichen Endpunkt nutzen |

### Source-Labels

Jedes Kontext-Ereignis trägt ein `source`-Feld, das den Ursprung kennzeichnet:

| Source | Erzeugt durch | Beschreibung |
|---|---|---|
| `browser_chrome` | Browser-Extension | Aktiver Tab, Dwell-Schwelle erreicht |
| `browser_history` | Browser-Extension | Chrome-Verlauf-Sync (alle 15 min) |
| `search_google` | Browser-Extension | Erkannte Google-Suchanfrage |
| `search_ddg` | Browser-Extension | DuckDuckGo-Suchanfrage |
| `search_github` | Browser-Extension | GitHub-Suchanfrage |
| `search_youtube` | Browser-Extension | YouTube-Suchanfrage |
| `search_bing` / `search_npm` / … | Browser-Extension | Weitere Suchmaschinen |
| `vscode` | VSCode-Extension | Aktive Datei oder Workspace |
| `google_activity` | Android-Scraper | Google MyActivity |

### Tag-Generierung (zweistufig)

1. **Kategorisierung**: Ollama analysiert alle Browsing-Titel des Tages → Kategorien mit Gewicht 0–10 (z.B. `{"IT/Security": 8, "Gaming": 3}`)
2. **Tag-Extraktion**: Pro Kategorie generiert Ollama 3–5 spezifische Tags mit Einzelgewicht

**Effektiv-Gewicht-Formel:**
```
effective_weight = max(1, min(10, round(tag_weight × category_weight / 10)))
```

---

## 3. Verzeichnisstruktur

```
myfeed/
├── .env                         # Secrets (nie einchecken)
├── .env.example                 # Vorlage
├── docker-compose.yml           # Container-Orchestrierung
│
├── db/
│   └── init.sql                 # DB-Schema: Tabellen, Erweiterungen, Indizes
│
├── gateway/
│   ├── Dockerfile               # Python 3.12-slim
│   ├── requirements.txt
│   └── main.py                  # FastAPI-Anwendung (2200+ Zeilen)
│
├── frontend/
│   └── index.html               # Admin-Dashboard (Single-Page, vanilla JS)
│
├── extension/                   # Browser-Extension (Chrome & Firefox MV3)
│   ├── manifest.json            # MV3-Manifest
│   ├── background.js            # Service Worker: Dwell-Timer, Suche, History-Sync
│   ├── options.html             # Einstellungs-Popup
│   ├── options.js               # Formular-Logik
│   ├── content_cookie_bridge.js # Content Script: Cookie-Weiterleitung
│   └── icons/                   # icon16.png, icon48.png, icon128.png
│
├── vscode-extension/            # VSCode-Extension (TypeScript)
│   ├── package.json             # Extension-Manifest, Settings-Schema
│   ├── tsconfig.json
│   ├── src/
│   │   ├── extension.ts         # Aktivierung, Dwell-Timer, Events
│   │   ├── gateway.ts           # HTTP-Client (Node http/https)
│   │   ├── collector.ts         # Kontextextraktion (Datei, Git, README)
│   │   └── settings.ts          # Config-Interface
│   └── out/                     # Kompiliertes JS (via npm run compile)
│
├── android_scraper/             # Google-MyActivity-Scraper
│   ├── Dockerfile
│   └── scraper.py
│
├── searxng/                     # SearXNG-Konfiguration
└── debug/                       # Debug-Ausgaben der Tag-Generierung
```

---

## 4. Schnellstart

### Voraussetzungen

- Docker ≥ 24 und Docker Compose V2
- Ollama erreichbar mit installiertem Modell
- Für Browser-Extension: Chrome 109+ oder Firefox 109+
- Für VSCode-Extension: VSCode 1.82+

### Schritt 1 – Repository klonen

```bash
git clone https://github.com/bmetallica/myfeed.git
cd myfeed
```

### Schritt 2 – .env befüllen

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"  # Token generieren
cp .env.example .env
nano .env
```

Mindestpflichtfelder:

```dotenv
API_BEARER_TOKEN=<erzeugter_token>
POSTGRES_PASSWORD=<sicheres_passwort>
```

### Schritt 3 – System starten

```bash
docker compose up -d
```

### Schritt 4 – Clients installieren

Im Admin-Dashboard (**http://localhost:7999**) unter **Extension Download**:

- **↓ Chrome (.zip)** – als entpackte Extension laden (`chrome://extensions` → Entwicklermodus)
- **↓ Firefox (.xpi)** – via `about:debugging` temporär laden
- **↓ VSCode (.vsix)** – `Strg+Shift+P` → *"Install from VSIX…"*

Alle drei Pakete sind bereits mit Gateway-URL und Token vorkonfiguriert.

---

## 5. Konfiguration (.env)

| Variable | Standard | Beschreibung |
|---|---|---|
| `GATEWAY_PORT` | `8000` | Externer Port des API-Gateways |
| `API_BEARER_TOKEN` | *(leer)* | Geheimes Token für alle Clients – min. 32 Zeichen |
| `FRONTEND_PORT` | `7999` | Port des Admin-Dashboards |
| `POSTGRES_USER` | `myfeed` | Datenbankbenutzer |
| `POSTGRES_PASSWORD` | *(leer)* | Datenbankpasswort |
| `POSTGRES_DB` | `myfeed` | Datenbankname |
| `ENABLE_ANDROID_SCRAPER` | `false` | Android-Scraper aktivieren |
| `SCRAPER_INTERVAL_SECS` | `300` | Scrape-Intervall in Sekunden |

> `.env` enthält Secrets und darf **niemals** eingecheckt werden.

---

## 6. Docker-Setup

### Container

| Service | Image | Port | Zweck |
|---|---|---|---|
| `db` | `pgvector/pgvector:pg16` | intern | PostgreSQL 16 + pgvector |
| `gateway-api` | Lokales Build (Python 3.12-slim) | 8000 | FastAPI-Backend |
| `frontend` | `nginx:alpine` | 7999 | Admin-Dashboard |
| `searxng` | `searxng/searxng` | intern | Metasuchmaschine |
| `android-scraper` | Lokales Build | intern + 6080 | Google-MyActivity-Scraper |

### Volumes (gateway-api)

```yaml
- ./extension:/app/extension:ro           # Browser-Extension-Dateien
- ./vscode-extension:/app/vscode-extension:ro  # VSCode-Extension-Dateien
- ./debug:/app/debug                      # Tag-Generierungs-Logs
```

### Nützliche Befehle

```bash
docker compose up -d                   # System starten
docker compose build gateway-api       # Gateway neu bauen (nach main.py-Änderung)
docker compose restart gateway-api     # Gateway neustarten
docker compose logs -f gateway-api     # Logs streamen
docker compose down -v                 # Vollständiger Reset (DATENVERLUST!)
```

---

## 7. Datenbank

### Tabellen

| Tabelle | Beschreibung |
|---|---|
| `context_queue` | Rohdaten aller Kontext-Ereignisse |
| `tags` | KI-generierte und manuelle Interessen-Tags |
| `long_term_tags` | Laufender Mittelwert der Interessen über Zeit |
| `news_results` | Gefundene Artikel mit Tag-Zuordnung |
| `system_settings` | Key-Value-Konfiguration (Ollama-URL, Zeitpläne, …) |

### Tabelle `context_queue`

| Spalte | Typ | Beschreibung |
|---|---|---|
| `id` | UUID PK | Automatisch generierte UUID v4 |
| `source` | VARCHAR(64) | Datenquelle (z.B. `browser_chrome`, `vscode`) |
| `title` | TEXT | Seitentitel, Dateiname oder Suchanfrage |
| `url` | TEXT (nullable) | URL oder `file://`-Pfad |
| `content` | TEXT (nullable) | Extrahierter Seiteninhalt (max. ~2000 Zeichen) |
| `created_at` | TIMESTAMPTZ | Erfassungszeitpunkt (UTC) |
| `processed` | BOOLEAN | `false` = noch nicht vektorisiert |
| `embedding` | VECTOR(384) (nullable) | Sentence-Embedding (KI-Worker) |

### Nützliche Abfragen

```sql
-- Einträge nach Quelle
SELECT source, COUNT(*) FROM context_queue GROUP BY source ORDER BY count DESC;

-- Letzte 24 Stunden
SELECT source, title, created_at FROM context_queue
WHERE created_at > NOW() - INTERVAL '24 hours' ORDER BY created_at DESC;

-- VSCode-Einträge
SELECT title, url, created_at FROM context_queue WHERE source = 'vscode';
```

---

## 8. API Gateway (FastAPI)

### Alle Endpunkte

| Methode | Pfad | Auth | Beschreibung |
|---|---|---|---|
| `GET` | `/health` | Nein | Systemstatus |
| `GET` | `/rss` | Nein | RSS-Feed |
| `POST` | `/api/v1/context` | Bearer | Kontext-Eintrag speichern |
| `GET` | `/api/v1/context` | Bearer | Kontext-Queue abrufen |
| `GET` | `/api/v1/tags` | Bearer | Alle Tags abrufen |
| `POST` | `/api/v1/tags` | Bearer | Manuellen Tag erstellen |
| `DELETE` | `/api/v1/tags/{id}` | Bearer | Tag löschen |
| `POST` | `/api/v1/tags/generate` | Bearer | Tag-Generierung auslösen |
| `GET` | `/api/v1/tags/longterm` | Bearer | Langzeit-Tags abrufen |
| `POST` | `/api/v1/news/search` | Bearer | News-Suche auslösen |
| `GET` | `/api/v1/news` | Bearer | News-Ergebnisse abrufen |
| `GET` | `/api/v1/settings/ollama` | Bearer | Ollama-Einstellungen lesen |
| `PUT` | `/api/v1/settings/ollama` | Bearer | Ollama-Einstellungen speichern |
| `GET` | `/api/v1/settings/news` | Bearer | News-Einstellungen lesen |
| `PUT` | `/api/v1/settings/news` | Bearer | News-Einstellungen speichern |
| `POST` | `/api/v1/download/extension` | Bearer | Vorkonfiguriertes Browser-Paket |
| `POST` | `/api/v1/download/vscode-extension` | Bearer | Vorkonfiguriertes VSCode-Paket (.vsix) |

### Kontext-Payload (`POST /api/v1/context`)

```json
{
  "source":    "vscode",
  "title":     "myfeed/src/extension.ts",
  "url":       "file:///opt/myfeed/vscode-extension/src/extension.ts",
  "content":   "Language: TypeScript\nProject: myfeed\nBranch: main",
  "timestamp": "2026-06-05T09:00:00Z"
}
```

| Feld | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| `source` | string (max. 64) | Ja | Datenquellen-Bezeichner |
| `title` | string | Ja | Titel oder Beschreibung |
| `url` | string | Nein | URL oder `file://`-Pfad |
| `content` | string | Nein | Optionaler Kontext (max. ~2000 Zeichen) |
| `timestamp` | ISO-8601 | Nein | Client-Zeitstempel; fällt auf Server-Zeit zurück |

---

## 9. Browser-Extension

### Dateien

| Datei | Zweck |
|---|---|
| `manifest.json` | MV3-Manifest (Chrome & Firefox 109+) |
| `background.js` | Service Worker: Dwell-Timer, Suche, History-Sync |
| `options.html/js` | Einstellungs-UI und Formular-Logik |
| `content_cookie_bridge.js` | Cookie-Weiterleitung für Android-Scraper-Login |

### Kumulativer Dwell-Timer

```
Tab aktiv → Zeit akkumulieren
Tab gewechselt → Timer pausieren (Zeit bleibt erhalten)
Tab zurück → Timer fortsetzen
Schwelle (dwellSecs) erreicht → POST /api/v1/context
```

- Standard-Schwelle: 15 Sekunden (konfigurierbar)
- Tab-Wechsel unterbricht nicht den Fortschritt, sondern pausiert ihn
- Cooldown pro URL: kein Doppel-Send innerhalb von `cooldownMins` (Standard: 30 min)

### Blocklist

Statt einer Allowlist gibt es eine **Blocklist** – alles wird erfasst **außer** den gelisteten Domains:
- Zahlungsdienste & Banking (PayPal, Stripe, DKB, …)
- Shopping (Amazon, eBay, Zalando, …)
- Social-Media-Feeds (Facebook, Instagram, TikTok, …)
- Kommunikation (WhatsApp, Discord, …)
- Entertainment (Netflix, Spotify, …)

Die Blocklist ist in den Extension-Einstellungen anpassbar.

### Sucherfassung

Suchanfragen auf diesen Plattformen werden **sofort** (ohne Dwell-Timer) gesendet:

| Plattform | Source-Label |
|---|---|
| Google | `search_google` |
| Bing | `search_bing` |
| DuckDuckGo | `search_ddg` |
| YouTube | `search_youtube` |
| GitHub | `search_github` |
| Stack Overflow | `search_stackoverflow` |
| npm / PyPI / MDN | `search_npm` / `search_pypi` / `search_mdn` |
| Ecosia | `search_ecosia` |

5-Minuten-Cooldown pro Query+Engine-Kombination.

### Chrome-Verlauf-Sync

- Läuft alle 15 Minuten via `chrome.alarms`
- Sendet Chrome-Verlauf der letzten 7 Tage mit `source: browser_history`
- Überspringt Google-eigene Domains (google.com, youtube.com, …)
- Beim ersten Sync: komplette 7-Tage-Rückschau (max. 500 Einträge)

### Einstellungen (`chrome.storage.local`)

| Schlüssel | Standard | Beschreibung |
|---|---|---|
| `gatewayUrl` | `http://localhost:8000` | Gateway-URL |
| `bearerToken` | *(leer)* | API Bearer Token |
| `dwellSecs` | `15` | Dwell-Schwelle in Sekunden |
| `cooldownMins` | `30` | Cooldown pro URL in Minuten |
| `blocklist` | *(Standard-Liste)* | Kommaseparierte Domain-Blocklist |
| `captureSearches` | `true` | Sucherfassung aktivieren |

### Vorkonfigurierter Download

`POST /api/v1/download/extension` mit `{"platform": "chrome"|"firefox", "gateway_url": "..."}` gibt ein ZIP/XPI zurück, das eine `myfeed_defaults.json` mit vorausgefüllter Gateway-URL und Token enthält. Die Extension liest diese beim ersten Start automatisch ein.

---

## 10. VSCode-Extension

### Quellcode

```
vscode-extension/src/
├── extension.ts    # Hauptlogik: activate/deactivate, Dwell-Timer, Events
├── gateway.ts      # HTTP-Client (Node http/https-Modul, keine externen Deps)
├── collector.ts    # Payload-Bau: Dateiinfo, Git-Branch, README-Snippet
└── settings.ts     # getConfig() → typisiertes Config-Interface
```

### Dwell-Timer-Logik

```
onDidChangeActiveTextEditor → Datei wechselt:
  1. Alten setInterval stoppen
  2. Neuen setInterval starten (dwellSeconds)
  3. Jeder Tick → maybeSendDocument()

maybeSendDocument():
  - Abbruch wenn: disabled, kein Token, Cooldown aktiv, Datei geblockt
  - Sonst: buildFilePayload() → POST /api/v1/context
  - Bei Erfolg: Datei in Cooldown-Map eintragen
```

### Kontextextraktion (`collector.ts`)

Für jede Datei wird ein Payload gebaut:

```json
{
  "source": "vscode",
  "title": "myfeed/src/extension.ts",
  "url": "file:///opt/myfeed/vscode-extension/src/extension.ts",
  "content": "Language: TypeScript\nProject: myfeed\nBranch: main\nREADME: ..."
}
```

- **Git-Branch**: Via direktem Lesen von `.git/HEAD` (kein Shell-Spawn)
- **README-Snippet**: Erste 500 Zeichen des Workspace-README, gecacht pro Session
- **Blocklist**: Pfad-Segment-Vergleich (z.B. `node_modules` trifft nur den Ordner, nicht Projekte mit "modules" im Namen)

### Vorkonfigurierter Download

`POST /api/v1/download/vscode-extension` mit `{"gateway_url": "..."}` baut ein VSIX-Paket (ZIP mit VSIX-Manifest) mit eingebetteter `defaults.json`. Die Extension liest diese beim ersten Start und trägt URL + Token automatisch in die globalen VSCode-Settings ein.

### VSCode-Settings

| Setting | Standard | Beschreibung |
|---|---|---|
| `myfeed.gatewayUrl` | `http://localhost:8000` | Gateway-URL |
| `myfeed.bearerToken` | *(leer)* | API Bearer Token (secret) |
| `myfeed.dwellSeconds` | `15` | Mindestverweilzeit pro Datei |
| `myfeed.cooldownMinutes` | `30` | Cooldown pro Dateipfad |
| `myfeed.blocklist` | `["node_modules", ".git", …]` | Ignorierte Pfad-Segmente |
| `myfeed.enabled` | `true` | Extension aktivieren/deaktivieren |

### Build

```bash
cd vscode-extension
npm install
npm run compile    # → out/*.js
```

Node.js ≥ 18 erforderlich. Keine externen Laufzeit-Abhängigkeiten (nur `@types/vscode` als devDep).

---

## 11. Android-Scraper

Läuft als Docker-Container mit Chromium und liest Google MyActivity:

- Aktivierung: `ENABLE_ANDROID_SCRAPER=true` in `.env`
- Einmaliges Google-Login über noVNC (Port 6080)
- Scrapet alle `SCRAPER_INTERVAL_SECS` Sekunden (Standard: 300)
- Sendet mit `source: google_activity`
- Session wird im persistenten Volume `chrome-profile` gespeichert

---

## 12. Sicherheit

| Bedrohung | Gegenmaßnahme |
|---|---|
| Unbefugter API-Zugriff | Bearer-Token auf allen Schreib-Endpunkten |
| Timing-Angriff | `hmac.compare_digest` (konstante Laufzeit) |
| SQL-Injection | Parametrisierte Queries via psycopg2 |
| DB-Exposition | Kein Port-Mapping für DB-Container |
| Secrets in Code | Ausschließlich via `.env` (niemals einchecken) |
| Token in VSCode-Extension | Gespeichert als `secret: true` in VSCode Settings |

**Empfehlungen für den Produktionsbetrieb:**
- Reverse Proxy mit TLS (nginx / Caddy / Traefik)
- Rate Limiting vorschalten
- `API_BEARER_TOKEN` regelmäßig rotieren
- Swagger-UI deaktivieren: `docs_url=None` in `main.py`

---

## 13. Troubleshooting

### Gateway startet nicht

```bash
docker compose logs gateway-api
```
Häufige Ursachen: `DATABASE_URL` falsch, DB nicht healthy.

### Extension sendet nichts (Browser)

1. Devtools öffnen: Rechtsklick auf Extension-Icon → Hintergrundseite → Konsole
2. Prüfen: Bearer-Token konfiguriert? Gateway-URL korrekt? Domain auf Blocklist?
3. Tab muss ≥ dwellSecs kumuliert aktiv gewesen sein

### VSCode-Extension sendet nichts

1. `Strg+Shift+P` → **"MyFeed: Status anzeigen"** → Output-Channel prüfen
2. `Strg+Shift+P` → **"MyFeed: Gateway-Verbindung testen"**
3. Output zeigt: `Dwell-Timer gestartet` → Timer läuft, ggf. 15s warten
4. Output zeigt: `Geblockt` → Datei-Pfad enthält Blocklist-Eintrag
5. Datei muss `file://`-Schema haben (keine Output-Channel-Dokumente o.ä.)

### Gateway-Endpoint nicht gefunden (404)

Nach Änderungen an `gateway/main.py` muss das Image **neu gebaut** werden:
```bash
docker compose build gateway-api && docker compose up -d gateway-api
```
Ein `restart` allein reicht nicht – der Code ist im Image eingebacken.

### Alle Container neu starten

```bash
docker compose restart
```

### Vollständiger Reset (Datenverlust!)

```bash
docker compose down -v
docker compose up -d
```

# MyFeed – Systemdokumentation

> **Personalisiertes KI-Newsfeed-System** – Selbstgehostetes Kontext-Erfassungssystem auf Basis von FastAPI, PostgreSQL/pgvector, Browser-Extension (Chrome & Firefox MV3), VSCode-Extension und Embedding Worker.

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
12. [MyActivities Timeline](#12-myactivities-timeline)
13. [Embedding Worker](#13-embedding-worker)
14. [Sicherheit](#14-sicherheit)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. Systemübersicht

MyFeed ist ein **selbstgehostetes Kontext-Erfassungssystem**, das Browsing-Aktivitäten, IDE-Nutzung und andere Datenquellen als strukturierte Ereignisse speichert. Diese Ereignisse bilden die Rohdatenbasis für einen personalisierten KI-Newsfeed und eine persönliche Aktivitäts-Zeitachse.

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
┌─────────────────────┐         ┌──────────────────────┐
│  FastAPI Gateway    │────────►│  PostgreSQL          │
│  (gateway-api)      │  INSERT │  + pgvector          │
└──────────┬──────────┘         └──────────┬───────────┘
           │                               │
           │                    ┌──────────┴───────────┐
           │                    │  Embedding Worker    │
           │                    │  BAAI/bge-small-en   │
           │                    │  (384-dim Vektoren)  │
           │                    └──────────────────────┘
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
       RSS-Feed  +  Admin-Dashboard (Port 7999)  +  MyActivities-Timeline
```

---

## 2. Architektur

### Prinzipien

| Prinzip | Umsetzung |
|---|---|
| **Entkopplung** | Ingest, Vektorisierung und Timeline laufen in getrennten Prozessen |
| **Einfachheit** | Ein `make build` oder `docker compose up -d` startet das gesamte System |
| **Sicherheit** | Jeder API-Request erfordert Bearer-Token; DB nicht nach außen exponiert |
| **Erweiterbarkeit** | Beliebige HTTP-Clients können den gleichen Endpunkt nutzen |

### Source-Labels

Jedes Kontext-Ereignis trägt ein `source`-Feld:

| Source | Erzeugt durch | Beschreibung |
|---|---|---|
| `browser_chrome` | Browser-Extension | Aktiver Tab, Dwell-Schwelle erreicht |
| `browser_history` | Browser-Extension | Chrome-Verlauf-Sync (alle 15 min, visitCount ≥ 2) |
| `search_google` | Browser-Extension | Erkannte Google-Suchanfrage |
| `search_ddg` | Browser-Extension | DuckDuckGo-Suchanfrage |
| `search_github` | Browser-Extension | GitHub-Suchanfrage |
| `search_youtube` | Browser-Extension | YouTube-Suchanfrage |
| `search_bing` / `search_npm` / … | Browser-Extension | Weitere Suchmaschinen |
| `vscode` | VSCode-Extension | Aktive Datei oder Workspace |
| `google_activity` | Android-Scraper | Google MyActivity |

### Tag-Generierung (zweistufig)

1. **Kategorisierung**: Ollama analysiert alle Browsing-Titel des Tages → Kategorien mit Gewicht 0–10
2. **Tag-Extraktion**: Pro Kategorie generiert Ollama 3–5 spezifische Tags mit Einzelgewicht

**Tag-Normalisierung**: Whitespace wird kollabiert, Duplikate werden case-insensitiv dedupliziert.

**Effektiv-Gewicht-Formel:**
```
effective_weight = max(1, min(10, round(tag_weight × category_weight / 10)))
```

---

## 3. Verzeichnisstruktur

```
myfeed/
├── .env                         # Secrets (nie einchecken)
├── .env.example                 # Vorlage mit allen Variablen
├── .gitignore
├── docker-compose.yml           # Container-Orchestrierung (6 Services)
├── Makefile                     # up, build, rebuild, logs, down, reset, ps
│
├── db/
│   └── init.sql                 # DB-Schema: Tabellen, Erweiterungen, Indizes
│
├── gateway/
│   ├── Dockerfile               # Python 3.12-slim
│   ├── requirements.txt
│   └── main.py                  # FastAPI-Anwendung
│
├── frontend/
│   ├── index.html               # Admin-Dashboard (Single-Page, vanilla JS)
│   └── timeline.html            # MyActivities-Zeitachse (Single-Page, vanilla JS)
│
├── extension/                   # Browser-Extension (Chrome & Firefox MV3)
│   ├── manifest.json            # MV3-Manifest
│   ├── background.js            # Service Worker: Dwell-Timer, Suche, History-Sync
│   ├── options.html             # Einstellungs-Popup
│   ├── options.js               # Formular-Logik
│   ├── content_cookie_bridge.js # Content Script: Cookie-Weiterleitung
│   └── icons/
│
├── vscode-extension/            # VSCode-Extension (TypeScript)
│   ├── package.json             # Extension-Manifest, Settings-Schema
│   ├── tsconfig.json
│   ├── src/
│   │   ├── extension.ts         # Aktivierung, Dwell-Timer, Events
│   │   ├── gateway.ts           # HTTP-Client (Node http/https)
│   │   ├── collector.ts         # Kontextextraktion (Datei, Git, README)
│   │   └── settings.ts          # Config-Interface
│   └── out/                     # Kompiliertes JS (via npm run compile, in .gitignore)
│
├── embedding_worker/            # Asynchroner Vektor-Berechnungs-Worker
│   ├── Dockerfile               # Python 3.12-slim
│   ├── requirements.txt         # fastembed, psycopg2-binary
│   └── worker.py                # FOR UPDATE SKIP LOCKED Batch-Verarbeitung
│
├── android_scraper/             # Google-MyActivity-Scraper
│   ├── Dockerfile
│   └── scraper.py
│
├── searxng/                     # SearXNG-Konfiguration
└── debug/                       # Debug-Ausgaben der Tag-Generierung (via ENABLE_DEBUG_LOGS)
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
make build
# oder: docker compose up -d --build
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
| `ENABLE_DEBUG_LOGS` | `true` | Tag-Generierungs-Debugging in `debug/` schreiben |
| `WORKER_BATCH_SIZE` | `32` | Batch-Größe des Embedding Workers |
| `WORKER_POLL_SECS` | `30` | Poll-Intervall des Embedding Workers in Sekunden |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Embedding-Modell (muss 384-dim ausgeben) |

> `.env` enthält Secrets und darf **niemals** eingecheckt werden. `.gitignore` schließt sie aus.

---

## 6. Docker-Setup

### Container

| Service | Image | Port | Zweck |
|---|---|---|---|
| `db` | `pgvector/pgvector:pg16` | intern | PostgreSQL 16 + pgvector |
| `gateway-api` | Lokales Build (Python 3.12-slim) | 8000 | FastAPI-Backend |
| `frontend` | `nginx:alpine` | 7999 | Admin-Dashboard + Timeline |
| `embedding-worker` | Lokales Build (Python 3.12-slim) | – | Vektorberechnung |
| `searxng` | `searxng/searxng` | intern | Metasuchmaschine |
| `android-scraper` | Lokales Build | intern + 6080 | Google-MyActivity-Scraper |

### Volumes

```yaml
# gateway-api
- ./extension:/app/extension:ro              # Browser-Extension-Dateien (Download)
- ./vscode-extension:/app/vscode-extension:ro # VSCode-Extension-Dateien (Download)
- ./debug:/app/debug                          # Tag-Generierungs-Logs

# embedding-worker
- embedding-models:/models                   # Modell-Cache (verhindert erneuten Download)
```

### Makefile-Targets

```bash
make up       # docker compose up -d
make build    # docker compose build && up -d
make rebuild  # docker compose build --no-cache && up -d
make logs     # docker compose logs -f
make down     # docker compose down
make reset    # docker compose down -v   (löscht Datenbankdaten!)
make ps       # docker compose ps
```

### Manuell (ohne Make)

```bash
docker compose build gateway-api      # Nach main.py-Änderungen
docker compose up -d gateway-api      # Gateway neustarten
docker compose logs -f embedding-worker  # Worker-Logs verfolgen
```

> **Wichtig:** Änderungen an `main.py` erfordern `docker compose build gateway-api`, nicht nur `restart` – der Code ist im Image eingebacken.

---

## 7. Datenbank

### Tabellen

| Tabelle | Beschreibung |
|---|---|
| `context_queue` | Rohdaten aller Kontext-Ereignisse mit Embedding |
| `activity_timeline` | Persönliche Aktivitäts-Zeitachse (dauerhaft) |
| `tags` | KI-generierte und manuelle Interessen-Tags |
| `long_term_tags` | Laufender Mittelwert der Interessen über Zeit |
| `news_results` | Gefundene Artikel mit Tag-Zuordnung |
| `system_settings` | Key-Value-Konfiguration (Ollama-URL, Zeitpläne, Feature-Toggles) |

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
| `embedding` | VECTOR(384) (nullable) | Sentence-Embedding (Embedding Worker) |

### Tabelle `activity_timeline`

| Spalte | Typ | Beschreibung |
|---|---|---|
| `id` | BIGSERIAL PK | Laufende ID |
| `activity_date` | DATE | Datum des Ereignisses (UTC) |
| `activity_ts` | TIMESTAMPTZ | Exakter Zeitstempel |
| `source` | VARCHAR(64) | Datenquelle |
| `title` | TEXT | Titel des Eintrags |
| `url` | TEXT | URL |
| `domain` | VARCHAR(255) | Extrahierte Domain (z.B. `github.com`) |
| `icon_type` | VARCHAR(32) | `web`/`code`/`search`/`video`/`mobile`/`maps`/`social` |
| `context_queue_id` | UUID FK UNIQUE | Verknüpfung zur context_queue |

### `system_settings` – Feature-Toggles

| Key | Standard | Beschreibung |
|---|---|---|
| `timeline_enabled` | `true` | MyActivities-Timeline aktivieren |
| `long_term_tags_enabled` | `false` | Langzeit-Tag-Speicher aktivieren |
| `news_duckduckgo_enabled` | `false` | DuckDuckGo-News aktivieren |
| `news_searxng_enabled` | `false` | SearXNG-News aktivieren |

### Nützliche Abfragen

```sql
-- Einträge nach Quelle
SELECT source, COUNT(*) FROM context_queue GROUP BY source ORDER BY count DESC;

-- Timeline-Aktivitäten nach Tag
SELECT activity_date, COUNT(*) FROM activity_timeline
GROUP BY activity_date ORDER BY activity_date DESC;

-- Aktivitäten nach Typ
SELECT icon_type, COUNT(*) FROM activity_timeline GROUP BY icon_type ORDER BY 2 DESC;

-- Noch nicht vektorisierte Einträge
SELECT COUNT(*) FROM context_queue WHERE processed = false;
```

---

## 8. API Gateway (FastAPI)

### Alle Endpunkte

| Methode | Pfad | Auth | Beschreibung |
|---|---|---|---|
| `GET` | `/health` | Nein | Systemstatus |
| `GET` | `/rss` | Nein | RSS-Feed |
| `POST` | `/api/v1/context` | Bearer | Kontext-Eintrag speichern + Timeline-Insert |
| `GET` | `/api/v1/context` | Bearer | Kontext-Queue abrufen |
| `GET` | `/api/v1/timeline?date=YYYY-MM-DD` | Bearer | Timeline für einen Tag |
| `GET` | `/api/v1/timeline/dates?months=3` | Bearer | Alle Tage mit Einträgen |
| `POST` | `/api/v1/timeline/backfill` | Bearer | Historische Daten einlesen (Background) |
| `GET` | `/api/v1/settings/timeline` | Bearer | Timeline-Einstellungen |
| `PUT` | `/api/v1/settings/timeline` | Bearer | Timeline aktivieren/deaktivieren |
| `GET` | `/api/v1/tags` | Bearer | Alle Tags abrufen |
| `POST` | `/api/v1/tags` | Bearer | Manuellen Tag erstellen |
| `DELETE` | `/api/v1/tags/{id}` | Bearer | Tag löschen |
| `POST` | `/api/v1/tags/generate` | Bearer | Tag-Generierung auslösen |
| `GET` | `/api/v1/tags/longterm` | Bearer | Langzeit-Tags abrufen |
| `POST` | `/api/v1/news/search` | Bearer | News-Suche auslösen |
| `GET` | `/api/v1/news` | Bearer | News-Ergebnisse abrufen |
| `GET/PUT` | `/api/v1/settings/ollama` | Bearer | Ollama-Einstellungen |
| `GET/PUT` | `/api/v1/settings/news` | Bearer | News-Einstellungen |
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
- Cooldown pro URL: kein Doppel-Send innerhalb von `cooldownMins` (Standard: 30 min)

### Blocklist

Statt einer Allowlist gibt es eine **Blocklist** – alles wird erfasst **außer** den gelisteten Domains (Banking, Shopping, Social-Media, Kommunikation, Entertainment). In den Extension-Einstellungen anpassbar.

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
- Überspringt Einträge mit `visitCount < 2` (einmalig geöffnete Seiten werden ignoriert)
- Überspringt Google-eigene Domains

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

`POST /api/v1/download/extension` mit `{"platform": "chrome"|"firefox", "gateway_url": "..."}` gibt ein ZIP/XPI zurück mit eingebetteter `myfeed_defaults.json`. Die Extension liest diese beim ersten Start automatisch ein.

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
- **Blocklist**: Pfad-Segment-Vergleich (kein Substring-Match auf Projektnamen)

### Vorkonfigurierter Download

`POST /api/v1/download/vscode-extension` baut ein VSIX-Paket (ZIP mit VSIX-Manifest) mit eingebetteter `defaults.json`. Die Extension liest diese beim ersten Start und trägt URL + Token automatisch in die globalen VSCode-Settings ein.

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
npm run compile    # → out/*.js  (out/ ist in .gitignore)
```

Node.js ≥ 18 erforderlich. Keine externen Laufzeit-Abhängigkeiten.

---

## 11. Android-Scraper

Läuft als Docker-Container mit Chromium und liest Google MyActivity:

- Aktivierung: `ENABLE_ANDROID_SCRAPER=true` in `.env`
- Einmaliges Google-Login über noVNC (Port 6080)
- Scrapet alle `SCRAPER_INTERVAL_SECS` Sekunden (Standard: 300)
- Sendet mit `source: google_activity`
- Session wird im persistenten Volume `chrome-profile` gespeichert

---

## 12. MyActivities Timeline

### Konzept

Jeder `POST /api/v1/context`-Ingest schreibt automatisch auch einen Eintrag in die `activity_timeline`-Tabelle (wenn `timeline_enabled = true`). Diese Daten werden **nicht täglich gelöscht** und bilden eine langfristige persönliche Aktivitätshistorie.

### Seite

Erreichbar unter **http://localhost:7999/timeline.html**. Die Seite liest ihre Credentials aus `localStorage` (gleiche Keys wie Admin-Dashboard). Beim ersten Aufruf ohne Credentials erscheint ein Auth-Eingabe-Formular.

### Funktionen

| Funktion | Beschreibung |
|---|---|
| Datumsnavigation | ← / → Buttons, Datepicker, Heute-Button |
| Statistiken | Gesamtaktivitäten, aktive Stunden, häufigste Quelle, unique Domains |
| Typ-Filter | Filterbuttons: Alle / Web / Code / Suche / Video / Android / Social / Maps |
| Timeline | Stündliche Gruppierung, farbige Einträge mit Icon, Zeit, Titel, Domain |
| URL-Öffnen | Klick auf Eintrag öffnet URL in neuem Tab |

### Icon-Typen

Die Zuordnung von `source` + URL zu `icon_type` erfolgt im Gateway:

| icon_type | Farbe | Zuordnung |
|---|---|---|
| `web` | Blau | `browser_chrome`, `browser_history` (allgemein) |
| `code` | Hellblau | `vscode`, GitHub/GitLab-URLs |
| `search` | Orange | Alle `search_*`-Quellen |
| `video` | Rot | YouTube-URLs |
| `mobile` | Grün | `google_activity` (allgemein) |
| `maps` | Grün | Google Maps-URLs |
| `social` | Lila | Reddit, Twitter/X, Instagram, Facebook, LinkedIn |

### Admin-Dashboard-Integration

Im Admin-Dashboard (`index.html`):

- **Toggle**: `timeline_enabled` ein/ausschalten → `PUT /api/v1/settings/timeline`
- **Backfill-Button**: Liest alle historischen `context_queue`-Einträge in die Timeline ein
  → `POST /api/v1/timeline/backfill` (läuft im Hintergrund via SQL-INSERT…SELECT)
- **Link**: `📅 MyActivities →` im Header öffnet `timeline.html`

### API

```
GET /api/v1/timeline?date=2026-06-05
→ {"date": "2026-06-05", "entries": [...], "total": 127}

GET /api/v1/timeline/dates?months=3
→ {"dates": [{"date": "2026-06-05", "count": 127}, ...]}

POST /api/v1/timeline/backfill
→ {"status": "backfill gestartet"}
```

---

## 13. Embedding Worker

### Konzept

Der Embedding Worker ist ein eigenständiger Docker-Container, der periodisch unverarbeitete Einträge aus `context_queue` (wo `processed = false`) liest, Sentence-Embeddings berechnet und zurückschreibt.

**Modell:** `BAAI/bge-small-en-v1.5` über `fastembed` (ONNX, kein PyTorch) → 384-dim Vektoren, kompatibel mit der `VECTOR(384)`-Spalte in PostgreSQL/pgvector.

### Verarbeitung

```
1. SELECT ... WHERE processed = false FOR UPDATE SKIP LOCKED LIMIT batch_size
2. Texte aus title + url + content (erste 500 Zeichen) zusammenbauen
3. fastembed.TextEmbedding.embed(texts) → Liste von 384-dim Vektoren
4. UPDATE context_queue SET embedding = vector, processed = true WHERE id = ...
5. COMMIT
6. Wenn keine Einträge: sleep(poll_interval)
```

`FOR UPDATE SKIP LOCKED` erlaubt mehrere parallele Worker-Instanzen ohne Konflikte.

### Konfiguration

| Env-Variable | Standard | Beschreibung |
|---|---|---|
| `DATABASE_URL` | (Pflicht) | PostgreSQL-Verbindungs-URL |
| `WORKER_BATCH_SIZE` | `32` | Einträge pro Batch |
| `WORKER_POLL_SECS` | `30` | Wartezeit wenn Queue leer ist |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | fastembed-Modellname |

### Modell-Cache

Das Modell (~67 MB) wird beim ersten Start heruntergeladen und im Docker-Volume `embedding-models` unter `/models` gecacht. Bei Container-Neustarts wird es nicht erneut geladen.

### Logs

```bash
docker compose logs -f embedding-worker
# 2026-06-05 09:07:22 [INFO] myfeed.worker – Modell geladen.
# 2026-06-05 09:07:23 [INFO] myfeed.worker – 32 Einträge vektorisiert.
```

---

## 14. Sicherheit

| Bedrohung | Gegenmaßnahme |
|---|---|
| Unbefugter API-Zugriff | Bearer-Token auf allen Schreib-Endpunkten |
| Timing-Angriff | `hmac.compare_digest` (konstante Laufzeit) |
| SQL-Injection | Parametrisierte Queries via psycopg2 |
| DB-Exposition | Kein Port-Mapping für DB-Container |
| Secrets in Code | Ausschließlich via `.env` (in `.gitignore`) |
| Token in VSCode-Extension | Gespeichert als `secret: true` in VSCode Settings |

**Empfehlungen für den Produktionsbetrieb:**
- Reverse Proxy mit TLS (nginx / Caddy / Traefik)
- Rate Limiting vorschalten
- `API_BEARER_TOKEN` regelmäßig rotieren
- `ENABLE_DEBUG_LOGS=false` in Produktion (kein Schreiben von Prompt/Antworten auf Disk)
- Swagger-UI deaktivieren: `docs_url=None` in `main.py`

---

## 15. Troubleshooting

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
3. Output zeigt: `Dwell-Timer gestartet` → Timer läuft, ggf. 15 s warten
4. Output zeigt: `Geblockt` → Datei-Pfad enthält Blocklist-Eintrag

### Timeline zeigt immer die gleichen Einträge

Veralteter Fehler (behoben in Commit `95c24b2`): FastAPI-Parameter brauchte `alias="date"`, damit `?date=YYYY-MM-DD` erkannt wird.

### Gateway-Endpoint nicht gefunden (404)

Nach Änderungen an `gateway/main.py` muss das Image **neu gebaut** werden:
```bash
make build
# oder: docker compose build gateway-api && docker compose up -d gateway-api
```

### Embedding Worker lädt Modell nicht

```bash
docker compose logs embedding-worker
```
Beim ersten Start wird das Modell von Hugging Face geladen (~67 MB) – das kann 30–60 s dauern.
Das Volume `embedding-models` cached das Modell für alle folgenden Starts.

### Vollständiger Reset (Datenverlust!)

```bash
make reset
# oder: docker compose down -v && docker compose up -d
```

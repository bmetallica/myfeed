<p align="center">
  <img src="https://img.shields.io/badge/FastAPI-0.110+-009688?style=for-the-badge&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/PostgreSQL-16+-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" />
  <img src="https://img.shields.io/badge/pgvector-Embeddings-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" />
  <img src="https://img.shields.io/badge/Ollama-local%20LLM-black?style=for-the-badge&logo=ollama&logoColor=white" />
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white" />
  <img src="https://img.shields.io/badge/VSCode-Extension-007ACC?style=for-the-badge&logo=visualstudiocode&logoColor=white" />
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge" />
</p>

<h1 align="center">🗞️ MyFeed</h1>
<p align="center"><strong>Selbstgehosteter, KI-gestützter personalisierter Newsfeed</strong></p>
<p align="center">
  Analysiert Browsing- und IDE-Aktivität · Generiert Interessen-Tags via Ollama · Sucht passende News · Liefert einen RSS-Feed
</p>

---

## Inhaltsverzeichnis

- [Überblick](#überblick)
- [Features](#features)
- [Architektur](#architektur)
- [Services](#services)
- [Schnellstart](#schnellstart)
- [Konfiguration](#konfiguration)
- [Admin-Dashboard](#admin-dashboard)
- [RSS-Feed](#rss-feed)
- [Browser-Extension](#browser-extension)
- [VSCode-Extension](#vscode-extension)
- [MyActivities Timeline](#myactivities-timeline)
- [Datenbank-Schema](#datenbank-schema)
- [API-Endpunkte](#api-endpunkte)
- [Sicherheit](#sicherheit)
- [Roadmap](#roadmap)

---

## Überblick

**MyFeed** ist ein vollständig selbstgehostetes System, das aus deinem Browsing- und IDE-Verhalten automatisch Interessenprofile erstellt und darauf basierend täglich personalisierte News-Artikel sammelt.

Kein Cloud-Dienst, keine Datenweitergabe – alles läuft lokal.

```
Browser-Extension (Chrome/Firefox)
VSCode-Extension
Android-Scraper (Google MyActivity)
           │  Kontext-Ereignisse (POST /api/v1/context)
           ▼
   ┌──────────────────┐
   │  FastAPI Gateway  │  ← Admin-Dashboard (Port 7999)
   │  (Port 8000)      │
   └────────┬─────────┘
            │
     ┌──────┴──────────────┐
     │                     │
     ▼                     ▼
┌─────────┐        ┌──────────────────┐
│PostgreSQL│◄──────│ Embedding Worker │  ← BAAI/bge-small-en-v1.5 (384-dim)
│pgvector  │        └──────────────────┘
└─────────┘
     │
     ▼
┌─────────────────────┐
│  Ollama (extern)    │  Tag-Generierung + Re-Ranking
└──────────┬──────────┘
           │
     ┌─────┴─────┐
     ▼           ▼
┌──────────┐ ┌─────────┐
│DuckDuckGo│ │ SearXNG │  News-Suche
└──────────┘ └─────────┘
           │
           ▼
       RSS-Feed  +  MyActivities Timeline
```

---

## Features

| Feature | Details |
|---|---|
| 🧠 **KI-Tag-Generierung** | Zweistufiger Ollama-Prozess: erst Kategorien, dann gewichtete Tags |
| 📰 **Multi-Source-Suche** | DuckDuckGo + SearXNG, konfigurierbar |
| 🤖 **Ollama Re-Ranking** | LLM bewertet und filtert gefundene Artikel nach Relevanz |
| 🏷️ **Zweischichtiges Tag-System** | Tag-Gewicht × Kategorie-Gewicht = effektive Priorität |
| 🗄️ **Langzeit-Tag-Speicher** | Akkumuliert Interessen über Zeit (laufender Mittelwert) |
| 🧬 **Embedding Worker** | Berechnet 384-dim Vektoren (BAAI/bge-small-en-v1.5) für alle Einträge |
| 📅 **MyActivities Timeline** | Persönliche Aktivitäts-Zeitachse, dauerhaft gespeichert, tagesweise abrufbar |
| 📡 **RSS-Feed** | Kompatibel mit allen Feed-Readern; Top-Artikel prominent platziert |
| 🌐 **Admin-Dashboard** | Web-UI für Tags, Settings, News-Ergebnisse, Timeline und Ollama-Konfiguration |
| 🔌 **Browser-Extension** | Chrome & Firefox MV3, kumulativer Dwell-Timer, Blocklist, Sucherfassung |
| 💻 **VSCode-Extension** | Erfasst aktive Projekte und Dateien aus der IDE |
| 🤖 **Android-Scraper** | Liest Google MyActivity, extrahiert Browsing-Daten |
| ⏰ **Automatische Zeitpläne** | Tag-Generierung und News-Suche via APScheduler (CEST) |
| 📦 **Vorkonfigurierte Downloads** | Chrome, Firefox und VSCode direkt aus dem Dashboard herunterladen |
| 🔇 **Debug-Log-Toggle** | `ENABLE_DEBUG_LOGS=false` schaltet Debug-Dateischreiben ab |
| 🔒 **Bearer-Token-Auth** | Alle API-Endpunkte geschützt, `hmac.compare_digest` |

---

## 🚀 Installation & Setup

### Voraussetzungen
- [Docker](https://docs.docker.com/get-docker/) & [Docker Compose](https://docs.docker.com/compose/install/)
- [Ollama](https://ollama.ai) erreichbar (lokal oder im Netzwerk) mit einem installierten Modell
- Optional: Chrome 109+ oder Firefox 109+ für die Browser-Extension
- Optional: VSCode 1.82+ für die VSCode-Extension

### 1. Repository klonen

```bash
git clone https://github.com/bmetallica/myfeed.git
cd myfeed
```

### 2. Konfiguration

```bash
cp .env.example .env
nano .env
```

Mindestpflichtfelder:

```dotenv
API_BEARER_TOKEN=<python3 -c "import secrets; print(secrets.token_hex(32))">
POSTGRES_PASSWORD=<sicheres-passwort>
```

### 3. System starten

```bash
make build        # Images bauen und alle Container starten
# oder:
docker compose up -d --build
```

### 4. Ollama-URL konfigurieren

Admin-Dashboard öffnen: **http://localhost:7999**

→ Abschnitt **Ollama** → URL eintragen (z.B. `http://192.168.1.100:11434`) → Modell wählen → Speichern

### 5. Tags generieren

Im Dashboard: **Tags** → **Jetzt generieren** (oder automatischen Zeitplan konfigurieren)

### 6. News suchen

Im Dashboard: **News-Suche** → Suchmethode aktivieren → **Jetzt suchen**

---

## Architektur

### Datenquellen und Source-Labels

| Source | Erzeugt durch | Beschreibung |
|---|---|---|
| `browser_chrome` | Browser-Extension | Aktiver Tab ≥ Dwell-Schwelle (kumuliert) |
| `browser_history` | Browser-Extension | Chrome-Verlauf-Sync (alle 15 min, 7 Tage, visitCount ≥ 2) |
| `search_google` / `search_ddg` / … | Browser-Extension | Automatisch erkannte Suchanfragen |
| `google_activity` | Android-Scraper | Google MyActivity |
| `vscode` | VSCode-Extension | Aktive Dateien und Workspace-Info |

### Datenfluss

1. **Kontext-Erfassung**: Clients senden Ereignisse an `POST /api/v1/context`
2. **Timeline-Eintrag**: Jedes Ereignis wird sofort auch in `activity_timeline` geschrieben (wenn aktiviert)
3. **Vektorisierung**: Embedding Worker berechnet 384-dim Vektor für jeden Eintrag (asynchron)
4. **Tag-Generierung** (manuell oder geplant): Ollama analysiert Browsing-Titel → Tags
5. **News-Suche** (manuell oder geplant): Tags → DuckDuckGo/SearXNG → Re-Ranking → DB
6. **Ausgabe**: RSS-Feed (`/rss`), Admin-Dashboard (Port 7999), MyActivities-Seite

### Gewichtungs-Formel

```
effective_weight = max(1, min(10, round(tag_weight × category_weight / 10)))
```

---

## Services

| Service | Image | Port | Beschreibung |
|---|---|---|---|
| `gateway-api` | Python 3.12-slim (Build) | 8000 | FastAPI-Backend, Kernlogik |
| `frontend` | nginx:alpine | 7999 | Admin-Dashboard + MyActivities |
| `db` | pgvector/pgvector:pg16 | intern | PostgreSQL 16 + pgvector |
| `embedding-worker` | Python 3.12-slim (Build) | – | Berechnet Embeddings aus context_queue |
| `android-scraper` | Python 3.12-slim (Build) | intern | Google-MyActivity-Scraper |
| `searxng` | searxng/searxng | intern | Selbstgehostete Meta-Suchmaschine |

---

## Schnellstart

Siehe [Installation & Setup](#-installation--setup) oben.

**Makefile-Kurzreferenz:**

```bash
make up        # Alle Container starten (kein Rebuild)
make build     # Geänderte Images bauen + starten
make rebuild   # Vollständiger Rebuild ohne Cache
make logs      # Live-Logs aller Container
make down      # Container stoppen
make reset     # Container + Volumes löschen (Datenverlust!)
make ps        # Container-Status
```

---

## Konfiguration

### Umgebungsvariablen (`.env`)

| Variable | Standard | Beschreibung |
|---|---|---|
| `GATEWAY_PORT` | `8000` | Externer Port des API-Gateways |
| `API_BEARER_TOKEN` | *(leer)* | Bearer-Token für alle Clients – min. 32 Zeichen |
| `FRONTEND_PORT` | `7999` | Port des Admin-Dashboards |
| `POSTGRES_USER` | `myfeed` | Datenbankbenutzer |
| `POSTGRES_PASSWORD` | *(leer)* | Datenbankpasswort |
| `POSTGRES_DB` | `myfeed` | Datenbankname |
| `ENABLE_ANDROID_SCRAPER` | `false` | Android-Scraper aktivieren |
| `SCRAPER_INTERVAL_SECS` | `300` | Scrape-Intervall in Sekunden |
| `ENABLE_DEBUG_LOGS` | `true` | Debug-Dateien für Tag-Generierung schreiben |
| `WORKER_BATCH_SIZE` | `32` | Batch-Größe des Embedding Workers |
| `WORKER_POLL_SECS` | `30` | Poll-Intervall des Embedding Workers |

---

## Admin-Dashboard

Aufrufbar unter **http://localhost:7999**.

| Karte | Funktion |
|---|---|
| Verbindung | Gateway-URL + Bearer-Token eintragen |
| **Extension Download** | Vorkonfigurierte Pakete für Chrome (.zip), Firefox (.xpi) und VSCode (.vsix) |
| **📅 MyActivities** | Toggle + Backfill-Button; Link zur Timeline-Seite |
| Ollama | Modell wählen, Tag-Generierung konfigurieren |
| Tags | KI-Tags und manuelle Tags verwalten |
| Langzeit-Tags | Akkumulierter Interessens-Speicher |
| News-Suche | Einstellungen, manuelle Auslösung |
| News-Ergebnisse | Gefundene Artikel anzeigen |

Der Header-Link **📅 MyActivities →** öffnet die persönliche Zeitachse direkt.

---

## RSS-Feed

```
GET /rss
```

- Kein Auth erforderlich (öffentlicher Lesezugriff)
- Artikel mit `effective_weight > 8` werden als Top-Ergebnisse platziert
- Enthält `pubDate` aus dem Original-Artikel
- Kompatibel mit allen Standard-RSS-Readern (Feedly, NewsBlur, Miniflux, …)

---

## Browser-Extension

### Installation

**Vorkonfiguriert (empfohlen):**  
Im Admin-Dashboard auf **↓ Chrome (.zip)** oder **↓ Firefox (.xpi)** klicken.

**Manuell – Chrome:**
1. `chrome://extensions` → Entwicklermodus aktivieren
2. „Entpackte Erweiterung laden" → Ordner `extension/` wählen

**Manuell – Firefox:**
1. `about:debugging#/runtime/this-firefox`
2. „Temporäres Add-on laden" → `extension/manifest.json` wählen

### Funktionsweise

- **Kumulativer Dwell-Timer**: Tab muss ≥ `dwellSecs` (Standard: 15 s) kumuliert aktiv gewesen sein
- **Blocklist**: Domains wie Banking, Shopping, Social-Media werden nie erfasst
- **Sucherfassung**: Suchanfragen auf Google, Bing, DuckDuckGo, GitHub, YouTube u.a.
- **Chrome-Verlauf-Sync**: Alle 15 Minuten, Einträge mit `visitCount ≥ 2` (`source: browser_history`)
- **Seitenkontext**: Meta-Description, H1 und Textausschnitte (bis 2000 Zeichen)

---

## VSCode-Extension

### Installation

**Vorkonfiguriert (empfohlen):**  
Im Admin-Dashboard auf **↓ VSCode (.vsix)** klicken. In VSCode: `Strg+Shift+P` → *"Install from VSIX…"*

**Manuell:**
```bash
cd vscode-extension
npm install
npm run compile
```

### Einstellungen

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `myfeed.gatewayUrl` | `http://localhost:8000` | Gateway-URL |
| `myfeed.bearerToken` | *(leer)* | API Bearer Token |
| `myfeed.dwellSeconds` | `15` | Mindestverweilzeit pro Datei in Sekunden |
| `myfeed.cooldownMinutes` | `30` | Cooldown pro Dateipfad |
| `myfeed.blocklist` | `["node_modules", ".git", …]` | Ignorierte Pfad-Segmente |
| `myfeed.enabled` | `true` | Extension aktivieren/deaktivieren |

### Funktionsweise

- Datei ≥ `dwellSeconds` aktiv → `POST /api/v1/context` mit `source: vscode`
- Beim Start / Workspace-Wechsel → Workspace-Name, Git-Branch und README-Snippet sofort senden
- Status-Bar zeigt `$(rss) MyFeed: OK` / `✗`; Klick öffnet Output-Channel

---

## MyActivities Timeline

Persönliche Aktivitäts-Zeitachse unter **http://localhost:7999/timeline.html**.

- Alle erfassten Ereignisse werden dauerhaft in `activity_timeline` gespeichert (kein tägliches Löschen)
- Tagesweise Navigation mit Datepicker und Vorwärts/Rückwärts-Buttons
- Filtern nach Typ: Web · Code · Suche · Video · Android · Social · Maps
- Statistiken pro Tag: Gesamtaktivitäten, aktive Stunden, häufigste Quelle, Domains
- Klick auf Eintrag öffnet URL im neuen Tab
- Im Admin-Dashboard: Toggle zum Aktivieren/Deaktivieren + Backfill-Button für historische Daten

**Aktivitäts-Typen:**

| Icon | Typ | Quellen |
|---|---|---|
| 🌐 | Web | `browser_chrome`, `browser_history` |
| 💻 | Code | `vscode`, GitHub/GitLab-URLs |
| 🔍 | Suche | `search_*` |
| 📺 | Video | YouTube-URLs |
| 📱 | Android | `google_activity` |
| 🗺️ | Maps | Google Maps-URLs |
| 👥 | Social | Reddit, Twitter/X, Instagram, … |

---

## Datenbank-Schema

```
context_queue      – Erfasste Kontext-Ereignisse (Rohdaten aller Quellen)
activity_timeline  – Persönliche Aktivitäts-Zeitachse (dauerhaft)
tags               – KI-generierte und manuelle Interessen-Tags
long_term_tags     – Akkumulierter Langzeit-Interessens-Speicher
news_results       – Gefundene News-Artikel
system_settings    – Key-Value-Konfiguration (inkl. Feature-Toggles)
```

### `context_queue`

| Spalte | Typ | Beschreibung |
|---|---|---|
| `id` | UUID PK | Automatisch generierte UUID v4 |
| `source` | VARCHAR(64) | Datenquelle (`browser_chrome`, `vscode`, …) |
| `title` | TEXT | Seitentitel, Dateiname oder Suchanfrage |
| `url` | TEXT | URL oder `file://`-Pfad |
| `content` | TEXT | Extrahierter Seiteninhalt (max. ~2000 Zeichen) |
| `created_at` | TIMESTAMPTZ | Erfassungszeitpunkt |
| `processed` | BOOLEAN | `false` = noch nicht vektorisiert |
| `embedding` | VECTOR(384) | Sentence-Embedding (Embedding Worker) |

### `activity_timeline`

| Spalte | Typ | Beschreibung |
|---|---|---|
| `id` | BIGSERIAL PK | Laufende ID |
| `activity_date` | DATE | Datum des Ereignisses (UTC) |
| `activity_ts` | TIMESTAMPTZ | Exakter Zeitstempel |
| `source` | VARCHAR(64) | Datenquelle |
| `title` | TEXT | Titel des Eintrags |
| `url` | TEXT | URL |
| `domain` | VARCHAR(255) | Extrahierte Domain |
| `icon_type` | VARCHAR(32) | `web`/`code`/`search`/`video`/`mobile`/`maps`/`social` |
| `context_queue_id` | UUID FK | Verknüpfung zur context_queue |

---

## API-Endpunkte

Alle Endpunkte (außer `/health` und `/rss`) erfordern `Authorization: Bearer <token>`.

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/health` | Systemstatus |
| `GET` | `/rss` | RSS-Feed |
| `POST` | `/api/v1/context` | Kontext-Eintrag speichern (+ Timeline-Insert) |
| `GET` | `/api/v1/context` | Kontext-Queue abrufen |
| `GET` | `/api/v1/timeline?date=YYYY-MM-DD` | Timeline-Einträge für einen Tag |
| `GET` | `/api/v1/timeline/dates?months=3` | Alle Tage mit Aktivitäten |
| `POST` | `/api/v1/timeline/backfill` | Historische Daten in Timeline einlesen |
| `GET` | `/api/v1/settings/timeline` | Timeline-Einstellungen lesen |
| `PUT` | `/api/v1/settings/timeline` | Timeline aktivieren/deaktivieren |
| `GET` | `/api/v1/tags` | Alle Tags abrufen |
| `POST` | `/api/v1/tags` | Manuellen Tag erstellen |
| `DELETE` | `/api/v1/tags/{id}` | Tag löschen |
| `POST` | `/api/v1/tags/generate` | Tag-Generierung auslösen |
| `GET` | `/api/v1/tags/longterm` | Langzeit-Tags abrufen |
| `POST` | `/api/v1/news/search` | News-Suche auslösen |
| `GET` | `/api/v1/news` | News-Ergebnisse abrufen |
| `GET/PUT` | `/api/v1/settings/ollama` | Ollama-Einstellungen |
| `GET/PUT` | `/api/v1/settings/news` | News-Einstellungen |
| `POST` | `/api/v1/download/extension` | Vorkonfiguriertes Browser-Paket (Chrome/Firefox) |
| `POST` | `/api/v1/download/vscode-extension` | Vorkonfiguriertes VSCode-Paket (.vsix) |

---

## Sicherheit

| Bedrohung | Gegenmaßnahme |
|---|---|
| Unbefugter API-Zugriff | Bearer-Token auf allen Schreib-Endpunkten |
| Timing-Angriff | `hmac.compare_digest` (konstante Laufzeit) |
| SQL-Injection | Parametrisierte Queries via psycopg2 |
| DB-Exposition | Kein Port-Mapping für DB-Container |
| Secrets in Code | Ausschließlich via `.env` |
| Token in VSCode-Extension | Gespeichert als `secret: true` in VSCode Settings |

**Empfehlungen für den Produktionsbetrieb:**
- Reverse Proxy mit TLS (nginx / Caddy / Traefik)
- Rate Limiting vorschalten
- `API_BEARER_TOKEN` regelmäßig rotieren

---

## Roadmap

- [x] Sentence-Embeddings für pgvector (Embedding Worker, BAAI/bge-small-en-v1.5)
- [x] Persönliche Aktivitäts-Zeitachse (MyActivities Timeline)
- [ ] Semantische Ähnlichkeitssuche via pgvector


---

## Lizenz

MIT – siehe [LICENSE](LICENSE)

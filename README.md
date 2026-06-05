<p align="center">
  <img src="https://img.shields.io/badge/FastAPI-0.110+-009688?style=for-the-badge&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/PostgreSQL-16+-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" />
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
     ┌──────┴──────┐
     │             │
     ▼             ▼
┌─────────┐  ┌──────────┐
│PostgreSQL│  │  Ollama  │  ← Lokales LLM (Tag-Generierung + Re-Ranking)
│pgvector  │  │ (extern) │
└─────────┘  └──────────┘
            │
     ┌──────┴──────┐
     │             │
     ▼             ▼
┌──────────┐  ┌─────────┐
│DuckDuckGo│  │ SearXNG │  ← News-Suche
└──────────┘  └─────────┘
            │
            ▼
        RSS-Feed
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
| 📡 **RSS-Feed** | Kompatibel mit allen Feed-Readern; Top-Artikel prominent platziert |
| 🌐 **Admin-Dashboard** | Web-UI für Tags, Settings, News-Ergebnisse und Ollama-Konfiguration |
| 🔌 **Browser-Extension** | Chrome & Firefox MV3, kumulativer Dwell-Timer, Blocklist, Sucherfassung |
| 💻 **VSCode-Extension** | Erfasst aktive Projekte und Dateien aus der IDE |
| 🤖 **Android-Scraper** | Liest Google MyActivity, extrahiert Browsing-Daten |
| ⏰ **Automatische Zeitpläne** | Tag-Generierung und News-Suche via APScheduler (CEST) |
| 📦 **Vorkonfigurierte Downloads** | Chrome, Firefox und VSCode direkt aus dem Dashboard herunterladen |
| 🔒 **Bearer-Token-Auth** | Alle API-Endpunkte geschützt, `hmac.compare_digest` |

---

## 🚀 Installation & Setup

Der einfachste Weg, **MyFeed** inklusive aller Komponenten (Backend, Datenbank und KI-Modell) zu starten, ist über **Docker Compose**.

### Voraussetzungen
* [Docker](https://docs.docker.com/get-docker/) & [Docker Compose](https://docs.docker.com/compose/install/) installiert.
* Google Chrome (oder ein Chromium-basierter Browser) für die Extension.

---

### 1. Repository klonen
Kloniere das Projekt zuerst auf deinen lokalen Server oder Rechner:
```bash
##myfeed holen
git clone [https://github.com/bmetallica/myfeed.git](https://github.com/bmetallica/myfeed.git)
cd myfeed

## Umgebungsvariablen konfigurieren
cp .env.example .env
##Stelle sicher, dass die Datenbank-Verbindungsdaten für PostgreSQL mit den Einstellungen in deiner docker-compose.yml übereinstimmen.

##Container bauen und starten
docker compose up -d --build
```
Das FastAPI-Backend ist anschließend unter http://localhost:8000 erreichbar und das Admin-UI unter http://localhost:7999.

Das FastAPI-Backend muss für den Onlinezugang zum RSS-Feed aus dem Internet heraus erreichbar gemacht werden. 


## Architektur

### Datenquellen und Source-Labels

| Source | Erzeugt durch | Beschreibung |
|---|---|---|
| `browser_chrome` | Browser-Extension | Aktiver Tab ≥ Dwell-Schwelle (kumuliert) |
| `browser_history` | Browser-Extension | Chrome-Verlauf-Sync (alle 15 min, 7 Tage) |
| `search_google` / `search_ddg` / … | Browser-Extension | Automatisch erkannte Suchanfragen |
| `google_activity` | Android-Scraper | Google MyActivity |
| `vscode` | VSCode-Extension | Aktive Dateien und Workspace-Info |

### Datenfluss

1. **Kontext-Erfassung**: Clients senden Ereignisse an `POST /api/v1/context`
2. **Tag-Generierung** (manuell oder geplant):
   - Schritt 1: Ollama analysiert Browsing-Titel → Kategorien + Gewichtungen
   - Schritt 2: Ollama generiert pro Kategorie spezifische Tags
3. **News-Suche** (manuell oder geplant): Tags → DuckDuckGo/SearXNG → optionales Re-Ranking → DB
4. **Ausgabe**: RSS-Feed (`/rss`), Admin-Dashboard (Port 7999)

### Gewichtungs-Formel

```
effective_weight = max(1, min(10, round(tag_weight × category_weight / 10)))
```

---

## Services

| Service | Image | Port | Beschreibung |
|---|---|---|---|
| `gateway-api` | Python 3.12-slim | 8000 | FastAPI-Backend, Kernlogik |
| `frontend` | nginx:alpine | 7999 | Admin-Dashboard |
| `db` | pgvector/pgvector:pg16 | intern | PostgreSQL 16 + pgvector |
| `android-scraper` | Python 3.12-slim | intern | Google-MyActivity-Scraper |
| `searxng` | searxng/searxng | intern | Selbstgehostete Meta-Suchmaschine |

---

## Schnellstart

### Voraussetzungen

- Docker ≥ 24 + Docker Compose V2
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

---

## Admin-Dashboard

Aufrufbar unter **http://localhost:7999** (oder konfigurierbarer Port).

| Karte | Funktion |
|---|---|
| Verbindung | Gateway-URL + Bearer-Token eintragen |
| **Extension Download** | Vorkonfigurierte Pakete für Chrome (.zip), Firefox (.xpi) und **VSCode (.vsix)** herunterladen |
| Kontext-Queue | Gespeicherte Ereignisse anzeigen |
| Ollama | Modell wählen, Tag-Generierung konfigurieren |
| Tags | KI-Tags und manuelle Tags verwalten |
| News-Suche | Einstellungen, manuelle Auslösung |
| News-Ergebnisse | Gefundene Artikel anzeigen |
| RSS | RSS-Feed-URL anzeigen und kopieren |

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
Im Admin-Dashboard auf **↓ Chrome (.zip)** oder **↓ Firefox (.xpi)** klicken – Gateway-URL und Token sind bereits vorausgefüllt.

**Manuell – Chrome:**
1. `chrome://extensions` → Entwicklermodus aktivieren
2. „Entpackte Erweiterung laden" → Ordner `extension/` wählen

**Manuell – Firefox:**
1. `about:debugging#/runtime/this-firefox`
2. „Temporäres Add-on laden" → `extension/manifest.json` wählen

### Funktionsweise

- **Kumulativer Dwell-Timer**: Tab muss ≥ `dwellSecs` (Standard: 15 s) kumuliert aktiv gewesen sein – Tab-Wechsel pausiert den Timer, kehrt man zurück läuft er weiter
- **Blocklist**: Domains wie Banking, Shopping, Social-Media werden nie erfasst (konfigurierbar)
- **Sucherfassung**: Suchanfragen auf Google, Bing, DuckDuckGo, GitHub, YouTube u.a. werden sofort gesendet
- **Chrome-Verlauf-Sync**: Alle 15 Minuten werden Verlaufseinträge der letzten 7 Tage synchronisiert (`source: browser_history`)
- **Seitenkontext**: Meta-Description, H1 und Textausschnitte werden mitgeschickt (bis 2000 Zeichen)
- **Konfigurierbar**: Gateway-URL, Bearer-Token, Dwell-Zeit, Cooldown, Blocklist

---

## VSCode-Extension

### Installation

**Vorkonfiguriert (empfohlen):**  
Im Admin-Dashboard auf **↓ VSCode (.vsix)** klicken. In VSCode: `Strg+Shift+P` → *"Install from VSIX…"* → Datei auswählen. Gateway-URL und Token werden automatisch übernommen.

**Manuell:**
```bash
cd vscode-extension
npm install
npm run compile
# Dann in VSCode: Strg+Shift+P → "Install from VSIX..." oder F5 für Entwicklungsmodus
```

### Einstellungen (VSCode Settings)

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
- Beim Start / Workspace-Wechsel → Workspace-Name, Git-Branch und README-Snippet werden sofort gesendet
- Status-Bar zeigt `$(rss) MyFeed: OK` / `✗`; Klick öffnet den Output-Channel mit Logs
- Commands: `MyFeed: Gateway-Verbindung testen`, `MyFeed: Status anzeigen`

---

## Datenbank-Schema

```
context_queue     – Erfasste Kontext-Ereignisse (Rohdaten aller Quellen)
tags              – KI-generierte und manuelle Interessen-Tags
long_term_tags    – Akkumulierter Langzeit-Interessens-Speicher
news_results      – Gefundene News-Artikel
system_settings   – Key-Value-Konfiguration
```

### Tag-Tabelle

| Spalte | Typ | Beschreibung |
|---|---|---|
| `name` | VARCHAR(128) UNIQUE | Tag-Name |
| `weight` | INTEGER 1–10 | Tag-Gewicht |
| `category` | VARCHAR(64) | Hauptkategorie (z.B. "IT/Security") |
| `category_weight` | INTEGER 1–10 | Kategorie-Gewicht |
| `type` | VARCHAR(16) | `auto` / `manual` |
| `persistent` | BOOLEAN | Manuelle Tags überleben Tag-Generierung |

---

## API-Endpunkte

Alle Endpunkte (außer `/health` und `/rss`) erfordern `Authorization: Bearer <token>`.

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/health` | Systemstatus |
| `GET` | `/rss` | RSS-Feed |
| `POST` | `/api/v1/context` | Kontext-Eintrag speichern |
| `GET` | `/api/v1/context` | Kontext-Queue abrufen |
| `GET` | `/api/v1/tags` | Alle Tags abrufen |
| `POST` | `/api/v1/tags` | Manuellen Tag erstellen |
| `DELETE` | `/api/v1/tags/{id}` | Tag löschen |
| `POST` | `/api/v1/tags/generate` | Tag-Generierung auslösen |
| `GET` | `/api/v1/tags/longterm` | Langzeit-Tags abrufen |
| `POST` | `/api/v1/news/search` | News-Suche auslösen |
| `GET` | `/api/v1/news` | News-Ergebnisse abrufen |
| `GET` | `/api/v1/settings/ollama` | Ollama-Einstellungen lesen |
| `PUT` | `/api/v1/settings/ollama` | Ollama-Einstellungen speichern |
| `GET` | `/api/v1/settings/news` | News-Einstellungen lesen |
| `PUT` | `/api/v1/settings/news` | News-Einstellungen speichern |
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

**Empfehlungen für den Produktionsbetrieb:**
- Reverse Proxy mit TLS (nginx / Caddy / Traefik)
- Rate Limiting vorschalten
- `API_BEARER_TOKEN` regelmäßig rotieren

---

## Roadmap

- [ ] Sentence-Embeddings für semantische Ähnlichkeitssuche (pgvector)
- [ ] Mehrbenutzer-Unterstützung
- [ ] Mobile App / PWA
- [ ] Webhook-Unterstützung für neue News
- [ ] Export-Funktion (OPML, JSON)

---

## Lizenz

MIT – siehe [LICENSE](LICENSE)

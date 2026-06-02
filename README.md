<p align="center">
  <img src="https://img.shields.io/badge/FastAPI-0.110+-009688?style=for-the-badge&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/PostgreSQL-16+-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" />
  <img src="https://img.shields.io/badge/Ollama-local%20LLM-black?style=for-the-badge&logo=ollama&logoColor=white" />
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white" />
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge" />
</p>

<h1 align="center">🗞️ MyFeed</h1>
<p align="center"><strong>Selbstgehosteter, KI-gestützter personalisierter Newsfeed</strong></p>
<p align="center">
  Analysiert dein Browserverlauf · Generiert Interessen-Tags via Ollama · Sucht passende News · Liefert einen RSS-Feed
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
- [Datenbank-Schema](#datenbank-schema)
- [API-Endpunkte](#api-endpunkte)
- [Sicherheit](#sicherheit)
- [Roadmap](#roadmap)

---

## Überblick

**MyFeed** ist ein vollständig selbstgehostetes System, das aus deinem Browserverlauf automatisch Interessenprofile erstellt und darauf basierend täglich personalisierte News-Artikel sammelt.

Kein Cloud-Dienst, keine Datenweitergabe – alles läuft lokal.

```
Browser-Extension / Android-Scraper
           │  Browser-Aktivität
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
│ PostgreSQL│  │  Ollama  │  ← Lokales LLM (Tag-Generierung + Re-Ranking)
│ pgvector │  │ (extern) │
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
| 🔌 **Browser-Extension** | Chrome & Firefox MV3, Dwell-Time-Filter, Keyword-Filter |
| 🤖 **Android-Scraper** | Liest Google MyActivity, extrahiert Browsing-Daten |
| ⏰ **Automatische Zeitpläne** | Tag-Generierung und News-Suche via APScheduler (CEST) |
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

### Datenfluss

1. **Kontext-Erfassung**: Browser-Extension oder Android-Scraper senden Seitenbesuche an `POST /api/v1/context`
2. **Tag-Generierung** (manuell oder geplant):
   - Schritt 1: Ollama analysiert die Browsing-Titel → Kategorien + Gewichtungen
   - Schritt 2: Ollama generiert pro Kategorie spezifische Tags
   - Nicht-persistente Auto-Tags werden ersetzt; Langzeit-Speicher akkumuliert
3. **News-Suche** (manuell oder geplant): Aktive Tags → DuckDuckGo/SearXNG → optionales Ollama-Re-Ranking → DB
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
| `frontend` | nginx:alpine | 7999 | Admin-Dashboard (SPA) |
| `db` | pgvector/pgvector:pg16 | intern | PostgreSQL 16 + pgvector |
| `android-scraper` | Python 3.12-slim | intern | Google-MyActivity-Scraper |
| `searxng` | searxng/searxng | intern | Selbstgehostete Meta-Suchmaschine |

---

## Schnellstart

### Voraussetzungen

- Docker ≥ 24 + Docker Compose V2
- [Ollama](https://ollama.ai) erreichbar (lokal oder im Netzwerk) mit einem installierten Modell
- Optional: Chrome 109+ oder Firefox 109+ für die Browser-Extension

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
docker compose up -d
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

### System-Einstellungen (Admin-Dashboard)

Alle weiteren Einstellungen werden im Dashboard unter den jeweiligen Karten konfiguriert und in der Datenbank (`system_settings`) gespeichert:

- **Ollama**: URL, Modell, Zeitpläne für Tag-Generierung
- **News-Suche**: DuckDuckGo, SearXNG, Sprachen, Zeitraum, Max-Ergebnisse, Re-Ranking
- **Tags**: manuelle persistente Tags mit Kategorie-Gewichtung
- **Langzeit-Speicher**: optionale Einbeziehung bei der News-Suche

---

## Admin-Dashboard

Aufrufbar unter **http://localhost:7999** (oder konfigurierbarer Port).

| Karte | Funktion |
|---|---|
| Verbindung | Gateway-URL + Bearer-Token eintragen |
| Kontext-Queue | Gespeicherte Browser-Aktivitäten anzeigen |
| Ollama | Modell wählen, Tag-Generierung konfigurieren |
| Tags | KI-Tags und manuelle Tags verwalten, Langzeit-Speicher |
| News-Suche | Einstellungen, manuelle Auslösung |
| News-Ergebnisse | Gefundene Artikel anzeigen |
| RSS | RSS-Feed-URL anzeigen und kopieren |

---

## RSS-Feed

```
GET /rss
```

- Standardmäßig kein Auth erforderlich (öffentlicher Lesezugriff)
- Artikel mit `effective_weight > 8` werden als **Zukunfts-Pin** um 23:00 Uhr platziert
- Enthält `pubDate` aus dem Original-Artikel (via HTML-Metadaten-Extraktion)
- Kompatibel mit allen Standard-RSS-Readern (Feedly, NewsBlur, Miniflux, …)

---

## Browser-Extension

### Installation

**Chrome:**
1. `chrome://extensions` → Entwicklermodus aktivieren
2. „Entpackte Erweiterung laden" → Ordner `extension/` wählen

**Firefox:**
1. `about:debugging#/runtime/this-firefox`
2. „Temporäres Add-on laden" → `extension/manifest.json` wählen

### Funktionsweise

- Seiten werden nur erfasst, wenn der Tab **≥ 45 Sekunden** aktiv war
- Keyword-Filter: nur Seiten, deren URL/Titel ein konfiguriertes Keyword enthält
- Konfigurierbar: Gateway-URL, Bearer-Token, Keyword-Liste

---

## Datenbank-Schema

```
context_queue     – Erfasste Browsing-Ereignisse (Rohdaten)
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
| `DELETE` | `/api/v1/tags/longterm` | Alle Langzeit-Tags löschen |
| `DELETE` | `/api/v1/tags/longterm/{id}` | Einzelnen Langzeit-Tag löschen |
| `GET` | `/api/v1/news` | News-Ergebnisse abrufen |
| `POST` | `/api/v1/news/search` | News-Suche auslösen |
| `GET` | `/api/v1/settings/news` | News-Einstellungen lesen |
| `PUT` | `/api/v1/settings/news` | News-Einstellungen speichern |
| `GET` | `/api/v1/settings/ollama` | Ollama-Einstellungen lesen |
| `PUT` | `/api/v1/settings/ollama` | Ollama-Einstellungen speichern |
| `GET` | `/api/v1/ollama/models` | Verfügbare Ollama-Modelle |

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
- Swagger-UI deaktivieren: `docs_url=None` in `main.py`

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

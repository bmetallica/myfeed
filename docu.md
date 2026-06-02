# MyFeed – Systemdokumentation

> **Personalisiertes KI-Newsfeed-System** – Modulares, selbstgehostetes Kontext-Erfassungssystem auf Basis von FastAPI, PostgreSQL/pgvector und einer Browser-Extension (Chrome & Firefox MV3).

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
10. [Sicherheit](#10-sicherheit)
11. [Erweiterbarkeit (Roadmap)](#11-erweiterbarkeit-roadmap)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Systemübersicht

MyFeed ist ein **selbstgehostetes Kontext-Erfassungssystem**, das Browsing-Aktivitäten und andere Datenquellen als strukturierte Ereignisse speichert. Diese Ereignisse bilden die Rohdatenbasis für einen personalisierten KI-Newsfeed.

Das System folgt **Option-2-Architektur**: Der Ingest-Pfad (HTTP → Datenbank) ist bewusst von der KI-Verarbeitung (Embedding-Berechnung) entkoppelt. Dadurch bleibt das Gateway immer reaktionsfähig, und ein separater Worker kann Embeddings asynchron berechnen, ohne den Datenfluss zu blockieren.

```
┌─────────────────┐         HTTPS POST          ┌─────────────────────┐
│  Browser-       │  ──────────────────────────► │  FastAPI Gateway    │
│  Extension      │  Bearer Token Auth           │  (gateway-api)      │
└─────────────────┘                              └──────────┬──────────┘
                                                            │ INSERT processed=false
                                                            ▼
┌─────────────────┐         SELECT/UPDATE         ┌─────────────────────┐
│  KI-Worker      │  ◄──────────────────────────  │  PostgreSQL         │
│  (zukünftig)    │  embedding = vector(384)      │  + pgvector         │
└─────────────────┘                              └─────────────────────┘
```

---

## 2. Architektur

### Prinzipien

| Prinzip | Umsetzung |
|---|---|
| **Entkopplung** | Ingest und Vektorisierung laufen in getrennten Prozessen |
| **Einfachheit** | Ein `docker-compose up -d` startet das gesamte System |
| **Sicherheit** | Jeder API-Request erfordert einen Bearer-Token; DB ist nicht nach außen exponiert |
| **Erweiterbarkeit** | Neue Quellen (VS Code, GitHub Cron, RSS …) können den gleichen Endpunkt nutzen |

### Datenfluss

1. Die Browser-Extension registriert, dass ein Tab ≥ 45 Sekunden aktiv war und ein Tech-Keyword matched.
2. Sie schickt `POST /api/v1/context` mit Titel, URL und Quelle an das Gateway.
3. Das Gateway authentifiziert den Token, validiert die Daten (Pydantic) und führt ein einzelnes `INSERT` in `context_queue` aus (`processed = false`).
4. Ein zukünftiger KI-Worker liest alle Zeilen mit `processed = false`, berechnet ein 384-dimensionales Sentence-Embedding und schreibt es in die `embedding`-Spalte.
5. Ein Such-/Empfehlungsservice nutzt den HNSW-Index für Nearest-Neighbour-Queries zur Newsfeed-Generierung.

---

## 3. Verzeichnisstruktur

```
myfeed/
├── .env                        # Umgebungsvariablen (aus .env.example befüllen)
├── docker-compose.yml          # Container-Orchestrierung
│
├── db/
│   └── init.sql                # DB-Schema: Tabellen, Erweiterungen, Indizes
│
├── gateway/
│   ├── Dockerfile              # Python 3.12-slim Image
│   ├── requirements.txt        # fastapi, uvicorn, psycopg2-binary, pydantic
│   └── main.py                 # FastAPI-Anwendung
│
└── extension/
    ├── manifest.json           # MV3-Manifest (Chrome & Firefox)
    ├── background.js           # Service Worker: Dwell-Timer + Keyword-Filter
    ├── options.html            # Einstellungs-Popup/Seite
    ├── options.js              # Formular-Logik (Speichern, Verbindungstest)
    └── icons/                  # Extension-Icons (16, 48, 128 px – selbst hinzufügen)
```

---

## 4. Schnellstart

### Voraussetzungen

- Docker ≥ 24 und Docker Compose V2
- Ein moderner Browser (Chrome 109+ oder Firefox 109+)

### Schritt 1 – Repository klonen / Dateien ablegen

```bash
# Alle Dateien befinden sich bereits in /opt/myfeed
cd /opt/myfeed
```

### Schritt 2 – .env befüllen

```bash
# Sicheren Token generieren
python3 -c "import secrets; print(secrets.token_hex(32))"

# .env anpassen
nano .env
```

Mindestens diese Werte müssen gesetzt werden:

```dotenv
API_BEARER_TOKEN=<erzeugter_token>
POSTGRES_PASSWORD=<sicheres_passwort>
```

### Schritt 3 – System starten

```bash
docker compose up -d
```

Docker startet:
1. **db** – PostgreSQL 16 + pgvector; führt `init.sql` beim ersten Start aus.
2. **gateway-api** – FastAPI/Uvicorn; wartet auf den Health-Check der DB.

### Schritt 4 – API testen

```bash
curl -s http://localhost:8000/health
# → {"status":"ok"}

curl -s -X POST http://localhost:8000/api/v1/context \
  -H "Authorization: Bearer <dein_token>" \
  -H "Content-Type: application/json" \
  -d '{"source":"test","title":"Hello MyFeed","url":"https://example.com"}'
# → {"id":"<uuid>","status":"queued"}
```

### Schritt 5 – Browser-Extension laden

**Chrome:**
1. `chrome://extensions` öffnen → **Entwicklermodus** aktivieren.
2. **Entpackte Erweiterung laden** → Ordner `extension/` auswählen.

**Firefox:**
1. `about:debugging#/runtime/this-firefox` öffnen.
2. **Temporäres Add-on laden** → `extension/manifest.json` auswählen.

**Extension konfigurieren:**
- Extension-Icon anklicken → Gateway-URL und Bearer-Token eintragen → **Speichern**.

---

## 5. Konfiguration (.env)

| Variable | Standard | Beschreibung |
|---|---|---|
| `GATEWAY_PORT` | `8000` | Externer Port des API-Gateways |
| `API_BEARER_TOKEN` | *(leer)* | Geheimes Token für alle Clients. Mindestens 32 Zeichen empfohlen. |
| `POSTGRES_USER` | `myfeed` | Datenbankbenutzer |
| `POSTGRES_PASSWORD` | *(leer)* | Datenbankpasswort – **muss gesetzt werden** |
| `POSTGRES_DB` | `myfeed` | Name der Datenbank |

> **Sicherheitshinweis:** Die `.env`-Datei enthält Secrets. Sie darf **niemals** in ein öffentliches Repository eingecheckt werden. Eine `.gitignore`-Regel `*.env` ist dringend empfohlen.

---

## 6. Docker-Setup

### Container

| Service | Image | Zweck |
|---|---|---|
| `db` | `pgvector/pgvector:pg16` | PostgreSQL 16 mit pgvector-Erweiterung |
| `gateway-api` | Lokales Build (Python 3.12-slim) | FastAPI-Ingest-Endpunkt |

### Netzwerk

Beide Container kommunizieren über das interne Docker-Netzwerk `newsfeed-net`. Die Datenbank ist von außen **nicht** erreichbar (kein Port-Mapping für `db`).

### Persistenz

Das Volume `pgdata` speichert die Datenbankdaten persistent. Ein `docker compose down` löscht es **nicht**. Zum vollständigen Reset:

```bash
docker compose down -v   # Löscht auch das Volume (ACHTUNG: Datenverlust!)
```

### Healthcheck

Der Gateway-Container startet erst, wenn PostgreSQL seinen Healthcheck besteht (`pg_isready`). Intervall: 10 s, Timeout: 5 s, max. 5 Versuche.

### Logs

```bash
docker compose logs -f gateway-api   # Gateway-Logs streamen
docker compose logs -f db             # Datenbank-Logs
```

---

## 7. Datenbank

### Schema

#### Tabelle `context_queue`

| Spalte | Typ | Beschreibung |
|---|---|---|
| `id` | `UUID` PK | Automatisch generierte UUID v4 |
| `source` | `VARCHAR(64)` | Quelle des Ereignisses (z.B. `browser_chrome`, `vscode`) |
| `title` | `TEXT` | Seitentitel oder Ereignisbeschreibung |
| `url` | `TEXT` (nullable) | URL der Ressource |
| `created_at` | `TIMESTAMPTZ` | Zeitstempel der Erfassung (UTC) |
| `processed` | `BOOLEAN` | `false` = noch nicht vektorisiert, `true` = vom Worker verarbeitet |
| `embedding` | `VECTOR(384)` (nullable) | Sentence-Embedding (befüllt vom KI-Worker) |

#### Indizes

| Index | Typ | Spalten | Zweck |
|---|---|---|---|
| `idx_context_queue_processed` | B-Tree | `processed, created_at ASC` | Effizienter Abruf unverarbeiteter Einträge durch den Worker |
| `idx_context_queue_created_at` | B-Tree | `created_at DESC` | Zeitbasierte Abfragen (z.B. "letzte 24h") |
| `idx_context_queue_embedding_hnsw` | HNSW | `embedding` (cosine) | Nearest-Neighbour-Vektorsuche |

### Nützliche Abfragen

```sql
-- Alle unverarbeiteten Einträge (für den Worker)
SELECT id, source, title, url, created_at
FROM context_queue
WHERE processed = false
ORDER BY created_at ASC
LIMIT 100;

-- Statistik: Einträge nach Quelle
SELECT source, COUNT(*) AS count
FROM context_queue
GROUP BY source
ORDER BY count DESC;

-- Einträge der letzten 24 Stunden
SELECT title, url, created_at
FROM context_queue
WHERE created_at > NOW() - INTERVAL '24 hours'
ORDER BY created_at DESC;

-- Embedding nach Worker-Verarbeitung setzen (Beispiel)
UPDATE context_queue
SET embedding = '[0.1, 0.2, ...]'::vector, processed = true
WHERE id = '<uuid>';
```

---

## 8. API Gateway (FastAPI)

### Endpunkte

| Methode | Pfad | Auth | Beschreibung |
|---|---|---|---|
| `GET` | `/health` | Nein | Systemstatus (für Docker-Healthcheck und Monitoring) |
| `POST` | `/api/v1/context` | Bearer-Token | Kontext-Eintrag speichern |
| `PUT` | `/api/v1/context` | Bearer-Token | Alias für POST (für alternative Clients) |
| `GET` | `/docs` | Nein | Swagger-UI (OpenAPI) |
| `GET` | `/redoc` | Nein | ReDoc-Dokumentation |

### Request-Schema (`POST /api/v1/context`)

```json
{
  "source":    "browser_chrome",
  "title":     "FastAPI Documentation",
  "url":       "https://fastapi.tiangolo.com",
  "timestamp": "2026-05-30T14:23:00Z"
}
```

| Feld | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| `source` | `string` (max. 64 Zeichen) | Ja | Bezeichner der Datenquelle |
| `title` | `string` | Ja | Titel oder Beschreibung |
| `url` | `string` | Nein | URL der Ressource |
| `timestamp` | ISO-8601-String | Nein | Client-seitiger Zeitstempel; fällt auf Server-Zeit zurück |

### Response-Schema

```json
{
  "id":     "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued"
}
```

### Authentifizierung

Jeder Request an `/api/v1/context` muss folgenden Header enthalten:

```
Authorization: Bearer <API_BEARER_TOKEN>
```

Fehlt der Header oder ist der Token falsch, antwortet der Server mit `401 Unauthorized`. Der Vergleich nutzt `hmac.compare_digest` (konstante Laufzeit, kein Timing-Leak).

### Fehler-Codes

| HTTP-Status | Bedeutung |
|---|---|
| `201 Created` | Eintrag erfolgreich gespeichert |
| `401 Unauthorized` | Fehlender oder ungültiger Bearer-Token |
| `422 Unprocessable Entity` | Validierungsfehler (Pydantic) |
| `503 Service Unavailable` | Datenbankfehler |

---

## 9. Browser-Extension

### Dateien

| Datei | Zweck |
|---|---|
| `manifest.json` | MV3-Manifest, deklariert Permissions und Entry-Points |
| `background.js` | Service Worker: Dwell-Timer, Keyword-Filter, HTTP-Ingest |
| `options.html` | Einstellungs-UI (Popup und Options-Seite) |
| `options.js` | Formular-Logik: Laden, Speichern, Verbindungstest |

### Dwell-Time-Logik

```
Tab aktiviert / Seite geladen
         │
         ▼
  Keyword vorhanden?  ──nein──► ignorieren
         │ ja
         ▼
   Timer starten (45 s)
         │
  Tab gewechselt / ──ja──► Timer abbrechen
  Fenster verloren?
         │ nein
         ▼
  Timer abgelaufen?  ──ja──► POST /api/v1/context
```

Der Timer wird in folgenden Situationen **zurückgesetzt**:
- User wechselt den Tab (`tabs.onActivated`)
- User wechselt das Fenster (`windows.onFocusChanged`)
- Der aktive Tab navigiert zu einer neuen Seite
- Der Tab wird geschlossen (`tabs.onRemoved`)

### Keyword-Filter

Die Extension sendet nur Seiten, deren **URL oder Titel** (case-insensitiv) mindestens eines der konfigurierten Keywords enthält. Standard-Keywords:

```
github.com, stackoverflow.com, localhost, docs.,
python, rust, docker, kubernetes, typescript, react,
fastapi, llm, openai, huggingface, arxiv, linux
```

Die Liste ist über die Options-Seite beliebig anpassbar.

### Gespeicherte Einstellungen (`chrome.storage.local`)

| Schlüssel | Typ | Beschreibung |
|---|---|---|
| `gatewayUrl` | `string` | URL des Gateways (z.B. `http://localhost:8000`) |
| `bearerToken` | `string` | API Bearer Token |
| `keywords` | `string` | Kommaseparierte Keyword-Liste |

### Icons

Die Extension erwartet PNG-Icons in `extension/icons/`:
- `icon16.png` (16×16 px)
- `icon48.png` (48×48 px)
- `icon128.png` (128×128 px)

Diese müssen manuell hinzugefügt werden (z.B. mit einem einfachen Text-Editor-Icon).

---

## 10. Sicherheit

### Implementierte Maßnahmen

| Bedrohung | Gegenmaßnahme |
|---|---|
| Unbefugter API-Zugriff | Bearer-Token-Authentifizierung auf allen Schreib-Endpunkten |
| Timing-Angriff auf Token-Vergleich | `hmac.compare_digest` (konstante Laufzeit) |
| SQL-Injection | Parametrisierte Queries via `psycopg2` (keine String-Konkatenation) |
| Datenbankexposition | DB-Container hat kein Port-Mapping; nur im Docker-internen Netzwerk erreichbar |
| Secrets in Code | Alle Secrets ausschließlich in `.env` (nie im Quellcode) |
| Übermäßige Requests | Connection-Pool begrenzt DB-Verbindungen auf max. 10 |

### Empfehlungen für den Produktionsbetrieb

- **HTTPS verwenden:** Das Gateway sollte hinter einem Reverse Proxy (nginx, Caddy, Traefik) mit TLS betrieben werden.
- **Token rotieren:** Den `API_BEARER_TOKEN` regelmäßig erneuern und in allen Clients aktualisieren.
- **Rate Limiting:** Nginx oder ein API-Gateway (z.B. Kong) vorschalten.
- **Swagger-UI deaktivieren:** `docs_url=None, redoc_url=None` in `main.py` für Produktionsumgebungen setzen.
- **DB-Backups:** Regelmäßige `pg_dump`-Backups des Volumes konfigurieren.

---

## 11. Erweiterbarkeit (Roadmap)

### Neue Datenquellen hinzufügen

Jeder Client, der HTTP-Requests mit dem Bearer-Token senden kann, ist eine gültige Datenquelle:

```bash
# Beispiel: GitHub-Cron-Job
curl -X POST http://localhost:8000/api/v1/context \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source":"github_cron","title":"rust-lang/rust – new PR","url":"https://github.com/…"}'
```

Weitere mögliche Quellen:
- **VS Code Extension** – sendet geöffnete Dateipfade / Repository-Namen
- **RSS-Crawler** – sendet neue Artikel aus konfigurierten Feeds
- **Clipboard-Monitor** – sendet kopierte URLs

### Asynchroner KI-Worker (nächster Schritt)

Ein separater Python-Dienst (z.B. mit `sentence-transformers` und dem Modell `all-MiniLM-L6-v2`) liest periodisch unverarbeitete Einträge:

```python
# Pseudocode Worker
while True:
    rows = db.query("SELECT id, title, url FROM context_queue WHERE processed=false LIMIT 50")
    for row in rows:
        text = f"{row.title} {row.url}"
        embedding = model.encode(text).tolist()
        db.execute(
            "UPDATE context_queue SET embedding=%s, processed=true WHERE id=%s",
            (embedding, row.id)
        )
    time.sleep(30)
```

### Newsfeed-Suche

Nach der Vektorisierung können semantisch ähnliche Artikel gefunden werden:

```sql
-- Top-5 ähnlichste Einträge zu einem gegebenen Embedding
SELECT title, url, 1 - (embedding <=> '[…]'::vector) AS similarity
FROM context_queue
WHERE processed = true
ORDER BY embedding <=> '[…]'::vector
LIMIT 5;
```

---

## 12. Troubleshooting

### Gateway startet nicht

```bash
docker compose logs gateway-api
```

Häufige Ursachen:
- `DATABASE_URL` falsch konfiguriert (prüfe `.env`)
- DB-Container nicht gesund (`docker compose ps`)

### Datenbank nicht erreichbar

```bash
docker compose exec db pg_isready -U myfeed
```

### Extension sendet nichts

1. **Devtools öffnen:** Rechtsklick auf Extension-Icon → Hintergrundseite prüfen → Konsole.
2. Häufige Ursachen:
   - Bearer-Token nicht konfiguriert
   - Gateway-URL falsch (kein abschließendes `/`)
   - Tab enthält kein Keyword
   - Tab war weniger als 45 Sekunden aktiv

### Tabelle existiert nicht

```bash
# Init-Skript manuell ausführen
docker compose exec db psql -U myfeed -d myfeed -f /docker-entrypoint-initdb.d/init.sql
```

### pgvector-Erweiterung fehlt

Das `pgvector/pgvector:pg16`-Image enthält die Erweiterung. Falls sie fehlt:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### Alle Container neu starten

```bash
docker compose restart
```

### Vollständiger Reset (Datenverlust!)

```bash
docker compose down -v
docker compose up -d
```

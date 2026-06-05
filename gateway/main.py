"""
main.py – MyFeed API Gateway
============================================================
Leichtgewichtiger FastAPI-Server, der Kontext-Ereignisse von
Clients (Browser-Extension, VS Code, Cron-Jobs …) entgegennimmt
und direkt in die PostgreSQL-Tabelle context_queue schreibt.

Option-2-Architektur: Keine Vektorberechnung hier.
Der asynchrone KI-Worker liest unverarbeitete Zeilen (processed=false)
und befüllt das Embedding-Feld separat.
============================================================
"""

import asyncio
import os
import io
import re
import json as _json
import logging
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Optional, List
from zoneinfo import ZoneInfo

import httpx
import psycopg2
import psycopg2.pool
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from ddgs import DDGS
try:
    from langdetect import detect as _lang_detect, LangDetectException as _LangDetectException
    _LANGDETECT_OK = True
except ImportError:
    _LANGDETECT_OK = False
from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, Query, Request, status
from urllib.parse import unquote as _url_unquote, urlparse as _urlparse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, field_validator

# ── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
logger = logging.getLogger("myfeed.gateway")

# ── Umgebungsvariablen ───────────────────────────────────────
DATABASE_URL: str = os.environ["DATABASE_URL"]
API_BEARER_TOKEN: str = os.environ["API_BEARER_TOKEN"]

if not API_BEARER_TOKEN or len(API_BEARER_TOKEN) < 16:
    raise RuntimeError("API_BEARER_TOKEN muss gesetzt und mindestens 16 Zeichen lang sein.")

# ── Connection-Pool ──────────────────────────────────────────
# Ein einfacher Thread-basierter Pool reicht für den Ingest-Workload.
_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_scheduler: AsyncIOScheduler | None = None
_BERLIN = ZoneInfo("Europe/Berlin")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Öffnet den DB-Pool beim Start, startet den Scheduler und räumt beim Herunterfahren auf."""
    global _pool, _scheduler
    logger.info("Verbinde mit Datenbank …")
    _pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=10,
        dsn=DATABASE_URL,
    )
    logger.info("Datenbankverbindung hergestellt.")
    _scheduler = AsyncIOScheduler()
    _reschedule_tag_jobs()
    _reschedule_news_job()
    _scheduler.start()
    logger.info("APScheduler gestartet.")
    yield
    _scheduler.shutdown(wait=False)
    if _pool:
        _pool.closeall()
    logger.info("Datenbankverbindungen und Scheduler geschlossen.")


# ── FastAPI-App ──────────────────────────────────────────────
app = FastAPI(
    title="MyFeed API Gateway",
    description="Kontext-Ingest-Endpunkt für den personalisierten KI-Newsfeed.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS – erlaubt Requests von Browser-Extensions (chrome-extension://, moz-extension://)
# und von lokalen Entwicklungsumgebungen. Preflights (OPTIONS) werden korrekt beantwortet.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Extensions haben keinen festen Origin-Wert
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── Authentifizierung ────────────────────────────────────────
_bearer_scheme = HTTPBearer()


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme)) -> None:
    """
    Vergleicht den übermittelten Bearer-Token mit dem konfigurierten Secret.
    Nutzt einen konstant-Zeit-Vergleich, um Timing-Angriffe zu verhindern.
    """
    import hmac
    provided = credentials.credentials.encode()
    expected = API_BEARER_TOKEN.encode()
    if not hmac.compare_digest(provided, expected):
        logger.warning("Ungültiger Bearer-Token empfangen.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger oder fehlender Bearer-Token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── Pydantic-Schema ──────────────────────────────────────────

class ContextPayload(BaseModel):
    """Eingehende Nutzlast von einem Client-Collector."""

    source: str
    title: str
    url: Optional[str] = None
    # Extrahierter Seiteninhalt (Meta-Description, Heading, Text-Snippet)
    content: Optional[str] = None
    # Optionaler ISO-8601-Zeitstempel vom Client; fällt auf NOW() zurück.
    timestamp: Optional[datetime] = None

    @field_validator("source")
    @classmethod
    def source_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("'source' darf nicht leer sein.")
        if len(v) > 64:
            raise ValueError("'source' darf maximal 64 Zeichen lang sein.")
        return v

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("'title' darf nicht leer sein.")
        return v


class CookiesPayload(BaseModel):
    """Cookie-Array von der Browser-Extension (Google-Session-Cookies)."""

    cookies: List[dict]

    @field_validator("cookies")
    @classmethod
    def cookies_not_empty(cls, v: List[dict]) -> List[dict]:
        if not v:
            raise ValueError("'cookies' darf nicht leer sein.")
        return v


class ExtensionBuildRequest(BaseModel):
    """Anfrage-Nutzlast für den vorkonfigurierten Extension-Download."""

    platform:    str   # "chrome" oder "firefox"
    gateway_url: str   # Externe Gateway-URL, die in die Extension eingebettet wird

    @field_validator("platform")
    @classmethod
    def platform_valid(cls, v: str) -> str:
        if v not in ("chrome", "firefox"):
            raise ValueError("platform muss 'chrome' oder 'firefox' sein.")
        return v


class VscodeExtensionBuildRequest(BaseModel):
    """Anfrage-Nutzlast für den vorkonfigurierten VSCode-Extension-Download."""
    gateway_url: str


class SettingsBulkPayload(BaseModel):
    """Bulk-Update für system_settings (nur erlaubte Keys)."""
    settings: dict


class TagCreatePayload(BaseModel):
    """Nutzlast zum Anlegen eines manuellen Tags."""
    name: str
    weight: int = 10
    category: str = ""
    category_weight: int = 10  # Manuelle Tags: volle Priorität als Standard

    @field_validator("name")
    @classmethod
    def name_valid(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("'name' darf nicht leer sein.")
        if len(v) > 128:
            raise ValueError("'name' darf maximal 128 Zeichen lang sein.")
        return v

    @field_validator("weight")
    @classmethod
    def weight_valid(cls, v: int) -> int:
        if v < 1 or v > 10:
            raise ValueError("Gewichtung muss zwischen 1 und 10 liegen.")
        return v

    @field_validator("category")
    @classmethod
    def category_valid(cls, v: str) -> str:
        return v.strip()[:64]

    @field_validator("category_weight")
    @classmethod
    def category_weight_valid(cls, v: int) -> int:
        if v < 1 or v > 10:
            raise ValueError("Kategoriegewichtung muss zwischen 1 und 10 liegen.")
        return v


# ── Hilfs-Funktionen: DB ─────────────────────────────────────

_COOKIE_KEY = "google_sync_cookies"


def _upsert_setting(key: str, value: str) -> None:
    """Schreibt oder überschreibt einen Eintrag in system_settings."""
    assert _pool is not None
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO system_settings (key, value, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = NOW()
                """,
                (key, value),
            )
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def _get_setting(key: str) -> Optional[str]:
    """Liest einen Eintrag aus system_settings. Gibt None zurück wenn nicht vorhanden."""
    assert _pool is not None
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM system_settings WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        _pool.putconn(conn)


def _insert_context(payload: ContextPayload) -> str:
    """
    Schreibt einen Kontext-Eintrag in context_queue und gibt die neue UUID zurück.
    Kein Embedding – das ist Aufgabe des asynchronen KI-Workers.
    """
    assert _pool is not None, "DB-Pool wurde nicht initialisiert."

    created_at = payload.timestamp or datetime.now(timezone.utc)

    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO context_queue (source, title, url, content, created_at, processed)
                VALUES (%s, %s, %s, %s, %s, false)
                RETURNING id::text
                """,
                (payload.source, payload.title, payload.url, payload.content, created_at),
            )
            row = cur.fetchone()
            conn.commit()
            return row[0]  # type: ignore[index]
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


# ── Timeline-Hilfs-Funktionen ────────────────────────────────

_tl_cache: dict = {"enabled": True, "ts": 0.0}


def _timeline_enabled() -> bool:
    """Prüft ob Timeline aktiviert ist (gecacht 60 s)."""
    import time
    now = time.monotonic()
    if now - _tl_cache["ts"] > 60:
        val = _get_setting("timeline_enabled")
        _tl_cache["enabled"] = (val or "true").lower() == "true"
        _tl_cache["ts"] = now
    return bool(_tl_cache["enabled"])


def _derive_icon_type(source: str, url: Optional[str]) -> str:
    if source.startswith("search_"):
        return "search"
    if source == "vscode":
        return "code"
    if source == "google_activity":
        if url:
            if "youtube.com" in url or "youtu.be" in url:
                return "video"
            if "maps.google" in url or "google.com/maps" in url:
                return "maps"
        return "mobile"
    if url:
        if "youtube.com" in url or "youtu.be" in url:
            return "video"
        if "github.com" in url or "gitlab.com" in url:
            return "code"
        if any(d in url for d in ("reddit.com", "twitter.com", "x.com/", "instagram.com", "facebook.com", "linkedin.com")):
            return "social"
    return "web"


def _insert_timeline_entry(
    queue_id: str,
    source: str,
    title: str,
    url: Optional[str],
    activity_ts: datetime,
) -> None:
    assert _pool is not None
    domain: Optional[str] = None
    if url:
        try:
            domain = _urlparse(url).netloc or None
        except Exception:
            pass
    icon_type = _derive_icon_type(source, url)
    activity_date = activity_ts.date()
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO activity_timeline
                    (activity_date, activity_ts, source, title, url, domain, icon_type, context_queue_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::uuid)
                ON CONFLICT (context_queue_id) DO NOTHING
                """,
                (activity_date, activity_ts, source, title, url, domain, icon_type, queue_id),
            )
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def _get_timeline_entries(target_date: date) -> list:
    assert _pool is not None
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, activity_ts, source, title, url, domain, icon_type
                FROM activity_timeline
                WHERE activity_date = %s
                ORDER BY activity_ts ASC
                """,
                (target_date,),
            )
            rows = cur.fetchall()
    finally:
        _pool.putconn(conn)
    return [
        {
            "id": r[0],
            "activity_ts": r[1].isoformat() if r[1] else None,
            "source": r[2],
            "title": r[3],
            "url": r[4],
            "domain": r[5],
            "icon_type": r[6],
        }
        for r in rows
    ]


def _get_timeline_dates(months: int = 3) -> list:
    assert _pool is not None
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT activity_date, COUNT(*) as cnt
                FROM activity_timeline
                WHERE activity_date >= CURRENT_DATE - (%s * 31)
                GROUP BY activity_date
                ORDER BY activity_date DESC
                """,
                (months,),
            )
            rows = cur.fetchall()
    finally:
        _pool.putconn(conn)
    return [{"date": r[0].isoformat(), "count": r[1]} for r in rows]


def _timeline_backfill_sql() -> int:
    """Befüllt activity_timeline mit allen noch nicht erfassten context_queue-Einträgen."""
    assert _pool is not None
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO activity_timeline
                    (activity_date, activity_ts, source, title, url, domain, icon_type, context_queue_id)
                SELECT
                    (created_at AT TIME ZONE 'UTC')::date,
                    created_at,
                    source,
                    title,
                    url,
                    CASE WHEN url ~ '^https?://'
                         THEN regexp_replace(url, '^https?://([^/?#]+).*', E'\\\\1')
                         ELSE NULL END,
                    CASE
                        WHEN source LIKE 'search_%%'                                          THEN 'search'
                        WHEN source = 'vscode'                                                THEN 'code'
                        WHEN source = 'google_activity'
                             AND (url LIKE '%%youtube.com%%' OR url LIKE '%%youtu.be%%')      THEN 'video'
                        WHEN source = 'google_activity'
                             AND (url LIKE '%%maps.google%%' OR url LIKE '%%google.com/maps%%') THEN 'maps'
                        WHEN source = 'google_activity'                                       THEN 'mobile'
                        WHEN url LIKE '%%youtube.com%%' OR url LIKE '%%youtu.be%%'            THEN 'video'
                        WHEN url LIKE '%%github.com%%'  OR url LIKE '%%gitlab.com%%'          THEN 'code'
                        WHEN url LIKE '%%reddit.com%%'  OR url LIKE '%%twitter.com%%'
                          OR url LIKE '%%instagram.com%%' OR url LIKE '%%facebook.com%%'
                          OR url LIKE '%%linkedin.com%%'                                      THEN 'social'
                        ELSE 'web'
                    END,
                    id
                FROM context_queue
                WHERE id NOT IN (
                    SELECT context_queue_id FROM activity_timeline
                    WHERE context_queue_id IS NOT NULL
                )
                ON CONFLICT (context_queue_id) DO NOTHING
                """
            )
            count = cur.rowcount
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)
    return count


# ── Hilfs-Funktionen: Ollama-Einstellungen ───────────────────

_OLLAMA_SETTING_KEYS = {"ollama_url", "ollama_model", "tag_schedule_1", "tag_schedule_2"}
_OLLAMA_DEFAULTS = {
    "ollama_url":     "http://localhost:11434",
    "ollama_model":   "",
    "tag_schedule_1": "23:30",
    "tag_schedule_2": "",
}


def _get_ollama_settings() -> dict:
    """Liest alle Ollama-/Schedule-Einstellungen in einem Query."""
    assert _pool is not None
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT key, value FROM system_settings WHERE key = ANY(%s)",
                (list(_OLLAMA_SETTING_KEYS),),
            )
            result = dict(cur.fetchall())
    finally:
        _pool.putconn(conn)
    for k, v in _OLLAMA_DEFAULTS.items():
        result.setdefault(k, v)
    return result


# ── Hilfs-Funktionen: Tags ───────────────────────────────────

def _get_context_titles_for_date(target_date: date) -> list:
    """Liefert alle Titel aus context_queue für den angegebenen CEST-Kalendertag."""
    assert _pool is not None
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT title FROM context_queue
                WHERE (created_at AT TIME ZONE 'Europe/Berlin')::date = %s
                ORDER BY created_at ASC
                """,
                (target_date,),
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        _pool.putconn(conn)


def _effective_weight(tag_weight: int, category_weight: int) -> int:
    """Effektives Gewicht = round(tag_weight × category_weight / 10), clamp 1–10."""
    return max(1, min(10, round(tag_weight * category_weight / 10)))


def _get_tags() -> list:
    """Liefert alle Tags sortiert nach Gewichtung absteigend."""
    assert _pool is not None
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, name, weight, type, source_date, persistent,
                       category, category_weight, created_at, updated_at
                FROM tags
                ORDER BY weight DESC, name ASC
                """
            )
            cols = ["id", "name", "weight", "type", "source_date", "persistent",
                    "category", "category_weight", "created_at", "updated_at"]
            rows = []
            for row in cur.fetchall():
                r = dict(zip(cols, row))
                r["source_date"]    = r["source_date"].isoformat() if r["source_date"] else None
                r["created_at"]     = r["created_at"].isoformat()
                r["updated_at"]     = r["updated_at"].isoformat()
                r["effective_weight"] = _effective_weight(r["weight"], r["category_weight"])
                rows.append(r)
            return rows
    finally:
        _pool.putconn(conn)


def _create_manual_tag(name: str, weight: int, category: str = "", category_weight: int = 10) -> str:
    """Legt einen manuellen persistenten Tag an (Upsert by name)."""
    assert _pool is not None
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tags (name, weight, type, source_date, persistent, category, category_weight)
                VALUES (%s, %s, 'manual', NULL, true, %s, %s)
                ON CONFLICT (name) DO UPDATE
                    SET weight = EXCLUDED.weight, persistent = true,
                        category = EXCLUDED.category,
                        category_weight = EXCLUDED.category_weight,
                        updated_at = NOW()
                RETURNING id::text
                """,
                (name, weight, category[:64], category_weight),
            )
            row = cur.fetchone()
            conn.commit()
            return row[0]
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def _delete_tag(tag_id: str) -> bool:
    """Löscht einen Tag. Gibt False zurück wenn nicht gefunden."""
    assert _pool is not None
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM tags WHERE id = %s::uuid RETURNING id",
                (tag_id,),
            )
            deleted = cur.fetchone() is not None
            conn.commit()
            return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def _upsert_auto_tags(tags: list, source_date: date, categories_map: Optional[dict] = None) -> int:
    """
    Löscht alle nicht-persistenten Auto-Tags und fügt die neu generierten ein.
    Persistente Tags werden NICHT berührt.
    categories_map: {"Gaming": 8, "IT/Security": 6, ...} aus Schritt-1-Analyse.
    """
    assert _pool is not None
    conn = _pool.getconn()
    _cats = categories_map or {}
    count = 0
    try:
        with conn.cursor() as cur:
            # Alte nicht-persistente Auto-Tags vor der Neu-Generierung löschen
            cur.execute("DELETE FROM tags WHERE type = 'auto' AND persistent = false")
            seen_names: set[str] = set()
            for t in tags:
                name = " ".join(str(t.get("tag", "")).split())
                if not name or len(name) > 128:
                    continue
                if name.lower() in seen_names:
                    continue
                seen_names.add(name.lower())
                try:
                    weight = max(1, min(10, int(t.get("weight", 5))))
                except (TypeError, ValueError):
                    weight = 5
                category = str(t.get("category", "")).strip()[:64]
                try:
                    cat_weight = max(1, min(10, int(_cats.get(category, 5))))
                except (TypeError, ValueError):
                    cat_weight = 5
                cur.execute(
                    """
                    INSERT INTO tags (name, weight, type, source_date, persistent, category, category_weight)
                    VALUES (%s, %s, 'auto', %s, false, %s, %s)
                    ON CONFLICT (name) DO NOTHING
                    """,
                    (name, weight, source_date, category, cat_weight),
                )
                count += cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)
    return count


def _upsert_long_term_tags(tags: list, categories_map: Optional[dict] = None,
                           source_date: Optional[date] = None) -> int:
    """
    Akkumuliert auto-generierte Tags im Langzeit-Speicher (laufender Mittelwert).
    Jeder Tag erhält einen inkrementellen mention_count; das Gewicht wird als
    laufender Durchschnitt berechnet.
    """
    assert _pool is not None
    conn = _pool.getconn()
    _cats = categories_map or {}
    _date = source_date or date.today()
    count = 0
    try:
        with conn.cursor() as cur:
            for t in tags:
                name = str(t.get("tag", "")).strip()
                try:
                    weight = max(1, min(10, int(t.get("weight", 5))))
                except (TypeError, ValueError):
                    weight = 5
                if not name or len(name) > 128:
                    continue
                category = str(t.get("category", "")).strip()[:64]
                try:
                    cat_weight = max(1, min(10, int(_cats.get(category, 5))))
                except (TypeError, ValueError):
                    cat_weight = 5
                cur.execute(
                    """
                    INSERT INTO long_term_tags
                        (name, weight, category, category_weight, mention_count,
                         first_seen, last_seen)
                    VALUES (%s, %s, %s, %s, 1, %s, %s)
                    ON CONFLICT (name) DO UPDATE
                        SET weight = GREATEST(1, LEAST(10,
                                ROUND((long_term_tags.weight::numeric
                                       * long_term_tags.mention_count
                                       + EXCLUDED.weight::numeric)
                                      / (long_term_tags.mention_count + 1)
                                )::integer)),
                            category        = EXCLUDED.category,
                            category_weight = EXCLUDED.category_weight,
                            mention_count   = long_term_tags.mention_count + 1,
                            last_seen       = EXCLUDED.last_seen,
                            updated_at      = NOW()
                    """,
                    (name, weight, category, cat_weight, _date, _date),
                )
                count += cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)
    return count


def _get_long_term_tags() -> list:
    """Gibt alle Langzeit-Tags zurück, sortiert nach effective_weight DESC."""
    assert _pool is not None
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, weight, category, category_weight,
                       mention_count, first_seen, last_seen
                FROM long_term_tags
                ORDER BY weight DESC, mention_count DESC
                """
            )
            rows = cur.fetchall()
    finally:
        _pool.putconn(conn)
    result = []
    for row in rows:
        eff = _effective_weight(row[2], row[4])
        result.append({
            "id":              str(row[0]),
            "name":            row[1],
            "weight":          row[2],
            "category":        row[3],
            "category_weight": row[4],
            "effective_weight": eff,
            "mention_count":   row[5],
            "first_seen":      str(row[6]) if row[6] else None,
            "last_seen":       str(row[7]) if row[7] else None,
        })
    return result


def _delete_long_term_tag(tag_id: str) -> bool:
    """Löscht einen einzelnen Langzeit-Tag anhand der UUID."""
    assert _pool is not None
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM long_term_tags WHERE id = %s::uuid RETURNING id",
                (tag_id,),
            )
            deleted = cur.rowcount > 0
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)
    return deleted


def _clear_long_term_tags() -> int:
    """Löscht alle Langzeit-Tags und gibt die Anzahl zurück."""
    assert _pool is not None
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM long_term_tags")
            count = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)
    return count


# ── Ollama: HTTP-Hilfsfunktionen ─────────────────────────────

async def _fetch_ollama_models(ollama_url: str) -> list:
    """Ruft verfügbare Modelle vom Ollama-Server ab."""
    url = ollama_url.rstrip("/") + "/api/tags"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]


def _parse_ollama_json_list(response_text: str) -> list:
    """Versucht ein JSON-Array aus der Ollama-Antwort zu extrahieren."""
    try:
        parsed = _json.loads(response_text)
        if isinstance(parsed, list):
            return parsed
    except (_json.JSONDecodeError, ValueError):
        pass
    match = re.search(r"\[.*?\]", response_text, re.DOTALL)
    if match:
        try:
            return _json.loads(match.group(0))
        except (_json.JSONDecodeError, ValueError):
            pass
    return []


async def _generate_categories_via_ollama(
    titles: list, ollama_url: str, model: str
) -> tuple:
    """
    Schritt 1: Erkennt Hauptinteressen-Kategorien aus der Browser-History.
    Gibt (categories, prompt, request_body, raw_response) zurück.
    categories = [{"category": "Gaming", "weight": 8}, ...]
    """
    titles_text = "\n".join(f"- {t}" for t in titles[:500])
    prompt = (
        "Du analysierst Browser-Aktivitäten eines Tages und erkennst die HAUPTINTERESSEN-KATEGORIEN des Nutzers.\n\n"
        "WICHTIG – Ausgabeformat: Antworte NUR mit einem JSON-Array, KEIN zusätzlicher Text.\n"
        'Format: [{"category": "Kategoriename", "weight": 8}, ...]\n\n'
        "Regeln:\n"
        "- 3 bis 8 Hauptkategorien, die alle Browser-Aktivitäten abdecken\n"
        "- Gewichtung 1–10 nach Häufigkeit und Intensität der Beschäftigung mit dem Thema\n"
        "- Kategorien auf Deutsch (z.B. 'Gaming', 'IT/Security', 'Nachrichten', 'Finanzen', 'Wissenschaft', 'Unterhaltung')\n"
        "- Nur das JSON-Array, KEIN weiterer Text\n\n"
        f"Browser-Aktivitäten:\n{titles_text}"
    )
    request_body = {"model": model, "prompt": prompt, "stream": False}
    url = ollama_url.rstrip("/") + "/api/generate"
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(url, json=request_body)
        r.raise_for_status()
        raw_response = r.json()
    categories = _parse_ollama_json_list(raw_response.get("response", ""))
    if not categories:
        logger.warning("Konnte keine Kategorien aus Ollama-Antwort parsen.")
    return categories, prompt, request_body, raw_response


async def _generate_tags_via_ollama(
    titles: list, categories: list, ollama_url: str, model: str
) -> tuple:
    """
    Schritt 2: Extrahiert spezifische Tags und ordnet sie Hauptkategorien zu.
    Gibt (tags, prompt, request_body, raw_response) zurück.
    tags = [{"tag": "Pokopia", "category": "Gaming", "weight": 10}, ...]
    """
    titles_text = "\n".join(f"- {t}" for t in titles[:500])
    if categories:
        cat_str = "\n".join(
            f"- {c.get('category', '?')} (Intensität: {c.get('weight', 5)})"
            for c in categories
        )
        cat_section = f"Erkannte Hauptkategorien des Nutzers (Intensität 1–10):\n{cat_str}\n\n"
    else:
        cat_section = ""
    prompt = (
        "Du analysierst Browser-Aktivitäten eines Tages und extrahierst spezifische Themen-Tags.\n\n"
        + cat_section
        + "WICHTIG – Ausgabeformat: Antworte NUR mit einem JSON-Array, KEIN zusätzlicher Text.\n"
        'Format: [{"tag": "Tagname", "category": "Kategoriename", "weight": 7}, ...]\n\n'
        "Regeln:\n"
        "- Gewichtung 1–10: wie spezifisch/dominierend ist dieses Thema INNERHALB seiner Kategorie\n"
        "- Ordne jeden Tag der passenden Hauptkategorie zu (exakt wie oben angegeben)\n"
        "- Spezifischere Tags bekommen höhere Gewichtung als allgemeine Oberbegriffe\n"
        "- So viele Tags wie sinnvoll – keine künstliche Obergrenze\n"
        "- Tags auf Deutsch, außer international bekannte Eigennamen (YouTube, Netflix, GitHub …)\n"
        "- Nur das JSON-Array, KEIN weiterer Text\n\n"
        f"Browser-Aktivitäten:\n{titles_text}"
    )
    request_body = {"model": model, "prompt": prompt, "stream": False}
    url = ollama_url.rstrip("/") + "/api/generate"
    async with httpx.AsyncClient(timeout=180.0) as client:
        r = await client.post(url, json=request_body)
        r.raise_for_status()
        raw_response = r.json()
    tags = _parse_ollama_json_list(raw_response.get("response", ""))
    if not tags:
        logger.warning("Konnte keine Tags aus Ollama-Antwort parsen: %.200s", raw_response.get("response", ""))
    return tags, prompt, request_body, raw_response


# ── Debug-Ausgabe ────────────────────────────────────────────

_DEBUG_DIR = Path("/app/debug")


def _debug_write(run_dir: Path, filename: str, content: str) -> None:
    """Schreibt eine Debug-Datei. Via ENABLE_DEBUG_LOGS=false komplett deaktivierbar."""
    if os.environ.get("ENABLE_DEBUG_LOGS", "true").lower() != "true":
        return
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / filename).write_text(content, encoding="utf-8")
    except Exception as exc:
        logger.warning("Debug-Schreiben fehlgeschlagen (%s): %s", filename, exc)


# ── Tag-Generierung ──────────────────────────────────────────

async def run_tag_generation(for_date: Optional[date] = None) -> dict:
    """
    Generiert Tags für das angegebene oder das korrekte CEST-Datum.
    Läuft nach Mitternacht (<06:00 CEST) → generiert für Vortag.
    Schreibt jeden Schritt als Debug-Dateien nach /app/debug/<run>/
    """
    now_berlin = datetime.now(tz=_BERLIN)
    run_ts = now_berlin.strftime("%Y-%m-%d_%H-%M-%S")

    if for_date is None:
        if now_berlin.hour < 6:
            target_date = (now_berlin - timedelta(days=1)).date()
        else:
            target_date = now_berlin.date()
    else:
        target_date = for_date

    run_dir = _DEBUG_DIR / f"tags_{run_ts}"
    logger.info("Tag-Generierung gestartet für %s (debug: %s)", target_date, run_dir)

    settings = _get_ollama_settings()
    ollama_url = settings.get("ollama_url") or _OLLAMA_DEFAULTS["ollama_url"]
    model = settings.get("ollama_model", "").strip()

    # ── 00: Run-Info ──────────────────────────────────────────
    run_info = (
        f"Run-Zeitpunkt : {now_berlin.isoformat()}\n"
        f"Ziel-Datum    : {target_date}\n"
        f"Ollama URL    : {ollama_url}\n"
        f"Modell        : {model or '(nicht gesetzt)'}\n"
    )
    _debug_write(run_dir, "00_run_info.txt", run_info)

    if not model:
        msg = "Kein Ollama-Modell konfiguriert – Tag-Generierung übersprungen."
        logger.warning(msg)
        _debug_write(run_dir, "00_run_info.txt", run_info + f"\nFehler: {msg}\n")
        return {"error": "Kein Modell konfiguriert", "date": str(target_date)}

    titles = _get_context_titles_for_date(target_date)

    # ── 01: Context-Einträge ──────────────────────────────────
    entries_text = f"Einträge für {target_date} (CEST): {len(titles)}\n{'='*60}\n"
    entries_text += "\n".join(f"{i+1:4}. {t}" for i, t in enumerate(titles))
    _debug_write(run_dir, "01_context_entries.txt", entries_text)
    _debug_write(run_dir, "00_run_info.txt",
                 run_info + f"Einträge in DB  : {len(titles)}\n")

    if not titles:
        msg = f"Keine Einträge für {target_date} – Generierung übersprungen."
        logger.info(msg)
        _debug_write(run_dir, "01_context_entries.txt", entries_text + f"\n\n{msg}")
        return {"skipped": True, "reason": "Keine Einträge", "date": str(target_date)}

    logger.info("%d Einträge für %s – sende an Ollama (%s) …", len(titles), target_date, model)

    # ── 02–03: Schritt 1 – Hauptkategorien erkennen ──────────
    try:
        categories, cat_prompt, cat_req, cat_resp = await _generate_categories_via_ollama(
            titles, ollama_url, model
        )
    except httpx.HTTPError as exc:
        logger.error("Ollama-Fehler (Kategorien): %s", exc)
        _debug_write(run_dir, "02_categories_error.txt", f"HTTP-Fehler: {exc}\n")
        categories = []  # Fallback: ohne Kategorien weitermachen

    _debug_write(run_dir, "02_categories_prompt.txt", cat_prompt if categories or True else "")
    _debug_write(run_dir, "02_categories_response.json",
                 _json.dumps(cat_resp if 'cat_resp' in dir() else {}, ensure_ascii=False, indent=2))
    _debug_write(run_dir, "02_categories_parsed.json",
                 _json.dumps(categories, ensure_ascii=False, indent=2))

    categories_map = {c.get("category", ""): c.get("weight", 5) for c in categories if c.get("category")}
    logger.info("Schritt 1: %d Kategorien erkannt: %s", len(categories),
                ", ".join(f"{k}({v})" for k, v in categories_map.items()))

    # ── 04–05: Schritt 2 – spezifische Tags extrahieren ──────
    try:
        tags, prompt, request_body, raw_response = await _generate_tags_via_ollama(
            titles, categories, ollama_url, model
        )
    except httpx.HTTPError as exc:
        logger.error("Ollama-Fehler (Tags): %s", exc)
        _debug_write(run_dir, "04_ollama_error.txt", f"HTTP-Fehler: {exc}\n")
        return {"error": str(exc), "date": str(target_date)}

    _debug_write(run_dir, "04_tags_prompt.txt", prompt)
    _debug_write(run_dir, "04_ollama_request.json",
                 _json.dumps(request_body, ensure_ascii=False, indent=2))
    _debug_write(run_dir, "04_ollama_raw_response.json",
                 _json.dumps(raw_response, ensure_ascii=False, indent=2))

    # ── 05: Geparste Tags ─────────────────────────────────────
    _debug_write(run_dir, "05_parsed_tags.json",
                 _json.dumps(tags, ensure_ascii=False, indent=2))

    if not tags:
        _debug_write(run_dir, "00_run_info.txt",
                     run_info + f"Einträge in DB  : {len(titles)}\n"
                     "Fehler          : Keine Tags aus Antwort geparst\n")
        return {"error": "Keine Tags aus Ollama-Antwort geparst", "date": str(target_date)}

    # ── 06: DB-Ergebnis ───────────────────────────────────────
    count = _upsert_auto_tags(tags, target_date, categories_map)
    logger.info("Tag-Generierung abgeschlossen: %d Tags für %s gespeichert.", count, target_date)

    lt_count = _upsert_long_term_tags(tags, categories_map, target_date)
    logger.info("Langzeit-Speicher: %d Tags aktualisiert.", lt_count)

    db_result = {
        "date":             str(target_date),
        "tags_saved":       count,
        "tags_parsed":      len(tags),
        "longterm_updated": lt_count,
        "categories":       categories,
        "tags":             tags,
    }
    _debug_write(run_dir, "06_db_result.json",
                 _json.dumps(db_result, ensure_ascii=False, indent=2))
    _debug_write(run_dir, "00_run_info.txt",
                 run_info
                 + f"Einträge in DB  : {len(titles)}\n"
                 + f"Kategorien      : {len(categories)}\n"
                 + f"Tags geparst    : {len(tags)}\n"
                 + f"Tags gespeichert: {count}\n"
                 + f"Debug-Ordner    : {run_dir}\n")

    return {"success": True, "date": str(target_date), "tags_saved": count,
            "categories": categories, "tags": tags, "debug_dir": str(run_dir)}


# ── Scheduler ────────────────────────────────────────────────

def _reschedule_tag_jobs() -> None:
    """Liest Schedule aus DB und konfiguriert APScheduler-Jobs neu."""
    if _scheduler is None or _pool is None:
        return
    for job_id in ("tag_gen_1", "tag_gen_2"):
        try:
            _scheduler.remove_job(job_id)
        except Exception:
            pass
    settings = _get_ollama_settings()
    for idx, key in enumerate(("tag_schedule_1", "tag_schedule_2"), 1):
        sched = (settings.get(key) or "").strip()
        if not sched:
            continue
        try:
            hour, minute = map(int, sched.split(":"))
        except ValueError:
            logger.warning("Ungültige Schedule-Zeit '%s' ignoriert.", sched)
            continue
        _scheduler.add_job(
            run_tag_generation,
            CronTrigger(hour=hour, minute=minute, timezone=_BERLIN),
            id=f"tag_gen_{idx}",
            name=f"Tag-Generierung {idx}",
            replace_existing=True,
        )
        logger.info("Tag-Generierung geplant: %02d:%02d CEST (Job tag_gen_%d)", hour, minute, idx)

@app.get("/health", tags=["System"])
def health_check():
    """Einfacher Health-Check – kein Token erforderlich."""
    return {"status": "ok"}


@app.get("/debug/auth", tags=["System"])
def debug_auth(request: Request):
    """
    Prüft ob der Authorization-Header den Proxy erreicht.
    Zeigt NUR ob der Header vorhanden ist und welche Header insgesamt ankommen.
    Kein Token-Wert wird zurückgegeben. Kann nach der Diagnose entfernt werden.
    """
    auth = request.headers.get("Authorization", "")
    return {
        "authorization_present": bool(auth),
        "scheme_detected": auth.split(" ")[0] if auth else None,
        "received_header_names": sorted(request.headers.keys()),
    }


@app.post(
    "/api/v1/context",
    status_code=status.HTTP_201_CREATED,
    tags=["Context"],
    dependencies=[Depends(verify_token)],
)
def ingest_context(payload: ContextPayload, request: Request):
    """
    Nimmt einen Kontext-Eintrag entgegen und persistiert ihn in der DB.

    - Authentifizierung: Bearer-Token erforderlich.
    - Kein Embedding-Schritt (Option-2-Architektur).
    - Gibt die neue Datensatz-UUID zurück.
    """
    logger.info("Neuer Eintrag: source=%s url=%s", payload.source, payload.url)
    try:
        new_id = _insert_context(payload)
    except psycopg2.Error as exc:
        logger.error("Datenbankfehler beim Einfügen: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Datenbankfehler. Bitte später erneut versuchen.",
        ) from exc

    if _timeline_enabled():
        try:
            activity_ts = payload.timestamp if isinstance(payload.timestamp, datetime) \
                else datetime.now(timezone.utc)
            _insert_timeline_entry(new_id, payload.source, payload.title, payload.url, activity_ts)
        except Exception as exc:
            logger.warning("Timeline-Insert fehlgeschlagen: %s", exc)

    return {"id": new_id, "status": "queued"}


# Alias: PUT auf denselben Endpunkt (für Clients, die PUT bevorzugen)
@app.put(
    "/api/v1/context",
    status_code=status.HTTP_201_CREATED,
    tags=["Context"],
    dependencies=[Depends(verify_token)],
)
def ingest_context_put(payload: ContextPayload, request: Request):
    """Alias für POST /api/v1/context – identische Logik."""
    return ingest_context(payload, request)


# ── Einstellungs-Endpunkte (Cookies für Android-Scraper) ─────

@app.post(
    "/api/v1/settings/cookies",
    status_code=status.HTTP_200_OK,
    tags=["Settings"],
    dependencies=[Depends(verify_token)],
)
def save_cookies(payload: CookiesPayload):
    """
    Speichert Google-Session-Cookies aus der Browser-Extension.
    Die Cookies werden verschlüsselt (als JSON-String) in system_settings abgelegt
    und vom Android-Scraper-Container intern abgerufen.
    """
    serialized = _json.dumps(payload.cookies)
    try:
        _upsert_setting(_COOKIE_KEY, serialized)
    except psycopg2.Error as exc:
        logger.error("DB-Fehler beim Speichern der Cookies: %s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Datenbankfehler.")
    logger.info("Google-Cookies aktualisiert: %d Cookie(s) gespeichert.", len(payload.cookies))
    return {"status": "ok", "saved": len(payload.cookies)}


@app.get(
    "/api/v1/settings/cookies",
    tags=["Settings"],
    dependencies=[Depends(verify_token)],
)
def get_cookies():
    """
    Gibt die gespeicherten Google-Session-Cookies zurück.
    Wird intern vom Android-Scraper-Container aufgerufen.
    """
    try:
        value = _get_setting(_COOKIE_KEY)
    except psycopg2.Error as exc:
        logger.error("DB-Fehler beim Lesen der Cookies: %s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Datenbankfehler.")
    if value is None:
        return {"cookies": None, "message": "Keine Cookies gespeichert."}
    return {"cookies": _json.loads(value)}


# ── Extension-Download (vorkonfiguriert) ─────────────────────

_EXTENSION_DIR = Path("/app/extension")

# Dateien, die ins Extension-Paket aufgenommen werden
_EXTENSION_FILES = [
    "manifest.json",
    "background.js",
    "content_cookie_bridge.js",
    "options.html",
    "options.js",
    "icons/icon16.png",
    "icons/icon48.png",
    "icons/icon128.png",
]


@app.post(
    "/api/v1/download/extension",
    tags=["Admin"],
    dependencies=[Depends(verify_token)],
)
def download_extension(req: ExtensionBuildRequest):
    """
    Gibt ein vorkonfiguriertes Extension-Paket als ZIP zurück.
    Bettet die angegebene Gateway-URL und den API-Token in
    _myfeed_defaults.json ein – die Extension liest diese beim
    ersten Start und befüllt die Einstellungen automatisch.
    """
    if not _EXTENSION_DIR.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Extension-Dateien nicht gefunden (Volume nicht gemountet?).",
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in _EXTENSION_FILES:
            src = _EXTENSION_DIR / rel
            if src.is_file():
                zf.write(src, rel)

        # Vorkonfigurierte Defaults einbetten
        defaults = {
            "gatewayUrl":  req.gateway_url.rstrip("/"),
            "bearerToken": API_BEARER_TOKEN,
        }
        zf.writestr("myfeed_defaults.json", _json.dumps(defaults))

    buf.seek(0)
    ext = "xpi" if req.platform == "firefox" else "zip"
    filename = f"myfeed-{req.platform}-preconfigured.{ext}"
    logger.info("Extension-Paket erstellt: platform=%s gateway=%s",
                req.platform, req.gateway_url)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


_VSCODE_EXT_DIR = Path("/app/vscode-extension")

_VSCODE_EXT_FILES = [
    "package.json",
    "out/extension.js",
    "out/gateway.js",
    "out/collector.js",
    "out/settings.js",
]

_VSIX_CONTENT_TYPES = """\
<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension=".vsixmanifest" ContentType="text/xml" />
  <Default Extension=".json" ContentType="application/json" />
  <Default Extension=".js" ContentType="application/javascript" />
</Types>"""

_VSIX_MANIFEST = """\
<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="myfeed-vscode" Version="1.0.0" Publisher="myfeed" TargetPlatform="universal" />
    <DisplayName>MyFeed</DisplayName>
    <Description xml:space="preserve">Sendet VSCode-Kontext an den MyFeed Gateway zur Interessensgenerierung</Description>
    <Tags>myfeed,personalization,context</Tags>
    <Categories>Other</Categories>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="^1.82.0" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value="" />
    </Properties>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code" Version="[1.82,)" />
  </Installation>
  <Dependencies />
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" />
  </Assets>
</PackageManifest>"""


@app.post(
    "/api/v1/download/vscode-extension",
    tags=["Admin"],
    dependencies=[Depends(verify_token)],
)
def download_vscode_extension(req: VscodeExtensionBuildRequest):
    """
    Gibt ein vorkonfiguriertes VSCode-Extension-Paket (.vsix) zurück.
    Bettet die Gateway-URL und den API-Token als defaults.json ein –
    die Extension liest diese beim ersten Start und befüllt die Einstellungen automatisch.
    """
    if not _VSCODE_EXT_DIR.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VSCode-Extension-Dateien nicht gefunden (Volume nicht gemountet?).",
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _VSIX_CONTENT_TYPES)
        zf.writestr("extension.vsixmanifest", _VSIX_MANIFEST)

        for rel in _VSCODE_EXT_FILES:
            src = _VSCODE_EXT_DIR / rel
            if src.is_file():
                zf.write(src, f"extension/{rel}")

        defaults = {
            "gatewayUrl":  req.gateway_url.rstrip("/"),
            "bearerToken": API_BEARER_TOKEN,
        }
        zf.writestr("extension/defaults.json", _json.dumps(defaults))

    buf.seek(0)
    logger.info("VSCode-Extension-Paket erstellt: gateway=%s", req.gateway_url)
    return StreamingResponse(
        buf,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="myfeed-vscode-preconfigured.vsix"'},
    )


# ── Ollama-Einstellungen ─────────────────────────────────────

@app.get(
    "/api/v1/settings/ollama",
    tags=["Settings"],
    dependencies=[Depends(verify_token)],
)
def get_ollama_settings():
    """Liefert alle Ollama- und Zeitplan-Einstellungen."""
    try:
        return _get_ollama_settings()
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="Datenbankfehler.") from exc


@app.put(
    "/api/v1/settings/ollama",
    tags=["Settings"],
    dependencies=[Depends(verify_token)],
)
def update_ollama_settings(payload: SettingsBulkPayload):
    """Aktualisiert Ollama- und Zeitplan-Einstellungen und rescheduled Jobs."""
    try:
        updated = []
        for key, value in payload.settings.items():
            if key in _OLLAMA_SETTING_KEYS:
                _upsert_setting(key, str(value))
                updated.append(key)
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="Datenbankfehler.") from exc
    _reschedule_tag_jobs()
    return {"status": "ok", "updated": updated}


@app.get(
    "/api/v1/ollama/models",
    tags=["Ollama"],
    dependencies=[Depends(verify_token)],
)
async def get_ollama_models():
    """Ruft verfügbare Modelle vom konfigurierten Ollama-Server ab."""
    url = _get_setting("ollama_url") or _OLLAMA_DEFAULTS["ollama_url"]
    try:
        models = await _fetch_ollama_models(url)
        return {"models": models}
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Ollama nicht erreichbar: {exc}") from exc


@app.get(
    "/api/v1/ollama/status",
    tags=["Ollama"],
)
async def get_ollama_status():
    """Prüft ob der Ollama-Server erreichbar ist (kein Auth erforderlich für UI-Feedback)."""
    url = _get_setting("ollama_url") or _OLLAMA_DEFAULTS["ollama_url"]
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url.rstrip("/") + "/api/tags")
            return {"reachable": r.is_success, "http_status": r.status_code}
    except Exception as exc:
        return {"reachable": False, "error": str(exc)}


# ── Tags ─────────────────────────────────────────────────────

@app.get(
    "/api/v1/tags",
    tags=["Tags"],
    dependencies=[Depends(verify_token)],
)
def list_tags():
    """Liefert alle Tags (auto + manuell), sortiert nach Gewichtung."""
    try:
        return {"tags": _get_tags()}
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="Datenbankfehler.") from exc


@app.post(
    "/api/v1/tags",
    status_code=status.HTTP_201_CREATED,
    tags=["Tags"],
    dependencies=[Depends(verify_token)],
)
def create_tag(payload: TagCreatePayload):
    """Legt einen manuellen persistenten Tag an."""
    try:
        tag_id = _create_manual_tag(
            payload.name, payload.weight, payload.category, payload.category_weight
        )
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="Tag mit diesem Namen existiert bereits.")
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="Datenbankfehler.") from exc
    return {
        "id":               tag_id,
        "name":             payload.name,
        "weight":           payload.weight,
        "category":         payload.category,
        "category_weight":  payload.category_weight,
        "effective_weight": _effective_weight(payload.weight, payload.category_weight),
    }


@app.get(
    "/api/v1/tags/longterm",
    tags=["Tags"],
    dependencies=[Depends(verify_token)],
)
def get_longterm_tags():
    """Gibt alle Langzeit-Tags zurück."""
    try:
        return {"tags": _get_long_term_tags()}
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="Datenbankfehler.") from exc


@app.delete(
    "/api/v1/tags/longterm",
    tags=["Tags"],
    dependencies=[Depends(verify_token)],
)
def clear_longterm_tags():
    """Löscht alle Langzeit-Tags."""
    try:
        count = _clear_long_term_tags()
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="Datenbankfehler.") from exc
    return {"deleted": count}


@app.delete(
    "/api/v1/tags/longterm/{tag_id}",
    tags=["Tags"],
    dependencies=[Depends(verify_token)],
)
def delete_longterm_tag(tag_id: str):
    """Löscht einen einzelnen Langzeit-Tag."""
    try:
        found = _delete_long_term_tag(tag_id)
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="Datenbankfehler.") from exc
    if not found:
        raise HTTPException(status_code=404, detail="Langzeit-Tag nicht gefunden.")
    return {"status": "deleted"}


@app.delete(
    "/api/v1/tags/{tag_id}",
    tags=["Tags"],
    dependencies=[Depends(verify_token)],
)
def delete_tag(tag_id: str):
    """Löscht einen Tag (manuell oder auto-generiert)."""
    try:
        found = _delete_tag(tag_id)
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="Datenbankfehler.") from exc
    if not found:
        raise HTTPException(status_code=404, detail="Tag nicht gefunden.")
    return {"status": "deleted"}


@app.post(
    "/api/v1/tags/generate",
    tags=["Tags"],
    dependencies=[Depends(verify_token)],
)
async def trigger_tag_generation(
    date_str: Optional[str] = Query(default=None, description="Datum YYYY-MM-DD (leer = heute)")
):
    """
    Löst die Tag-Generierung manuell aus.
    Optionaler Query-Parameter: ?date_str=YYYY-MM-DD
    """
    target_date: Optional[date] = None
    if date_str:
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(status_code=422, detail="date_str muss Format YYYY-MM-DD haben.")
    result = await run_tag_generation(for_date=target_date)
    if "error" in result and not result.get("success"):
        raise HTTPException(status_code=502, detail=result["error"])
    return result


# ════════════════════════════════════════════════════════════
# NEWS-SUCHE
# ════════════════════════════════════════════════════════════

# ── Einstellungen ────────────────────────────────────────────

# DuckDuckGo: Sprache → Region
_LANG_TO_REGION: dict[str, str] = {
    "de": "de-de",
    "en": "us-en",
    "fr": "fr-fr",
    "es": "es-es",
    "it": "it-it",
    "nl": "nl-nl",
    "pl": "pl-pl",
    "pt": "pt-pt",
    "ru": "ru-ru",
    "ja": "jp-ja",
    "zh": "cn-zh",
}

_NEWS_SETTING_KEYS = {
    "news_duckduckgo_enabled", "news_searxng_enabled",
    "searxng_url",
    "news_max_results_per_tag", "news_schedule",
    "news_languages", "news_timelimit",
    "news_ollama_rerank",
    "long_term_tags_enabled",
}
_NEWS_DEFAULTS = {
    "news_duckduckgo_enabled":  "false",
    "news_searxng_enabled":     "false",
    "searxng_url":              "http://searxng:8080",
    "news_max_results_per_tag": "5",
    "news_schedule":            "",
    "news_languages":           "de,en",
    "news_timelimit":           "w",
    "news_ollama_rerank":       "false",
    "long_term_tags_enabled":   "false",
}


def _get_news_settings() -> dict:
    """Liest alle News-Sucheinstellungen in einem Query."""
    assert _pool is not None
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT key, value FROM system_settings WHERE key = ANY(%s)",
                (list(_NEWS_SETTING_KEYS),),
            )
            result = dict(cur.fetchall())
    finally:
        _pool.putconn(conn)
    for k, v in _NEWS_DEFAULTS.items():
        result.setdefault(k, v)
    return result


# ── DB-Hilfsfunktionen ───────────────────────────────────────

def _get_active_tags_for_search(include_longterm: bool = False) -> list:
    """Liefert alle Tags mit effektivem Gewicht, sortiert nach effective_weight DESC.
    Wenn include_longterm=True werden Langzeit-Tags ergänzt (aktuelle Tags haben Vorrang)."""
    assert _pool is not None
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, weight, category, category_weight FROM tags ORDER BY weight DESC"
            )
            tag_map: dict = {}
            for row in cur.fetchall():
                eff = _effective_weight(row[1], row[3])
                tag_map[row[0]] = {
                    "name":             row[0],
                    "weight":           row[1],
                    "category":         row[2],
                    "category_weight":  row[3],
                    "effective_weight": eff,
                    "_source":          "current",
                }
            if include_longterm:
                cur.execute(
                    "SELECT name, weight, category, category_weight FROM long_term_tags"
                )
                for row in cur.fetchall():
                    if row[0] not in tag_map:
                        eff = _effective_weight(row[1], row[3])
                        tag_map[row[0]] = {
                            "name":             row[0],
                            "weight":           row[1],
                            "category":         row[2],
                            "category_weight":  row[3],
                            "effective_weight": eff,
                            "_source":          "longterm",
                        }
            results = sorted(tag_map.values(), key=lambda x: -x["effective_weight"])
            return results
    finally:
        _pool.putconn(conn)


def _get_news(found_date: Optional[date] = None, tag_name: Optional[str] = None) -> list:
    """Liefert News-Ergebnisse, optional gefiltert nach Datum und/oder Tag."""
    assert _pool is not None
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            where, params = [], []
            if found_date:
                where.append("found_date = %s")
                params.append(found_date)
            if tag_name:
                where.append("tag_name = %s")
                params.append(tag_name)
            sql = (
                "SELECT id::text, tag_name, tag_weight, headline, snippet, "
                "url, image_url, source_name, search_method, found_date, published_at, created_at "
                "FROM news_results"
            )
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY tag_weight DESC, COALESCE(published_at, found_date::timestamptz) DESC, headline ASC"
            cur.execute(sql, params)
            cols = ["id", "tag_name", "tag_weight", "headline", "snippet",
                    "url", "image_url", "source_name", "search_method", "found_date", "published_at", "created_at"]
            rows = []
            for row in cur.fetchall():
                r = dict(zip(cols, row))
                r["found_date"]   = r["found_date"].isoformat() if r["found_date"] else None
                r["published_at"] = r["published_at"].isoformat() if r["published_at"] else None
                r["created_at"]   = r["created_at"].isoformat()
                rows.append(r)
            return rows
    finally:
        _pool.putconn(conn)


def _upsert_news(
    tag_name: str, tag_weight: int, headline: str, snippet: Optional[str],
    url: str, image_url: Optional[str], source_name: Optional[str],
    search_method: str, found_date: date,
    published_at: Optional[str] = None,
) -> bool:
    """Fügt ein News-Ergebnis ein (ON CONFLICT DO NOTHING). True = neu eingefügt."""
    assert _pool is not None
    # published_at: ISO-String aus _fetch_article_meta_batch (z.B. "2026-06-02T10:30:00+02:00")
    pub_dt: Optional[datetime] = None
    if published_at:
        try:
            pub_dt = datetime.fromisoformat(published_at)
        except ValueError:
            pass
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO news_results
                    (tag_name, tag_weight, headline, snippet, url, image_url, source_name,
                     search_method, found_date, published_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (url, tag_name, found_date) DO NOTHING
                """,
                (tag_name, tag_weight, headline[:500], (snippet or "")[:1000],
                 url, (image_url or "")[:2048], (source_name or "")[:256],
                 search_method, found_date, pub_dt),
            )
            inserted = cur.rowcount > 0
            conn.commit()
            return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def _delete_news_item(news_id: str) -> bool:
    """Löscht ein News-Ergebnis."""
    assert _pool is not None
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM news_results WHERE id = %s::uuid RETURNING id", (news_id,)
            )
            deleted = cur.fetchone() is not None
            conn.commit()
            return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)



# ── Suchmethode 1: DuckDuckGo ────────────────────────────────

async def _search_news_duckduckgo(
    tags: list,
    max_per_tag: int,
    languages: str = "de,en",
    timelimit: str = "w",
) -> list:
    """
    Sucht News via DuckDuckGo (ddgs-Paket) für jeden Tag.
    - Läuft synchron in einem Thread-Executor (ddgs hat keine async-API).
    - Erstellt pro Tag eine neue DDGS()-Instanz (verhindert Cascade-Fehler).
    - Mehrere Sprachen → region=wt-wt; eine Sprache → spezifische Region.
    - 1,5 s Pause zwischen Requests.
    """
    # Sprachen → DDG-Region
    lang_list = [l.strip().lower() for l in languages.split(",") if l.strip()]
    unique_regions = []
    for lang in lang_list:
        r = _LANG_TO_REGION.get(lang, "wt-wt")
        if r not in unique_regions:
            unique_regions.append(r)
    region = unique_regions[0] if len(unique_regions) == 1 else "wt-wt"

    tl = timelimit.strip() or None

    # Wenn konkrete Sprachen angegeben: mehr Ergebnisse von DDG anfordern,
    # da Sprachfilterung danach einige aussortiert.
    do_lang_filter = bool(lang_list) and _LANGDETECT_OK
    fetch_max = max_per_tag * 3 if do_lang_filter else max_per_tag

    seen_urls: set = set()
    results:   list = []
    rate_limited = False

    for tag in tags:
        if rate_limited:
            break
        await asyncio.sleep(1.5)
        _kw  = tag["name"]
        _reg = region
        _max = fetch_max
        _tl  = tl
        try:
            news = await asyncio.to_thread(
                lambda: DDGS().news(_kw, region=_reg, max_results=_max, timelimit=_tl)
            )
            tag_count = 0
            for item in news:
                if tag_count >= max_per_tag:
                    break
                url      = item.get("url", "").strip()
                headline = item.get("title", "").strip()
                if not url or not headline or url in seen_urls:
                    continue
                # Sprachfilterung per langdetect
                if do_lang_filter:
                    try:
                        detected = _lang_detect(
                            (headline + " " + item.get("body", "")).strip()
                        )
                    except Exception:
                        detected = None  # bei Fehler: behalten
                    if detected and detected not in lang_list:
                        continue
                seen_urls.add(url)
                tag_count += 1
                results.append({
                    "tag":      tag["name"],
                    "weight":   tag.get("effective_weight", tag["weight"]),
                    "headline": headline,
                    "snippet":  item.get("body", "")[:500],
                    "url":      url,
                    "image":    (item.get("image") or "").strip(),
                    "source":   item.get("source", ""),
                })
        except Exception as exc:
            err = str(exc)
            if "403" in err or "atelimit" in err:
                logger.warning("DDG Rate-Limit bei Tag='%s'. Suche abgebrochen.", tag["name"])
                rate_limited = True
            else:
                logger.warning("DDG-Fehler Tag='%s' region=%s: %s", tag["name"], region, err)
    return results


# ── Suchmethode 2: SearXNG ───────────────────────────────────

async def _search_news_searxng(
    tags: list,
    searxng_url: str,
    max_per_tag: int,
    languages: str = "de,en",
    timelimit: str = "w",
) -> list:
    """
    Sucht News via SearXNG JSON API für jeden Tag.
    GET /search?q=...&categories=news&format=json&time_range=...
    """
    _TL_MAP = {"d": "day", "w": "week", "m": "month"}
    time_range = _TL_MAP.get(timelimit)

    lang_list = [l.strip().lower() for l in languages.split(",") if l.strip()]

    seen_urls: set = set()
    results: list = []
    base_url = searxng_url.rstrip("/") + "/search"

    async with httpx.AsyncClient(timeout=30.0) as client:
        for tag in tags:
            await asyncio.sleep(0.5)
            params: dict = {
                "q":          tag["name"],
                "categories": "news",
                "format":     "json",
            }
            if time_range:
                params["time_range"] = time_range
            if len(lang_list) == 1:
                params["language"] = lang_list[0]
            try:
                r = await client.get(base_url, params=params)
                r.raise_for_status()
                data = r.json()
                tag_count = 0
                for item in data.get("results", []):
                    if tag_count >= max_per_tag:
                        break
                    url      = (item.get("url") or "").strip()
                    headline = (item.get("title") or "").strip()
                    if not url or not headline or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    tag_count += 1
                    results.append({
                        "tag":      tag["name"],
                        "weight":   tag.get("effective_weight", tag["weight"]),
                        "headline": headline,
                        "snippet":  (item.get("content") or "")[:500],
                        "url":      url,
                        "image":    (item.get("thumbnail") or "").strip(),
                        "source":   (item.get("engine") or ""),
                    })
            except Exception as exc:
                logger.warning("SearXNG-Fehler Tag='%s': %s", tag["name"], exc)

    return results


# ── Ollama Re-Ranking ─────────────────────────────────────────

async def _rerank_news_via_ollama(
    items: list,
    tags: list,
    ollama_url: str,
    model: str,
) -> list:
    """
    Lässt Ollama alle gesammelten News-Artikel bewerten:
    - score 0  → herausfiltern (irrelevant, veraltet, Clickbait)
    - score 1-10 → Relevanz zum Tag
    Gibt gefilterte, neu gewichtete Liste zurück (absteigend sortiert).
    """
    if not items or not model:
        return items

    tag_map = {t["name"]: t["weight"] for t in tags}

    # Kompaktes JSON für den Prompt (nur id, tag, headline, snippet)
    articles_for_prompt = [
        {
            "id":       i,
            "tag":      it["tag"],
            "headline": it["headline"],
            "snippet":  (it.get("snippet") or "")[:200],
        }
        for i, it in enumerate(items)
    ]

    tag_list_str = "; ".join(
        f"{t['name']} (Effektiv-Gewicht {t.get('effective_weight', t['weight'])}"
        f", Kategorie: {t.get('category') or '?'}"
        + (", Langzeit-Interesse" if t.get("_source") == "longterm" else "")
        + ")"
        for t in sorted(tags, key=lambda x: -x.get("effective_weight", x["weight"]))
    )

    prompt = (
        "Du bist ein persönlicher News-Filter. Bewerte jeden Artikel für einen Nutzer\n"
        "anhand seines Interessen-Profils UND der allgemeinen Nachrichtenwichtigkeit.\n\n"
        f"Interessen-Profil des Nutzers (Thema → relative Intensität 1-10):\n{tag_list_str}\n\n"
        "Scoring-Skala 0-10:\n"
        "- 0: herausfiltern (Clickbait, Werbung, keine echte Nachricht, passt zu keinem Interesse)\n"
        "- 1-3: schwacher Bezug zu Nutzerinteressen oder sehr allgemeines Thema\n"
        "- 4-6: klarer Bezug zu Nutzerinteressen, solide aktuelle Nachricht\n"
        "- 7-9: wichtige aktuelle Nachricht, direkter Bezug zu einem Nutzerinteresse\n"
        "- 10: herausragend: Top-Aktualität + perfekte Relevanz für den Nutzer\n\n"
        "Wichtig: Die Profilgewichtung zeigt Interessensintensität, NICHT den Score.\n"
        "Ein Artikel zu einem Thema mit Gewicht 3 kann Score 10 bekommen wenn er sehr wichtig ist.\n"
        "Beurteile: Aktualität, Nachrichtenwert, Relevanz zum Profil, Qualität (kein Clickbait).\n\n"
        "Artikel:\n"
        + _json.dumps(articles_for_prompt, ensure_ascii=False)
        + "\n\nAntworte NUR mit einem JSON-Array (keine Erklärungen):\n"
        '[{"id": 0, "score": 8}, {"id": 1, "score": 0}, ...]'
    )

    request_body = {"model": model, "prompt": prompt, "stream": False}
    url = ollama_url.rstrip("/") + "/api/generate"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url, json=request_body)
            r.raise_for_status()
            raw = r.json()

        response_text = raw.get("response", "")
        scores: list = []
        try:
            scores = _json.loads(response_text)
        except (_json.JSONDecodeError, ValueError):
            match = re.search(r"\[.*?\]", response_text, re.DOTALL)
            if match:
                try:
                    scores = _json.loads(match.group(0))
                except (_json.JSONDecodeError, ValueError):
                    pass

        if not scores:
            logger.warning("Ollama-Reranking: Konnte keine Scores parsen – behalte Original.")
            return items

        score_map = {
            s["id"]: s.get("score", 5)
            for s in scores
            if isinstance(s, dict) and "id" in s
        }

        reranked = []
        for i, item in enumerate(items):
            score = score_map.get(i, 5)
            if score <= 0:
                continue  # herausfiltern
            # Direkter Score als Gewicht – keine Tag-Gewicht-Multiplikation
            final_weight = max(1, min(10, score))
            reranked.append((final_weight, dict(item, weight=final_weight)))

        reranked.sort(key=lambda x: -x[0])
        logger.info("Ollama-Reranking: %d → %d Artikel.", len(items), len(reranked))
        return [it for _, it in reranked]

    except Exception as exc:
        logger.warning("Ollama-Reranking fehlgeschlagen: %s – behalte Original.", exc)
        return items


# ── og:image + Publikationsdatum Fetcher ───────────────────────

_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property=["\']og:image["\']|name=["\']twitter:image["\'|name=["\']twitter:image:src["\'])'  # noqa
    r'[^>]+content=["\']([^"\']+)["\']'
    r'|<meta[^>]+content=["\']([^"\']+)["\']'
    r'[^>]+(?:property=["\']og:image["\']|name=["\']twitter:image["\'|name=["\']twitter:image:src["\'])',
    re.IGNORECASE | re.DOTALL,
)

# Datum-Extraktion: Priorität: JSON-LD > OpenGraph > Schema.org > <time> > generic meta
_PUBDATE_PATTERNS: list = [
    # JSON-LD datePublished (höchste Zuverlässigkeit)
    re.compile(r'"datePublished"\s*:\s*"([\d\-T:+Z. ]{10,35})"', re.IGNORECASE),
    # OpenGraph article:published_time
    re.compile(
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([à\d\-T:+Z ]{10,35})["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]+content=["\']([\d\-T:+Z ]{10,35})["\'][^>]+property=["\']article:published_time["\']',
        re.IGNORECASE,
    ),
    # Schema.org <meta itemprop="datePublished">
    re.compile(
        r'<meta[^>]+itemprop=["\']datePublished["\'][^>]+content=["\']([\d\-T:+Z ]{10,35})["\']',
        re.IGNORECASE,
    ),
    # <time itemprop="datePublished" datetime="..."> oder <time pubdate datetime="...">
    re.compile(
        r'<time[^>]+(?:itemprop=["\']datePublished["\'][^>]+|pubdate[^>]+)?datetime=["\']([\d\-T:+Z ]{10,35})["\']',
        re.IGNORECASE,
    ),
    # Generische Meta-Tags
    re.compile(
        r'<meta[^>]+name=["\'](?:publishdate|pubdate|publish[_-]date|date|date\.created)["\'][^>]+'
        r'content=["\']([\d\-T:+Z/ ]{10,35})["\']',
        re.IGNORECASE,
    ),
]


def _parse_article_date(raw: str) -> Optional[datetime]:
    """Parst verschiedene Datums-Formate; gibt immer timezone-aware datetime zurück."""
    s = raw.strip().replace("Z", "+00:00")
    # Leerzeichen als T-Trenner (z.B. "2026-06-02 10:30:00")
    s = re.sub(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})', r'\1T\2', s)
    # Sub-Sekunden entfernen
    s = re.sub(r'(\.\d+)(?=[+\-]|$)', '', s)
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    # Nur Datum (YYYY-MM-DD)
    try:
        d = date.fromisoformat(s[:10])
        return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)
    except ValueError:
        pass
    return None


async def _fetch_article_meta(url: str) -> tuple:
    """
    Liest aus einem Artikel-URL in einem einzigen HTTP-Request:
    - og:image / twitter:image (image_url)
    - Publikationsdatum via JSON-LD, OpenGraph, Schema.org, <time> (published_at_iso)
    Gibt (image_url: str, published_at_iso: str) zurück.
    4 s Timeout, nur erste 40 KB werden gescannt.
    """
    try:
        async with httpx.AsyncClient(
            timeout=4.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; MyFeed/1.0)"},
        ) as client:
            r = await client.get(url)
            if not r.is_success:
                return "", ""
            chunk = r.text[:40000]

            # og:image
            img = ""
            m = _OG_IMAGE_RE.search(chunk)
            if m:
                img = (m.group(1) or m.group(2) or "")[:2048]

            # Publikationsdatum
            pub_iso = ""
            for pat in _PUBDATE_PATTERNS:
                pm = pat.search(chunk)
                if pm:
                    dt = _parse_article_date(pm.group(1))
                    if dt:
                        pub_iso = dt.isoformat()
                        break

            return img, pub_iso
    except Exception:
        pass
    return "", ""


async def _fetch_article_meta_batch(items: list) -> list:
    """Ergänzt image_url und published_at für alle Items (max. 8 gleichzeitig, 4 s Timeout je)."""
    if not items:
        return items
    sem = asyncio.Semaphore(8)

    async def _one(item: dict) -> dict:
        async with sem:
            img, pub_iso = await _fetch_article_meta(item.get("url", ""))
            updates: dict = {}
            if img:
                updates["image_url"] = img
            if pub_iso:
                updates["published_at"] = pub_iso
            return dict(item, **updates) if updates else item

    return list(await asyncio.gather(*(_one(it) for it in items)))


# ── Haupt-News-Suchfunktion ──────────────────────────────────

async def run_news_search(for_date: Optional[date] = None) -> dict:
    """
    Führt die News-Suche mit den konfigurierten Methoden durch.
    Schreibt jeden Schritt als Debug-Dateien nach /app/debug/news_<ts>/
    """
    now_berlin = datetime.now(tz=_BERLIN)
    run_ts = now_berlin.strftime("%Y-%m-%d_%H-%M-%S")
    target_date = for_date or now_berlin.date()
    run_dir = _DEBUG_DIR / f"news_{run_ts}"

    logger.info("News-Suche gestartet für %s (debug: %s)", target_date, run_dir)

    settings = _get_news_settings()
    ddg_enabled     = settings.get("news_duckduckgo_enabled", "false").lower() == "true"
    searxng_enabled = settings.get("news_searxng_enabled", "false").lower() == "true"
    searxng_url     = (settings.get("searxng_url") or "http://searxng:8080").strip()
    languages       = (settings.get("news_languages") or "de,en").strip()
    timelimit       = (settings.get("news_timelimit") or "w").strip()
    try:
        max_per_tag = int(settings.get("news_max_results_per_tag") or "5")
    except ValueError:
        max_per_tag = 5

    ollama_cfg   = _get_ollama_settings()
    ollama_url   = ollama_cfg.get("ollama_url") or _OLLAMA_DEFAULTS["ollama_url"]
    ollama_model = ollama_cfg.get("ollama_model", "").strip()
    ollama_rerank = (
        settings.get("news_ollama_rerank", "false").lower() == "true"
        and bool(ollama_model)
    )

    run_info = (
        f"Run-Zeitpunkt    : {now_berlin.isoformat()}\n"
        f"Ziel-Datum       : {target_date}\n"
        f"DuckDuckGo       : {'aktiv' if ddg_enabled else 'inaktiv'}\n"
        f"SearXNG          : {'aktiv' if searxng_enabled else 'inaktiv'} ({searxng_url})\n"
        f"Ollama Reranking : {'aktiv' if ollama_rerank else 'inaktiv'}\n"
        f"Sprachen         : {languages}\n"
        f"Zeitraum         : {timelimit or 'kein Limit'}\n"
        f"Max pro Tag      : {max_per_tag}\n"
    )
    _debug_write(run_dir, "00_run_info.txt", run_info)

    if not ddg_enabled and not searxng_enabled:
        return {"skipped": True, "reason": "Keine Suchmethode aktiviert"}

    lt_enabled = settings.get("long_term_tags_enabled", "false").lower() == "true"
    tags = _get_active_tags_for_search(include_longterm=lt_enabled)
    if not tags:
        return {"skipped": True, "reason": "Keine Tags vorhanden"}

    _debug_write(run_dir, "01_tags.json", _json.dumps(tags, ensure_ascii=False, indent=2))
    _debug_write(run_dir, "00_run_info.txt", run_info + f"Tags             : {len(tags)}\n")

    total_found = 0
    total_saved = 0
    errors: list = []
    all_items: list = []   # alle gesammelten Ergebnisse vor dem Speichern

    # ── DuckDuckGo ────────────────────────────────────────────
    if ddg_enabled:
        try:
            ddg_items = await _search_news_duckduckgo(tags, max_per_tag, languages, timelimit)
            _debug_write(run_dir, "02_duckduckgo_results.json",
                         _json.dumps(ddg_items, ensure_ascii=False, indent=2))
            if not ddg_items:
                _debug_write(run_dir, "02_duckduckgo_empty.txt",
                             "DDG lieferte 0 Ergebnisse. Mögliche Ursachen:\n"
                             "- IP rate-limitiert (warte 30 Min und versuche es erneut)\n"
                             "- Tags zu spezifisch / keine aktuellen News\n"
                             "- Zeitraum-Einstellung zu restriktiv\n")
            for item in ddg_items:
                item["_method"] = "duckduckgo"
            all_items.extend(ddg_items)
            logger.info("DDG: %d Ergebnisse gefunden.", len(ddg_items))
        except Exception as exc:
            logger.error("DuckDuckGo-Suche fehlgeschlagen: %s", exc)
            _debug_write(run_dir, "02_duckduckgo_error.txt", f"Fehler: {exc}\n")
            errors.append(f"DuckDuckGo: {exc}")

    # ── SearXNG ───────────────────────────────────────────────
    if searxng_enabled:
        try:
            searxng_items = await _search_news_searxng(
                tags, searxng_url, max_per_tag, languages, timelimit
            )
            _debug_write(run_dir, "03_searxng_results.json",
                         _json.dumps(searxng_items, ensure_ascii=False, indent=2))
            if not searxng_items:
                _debug_write(run_dir, "03_searxng_empty.txt",
                             "SearXNG lieferte 0 Ergebnisse. Mögliche Ursachen:\n"
                             "- SearXNG-Container nicht erreichbar\n"
                             "- Keine News-Engines in SearXNG konfiguriert\n"
                             "- Tags zu spezifisch / keine aktuellen News\n")
            for item in searxng_items:
                item["_method"] = "searxng"
            all_items.extend(searxng_items)
            logger.info("SearXNG: %d Ergebnisse gefunden.", len(searxng_items))
        except Exception as exc:
            logger.error("SearXNG-Suche fehlgeschlagen: %s", exc)
            _debug_write(run_dir, "03_searxng_error.txt", f"Fehler: {exc}\n")
            errors.append(f"SearXNG: {exc}")

    # ── Cross-Source-Deduplizierung ─────────────────────────────
    _before_dedup = len(all_items)
    # 1. URL + Tag deduplizieren (gleiche URL für gleichen Tag → höchstes Gewicht behalten)
    # URL-decode vor Vergleich: %C3%BC == ü sollen als identisch gelten
    _url_tag_seen: dict = {}
    _deduped: list = []
    for _it in all_items:
        _key = (_url_unquote(_it.get("url", "")).lower().rstrip("/"), _it.get("tag", ""))
        if _key not in _url_tag_seen:
            _url_tag_seen[_key] = len(_deduped)
            _deduped.append(_it)
        elif _it.get("weight", 0) > _deduped[_url_tag_seen[_key]].get("weight", 0):
            _deduped[_url_tag_seen[_key]] = _it
    # 2. Headline + Tag deduplizieren (gleicher Artikel mit leicht verschiedener URL)
    _hl_tag_seen: set = set()
    _deduped2: list = []
    for _it in _deduped:
        _hk = (re.sub(r'\W+', '', _it.get("headline", "").lower())[:80], _it.get("tag", ""))
        if _hk not in _hl_tag_seen:
            _hl_tag_seen.add(_hk)
            _deduped2.append(_it)
    all_items = _deduped2
    _removed = _before_dedup - len(all_items)
    if _removed:
        logger.info("Deduplizierung: %d Duplikate entfernt (%d → %d).",
                    _removed, _before_dedup, len(all_items))
        _debug_write(run_dir, "04_dedup_info.txt",
                     f"Vor Dedup: {_before_dedup}\nNach Dedup: {len(all_items)}\nEntfernt: {_removed}\n")

    # ── Ollama Re-Ranking (optional) ──────────────────────────
    if all_items and ollama_rerank:
        try:
            all_items = await _rerank_news_via_ollama(
                all_items, tags, ollama_url, ollama_model
            )
            _debug_write(run_dir, "04_reranked.json",
                         _json.dumps(all_items, ensure_ascii=False, indent=2))
        except Exception as exc:
            logger.error("Ollama-Reranking fehlgeschlagen: %s", exc)
            errors.append(f"Ollama-Reranking: {exc}")

    # ── Artikel-Metadaten laden (Bild + Datum) ────────────────────
    if all_items:
        try:
            all_items = await _fetch_article_meta_batch(all_items)
            _img_count  = sum(1 for it in all_items if it.get("image_url"))
            _date_count = sum(1 for it in all_items if it.get("published_at"))
            logger.info("Artikel-Metadaten: %d/%d Bilder, %d/%d Publikationsdaten.",
                        _img_count, len(all_items), _date_count, len(all_items))
        except Exception as _meta_exc:
            logger.warning("Artikel-Metadaten-Fetch fehlgeschlagen: %s", _meta_exc)

    # ── Speichern ─────────────────────────────────────────────
    for item in all_items:
        url       = str(item.get("url", "")).strip()
        headline  = str(item.get("headline", "")).strip()
        tag_name  = str(item.get("tag", "")).strip()
        if not url or not headline or not tag_name:
            continue
        try:
            weight = max(1, min(10, int(item.get("weight", 5))))
        except (TypeError, ValueError):
            weight = 5
        method       = item.get("_method", "unknown")
        image_url    = str(item.get("image_url") or item.get("image") or "")
        published_at = item.get("published_at") or None  # ISO-String oder None
        if _upsert_news(
            tag_name=tag_name, tag_weight=weight,
            headline=headline, snippet=str(item.get("snippet", "")),
            url=url, image_url=image_url, source_name=str(item.get("source", "")),
            search_method=method, found_date=target_date,
            published_at=published_at,
        ):
            total_saved += 1
    total_found = len(all_items)
    logger.info("News gesamt: %d gefunden, %d neu gespeichert.", total_found, total_saved)

    _debug_write(run_dir, "00_run_info.txt",
                 run_info
                 + f"Tags             : {len(tags)}\n"
                 + f"Ergebnisse gesamt: {total_found}\n"
                 + f"Neu gespeichert  : {total_saved}\n"
                 + f"Debug-Ordner     : {run_dir}\n")

    logger.info("News-Suche abgeschlossen: %d gefunden, %d neu.", total_found, total_saved)
    result: dict = {
        "success":       True,
        "date":          str(target_date),
        "results_found": total_found,
        "results_saved": total_saved,
        "debug_dir":     str(run_dir),
    }
    if errors:
        result["errors"] = errors
    return result


# ── News-Scheduler ───────────────────────────────────────────

def _reschedule_news_job() -> None:
    """Liest News-Schedule aus DB und konfiguriert APScheduler-Job neu."""
    if _scheduler is None or _pool is None:
        return
    try:
        _scheduler.remove_job("news_search_1")
    except Exception:
        pass
    sched = (_get_setting("news_schedule") or "").strip()
    if not sched:
        return
    try:
        hour, minute = map(int, sched.split(":"))
    except ValueError:
        logger.warning("Ungültige News-Schedule-Zeit '%s' ignoriert.", sched)
        return
    _scheduler.add_job(
        run_news_search,
        CronTrigger(hour=hour, minute=minute, timezone=_BERLIN),
        id="news_search_1",
        name="News-Suche",
        replace_existing=True,
    )
    logger.info("News-Suche geplant: %02d:%02d CEST", hour, minute)


# ── News-Einstellungs-Endpunkte ──────────────────────────────

@app.get(
    "/api/v1/settings/news",
    tags=["Settings"],
    dependencies=[Depends(verify_token)],
)
def get_news_settings_endpoint():
    """Liefert alle News-Sucheinstellungen."""
    try:
        return _get_news_settings()
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="Datenbankfehler.") from exc


@app.put(
    "/api/v1/settings/news",
    tags=["Settings"],
    dependencies=[Depends(verify_token)],
)
def update_news_settings_endpoint(payload: SettingsBulkPayload):
    """Aktualisiert News-Sucheinstellungen und rescheduled den News-Job."""
    try:
        updated = []
        for key, value in payload.settings.items():
            if key in _NEWS_SETTING_KEYS:
                _upsert_setting(key, str(value))
                updated.append(key)
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="Datenbankfehler.") from exc
    _reschedule_news_job()
    return {"status": "ok", "updated": updated}


# ── News-Ergebnis-Endpunkte ──────────────────────────────────

@app.get(
    "/api/v1/news",
    tags=["News"],
    dependencies=[Depends(verify_token)],
)
def list_news(
    date_str: Optional[str] = Query(default=None, description="Datum YYYY-MM-DD"),
    tag: Optional[str] = Query(default=None, description="Tag-Name filtern"),
):
    """Liefert News-Ergebnisse, optional nach Datum und/oder Tag gefiltert."""
    fd: Optional[date] = None
    if date_str:
        try:
            fd = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(status_code=422, detail="date_str muss Format YYYY-MM-DD haben.")
    try:
        return {"news": _get_news(found_date=fd, tag_name=tag)}
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="Datenbankfehler.") from exc


@app.post(
    "/api/v1/news/search",
    tags=["News"],
    dependencies=[Depends(verify_token)],
    status_code=202,
)
async def trigger_news_search(
    background_tasks: BackgroundTasks,
    date_str: Optional[str] = Query(default=None, description="Datum YYYY-MM-DD (leer = heute)"),
):
    """Startet die News-Suche im Hintergrund (kehrt sofort zurück)."""
    target_date: Optional[date] = None
    if date_str:
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(status_code=422, detail="date_str muss Format YYYY-MM-DD haben.")
    background_tasks.add_task(run_news_search, for_date=target_date)
    return {"status": "started", "date": str(target_date or date.today())}


@app.delete(
    "/api/v1/news/{news_id}",
    tags=["News"],
    dependencies=[Depends(verify_token)],
)
def delete_news(news_id: str):
    """Löscht einen News-Eintrag."""
    try:
        found = _delete_news_item(news_id)
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="Datenbankfehler.") from exc
    if not found:
        raise HTTPException(status_code=404, detail="News-Eintrag nicht gefunden.")
    return {"status": "deleted"}


# ── RSS-Hilfsfunktionen ──────────────────────────────────────

def _xml_escape(text: str) -> str:
    """Escapt XML/HTML-Sonderzeichen."""
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _rfc2822_dt(dt: datetime) -> str:
    """Formatiert datetime als RFC 2822 (für RSS pubDate)."""
    import email.utils
    return email.utils.format_datetime(dt)


def _rfc2822_date(date_str: str) -> str:
    """Konvertiert ein YYYY-MM-DD-String in RFC-2822-Format (Mittagszeit Berlin)."""
    if not date_str:
        return ""
    try:
        d = date.fromisoformat(str(date_str))
        dt = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=_BERLIN)
        return _rfc2822_dt(dt)
    except Exception:
        return ""


# ── Timeline-Endpunkte ────────────────────────────────────────

@app.get(
    "/api/v1/timeline",
    tags=["Timeline"],
    dependencies=[Depends(verify_token)],
)
def get_timeline(
    date_str: Optional[str] = Query(default=None, alias="date", description="Datum YYYY-MM-DD (Standard: heute)"),
):
    """Liefert alle Aktivitäts-Einträge für einen Tag."""
    if date_str:
        try:
            target = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(status_code=422, detail="date_str muss Format YYYY-MM-DD haben.")
    else:
        target = datetime.now(tz=_BERLIN).date()
    try:
        entries = _get_timeline_entries(target)
        return {"date": target.isoformat(), "entries": entries, "total": len(entries)}
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="Datenbankfehler.") from exc


@app.get(
    "/api/v1/timeline/dates",
    tags=["Timeline"],
    dependencies=[Depends(verify_token)],
)
def get_timeline_dates(months: int = Query(default=3, ge=1, le=24)):
    """Liefert alle Daten mit Aktivitäts-Einträgen der letzten N Monate."""
    try:
        return {"dates": _get_timeline_dates(months)}
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="Datenbankfehler.") from exc


@app.post(
    "/api/v1/timeline/backfill",
    tags=["Timeline"],
    dependencies=[Depends(verify_token)],
    status_code=202,
)
def timeline_backfill_endpoint(background_tasks: BackgroundTasks):
    """Befüllt activity_timeline mit allen historischen context_queue-Einträgen."""
    def _run():
        try:
            count = _timeline_backfill_sql()
            logger.info("Timeline Backfill: %d Einträge eingefügt.", count)
        except Exception as exc:
            logger.error("Timeline Backfill fehlgeschlagen: %s", exc)
    background_tasks.add_task(_run)
    return {"status": "backfill gestartet"}


@app.get(
    "/api/v1/settings/timeline",
    tags=["Timeline"],
    dependencies=[Depends(verify_token)],
)
def get_timeline_settings():
    """Liefert Timeline-Einstellungen."""
    val = _get_setting("timeline_enabled") or "true"
    return {"timeline_enabled": val}


@app.put(
    "/api/v1/settings/timeline",
    tags=["Timeline"],
    dependencies=[Depends(verify_token)],
)
def update_timeline_settings(payload: SettingsBulkPayload):
    """Aktualisiert Timeline-Einstellungen."""
    if "timeline_enabled" in payload.settings:
        _upsert_setting("timeline_enabled", str(payload.settings["timeline_enabled"]))
        _tl_cache["ts"] = 0.0  # Cache invalidieren
    return {"status": "ok"}


# ── RSS-Feed-Endpunkt ─────────────────────────────────────────

@app.get(
    "/rss",
    response_class=Response,
    tags=["Feed"],
    summary="RSS 2.0 Newsfeed mit Bildern",
    include_in_schema=True,
)
async def rss_feed(
    days: int = Query(default=3, ge=1, le=30, description="News der letzten N Tage"),
):
    """
    Öffentlicher personalisierter RSS 2.0 Feed (kein Token erforderlich).
    Artikel nach Relevanz (Ollama-Score) sortiert.
    Bilder via &lt;media:thumbnail&gt; falls vorhanden.
    """
    now_berlin = datetime.now(tz=_BERLIN)
    all_news: list = []
    for _offset in range(days):
        _day = (now_berlin - timedelta(days=_offset)).date()
        try:
            all_news.extend(_get_news(found_date=_day))
        except Exception:
            pass

    # Sortierung: tag_weight (= Ollama-Relevanz-Score) DESC, dann Datum DESC
    all_news.sort(
        key=lambda n: (n.get("tag_weight", 0), n.get("found_date") or ""),
        reverse=True,
    )

    now_rfc = _rfc2822_dt(now_berlin)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"',
        '     xmlns:media="http://search.yahoo.com/mrss/"',
        '     xmlns:atom="http://www.w3.org/2005/Atom">',
        '  <channel>',
        '    <title>MyFeed – Personalisierter Newsfeed</title>',
        '    <description>Auf Basis deiner Browser-Aktivitäten abgestimmte Nachrichten.</description>',
        '    <language>de</language>',
        f'    <lastBuildDate>{now_rfc}</lastBuildDate>',
        '    <ttl>60</ttl>',
    ]

    seen_rss: set = set()
    for n in all_news[:300]:
        _url = n.get("url", "")
        if not _url:
            continue
        # URL-decode vor Vergleich (encoded vs non-encoded URLs gleich behandeln)
        _url_norm = _url_unquote(_url).lower().rstrip("/")
        if _url_norm in seen_rss:
            continue
        seen_rss.add(_url_norm)
        _title  = _xml_escape(n.get("headline") or "")
        _link   = _xml_escape(_url)
        _img    = (n.get("image_url") or "").strip()
        _tag    = _xml_escape(n.get("tag_name") or "")
        _src    = _xml_escape(n.get("source_name") or "")
        _weight = int(n.get("tag_weight") or 0)

        # Echtes Publikationsdatum ermitteln (published_at > found_date)
        _pa = n.get("published_at")
        _real_dt: Optional[datetime] = None
        if _pa:
            try:
                _real_dt = datetime.fromisoformat(_pa)
            except Exception:
                pass
        if _real_dt is None:
            _fd = n.get("found_date") or ""
            if _fd:
                try:
                    _d = date.fromisoformat(_fd[:10])
                    _real_dt = datetime(_d.year, _d.month, _d.day, 12, 0, 0, tzinfo=_BERLIN)
                except Exception:
                    pass

        # Zukunfts-Pin: Top-Artikel (Gewicht > 8) auf heute 23:00 Uhr Berlin pinnen
        if _weight > 8:
            _today = datetime.now(tz=_BERLIN).date()
            _pin_dt = datetime(_today.year, _today.month, _today.day, 23, 0, 0, tzinfo=_BERLIN)
            _pub = _rfc2822_dt(_pin_dt)
        elif _real_dt is not None:
            _pub = _rfc2822_dt(_real_dt)
        else:
            _pub = ""

        # Beschreibung: Snippet + Original-Datum bei gepinnten Artikeln
        _snip = _xml_escape(n.get("snippet") or "")
        if _weight > 8 and _real_dt is not None:
            _orig_label = _real_dt.astimezone(_BERLIN).strftime("%-d. %-m. %Y, %H:%M Uhr")
            _orig_note  = _xml_escape(f"Originalartikel vom: {_orig_label}")
            _desc = f"{_snip} [{_orig_note}]" if _snip else f"[{_orig_note}]"
        else:
            _desc = _snip

        lines.append('    <item>')
        lines.append(f'      <title>{_title}</title>')
        lines.append(f'      <link>{_link}</link>')
        lines.append(f'      <guid isPermaLink="true">{_link}</guid>')
        if _desc:
            lines.append(f'      <description>{_desc}</description>')
        if _pub:
            lines.append(f'      <pubDate>{_pub}</pubDate>')
        if _tag:
            lines.append(f'      <category>{_tag}</category>')
        if _src:
            lines.append(f'      <author>{_src}</author>')
        if _img:
            lines.append(f'      <media:thumbnail url="{_xml_escape(_img)}"/>')
            lines.append(f'      <media:content url="{_xml_escape(_img)}" medium="image"/>')
        lines.append('    </item>')

    lines.append('  </channel>')
    lines.append('</rss>')

    return Response(
        content="\n".join(lines),
        media_type="application/rss+xml; charset=utf-8",
    )

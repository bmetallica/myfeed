"""
worker.py – MyFeed Embedding Worker
====================================
Liest unverarbeitete Einträge aus context_queue (processed=false),
berechnet Sentence-Embeddings via fastembed und schreibt sie zurück.

Modell: BAAI/bge-small-en-v1.5 → 384-dim (passt zu VECTOR(384) in der DB)
"""

import os
import time
import logging
import psycopg2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
logger = logging.getLogger("myfeed.worker")

DATABASE_URL   = os.environ["DATABASE_URL"]
BATCH_SIZE     = int(os.environ.get("WORKER_BATCH_SIZE", "32"))
POLL_INTERVAL  = int(os.environ.get("WORKER_POLL_SECS",  "30"))
MODEL_NAME     = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")


def build_text(title: str, url: str | None, content: str | None) -> str:
    parts = [title or ""]
    if url and not url.startswith("file://"):
        parts.append(url)
    if content:
        parts.append(content[:500])
    return " ".join(filter(None, parts))[:1000]


def process_batch(conn, model) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, url, content
            FROM context_queue
            WHERE processed = false
            ORDER BY created_at ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (BATCH_SIZE,),
        )
        rows = cur.fetchall()

    if not rows:
        return 0

    ids   = [r[0] for r in rows]
    texts = [build_text(r[1], r[2], r[3]) for r in rows]

    embeddings = list(model.embed(texts))

    with conn.cursor() as cur:
        for row_id, emb in zip(ids, embeddings):
            cur.execute(
                "UPDATE context_queue SET embedding = %s::vector, processed = true WHERE id = %s",
                (str(emb.tolist()), str(row_id)),
            )
    conn.commit()
    logger.info("%d Einträge vektorisiert.", len(rows))
    return len(rows)


def main() -> None:
    logger.info("Lade Embedding-Modell: %s", MODEL_NAME)
    # Import hier damit der Container ohne Modell-Download startbereit bleibt
    from fastembed import TextEmbedding  # noqa: PLC0415
    model = TextEmbedding(model_name=MODEL_NAME)
    logger.info("Modell geladen. Verbinde mit Datenbank …")

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    logger.info("Bereit. Starte Worker-Loop (Batch=%d, Intervall=%ds).", BATCH_SIZE, POLL_INTERVAL)

    while True:
        try:
            count = process_batch(conn, model)
            if count == 0:
                time.sleep(POLL_INTERVAL)
        except Exception as exc:
            logger.error("Fehler im Worker: %s", exc)
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
            time.sleep(10)
            conn = psycopg2.connect(DATABASE_URL)
            conn.autocommit = False


if __name__ == "__main__":
    main()

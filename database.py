"""SQLite storage via aiosqlite: model snapshots + event history."""
import json

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
    model_key        TEXT PRIMARY KEY,
    display_name     TEXT NOT NULL,
    organization     TEXT,
    rating           REAL,
    rank             INTEGER,
    rating_upper     REAL,
    rating_lower     REAL,
    votes            INTEGER,
    license          TEXT,
    context_length   INTEGER,
    input_price      REAL,
    output_price     REAL,
    model_url        TEXT,
    modalities       TEXT,
    is_anonymous     INTEGER DEFAULT 0,
    first_seen       TEXT,
    last_updated     TEXT,
    raw_data_json    TEXT
);
CREATE TABLE IF NOT EXISTS history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    model_key    TEXT,
    display_name TEXT,
    details      TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_ts ON history(ts);
CREATE TABLE IF NOT EXISTS subscribers (
    chat_id   INTEGER PRIMARY KEY,
    chat_type TEXT,
    title     TEXT,
    added_at  TEXT NOT NULL,
    active    INTEGER DEFAULT 1
);
"""

_COLS = (
    "model_key", "display_name", "organization", "rating", "rank",
    "rating_upper", "rating_lower", "votes", "license", "context_length",
    "input_price", "output_price", "model_url", "modalities", "is_anonymous",
    "first_seen", "last_updated", "raw_data_json",
)

_INSERT_SQL = f"""
INSERT INTO models ({', '.join(_COLS)})
VALUES ({', '.join('?' * len(_COLS))})
ON CONFLICT(model_key) DO UPDATE SET
    display_name   = excluded.display_name,
    organization   = excluded.organization,
    rating         = excluded.rating,
    rank           = excluded.rank,
    rating_upper   = excluded.rating_upper,
    rating_lower   = excluded.rating_lower,
    votes          = excluded.votes,
    license        = excluded.license,
    context_length = excluded.context_length,
    input_price    = excluded.input_price,
    output_price   = excluded.output_price,
    model_url      = excluded.model_url,
    modalities     = excluded.modalities,
    is_anonymous   = excluded.is_anonymous,
    last_updated   = excluded.last_updated,
    raw_data_json  = excluded.raw_data_json
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()
            self.conn = None

    @staticmethod
    def _decode_row(row: dict) -> dict:
        mods = row.get("modalities")
        if isinstance(mods, str):
            row["modalities"] = json.loads(mods)
        return row

    async def get_all_models(self) -> dict[str, dict]:
        async with self.conn.execute("SELECT * FROM models") as cur:
            rows = await cur.fetchall()
        return {r["model_key"]: self._decode_row(dict(r)) for r in rows}

    async def get_models_ordered(self) -> list[dict]:
        async with self.conn.execute(
            "SELECT * FROM models WHERE rank IS NOT NULL ORDER BY rank ASC"
        ) as cur:
            rows = await cur.fetchall()
        return [self._decode_row(dict(r)) for r in rows]

    async def search_models(self, query: str) -> list[dict]:
        like = f"%{query}%"
        async with self.conn.execute(
            "SELECT * FROM models "
            "WHERE display_name LIKE ? OR model_key LIKE ? OR organization LIKE ? "
            "ORDER BY rank ASC LIMIT 10",
            (like, like, like),
        ) as cur:
            rows = await cur.fetchall()
        return [self._decode_row(dict(r)) for r in rows]

    async def upsert_models(self, entries: list[dict], ts: str) -> None:
        rows = []
        for m in entries:
            rows.append((
                m["model_key"], m["display_name"], m["organization"],
                m["rating"], m["rank"], m["rating_upper"], m["rating_lower"],
                m["votes"], m["license"], m["context_length"],
                m["input_price"], m["output_price"], m["model_url"],
                json.dumps(m["modalities"], ensure_ascii=False),
                1 if m["is_anonymous"] else 0,
                ts, ts, json.dumps(m, ensure_ascii=False),
            ))
        await self.conn.executemany(_INSERT_SQL, rows)
        await self.conn.commit()

    async def record_history(self, ts: str, event_type: str, model_key: str,
                             display_name: str, details: dict) -> None:
        await self.conn.execute(
            "INSERT INTO history (ts, event_type, model_key, display_name, details) "
            "VALUES (?,?,?,?,?)",
            (ts, event_type, model_key, display_name, json.dumps(details, ensure_ascii=False)),
        )
        await self.conn.commit()

    # --- subscribers (broadcast targets) ---

    async def add_subscriber(self, chat_id: int, chat_type: str, title: str, ts: str) -> None:
        await self.conn.execute(
            "INSERT INTO subscribers (chat_id, chat_type, title, added_at, active) "
            "VALUES (?,?,?,?,1) "
            "ON CONFLICT(chat_id) DO UPDATE SET "
            "chat_type=excluded.chat_type, title=excluded.title, active=1",
            (chat_id, chat_type, title, ts),
        )
        await self.conn.commit()

    async def remove_subscriber(self, chat_id: int) -> None:
        await self.conn.execute("UPDATE subscribers SET active=0 WHERE chat_id=?", (chat_id,))
        await self.conn.commit()

    async def get_subscribers(self) -> list[int]:
        async with self.conn.execute("SELECT chat_id FROM subscribers WHERE active=1") as cur:
            rows = await cur.fetchall()
        return [r["chat_id"] for r in rows]

    async def count_subscribers(self) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM subscribers WHERE active=1"
        ) as cur:
            row = await cur.fetchone()
        return row["n"]

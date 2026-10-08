"""The API's own table of chats (the checkpointer holds the conversations themselves).

Besides when each chat started, it tracks when it was last active and whether it has ended, so
the runner can count open chats against the capacity limit. Timestamps are UTC ISO strings with
the same format everywhere, so they compare correctly as text.
"""

from pathlib import Path

import aiosqlite


class AppDB:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    @classmethod
    async def open(cls, path: Path) -> "AppDB":
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path)
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS chats (chat_id TEXT PRIMARY KEY, created_at TEXT NOT NULL)"
        )
        # Columns added after the first version: add them to older databases.
        async with conn.execute("PRAGMA table_info(chats)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if "last_active_at" not in columns:
            await conn.execute("ALTER TABLE chats ADD COLUMN last_active_at TEXT")
            await conn.execute("UPDATE chats SET last_active_at = created_at")
        if "ended" not in columns:
            await conn.execute("ALTER TABLE chats ADD COLUMN ended INTEGER NOT NULL DEFAULT 0")
        await conn.commit()
        return cls(conn)

    async def add(self, chat_id: str, created_at: str) -> None:
        await self._conn.execute(
            "INSERT INTO chats (chat_id, created_at, last_active_at) VALUES (?, ?, ?)",
            (chat_id, created_at, created_at),
        )
        await self._conn.commit()

    async def get(self, chat_id: str) -> str | None:
        async with self._conn.execute(
            "SELECT created_at FROM chats WHERE chat_id = ?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def all(self) -> list[tuple[str, str]]:
        async with self._conn.execute("SELECT chat_id, created_at FROM chats") as cur:
            return [(r[0], r[1]) for r in await cur.fetchall()]

    async def touch(self, chat_id: str, at: str, *, ended: bool | None = None) -> None:
        """Mark the chat active at `at` (and, if given, whether it has ended)."""
        if ended is None:
            sql, args = "UPDATE chats SET last_active_at = ? WHERE chat_id = ?", (at, chat_id)
        else:
            sql = "UPDATE chats SET last_active_at = ?, ended = ? WHERE chat_id = ?"
            args = (at, int(ended), chat_id)
        await self._conn.execute(sql, args)
        await self._conn.commit()

    async def count_open(self, active_since: str) -> int:
        """Chats that haven't ended and were active at or after `active_since`."""
        async with self._conn.execute(
            "SELECT COUNT(*) FROM chats WHERE ended = 0 AND last_active_at >= ?", (active_since,)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    async def is_open(self, chat_id: str, active_since: str) -> bool:
        async with self._conn.execute(
            "SELECT 1 FROM chats WHERE chat_id = ? AND ended = 0 AND last_active_at >= ?",
            (chat_id, active_since),
        ) as cur:
            return await cur.fetchone() is not None

    async def clear(self) -> None:
        await self._conn.execute("DELETE FROM chats")
        await self._conn.commit()

    async def ping(self) -> None:
        async with self._conn.execute("SELECT 1") as cur:
            await cur.fetchone()

    async def close(self) -> None:
        await self._conn.close()

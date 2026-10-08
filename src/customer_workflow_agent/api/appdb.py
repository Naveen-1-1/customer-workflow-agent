"""The API's own table of chats (the checkpointer holds the conversations themselves)."""

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
        await conn.commit()
        return cls(conn)

    async def add(self, chat_id: str, created_at: str) -> None:
        await self._conn.execute("INSERT INTO chats VALUES (?, ?)", (chat_id, created_at))
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

    async def clear(self) -> None:
        await self._conn.execute("DELETE FROM chats")
        await self._conn.commit()

    async def close(self) -> None:
        await self._conn.close()

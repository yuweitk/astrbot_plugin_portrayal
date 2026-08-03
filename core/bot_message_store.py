"""机器人自己消息的持久化存储。

为什么需要这个模块：
- QQ官方Bot 的 webhook 不会把机器人自己发的消息回推，因此本体
  platform_message_history 中永远不会有机器人自己的记录；
- conversations 表（本体会话历史）有截断机制，历史会被裁剪；
- livingmemory 是第三方插件，不应作为唯一依赖。

因此本模块利用 AstrBot 本体提供的 OnAfterMessageSentEvent 事件钩子，
在机器人每次发送消息后把内容写入插件自己的 sqlite 库，形成
"从安装之日起、永不丢失"的机器人发言档案。

它是机器人画像消息源的第一优先级（三重架构之一）：
  1. 事件钩子记录（本模块，最完整）
  2. conversations 表（本体会话历史，有截断）
  3. livingmemory 的 conversations.db（最早历史，第三方）
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

from astrbot.api import logger

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform_id TEXT NOT NULL DEFAULT '',
    group_id   TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bot_messages_group ON bot_messages(group_id, id);
"""


class BotMessageStore:
    """机器人自己消息的 sqlite 存储。线程安全，写入异步化避免阻塞事件循环。"""

    def __init__(self, data_dir: Path):
        self._db_path = data_dir / "bot_messages.db"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        try:
            with self._lock, self._connect() as conn:
                conn.executescript(_SCHEMA)
                conn.commit()
        except Exception as e:
            logger.error(f"[portrayal] bot_message_store 初始化失败: {e}")

    def _store_sync(self, platform_id: str, group_id: str, content: str) -> None:
        if not content or not content.strip():
            return
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO bot_messages (platform_id, group_id, content, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    (platform_id, group_id, content.strip(), time.time()),
                )
                conn.commit()
        except Exception as e:
            logger.debug(f"[portrayal] 存储机器人消息失败: {e}")

    async def store(self, platform_id: str, group_id: str, content: str) -> None:
        """异步存储一条机器人消息。"""
        if not group_id:
            return
        await asyncio.to_thread(self._store_sync, platform_id, group_id, content)

    def _get_texts_sync(self, group_id: str, max_count: int) -> list[str]:
        texts: list[str] = []
        try:
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    "SELECT content FROM bot_messages WHERE group_id = ?"
                    " ORDER BY id DESC LIMIT ?",
                    (group_id, max_count),
                ).fetchall()
            for row in rows:
                texts.append(str(row["content"]))
            texts.reverse()  # 恢复时间正序
        except Exception as e:
            logger.debug(f"[portrayal] 查询机器人消息失败: {e}")
        return texts

    async def get_texts(self, group_id: str, max_count: int) -> list[str]:
        """查询某群机器人自己的消息（时间正序，最多 max_count 条）。"""
        if not group_id or max_count <= 0:
            return []
        return await asyncio.to_thread(self._get_texts_sync, group_id, max_count)

    def _count_sync(self) -> int:
        try:
            with self._lock, self._connect() as conn:
                return int(conn.execute("SELECT COUNT(*) FROM bot_messages").fetchone()[0])
        except Exception:
            return 0

    async def count(self) -> int:
        """机器人消息总数。"""
        return await asyncio.to_thread(self._count_sync)

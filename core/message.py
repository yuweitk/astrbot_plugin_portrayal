"""群聊消息管理器 —— 支持 aiocqhttp (拉取历史) + QQ官方Bot (实时缓存)。

aiocqhttp 模式: 通过 get_group_msg_history API 拉取历史消息
QQ官方Bot 模式: 通过 @filter 拦截实时消息并本地缓存
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import time
from typing import Any

from astrbot.api import logger

from .config import PluginConfig
from .message_cache import CachedMessages, MessageCacheStorage


@dataclass
class MessageQueryResult:
    texts: list[str]
    scanned_messages: int
    from_cache: bool

    @property
    def count(self) -> int:
        return len(self.texts)

    @property
    def is_empty(self) -> bool:
        return not self.texts


class MessageManager:
    def __init__(self, config: PluginConfig):
        self.cfg = config.message
        self._storage = MessageCacheStorage(config.cache_dir)
        self._user_cache, self._group_cursor = self._storage.load()
        self._group_locks: dict[str, asyncio.Lock] = {}

    # =========================
    # cache helpers
    # =========================

    def _user_key(self, group_id: str, user_id: str) -> str:
        return f"{group_id}:{user_id}"

    def _get_user_cache(self, group_id: str, user_id: str) -> list[str] | None:
        key = self._user_key(group_id, user_id)
        cached = self._user_cache.get(key)
        if not cached:
            return None
        if time() - cached.timestamp > self.cfg.cache_ttl:
            self._group_cursor.pop(group_id, None)
            for k in tuple(self._user_cache):
                if k.startswith(f"{group_id}:"):
                    del self._user_cache[k]
            self.save_cache()
            return None
        return cached.texts

    def clear_cache(self):
        self._user_cache.clear()
        self._group_cursor.clear()
        self._storage.clear()

    def save_cache(self) -> None:
        self._storage.save(self._user_cache, self._group_cursor)

    # =========================
    # QQ官方Bot: 实时消息收集
    # =========================

    def collect_qqofficial_message(
        self, group_openid: str, user_id: str, text: str
    ) -> None:
        """QQ官方Bot: 将实时消息存入缓存。由消息拦截器调用。"""
        if not text or not text.strip():
            return
        group_id = str(group_openid)
        user_id = str(user_id)
        key = self._user_key(group_id, user_id)
        now = time()
        cached = self._user_cache.get(key)
        if not cached:
            self._user_cache[key] = CachedMessages(texts=[text], timestamp=now)
        else:
            cached.texts.append(text)
            cached.timestamp = now

    def get_user_texts_qqofficial(
        self, group_openid: str, target_id: str
    ) -> MessageQueryResult:
        """QQ官方Bot: 从实时缓存中读取用户消息。"""
        group_id = str(group_openid)
        target_id = str(target_id)
        cached = self._get_user_cache(group_id, target_id)
        if not cached:
            return MessageQueryResult(texts=[], scanned_messages=0, from_cache=True)
        texts = list(cached)
        return MessageQueryResult(
            texts=texts[: self.cfg.max_msg_count],
            scanned_messages=len(texts),
            from_cache=True,
        )

    # =========================
    # aiocqhttp: 历史消息拉取
    # =========================

    def _collect_messages(self, group_id: str, messages: list[dict[str, Any]]):
        now = time()
        for msg in messages:
            user_id = str(msg["sender"]["user_id"])
            text = "".join(
                seg["data"]["text"] for seg in msg["message"] if seg["type"] == "text"
            ).strip()
            if not text:
                continue
            key = self._user_key(group_id, user_id)
            cached = self._user_cache.get(key)
            if not cached:
                self._user_cache[key] = CachedMessages(texts=[text], timestamp=now)
            else:
                cached.texts.append(text)
                cached.timestamp = now

    async def get_user_texts(self, event, target_id: str, *, max_rounds: int) -> MessageQueryResult:
        """aiocqhttp: 拉取群聊历史消息。"""
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
            AiocqhttpMessageEvent,
        )

        if not isinstance(event, AiocqhttpMessageEvent):
            raise TypeError("get_user_texts 仅支持 AiocqhttpMessageEvent")

        group_id = str(event.get_group_id())
        target_id = str(target_id)

        cached = self._get_user_cache(group_id, target_id)
        if cached and len(cached) >= self.cfg.max_msg_count:
            return MessageQueryResult(
                texts=cached[: self.cfg.max_msg_count],
                scanned_messages=0,
                from_cache=True,
            )

        texts = cached[:] if cached else []
        rounds = 0
        cache_changed = False
        group_lock = self._group_locks.setdefault(group_id, asyncio.Lock())

        while rounds < max_rounds and len(texts) < self.cfg.max_msg_count:
            try:
                async with group_lock:
                    cached = self._get_user_cache(group_id, target_id)
                    if cached and len(cached) >= self.cfg.max_msg_count:
                        texts = cached[:]
                        break
                    message_seq = self._group_cursor.get(group_id, 0)
                    result = await event.bot.api.call_action(
                        "get_group_msg_history",
                        group_id=group_id,
                        message_seq=message_seq,
                        count=self.cfg.per_query_count,
                        reverseOrder=True,
                    )
                    messages = result.get("messages", [])
                    if messages:
                        message_seq = messages[0]["message_id"]
                        self._group_cursor[group_id] = message_seq
                        self._collect_messages(group_id, messages)
                        cache_changed = True
                    if not messages:
                        break
                    cached = self._get_user_cache(group_id, target_id)
                    if cached:
                        texts = cached[:]
            except Exception as e:
                logger.error(e)
                break
            rounds += 1

        if cache_changed:
            self.save_cache()

        return MessageQueryResult(
            texts=texts[: self.cfg.max_msg_count],
            scanned_messages=rounds * self.cfg.per_query_count,
            from_cache=cached is not None,
        )

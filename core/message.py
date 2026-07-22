"""群聊消息管理器 —— 使用 AstrBot 内置 message_history_manager 持久化存储。
对照 astrobot_plugin_qq_group_daily_analysis 的实现方式。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import time

from astrbot.api import logger


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
    """使用 AstrBot message_history_manager 存储和查询群消息。"""

    def __init__(self, context):
        self._context = context
        self._mgr = context.message_history_manager
        self._nickname_cache: dict[str, str] = {}
        self._group_locks: dict[str, asyncio.Lock] = {}

    # ---- 存储 ----

    async def store_message(
        self, platform_id: str, group_id: str, sender_id: str, sender_name: str, text: str
    ) -> None:
        """存储消息到 AstrBot 历史数据库（对照分析插件 message_processing_service.py:101）。"""
        if not text or not text.strip():
            return
        try:
            await self._mgr.insert(
                platform_id=platform_id,
                user_id=group_id,
                content={"type": "user", "message": text},
                sender_id=sender_id,
                sender_name=sender_name,
            )
        except Exception as e:
            logger.debug(f"[portrayal_qq] 存储消息失败: {e}")

    # ---- 昵称缓存 ----

    def cache_nickname(self, user_id: str, nickname: str) -> None:
        if nickname and nickname != user_id:
            self._nickname_cache[str(user_id)] = nickname

    def get_nickname(self, user_id: str) -> str | None:
        return self._nickname_cache.get(str(user_id))

    # ---- 查询 ----

    async def get_user_texts(
        self, platform_id: str, group_id: str, target_id: str, max_count: int,
    ) -> MessageQueryResult:
        """查询目标用户的消息（从 AstrBot 历史数据库）。"""
        target_id = str(target_id)
        texts: list[str] = []
        page = 1

        while len(texts) < max_count and page <= 20:
            try:
                records = await self._mgr.get(
                    platform_id=platform_id,
                    user_id=group_id,
                    page=page,
                    page_size=200,
                )
            except Exception as e:
                logger.warning(f"[portrayal_qq] 查询历史失败: {e}")
                break

            if not records:
                break

            for rec in records:
                sid = str(getattr(rec, "sender_id", "") or "")
                if sid == target_id:
                    content = getattr(rec, "content", None)
                    if isinstance(content, dict) and content.get("message"):
                        msg = content["message"]
                        if isinstance(msg, str):
                            texts.append(msg)
                        elif isinstance(msg, list):
                            texts.append(" ".join(str(x) for x in msg))
                    # 更新昵称缓存
                    sname = getattr(rec, "sender_name", None) or ""
                    if sname:
                        self.cache_nickname(target_id, str(sname))

                sn = getattr(rec, "sender_name", None) or ""
                if sn:
                    self.cache_nickname(str(getattr(rec, "sender_id", "") or ""), str(sn))

                if len(texts) >= max_count:
                    break

            page += 1

        texts = texts[:max_count]
        return MessageQueryResult(texts=texts, scanned_messages=len(texts), from_cache=False)

    # ---- 统计 ----

    async def estimate_user_count(self, platform_id: str, group_id: str) -> int:
        """估算群内活跃用户数（用户画像消息积累参考）。"""
        try:
            records = await self._mgr.get(
                platform_id=platform_id,
                user_id=group_id,
                page=1,
                page_size=200,
            )
            users = set()
            for rec in records:
                sid = str(getattr(rec, "sender_id", "") or "")
                if sid:
                    users.add(sid)
                sn = getattr(rec, "sender_name", None) or ""
                if sn and sid:
                    self.cache_nickname(sid, str(sn))
            return len(users)
        except Exception:
            return 0

    # ---- 清理 ----

    async def cleanup_old_messages(self, platform_id: str, group_id: str, days: int = 90) -> None:
        """清理过期消息。"""
        try:
            await self._mgr.delete(
                platform_id=platform_id,
                user_id=group_id,
                offset_sec=days * 86400,
            )
        except Exception as e:
            logger.debug(f"[portrayal_qq] 清理旧消息失败: {e}")

    # ---- 为兼容旧接口保留 ----

    def collect_qqofficial_message(self, group_openid: str, user_id: str, text: str, nickname: str = "") -> None:
        """兼容旧接口：不再是实时缓存，仅缓存昵称映射。"""
        if nickname:
            self.cache_nickname(user_id, nickname)

    def get_user_texts_qqofficial(self, group_openid: str, target_id: str):
        """兼容旧接口：空结果（应改用 get_user_texts）。"""
        return MessageQueryResult(texts=[], scanned_messages=0, from_cache=True)

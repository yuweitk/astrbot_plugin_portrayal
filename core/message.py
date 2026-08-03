"""群聊消息管理器 —— 使用 AstrBot 内置 message_history_manager 持久化存储。
对照 astrobot_plugin_qq_group_daily_analysis 的实现方式。

机器人自己的消息采用「三重数据源、依次回退」架构：
  源1（最完整）: 事件钩子记录 —— 通过 AstrBot 本体 OnAfterMessageSentEvent
                  在机器人每次发消息后写入插件自己的 bot_messages.db
  源2（有截断）: conversations 表 —— 本体会话历史中的 assistant 消息
  源3（最早）  : livingmemory 插件的 conversations.db —— 最早可回溯的历史
查询时源1不足则由源2补充，仍不足由源3补充，最后全局去重。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from time import time

from astrbot.api import logger

from .bot_message_store import BotMessageStore


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

    def __init__(self, context, data_dir: Path | None = None):
        self._context = context
        self._mgr = context.message_history_manager
        self._nickname_cache: dict[str, str] = {}
        self._group_locks: dict[str, asyncio.Lock] = {}
        # 机器人自己的消息存储（事件钩子写入）
        self.bot_store = BotMessageStore(data_dir) if data_dir else None

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

    async def get_bot_texts(
        self, platform_id: str, group_id: str, max_count: int,
    ) -> MessageQueryResult:
        """查询机器人自己发送的消息 —— 三重数据源，依次回退。

        优先级：
          1. 事件钩子记录（bot_messages.db，从安装起全量，最完整）
          2. conversations 表（本体会话历史 assistant 消息，可能被截断）
          3. livingmemory conversations.db（最早历史，第三方插件）
        各源不足时依次补充，最后全局去重。
        """
        all_texts: list[str] = []
        seen: set[str] = set()

        # ---- 源1: 事件钩子记录（优先） ----
        if self.bot_store is not None:
            try:
                hooked = await self.bot_store.get_texts(group_id, max_count)
                for t in hooked:
                    t = t.strip()
                    if t and t not in seen:
                        seen.add(t)
                        all_texts.append(t)
            except Exception as e:
                logger.debug(f"[portrayal_qq] 源1(事件钩子)读取失败: {e}")

        # ---- 源2: conversations 表 ----
        if len(all_texts) < max_count:
            try:
                conv_texts = await self._get_bot_texts_from_conversations(
                    platform_id, group_id, max_count
                )
                for t in conv_texts:
                    if t and t not in seen:
                        seen.add(t)
                        all_texts.append(t)
            except Exception as e:
                logger.debug(f"[portrayal_qq] 源2(conversations)读取失败: {e}")

        # ---- 源3: livingmemory ----
        if len(all_texts) < max_count:
            try:
                lm_texts = await self._get_bot_texts_from_livingmemory(
                    platform_id, group_id, max_count
                )
                for t in lm_texts:
                    if t and t not in seen:
                        seen.add(t)
                        all_texts.append(t)
            except Exception as e:
                logger.debug(f"[portrayal_qq] 源3(livingmemory)读取失败: {e}")

        all_texts = all_texts[:max_count]
        return MessageQueryResult(
            texts=all_texts, scanned_messages=len(all_texts), from_cache=True
        )

    # ---- 机器人消息：源2（conversations 表） ----

    async def _get_bot_texts_from_conversations(
        self, platform_id: str, group_id: str, max_count: int,
    ) -> list[str]:
        """从 AstrBot conversations 会话历史表提取机器人（assistant）发言。

        QQ官方Bot 的 webhook 不会把机器人自己发的消息推回，因此
        platform_message_history 中没有机器人自己的记录。机器人自己的
        发言存放在 AstrBot 的 conversations 会话历史表中（assistant 角色）。
        """
        texts: list[str] = []
        try:
            db = self._context.get_db()
            conversations = []
            # user_id 格式形如 "default:GroupMessage:<group_openid>"，尝试精确匹配
            for uid_candidate in (
                f"{platform_id}:GroupMessage:{group_id}",
                f"default:GroupMessage:{group_id}",
            ):
                try:
                    convs = await db.get_conversations(
                        user_id=uid_candidate, platform_id=platform_id
                    )
                except Exception:
                    convs = []
                if convs:
                    conversations.extend(convs)
            # 兜底：遍历全部会话，按群 ID 匹配
            if not conversations:
                all_convs = await db.get_all_conversations(page=1, page_size=500)
                for conv in all_convs:
                    uid = str(getattr(conv, "user_id", "") or "")
                    if f":GroupMessage:{group_id}" in uid:
                        conversations.append(conv)

            for conv in conversations:
                conv_content = getattr(conv, "content", None)
                if not conv_content:
                    continue
                if isinstance(conv_content, str):
                    try:
                        conv_content = json.loads(conv_content)
                    except Exception:
                        continue
                if not isinstance(conv_content, list):
                    continue
                for msg in conv_content:
                    if not isinstance(msg, dict):
                        continue
                    role = str(msg.get("role", "") or "")
                    if role != "assistant":
                        continue
                    c = msg.get("content", "")
                    if isinstance(c, list):
                        parts = []
                        for item in c:
                            if isinstance(item, dict):
                                t = str(item.get("text", "") or "")
                                if t:
                                    parts.append(t)
                        c = " ".join(parts)
                    c = str(c).strip()
                    if not c or len(c) < 2:
                        continue
                    # 过滤系统注入/内部工具痕迹
                    if c.startswith("[SYSTEM") or c.startswith("["):
                        continue
                    texts.append(c)
                    if len(texts) >= max_count:
                        break
                if len(texts) >= max_count:
                    break
        except Exception as e:
            logger.warning(f"[portrayal_qq] 查询机器人会话历史失败: {e}")

        return texts[:max_count]

    # ---- 机器人消息：源3（livingmemory） ----

    async def _get_bot_texts_from_livingmemory(
        self, platform_id: str, group_id: str, max_count: int,
    ) -> list[str]:
        """从 livingmemory 插件的 conversations.db 提取机器人（assistant）发言。

        这是最早的机器人发言来源（livingmemory 安装后即开始记录）。
        直接读取第三方插件的 sqlite，不依赖其 API。
        """
        texts: list[str] = []
        db_path = self._livingmemory_db_path()
        if db_path is None or not db_path.exists():
            return texts

        try:
            def _query() -> list[str]:
                result: list[str] = []
                conn = sqlite3.connect(str(db_path), timeout=5)
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute(
                        "SELECT content FROM messages"
                        " WHERE session_id LIKE ? AND role = 'assistant'"
                        " AND content IS NOT NULL"
                        " ORDER BY id DESC LIMIT ?",
                        (f"%:GroupMessage:{group_id}", max_count),
                    ).fetchall()
                    for row in rows:
                        c = str(row["content"]).strip()
                        if c and len(c) >= 2:
                            result.append(c)
                    result.reverse()  # 时间正序
                finally:
                    conn.close()
                return result

            texts = await asyncio.to_thread(_query)
        except Exception as e:
            logger.debug(f"[portrayal_qq] 查询 livingmemory 失败: {e}")

        return texts[:max_count]

    def _livingmemory_db_path(self) -> Path | None:
        """定位 livingmemory 的 conversations.db（动态获取插件数据目录）。"""
        try:
            from astrbot.core.star.star_tools import StarTools

            lm_dir = StarTools.get_data_dir("astrbot_plugin_livingmemory")
            return Path(lm_dir) / "conversations.db"
        except Exception as e:
            logger.debug(f"[portrayal_qq] 定位 livingmemory 目录失败: {e}")
            # 兜底：常见路径
            fallback = (
                Path("/root/AstrBot/data/plugin_data/astrbot_plugin_livingmemory")
                / "conversations.db"
            )
            return fallback if fallback.exists() else None

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

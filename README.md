# astrbot_plugin_portrayal (QQ官方Bot适配版)

_基于 [Zhalslar/astrbot_plugin_portrayal](https://github.com/Zhalslar/astrbot_plugin_portrayal) (v1.1.5) 的 QQ 官方机器人适配版本_

根据群友聊天记录，调用 LLM 分析群友性格画像。**原版仅支持 aiocqhttp（NapCat/LLOneBot），此版本新增 QQ 官方机器人 (qq_official / qq_official_webhook) 支持。**

## 核心差异：QQ官方Bot vs aiocqhttp

| 功能 | aiocqhttp（原版） | QQ官方Bot（新增） |
|------|-------------------|-------------------|
| 消息获取 | `get_group_msg_history` API 拉取历史 | **实时拦截 + 本地缓存** |
| 历史回溯 | ✅ 可查安装前的消息 | ❌ 只能查安装后的消息 |
| 消息缓存 | 按页拉取并缓存 | 每条消息实时存入缓存 |
| 机器人自己的消息 | ✅ API 直接返回 | **三重数据源回退**（见下） |
| 修改Bot昵称/头像 | ✅ `set_qq_profile` / `set_qq_avatar` | ❌ 不支持 |
| 切换人格 | 对话人格 + Bot昵称/头像同步 | **仅对话人格**（QQ官方API限制） |

### 为什么 QQ 官方 Bot 不能拉取历史消息？

QQ 官方机器人 API 不提供 `get_group_msg_history` 接口——这是 aiocqhttp（OneBot 协议）的专属能力。因此本插件改为**实时拦截每条群消息并存入本地缓存**，用户发起画像命令时从缓存读取。

**这意味着**：安装插件后需要群聊**积累一段时间**才有足够的消息用于画像分析。消息越多，画像越准确。

## 机器人自己的消息：三重数据源架构（本次新增）

QQ官方Bot 的 webhook **不会把机器人自己发的消息回推**，因此 `platform_message_history` 里永远没有机器人自己的记录。为让「画像 @机器人自己」有足够的素材，本插件采用**三重数据源、依次回退**架构：

```
┌─────────────────────────────────────────────────────────┐
│  源1: 事件钩子记录  ★最完整★                            │
│    OnAfterMessageSentEvent 钩子 → bot_messages.db       │
│    （从安装起，机器人每句话都存档，永不丢失）            │
├─────────────────────────────────────────────────────────┤
│  源2: conversations 表（本体会话历史 assistant 消息）   │
│    （有截断机制，历史可能被裁剪）                        │
├─────────────────────────────────────────────────────────┤
│  源3: livingmemory 插件的 conversations.db              │
│    （最早历史，livingmemory 安装后即开始记录）          │
└─────────────────────────────────────────────────────────┘
         │
         ▼ 查询时按 源1 → 源2 → 源3 依次取数
         ▼ 前一个源不足时，由后一个源补充
         ▼ 合并后全局去重（同一句话只保留一份）
```

### 回退规则

1. 先查 **源1（事件钩子记录）**：`data/plugin_data/astrbot_plugin_portrayal/bot_messages.db`，这是从插件安装起机器人说过的每句话，**不受 conversations 截断影响**，优先级最高。
2. 条数不足时，从 **源2（conversations 表）** 补充：查询本体会话历史中 role=assistant 的消息。
3. 仍不足时，从 **源3（livingmemory）** 补充：直接读取 `data/plugin_data/astrbot_plugin_livingmemory/conversations.db` 中的 assistant 消息（动态定位，不依赖硬编码路径）。
4. 三个来源合并后**按文本全局去重**，保证喂给 LLM 的素材没有重复。

### 升级提示

- 旧版本（仅 conversations 表）升级后自动生效，无需迁移数据。
- 安装本插件后，机器人每发一条消息都会通过事件钩子写入 `bot_messages.db`，越早安装积累越多。
- 若未安装 livingmemory 插件，源3 自动跳过，不影响前两个源。

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/yuweitk/astrbot_plugin_portrayal

# 安装依赖
pip install -r astrbot_plugin_portrayal/requirements.txt

# 重启 AstrBot
```

## 配置

AstrBot WebUI → 插件管理 → astrbot_plugin_portrayal → 配置

关键配置项：
- `llm.provider_id`: 画像分析使用的 LLM 提供商
- `message.max_msg_count`: 最多使用的消息条数
- `message.default_query_rounds`: 默认查询轮数
- `message.cache_ttl_min`: 缓存有效期（分钟）
- `inject_prompt`: 是否在每次对话中注入用户画像

## 指令表

### 提示词命令（可在配置中自定义增删）

| 指令 | 说明 | 支持平台 |
|:---:|:---|:---:|
| `画像 @群友 <轮数>` | 综合性格画像 | aiocqhttp + QQ官方 |
| `正画像 @群友 <轮数>` | 偏优点向的画像 | aiocqhttp + QQ官方 |
| `负画像 @群友 <轮数>` | 偏缺点向的画像 | aiocqhttp + QQ官方 |
| `克隆人格 @群友 <轮数>` | 生成克隆人格 prompt | aiocqhttp + QQ官方 |

### 内置命令

| 指令 | 权限 | 说明 | 支持平台 |
|:---:|:---:|:---|:---:|
| `查看画像 @群友` | 所有人 | 查看已有的画像 | aiocqhttp + QQ官方 |
| `切换人格 @群友` | Admin | 切换到群友的克隆人格 | 全平台 |
| `恢复人格` | Admin | 还原默认人格 | 全平台 |

### 平台差异说明

- **切换人格**：aiocqhttp 同步修改 Bot QQ 昵称和头像；QQ官方仅切换对话 personality，不影响 Bot 资料。
- **画像分析速度**：aiocqhttp 可即时拉取历史消息分析；QQ官方依赖消息积累，速度取决于群聊活跃度和插件运行时长。
- **画像 @机器人自己**：QQ官方下从三重数据源提取机器人自己的发言（见上文架构）。

## 技术架构

```
┌─────────────────────────────────────────────────────┐
│  消息流                                              │
│                                                     │
│  aiocqhttp:                                         │
│    get_group_msg_history ──► cache                  │
│                                                     │
│  QQ官方Bot 群成员消息:                               │
│    @filter拦截实时消息 ──► AstrBot历史库(conversations)│
│                                                     │
│  QQ官方Bot 机器人自己的消息:                         │
│    @filter.after_message_sent 钩子                  │
│        ──► bot_messages.db（源1）                   │
│    回退: conversations 表（源2）                    │
│    回退: livingmemory conversations.db（源3）       │
│                                                     │
│  画像命令 ──► 三重数据源合并去重 ──► LLM            │
└─────────────────────────────────────────────────────┘
```

## 最近更新要点

- **v1.1.5+ (QQ官方Bot 机器人自画像)**：新增机器人自己消息的**三重数据源架构**——事件钩子记录（`bot_messages.db`，最完整）+ conversations 表（本体会话历史）+ livingmemory（最早历史），依次回退、全局去重，解决了「QQ官方Bot 不回推自己的消息导致机器人自画像无素材」的问题。
- **事件钩子**：新增 `after_message_sent` 处理器，机器人每次发消息后自动存档到插件自己的 sqlite，不依赖任何第三方插件即保证消息不丢失。
- **livingmemory 集成**：动态定位 livingmemory 的 `conversations.db` 并直接读取 assistant 消息作为最早历史来源，插件未安装时自动跳过。

## 致谢

- 原作 [Zhalslar/astrbot_plugin_portrayal](https://github.com/Zhalslar/astrbot_plugin_portrayal)
- QQ 官方 Bot 适配参考 [SXP-Simon/astrbot_plugin_qq_group_daily_analysis](https://github.com/SXP-Simon/astrbot_plugin_qq_group_daily_analysis) 的消息拦截模式

## License

MIT（同原作）

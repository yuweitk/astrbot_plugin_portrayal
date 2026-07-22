# astrbot_plugin_portrayal (QQ官方Bot适配版)

_基于 [Zhalslar/astrbot_plugin_portrayal](https://github.com/Zhalslar/astrbot_plugin_portrayal) (v1.1.5) 的 QQ 官方机器人适配版本_

根据群友聊天记录，调用 LLM 分析群友性格画像。**原版仅支持 aiocqhttp（NapCat/LLOneBot），此版本新增 QQ 官方机器人 (qq_official / qq_official_webhook) 支持。**

## 核心差异：QQ官方Bot vs aiocqhttp

| 功能 | aiocqhttp（原版） | QQ官方Bot（新增） |
|------|-------------------|-------------------|
| 消息获取 | `get_group_msg_history` API 拉取历史 | **实时拦截 + 本地缓存** |
| 历史回溯 | ✅ 可查安装前的消息 | ❌ 只能查安装后的消息 |
| 消息缓存 | 按页拉取并缓存 | 每条消息实时存入缓存 |
| 修改Bot昵称/头像 | ✅ `set_qq_profile` / `set_qq_avatar` | ❌ 不支持 |
| 切换人格 | 对话人格 + Bot昵称/头像同步 | **仅对话人格**（QQ官方API限制） |

### 为什么 QQ 官方 Bot 不能拉取历史消息？

QQ 官方机器人 API 不提供 `get_group_msg_history` 接口——这是 aiocqhttp（OneBot 协议）的专属能力。因此本插件改为**实时拦截每条群消息并存入本地缓存**，用户发起画像命令时从缓存读取。

**这意味着**：安装插件后需要群聊**积累一段时间**才有足够的消息用于画像分析。消息越多，画像越准确。

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

## 技术架构

```
┌─────────────────────────────────────┐
│  消息流                              │
│                                     │
│  aiocqhttp:                         │
│    get_group_msg_history ──► cache  │
│                                     │
│  QQ官方Bot:                         │
│    @filter拦截实时消息 ──► cache    │
│                                     │
│  画像命令 ──► 从cache读取 ──► LLM   │
└─────────────────────────────────────┘
```

## 致谢

- 原作 [Zhalslar/astrbot_plugin_portrayal](https://github.com/Zhalslar/astrbot_plugin_portrayal)
- QQ 官方 Bot 适配参考 [SXP-Simon/astrbot_plugin_qq_group_daily_analysis](https://github.com/SXP-Simon/astrbot_plugin_qq_group_daily_analysis) 的消息拦截模式

## License

MIT（同原作）

# QQBot —— 一个会刷 B 站、记得住人的 QQ 机器人

> 用 AstrBot（LLM 框架）+ SnowLuma（QQ 接入）搭的私人机器人。
> 她有自己的脾气，会自己刷 B 站挑视频、在评论区 @ 你、在群里记得住谁是谁，还会在你冷落她的时候追问一句。

**本仓库只有"我们自己写的部分"**：AstrBot 插件、运维脚本、知识库数据、人格模板。
**不包含** AstrBot、SnowLuma、任何表情包图片、任何聊天记录或账号数据。

---

## 这个项目特别在哪

绝大多数"AI 接入 QQ"的项目，本质是 `消息 → LLM → 回复`。这个项目多做了六件事：

| # | 设计 | 为什么 |
|---|---|---|
| 1 | **判据门**：每条候选视频先问"值不值得占用主人一次注意力" | 不是为了发而发，宁可不发 |
| 2 | **三层内容**：字幕原文 → 官方总结 → **抽帧看画面** | 摘要二手、字幕一手、画面兜底 |
| 3 | **变化优先选帧**：逐帧灰度差分后挑 6 帧，而不是等间隔硬切 | 均匀采样会漏掉快动作 |
| 4 | **音频侦察**：先判"有没有声音"，再决定能不能提台词 | ASR 在纯 BGM 上会编台词；我们干脆不做 ASR |
| 5 | **记忆分层**：短期池 6 小时 → 有价值的升级到长期 | 看过全记，但不让垃圾占满长期记忆 |
| 6 | **指令蒸馏**：你说"以后别这样"，它抽成规则持续生效 | 这不是微调模型，是让规则可累积、可删除 |

还有几个小的：表情包**五维索引**（主情绪/标签/强度/场景/安全等级）、群聊**人物卡**（摘要时自动写特征）、**弹幕梗**自动入库、**守护进程**（端口探活 + 自动拉起 + QQ 通知）。

---

## 架构

```
                    ┌──────────────────────────────┐
     QQ 客户端 ────▶│ QQ NT + SnowLuma (hook 注入) │  单独安装，本仓库不含
                    └──────────────┬───────────────┘
                                   │ OneBot v11 反向 WS (127.0.0.1:6199)
                    ┌──────────────▼───────────────┐
                    │        AstrBot (Python)      │  单独安装，本仓库不含
                    │  ┌────────────────────────┐  │
                    │  │ plugins/  ← 本仓库内容  │  │
                    │  └────────────────────────┘  │
                    └──────────────┬───────────────┘
                                   │ HTTPS
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
        B 站 Web API         DeepSeek API         本地文件
     视频/字幕/弹幕/评论      判断/生成/看图      记忆·存档·词库
```

**数据流（一次刷 B 站）**

```
拉推荐流 → 规则层拦截(搬运/重复/低质) → 逐条取详情
   → 字幕 or 官方总结 or 抽帧看画面
   → 音频侦察
   → 判据门（YES/档位/理由）
   → 拉弹幕 → 学梗入库
   → 生成评论（两行：钩子 + 召叫你来看）→ 发布 → 复核可见
   → 写长期记忆 / 短期池 / 日报
```

---

## 插件一览

| 插件 | 作用 |
|---|---|
| `bili_pusher` | 定时刷 B 站、读内容、判别、评论、日报、弹幕学梗、记忆分层、QQ 指令控制 |
| `bili_tools` | 8 个查询工具：搜索 / 视频详情 / 热门 / 排行 / UP / 推荐流 / 历史 / 我的 |
| `sticker_sender` | 表情包**五维索引**选图；对话里发图先暂存，你说"保存"才入库（自动打标） |
| `group_memory` | 群聊人物卡 + 滚动摘要 + "她上一句"，压缩摘要时顺带写人物特征 |
| `chat_archive` | 按会话+日期存 JSONL，保留 30 天，提供 `search_chat` 检索 |
| `anime_kb` | 二次元词库 + 作品库，**命中才注入**，不整库塞上下文 |
| `browse_log` | 她刷过什么：`/刷了什么` 文字版，`/刷记录` 生成带封面的网页 |
| `proactive_dm` | 黏人模式：45–90 分钟、早晚仪式、没回会追问一次 |
| `feedback_learner` | 从主人的话里抽规则（去重/加权/45 天过期） |
| `bili_login_watch` | 每天自检 B 站登录态，失效私聊提醒 |
| `guard_report` | 被守护脚本拉起后私聊告知；`/守护状态` |

**工具脚本**（`tools/`）：表情导入与打标、词库构建、索引修复、日志回填、清理、守护进程、cookie 登录面板、脱敏检查、路径迁移。

---

## 快速开始

### 0. 前置

| 需要 | 说明 |
|---|---|
| Windows 10/11 | 项目按 `C:\QQBot` 路径写死（可用 `tools/relocate.py` 迁移） |
| Python 3.10+ | 建一个 venv 装 AstrBot |
| AstrBot ≥ 4.15 | **自行安装**，本项目是它的插件集合 |
| SnowLuma | **自行获取**。注意其许可是"源码可见、非商业"，**不可再分发** |
| 一个 QQ 小号 | 强烈建议小号，SnowLuma 是 hook 方案，违反 QQ 用户协议 |
| DeepSeek API Key | 或任何 OpenAI 兼容端点（需支持 image 输入才能看图） |

### 1. 装 AstrBot 与 SnowLuma

按它们各自的文档装到 `C:\QQBot`，确保 AstrBot 面板（6185）能打开、SnowLuma（5099）能注入 QQ。

### 2. 放插件

把本仓库 `plugins/*` 复制到 `C:\QQBot\astrbot\data\plugins\`，重启 AstrBot（面板 → 重启，或 `_astrbot.bat`）。

### 3. 填配置

```bash
copy examples\bili_push.example.json        C:\QQBot\bili_push.json
copy examples\bili_account.example.json     C:\QQBot\bili_account.json
copy examples\proactive_dm.example.json     C:\QQBot\proactive_dm.json
```

把 `YOUR_QQ` / `YOUR_BILI_UID` / `PASTE_SESSDATA_HERE` 等换成你自己的值。

**B 站 cookie 怎么拿**：浏览器登录小号 → F12 → Application → Cookies → `bilibili.com` → 复制 `SESSDATA`、`bili_jct`、`DedeUserID`。
三个缺一不可：`SESSDATA` 管登录，`bili_jct` 管评论（缺它报 `-111`）。

也可以用自带面板：

```bash
python tools\bili_login_ui.py   # http://127.0.0.1:8777 粘贴整段 cookie，自动解析并验证
```

### 4. 装人格

```bash
python tools\set_persona_whale.py
```

`examples/persona-whale-girl.txt` 是示例人格（傲娇鲸鱼娘），可以改成你自己的。

### 5. 跑起来

```bash
start.bat          # 启动 SnowLuma + AstrBot
```

在 QQ 里 @ 她，或私聊发 `/刷状态` 看配置。

### 6. 守护（可选但建议）

```powershell
$action  = New-ScheduledTaskAction -Execute "C:\QQBot\venv\Scripts\pythonw.exe" `
           -Argument '"C:\QQBot\tools\watchdog.py"' -WorkingDirectory "C:\QQBot"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
           -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "QQBotWatchdog" -Action $action -Trigger $trigger -Settings $settings -Force
```

守护逻辑：每 30 秒 TCP 探活 6185 → 连续 2 次失败 → 拉起 → 等 OneBot 连上 → 私聊告知。实测 12–13 秒恢复。

---

## 指令

### B 站

| 指令 | 作用 |
|---|---|
| `/刷状态` | 开关 / 间隔 / 时段 / 模式 / 判据 / 看图 / 登录态 / 今日统计 |
| `/刷` · `/刷看` | 立刻刷一轮（真评论）· 只挑不发，结果私聊预览 |
| `/刷开` · `/刷停` | 自动刷开关 |
| `/刷间隔 90` · `/刷时段 12-14,16-22` | 调频率与时段 |
| `/刷判据 宽/严` · `/刷模式 评论/私聊/预览` · `/刷看图 开/关` | 调行为 |
| `/刷了什么 [天]` · `/刷记录 [天]` | 看她刷过什么 / 生成带封面网页 |
| `/bilicheck` | 立刻检查 B 站登录态 |

### 群与私聊

| 指令 | 作用 |
|---|---|
| `/规则` · `/学 <规则>` · `/删规则 <id>` · `/确认 <id>` | 规则库管理 |
| `/黏人 开/关` | 主动搭话开关（不带参数看状态） |
| `/守护状态` | 守护进程是否活着 |

---

## 关键配置

`bili_push.json`

| 键 | 默认 | 说明 |
|---|---|---|
| `active_hours` | `[[12,14],[16,22]]` | 只在此时段刷 |
| `interval_minutes` / `jitter_minutes` | 120 / 20 | 间隔±抖动 |
| `strictness` | `loose` | 宽松＝情绪价值也算；严格＝拿不准不发 |
| `vision_mode` | `always` | `always` / `fallback` / `off` |
| `skip_repost` / `dedupe_ratio` | true / 0.82 | 搬运与重复拦截 |
| `content_source` | `subtitle_first` | 字幕优先，官方总结兜底 |

改完**不用重启**：`bili_pusher` 每轮重读配置。

---

## 数据与隐私

| 文件 | 内容 |
|---|---|
| `chat_archive/` | 聊天原文（你和她），保留 30 天 |
| `group_memory.json` | 群成员昵称/uid/备注、摘要 |
| `bili_memory.json` / `bili_recent.json` | 长期 / 短期视频记忆 |
| `bili_watched.jsonl` | 每条看过的视频（含被拒的）及理由 |
| `learned_rules.json` | 从你指令里学到的规则 |
| `logs/watchdog.log` | 守护日志 |

这些全部在 `.gitignore` 里。**提交前请跑 `python tools/check_secrets.py`**，它会扫 SESSDATA / token / QQ 号 / UID / 密码 / 本机用户名路径。

---

## 已知限制（别指望它是全能）

- **判据是模型判断**，不是硬规则；它会把 76% 的视频判成"不值得"，也会漏
- **关键词只能抓"自己标了搬运"的**，改标题的搬运抓不到
- **音频只判"有没有声音"**，分不清人声和配乐；**没有 ASR**，所以永远不知道具体说了什么
- **抽帧看不懂快动作**，间隔 4 帧以上只能分辨 0.1–0.5 秒级动作
- **表情包选图靠标签**，标签错就会用错图
- **记忆会被摘要覆盖**，细节靠 `search_chat` 兜底
- **B 站 cookie 会过期**（几小时到几个月），`bili_login_watch` 只能提醒，不能续期

---

## 许可与致谢

- 本仓库代码：**MIT**（见 `LICENSE`）
- 判据与评论逻辑参考了 [`c1overzz/dsh-bili-scroll`](https://github.com/c1overzz/dsh-bili-scroll)（MIT）的 `prompts/duty.md`
- 变化选帧与音频侦察思路参考了 [`xinhailaopo/video-watch-plus`](https://github.com/xinhailaopo/video-watch-plus)（MIT，上游 [CFITCorporation/video-watch-skill](https://github.com/CFITCorporation/video-watch-skill)）
- **AstrBot** 与 **SnowLuma** 不在本仓库内，遵循各自许可（SnowLuma 为源码可见、非商业）
- 表情包图片**不入库**：meme-pack 是 CC0，但其中角色图来自第三方二创，请自行获取

---

## ⚠️ 风险提示

1. **SnowLuma 是第三方 hook 方案，违反 QQ 用户协议**，账号可能被限制或封禁。**只用小号**。
2. **B 站 评论是用你的账号发的**，内容由模型生成；跑之前想清楚你能不能承担它在评论区说的话。
3. 本项目会**读你的 B 站 cookie**（存在本机 JSON），不要把那个文件传到任何地方。
4. 群里自动回复可能引发真实社交冲突，`active_reply` 概率别开太高。

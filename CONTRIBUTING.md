# 参与贡献

欢迎 PR，但请先看三条：

## 1. 一件事一个 PR

不要一个 PR 塞五个功能。这样我能独立合并，出问题也好回滚。

## 2. 提交前必须跑

```bash
python tools\check_secrets.py
```

有命中就别提交。特别是：

- **不要提交** `bili_account.json`、`token.txt`、`chat_archive/`、`learned_rules.json`
- **不要提交** 任何截图里带 QQ 号 / B 站 UID / 本机用户名
- **不要提交** 表情包图片（第三方版权）

## 3. Windows 上的三个硬约束

| 约束 | 原因 |
|---|---|
| `.bat` 必须**纯 ASCII + CRLF** | cmd 解析 UTF-8/LF 会报「不是内部或外部命令」 |
| Python 文件存 **UTF-8** | 中文注释/字符串会乱码 |
| 字符串里的中文引号用「」或**转义** | `"别提"规则"两个字"` 会直接 SyntaxError |

---

## 代码风格

- 单文件插件不要超过 ~600 行（`bili_pusher` 是历史遗留，1888 行，拆它欢迎）
- 每条 LLM 调用都要有 `try/except` + 日志，**不要静默失败**
- 需要结构化输出时，**用逐行格式而不是 JSON**：模型推理容易吃满 `max_tokens` 把 JSON 截断，逐行即使截断也能解析前面几条
- 新增配置项一定要有默认值，并且写进 README 的配置表

## 测试

没有正式测试套件（这是缺点）。最低要求：

- 改 LLM 提示词：跑 3–5 个真实样本，把输出贴进 PR 描述
- 改选帧/音频：贴日志里的量化指标（帧数、峰值差、dB）
- 改记忆：贴注入出来的 system_prompt 片段

## 想改哪一块

欢迎的方向：

- 把 `bili_pusher` 拆成 `judge` / `comment` / `memory` / `commands` 四个插件
- 路径改成 `QQBOT_HOME` 环境变量（现在硬编码 79 处）
- 加真正的单元测试（尤其相似度、去重、窗口计算这些纯函数）
- 接 ASR（`faster-whisper`，注意 `av<19`），替掉现在只能判"有没有声音"的音频侦察
- 让 `anime_kb` 的命中从子串匹配换成更严格的边界匹配

## 提交信息

```
feat(bili_pusher): 评论改成两句结构
fix(sticker): 索引里扩展名不一致导致选图失败
docs: 补守护进程的 AtLogOn 说明
```

前缀用 `feat / fix / docs / refactor / chore` 就行。

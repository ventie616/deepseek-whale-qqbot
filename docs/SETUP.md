# 安装与排障

假设装在 `C:\QQBot`。换路径见文末。

---

## 1. 装 AstrBot

```powershell
cd C:\QQBot
py -3.13 -m venv venv
.\venv\Scripts\python.exe -m pip install -U pip -i https://mirrors.aliyun.com/pypi/simple/
.\venv\Scripts\python.exe -m pip install astrbot -i https://mirrors.aliyun.com/pypi/simple/
```

启动：`.\venv\Scripts\astrbot.exe run --port 6185`，面板 `http://127.0.0.1:6185`。

**AstrBot 的平台配置**（面板 → 配置 → 平台）：

| 项 | 值 |
|---|---|
| 类型 | `aiocqhttp` |
| 端口 | `6199` |
| token | 与 SnowLuma 的 `wsClients.accessToken` 一致 |
| 绑定 | `127.0.0.1` |

---

## 2. 装 SnowLuma

从发布页下载 `win-x64.zip`，解压到 `C:\QQBot\snowluma`。

**注意**：SnowLuma 许可是源码可见、**非商业**，不要打包进你的发行版。

### 手动注入模式（强烈建议）

`config/runtime.json` 里 `hookAutoLoad` 设 `false`，`_snowluma.bat` 里 `SNOWLUMA_HOOK_AUTOLOAD=0`，两处必须一致。

> 自动注入会接管**任何已登录的 QQ**——包括你的主号。实际踩过：自动注入把主号（241 好友 / 89 群）接进了机器人。
> 宁可每次手动点一次「注入」，也不要让机器人拿到主号。

### 每次重启的固定动作

1. 先确认 QQ 登录的是**小号**
2. 启动 SnowLuma → 打开 `http://127.0.0.1:5099`
3. 在小号那一行点「注入 / Load」
4. 再启动 AstrBot

**切换账号必须先完全退出 QQ**：hook 残留会让手动模式也接管旧进程。

---

## 3. 放插件

```powershell
copy plugins\* C:\QQBot\astrbot\data\plugins\ -Recurse
```

每个插件目录里应有 `main.py`、`metadata.yaml`、`.astrbot-plugin`（标记文件）。

重启 AstrBot，日志里应出现：

```
Loading plugin bili_pusher ...
Added llm tool: keep_video
Plugin bili_pusher (1.0.0) by local: ...
```

如果出现 `Failed to load plugin xxx`，看完整 traceback——最常见是 `AttributeError`（调了不存在的方法）或 `SyntaxError`（中英文引号混用）。

---

## 4. 填配置

```powershell
copy examples\bili_push.example.json     C:\QQBot\bili_push.json
copy examples\bili_account.example.json  C:\QQBot\bili_account.json
copy examples\proactive_dm.example.json  C:\QQBot\proactive_dm.json
```

必改：`target_qq`（你的 QQ）、`bili_at_mid` / `bili_at_name`（B 站大号）、`dedeuserid`（B 站小号 UID）。

### B 站 cookie 怎么拿

1. 浏览器登录**小号**
2. F12 → Application → Cookies → `https://www.bilibili.com`
3. 复制三个值：`SESSDATA`、`bili_jct`、`DedeUserID`
4. 粘进 `bili_account.json`

或用自带面板（推荐，会先验证再保存）：

```powershell
python tools\bili_login_ui.py    # 打开 http://127.0.0.1:8777
```

它支持**粘贴整段 cookie**（`SESSDATA=..; bili_jct=..; DedeUserID=..`），自动解析、验证登录、顺带验 csrf。

**三个值各自的作用**：

| 值 | 缺了会怎样 |
|---|---|
| `SESSDATA` | 一切需要登录的接口返回 `-101 账号未登录` |
| `bili_jct` | 能读不能写，评论返回 `-111 CSRF 校验失败` |
| `DedeUserID` | 一般不影响，但历史/个人信息可能取不到 |

---

## 5. 装人格

```powershell
python tools\set_persona_whale.py
```

从 `examples/persona-whale-girl.txt` 写入 AstrBot 的人格库（`data_v4.db` 的 `personas` 表，`persona_id=whale-girl`）。

改人格：面板 → 人格，或直接改那个 txt 后重新跑脚本。**改完要重启 AstrBot**。

---

## 6. 表情包

```powershell
# 1) 把图丢进 C:\QQBot\sticker_inbox
# 2) 导入（会视觉打标 + 写索引）
python tools\import_stickers.py

# 或者先不调模型，只按文件名分类
python tools\import_stickers.py --no-tag
```

索引在 `C:\QQBot\stickers\index.json`，字段：`mood / tags / intensity / scenes / safety`。

**修索引**：`python tools\repair_index.py`（补库里存在但索引缺失的图）。

---

## 7. 二次元词库

`data/anime_kb/glossary.json`、`works.json` 复制到 `C:\QQBot\anime_kb\`。

改完**不用重启**（按 mtime 热加载）。想扩充：

```powershell
python tools\build_glossary.py   # 改脚本里的 RAW 段
python tools\build_works.py
```

---

## 8. 守护

见 README「守护」一节。三个要点：

- `AtLogOn` 而不是 `AtStartup`（SYSTEM 会话没有 QQ 登录态）
- `pythonw.exe` 无窗口
- `stop.bat` 会先建 `watchdog_pause` 再杀进程，否则守护会把它拉回来

---

## 9. 排障速查

| 现象 | 原因 / 处理 |
|---|---|
| `connect ECONNREFUSED 127.0.0.1:6199` | AstrBot 没起来，或 6199 被占 |
| AstrBot 日志无「反向连接成功」 | 两边 token 不一致，检查 `token.txt` / `onebot.json` / `cmd_config.json` |
| 群聊不回复 | 默认要 **@机器人**；检查唤醒配置 |
| `.bat` 报 `'--' 不是内部或外部命令` | 脚本被存成 UTF-8 或 LF。**必须是纯 ASCII + CRLF** |
| B 站 评论 `-101` | SESSDATA 过期，重新取 |
| B 站 评论 `-111` | bili_jct 过期/不对 |
| B 站 搜索 `-412` | 风控，先访问首页拿 buvid cookies 再重试（代码已做） |
| 服务无声消失、日志无 ERROR | 控制台窗口被关；看 `logs/watchdog.log`，或 `schtasks /run /tn QQBotWatchdog` |
| 改了 `bili_push.json` 没生效 | 确认日志没有「读取配置失败」；每轮会重读文件 |
| 群里发的图没进表情库 | `sticker_inbox/_pending` 保留 24 小时，超时需重发 |
| 表情用了不合适的图 | 改 `stickers/index.json` 里那张的 `mood/intensity/safety`，热加载 |

---

## 10. 换安装路径

```powershell
python tools\relocate.py D:\apps\QQBot
```

它会把所有文件里的 `C:\QQBot` 换掉。注意：

- `.bat` 必须是**纯 ASCII + CRLF**，改完检查一遍
- 运行数据（`chat_archive/`、`group_memory.json`、`stickers/` 等）要一起搬
- 守护的计划任务、`.url` 快捷方式里的路径也要改

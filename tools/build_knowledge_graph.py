"""从真实目录结构生成知识图谱数据（nodes / edges）。

输出：D:\\QQrobot\\dashboard\\knowledge.json
"""

import json
import pathlib
import re

ROOT = pathlib.Path(r"D:\QQrobot")
OUT = ROOT / "dashboard" / "knowledge.json"

nodes: list[dict] = []
edges: list[dict] = []
seen: set[str] = set()


def node(nid, label, group, kind, desc="", size=None):
    if nid in seen:
        return nid
    seen.add(nid)
    nodes.append({
        "id": nid, "label": label, "group": group, "kind": kind,
        "desc": desc, "size": size or (28 if kind == "service" else 18),
    })
    return nid


def edge(a, b, label="", kind="flow"):
    edges.append({"source": a, "target": b, "label": label, "kind": kind})


# ---------------- 服务 ----------------
node("svc-astrbot", "AstrBot 6185", "系统", "service", "QQ 机器人主进程，加载全部插件")
node("svc-snowluma", "SnowLuma 3000", "系统", "service", "OneBot v11 网关，收发 QQ 消息")
node("svc-webui", "SnowLuma 5099", "系统", "service", "SnowLuma 控制台，登录/注入 QQ")
node("svc-dashboard", "控制台 8765", "系统", "service", "自建面板：状态/Token/日记/兴趣/调音台")
node("svc-voice", "声音服务 9880", "声音", "service", "WSL 里的 FastAPI，调度 TTS 与 RVC")
node("svc-daemon", "常驻 TTS daemon", "声音", "service", "模型只加载一次，空闲 30 分钟退出")
node("db", "data_v4.db", "记忆", "data", "AstrBot 会话/人格数据库")
node("wsl", "WSL Ubuntu-22.04", "系统", "infra", "声音服务与两套推理环境所在（D:/wsl）")
node("sd", "SD1.5 + 她的 LoRA", "声音", "infra", "anything-v5 + whale-lora-chibi，13 MB")
node("cosy", "CosyVoice3", "声音", "infra", "两条说话声线 + 情绪后期")
node("rvc", "RVC 换声", "声音", "infra", "40kHz 换声模型")
node("anysearch", "AnySearch key", "外部", "external", "secrets/anysearch.json")

# ---------------- 外部 ----------------
node("ext-deepseek", "DeepSeek API", "外部", "external", "v4-pro 聊天 / flash 轻量任务")
node("ext-bili", "B站 API", "外部", "external", "视频、推荐、评论、登录态")
node("ext-anysearch", "AnySearch", "外部", "external", "联网搜索与网页正文提取")
node("ext-qq", "QQ", "外部", "external", "私聊/群聊")

edge("svc-astrbot", "ext-deepseek", "LLM 调用")
edge("svc-astrbot", "ext-bili", "刷视频/评论")
edge("svc-astrbot", "ext-anysearch", "搜索")
edge("svc-snowluma", "ext-qq", "消息收发")
edge("svc-astrbot", "svc-snowluma", "OneBot")
edge("svc-dashboard", "svc-astrbot", "读状态/改配置")
edge("svc-dashboard", "svc-voice", "调音台代理")
edge("svc-astrbot", "svc-voice", "语音合成")
edge("svc-voice", "svc-daemon", "socket")
edge("svc-daemon", "wsl", "运行于")
edge("svc-voice", "wsl", "运行于")
edge("svc-astrbot", "db", "读写")

# ---------------- 插件（自动扫 metadata.yaml）----------------
PLUGIN_GROUP = {
    "persona": ["mood", "feedback_learner", "anime_kb"],
    "对话": ["chat_archive", "group_memory", "chat_format", "cost_router", "image_slim",
             "timeline", "proactive_dm", "memory_backup", "token_meter"],
    "B站": ["bili_pusher", "bili_tools", "browse_log", "interest", "bili_login_watch"],
    "声音": ["feiyu_voice", "voice_clips", "voice_reply", "image_gen"],
    "系统": ["guard_report", "web_access", "web_explore", "slang_learn"],
}
group_of = {d: g for g, items in PLUGIN_GROUP.items() for d in items}

try:
    import yaml
except ImportError:
    yaml = None

plugins_dir = ROOT / "astrbot" / "data" / "plugins"
for p in sorted(plugins_dir.iterdir()):
    if not p.is_dir() or p.name.startswith("__"):
        continue
    meta = {}
    f = p / "metadata.yaml"
    if f.exists() and yaml:
        try:
            meta = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:
            meta = {}
    desc = (meta.get("desc") or "").strip()
    group = group_of.get(p.name, "系统")
    node(f"plug-{p.name}", p.name, group, "plugin", desc, 20)
    edge("svc-astrbot", f"plug-{p.name}", "加载")

# ---------------- 关键数据文件 ----------------
DATA = {
    "bili_memory.json": ("B站 记忆", "B站", "推送过/评论过的视频记忆"),
    "bili_activity.jsonl": ("B站 行为流", "B站", "刷视频与评论的流水"),
    "bili_watched.jsonl": ("看过清单", "B站", "浏览过的视频"),
    "interest_profile.json": ("兴趣画像", "B站", "分区/话题/博主三维"),
    "interest_history.json": ("兴趣快照", "B站", "每周快照，用于漂移"),
    "interest_drift.jsonl": ("兴趣漂移", "B站", "新出现/消失/升温/降温"),
    "mood.json": ("心情", "persona", "每天的心情与提示"),
    "persona/whale-girl.md": ("人格真源", "persona", "她的设定唯一真源"),
    "diary": ("日记", "persona", "她自己写的每日日记"),
    "learned_rules.json": ("学到的规则", "persona", "从主人话里抽的规则"),
    "token_usage.jsonl": ("Token 用量", "对话", "每次 LLM 调用记录"),
    "chat_archive": ("聊天存档", "对话", "按天 JSONL 原文"),
    "group_memory.json": ("群聊记忆", "对话", "人物卡 + 摘要"),
    "voice_reply_state.json": ("自动语音状态", "声音", "今日条数与冷却"),
    "voice_clips": ("曲库", "声音", "她唱的 8 首歌"),
    "secrets": ("密钥", "系统", "AnySearch / 声音服务 key"),
    "backup": ("每日备份", "系统", "记忆/规则/人格/索引"),
}
for rel, (label, group, desc) in DATA.items():
    path = ROOT / rel
    if not path.exists():
        continue
    node(f"data-{rel}", label, group, "data", desc, 16)

edge("plug-bili_pusher", "data-bili_memory.json", "写")
edge("plug-bili_pusher", "data-bili_activity.jsonl", "写")
edge("plug-bili_pusher", "ext-bili", "评论/推荐")
edge("plug-bili_tools", "ext-bili", "查询")
edge("plug-browse_log", "data-bili_watched.jsonl", "写")
edge("plug-interest", "data-interest_profile.json", "写")
edge("plug-interest", "data-interest_history.json", "写")
edge("plug-interest", "data-interest_drift.jsonl", "写")
edge("plug-mood", "data-mood.json", "写")
edge("plug-feedback_learner", "data-learned_rules.json", "写")
edge("plug-chat_archive", "data-chat_archive", "写")
edge("plug-group_memory", "data-group_memory.json", "写")
edge("plug-token_meter", "data-token_usage.jsonl", "写")
edge("plug-diary", "data-diary", "写")
edge("plug-memory_backup", "data-backup", "写")
edge("plug-memory_backup", "data-persona/whale-girl.md", "备份")
edge("plug-voice_reply", "data-voice_reply_state.json", "写")
edge("plug-voice_reply", "svc-voice", "合成")
edge("plug-feiyu_voice", "svc-voice", "调用")
edge("plug-voice_clips", "data-voice_clips", "读")
edge("plug-web_access", "ext-anysearch", "搜索")
edge("plug-web_explore", "ext-anysearch", "自己上网")
edge("plug-slang_learn", "ext-anysearch", "学热词")
edge("plug-image_gen", "sd", "生图")
edge("plug-cost_router", "ext-deepseek", "分流")
edge("plug-proactive_dm", "svc-snowluma", "主动私聊")
edge("plug-guard_report", "svc-snowluma", "私聊告知")
edge("plug-bili_login_watch", "ext-bili", "登录自检")
edge("plug-anime_kb", "data-", "", "none") if False else None
edge("plug-persona" if False else "plug-diary", "svc-dashboard", "展示")
edge("data-token_usage.jsonl", "svc-dashboard", "展示")
edge("data-interest_profile.json", "svc-dashboard", "展示")
edge("data-mood.json", "svc-dashboard", "展示")
edge("data-diary", "svc-dashboard", "展示")
edge("data-persona/whale-girl.md", "svc-astrbot", "注入人格")
edge("wsl", "svc-daemon", "承载模型")
edge("svc-daemon", "ext-deepseek", "", "none") if False else None

# 清理空边
edges = [e for e in edges if e["source"] in seen and e["target"] in seen]

groups = {}
for n in nodes:
    groups.setdefault(n["group"], 0)
    groups[n["group"]] += 1

payload = {
    "generated": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
    "nodes": nodes,
    "edges": edges,
    "groups": groups,
}
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"节点 {len(nodes)} / 边 {len(edges)} / 分组 {len(groups)} -> {OUT}")
for g, c in groups.items():
    print(f"  {g}: {c}")

"""识别 QQ 互动：被戳一戳、被点赞、被撤回消息等。

现在处理：
- 戳一戳（私聊/群聊）→ 按概率戳回去 / 说一句
- 消息撤回 → 问一句"撤回了什么"
- 其它通知 → 记日志，方便排查
"""

from __future__ import annotations

import json
import random
from datetime import datetime
from pathlib import Path

import httpx

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain

ROOT = Path(r"D:\QQrobot")

TOKEN_FILE = ROOT / "secrets" / "onebot_token.txt"


def _onebot_token() -> str:
    """OneBot 访问 token：优先读 secrets/onebot_token.txt，其次环境变量 ONEBOT_TOKEN。"""
    import os

    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return os.environ.get("ONEBOT_TOKEN", "").strip()

CFG = ROOT / "qq_interact.json"
LOG = ROOT / "qq_interact.jsonl"
ONEBOX = "http://127.0.0.1:3000"
TOKEN = ""

DEFAULT = {
    "enabled": True,
    "poke_back_chance": 0.6,
    "poke_say_chance": 0.5,
    "notice_recall": True,
    "cooldown_sec": 20,
    "self_id": 3862612755,
}

POKE_LINES = [
    "干嘛呀",
    "别戳了啦",
    "有事说事",
    "再戳我咬你",
    "唔…醒了",
    "戳我干嘛，我又不会跑",
]
RECALL_LINES = ["你撤回了什么？", "刚说了啥，没看见", "偷偷改口？"]


def _cfg() -> dict:
    out = dict(DEFAULT)
    try:
        out.update(json.loads(CFG.read_text(encoding="utf-8")))
    except Exception:
        pass
    return out


def _log(rec: dict) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **rec},
                                ensure_ascii=False) + "\n")
    except OSError:
        pass


def _post(action: str, payload: dict, timeout: float = 30) -> dict:
    r = httpx.post(f"{ONEBOX}/{action}",
                   headers={"Authorization": f"Bearer {TOKEN or _onebot_token()}"}, json=payload, timeout=timeout)
    return r.json()


class QQInteract(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._last: dict[str, float] = {}
        self._last_self = 0

    def _cool(self, key: str, sec: float) -> bool:
        import time

        now = time.time()
        if now - self._last.get(key, 0) < sec:
            return False
        self._last[key] = now
        return True


    async def _compose_poke_line(self, event, uid: str, gid: str | None) -> str:
        """被戳时用她自己的语气说一句（失败就用模板）。"""
        cfg = _cfg()
        if not cfg.get("llm_line", True):
            return random.choice(POKE_LINES)
        mood = ""
        try:
            mood = (json.loads((ROOT / "mood.json").read_text(encoding="utf-8")) or {}).get("mood", "")
        except Exception:
            pass
        prompt = (
            "主人刚刚在 QQ 上戳了你一下（戳一戳）。用你自己的语气回一句，"
            "10 字以内，口语，别解释，别问好，只输出这句话。"
            + (f"你今天的心情是：{mood}。" if mood else "")
        )
        try:
            provider = self.context.provider_manager.get_provider_by_id("deepseek/deepseek-flash")
            if provider is None:
                raise RuntimeError("没有 flash")
            resp = await provider.text_chat(
                prompt=prompt,
                system_prompt="你是一只懒洋洋、嘴有点硬的鲸鱼娘，说话短。",
            )
            text = (resp.completion_text or "").strip().strip("「」\"'")
            text = text.splitlines()[0][:24] if text else ""
            if text:
                return text
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[interact] 生成戳回复失败：{exc}")
        return random.choice(POKE_LINES)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_event(self, event: AstrMessageEvent):
        cfg = _cfg()
        if not cfg.get("enabled", True):
            return
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if not isinstance(raw, dict):
            return
        if raw.get("post_type") != "notice":
            return

        sub = raw.get("sub_type") or raw.get("notice_type") or ""
        uid = str(raw.get("user_id", ""))
        gid = str(raw.get("group_id", "")) or None
        target = str(raw.get("target_id", ""))
        self_id = str(raw.get("self_id") or cfg.get("self_id") or "")

        if sub in ("poke", "friend_recall", "group_recall", "honor", "like"):
            _log({"sub": sub, "notice": raw.get("notice_type"), "user": uid,
                  "sender": str(raw.get("sender_id", "")), "group": gid,
                  "target": target, "self": self_id, "raw": raw})

        # ---- 戳一戳 ----
        if sub == "poke":
            # 私聊：不管 target 写成谁，都算戳她
            # 群聊：只有戳她时才回
            if gid and target and target not in (self_id, ""):
                return
            if not self._cool(f"poke:{uid}:{gid}", float(cfg.get("cooldown_sec", 20))):
                return
            umo = event.unified_msg_origin
            poke_back = random.random() < float(cfg.get("poke_back_chance", 0.6))
            say = random.random() < float(cfg.get("poke_say_chance", 0.5))
            try:
                if poke_back:
                    payload = {"user_id": int(uid)} if gid is None else {
                        "user_id": int(uid), "group_id": int(gid)}
                    _post("send_poke", payload)
                if say:
                    line = await self._compose_poke_line(event, uid, gid)
                    await self.context.send_message(umo, MessageChain([Plain(line)]))
                    return  # 说过就不再多发
                if poke_back:
                    return
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[interact] 戳回应失败：{exc}")
            event.stop_event()
            return

        # ---- 撤回 ----
        if raw.get("notice_type") == "friend_recall" and cfg.get("notice_recall", True):
            if not self._cool(f"recall:{uid}", 60):
                return
            try:
                await self.context.send_message(
                    event.unified_msg_origin, MessageChain([Plain(random.choice(RECALL_LINES))]))
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[interact] 撤回提示失败：{exc}")
            return

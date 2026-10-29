"""让鲸鱼娘自己发 QQ 空间说说。

两种触发：
1. 你让她发：/说说 <内容>，或者她自己在对话里调用工具
2. 自己发：每天在活跃时段里随机挑一次，用心情 / 日记 / 最近刷到的视频凑一条
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime
from pathlib import Path

import httpx

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.star.filter.command import GreedyStr

ROOT = Path(r"D:\QQrobot")

TOKEN_FILE = ROOT / "secrets" / "onebot_token.txt"


def _onebot_token() -> str:
    """OneBot 访问 token：优先读 secrets/onebot_token.txt，其次环境变量 ONEBOT_TOKEN。"""
    import os

    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return os.environ.get("ONEBOT_TOKEN", "").strip()

CFG = ROOT / "qzone_post.json"
LOG = ROOT / "qzone_posts.jsonl"
STATE = ROOT / "qzone_state.json"
ONEBOX = "http://127.0.0.1:3000"
ONEBOX_TOKEN = ""

DEFAULT = {
    "enabled": True,
    "auto_daily": 1,
    "active_hours": [10, 23],
    "ugc_right": 1,
    "notify_qq": 3328480063,
}


def _cfg() -> dict:
    out = dict(DEFAULT)
    try:
        out.update(json.loads(CFG.read_text(encoding="utf-8")))
    except Exception:
        pass
    return out


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(d: dict) -> None:
    STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _api(action: str, payload: dict, timeout: float = 60) -> dict:
    r = httpx.post(
        f"{ONEBOX}/{action}",
        headers={"Authorization": f"Bearer {ONEBOX_TOKEN or _onebot_token()}"},
        json=payload,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


class QzonePost(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._last_auto = 0.0

    # ---------------- 发 ----------------
    def _post(self, content: str, images: list[str] | None = None, ugc_right: int | None = None) -> dict:
        cfg = _cfg()
        payload = {"content": content.strip()[:1500],
                   "ugc_right": int(ugc_right if ugc_right is not None else cfg.get("ugc_right", 1))}
        if images:
            payload["images"] = images[:9]
        resp = _api("send_qzone_msg", payload, timeout=120)
        if resp.get("status") != "ok":
            raise RuntimeError(resp.get("wording") or resp.get("message") or "发表失败")
        tid = (resp.get("data") or {}).get("tid", "")
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                 "content": content, "tid": tid}, ensure_ascii=False) + "\n")
        return resp.get("data") or {}

    # ---------------- 自动发 ----------------
    def _compose(self) -> str:
        """用心情 / 日记 / B站 素材凑一条说说。"""
        mood = ""
        diary = ""
        seen = ""
        try:
            mood = (json.loads((ROOT / "mood.json").read_text(encoding="utf-8")) or {}).get("mood", "")
        except Exception:
            pass
        try:
            files = sorted((ROOT / "diary").glob("*.md"))
            if files:
                diary = files[-1].read_text(encoding="utf-8").strip()[:200]
        except Exception:
            pass
        try:
            mem = json.loads((ROOT / "bili_memory.json").read_text(encoding="utf-8")) or []
            if mem:
                seen = str(mem[-1].get("title", ""))[:60]
        except Exception:
            pass

        provider = None
        try:
            provider = self.context.provider_manager.get_provider_by_id("deepseek/deepseek-flash")
        except Exception:
            provider = None

        parts = [f"今天心情：{mood}"] if mood else []
        if diary:
            parts.append(f"今天的日记：{diary}")
        if seen:
            parts.append(f"刚刷到的视频：{seen}")
        material = "\n".join(parts) or "（没什么素材，随便写一句）"

        prompt = (
            "你要在 QQ 空间发一条说说。用你自己的语气，像随手写的，不要标题不要引号。\n"
            "要求：1～2 句，30 字以内，别用「今天」开头，别喊口号，别提 AI。\n"
            f"参考素材（不一定全用）：\n{material}\n只输出说说正文。"
        )
        return self._llm_or_fallback(prompt, material)

    def _llm_or_fallback(self, prompt: str, material: str) -> str:
        try:
            import asyncio

            async def go() -> str:
                provider = self.context.provider_manager.get_provider_by_id("deepseek/deepseek-flash")
                if provider is None:
                    return ""
                r = await provider.text_chat(prompt=prompt, system_prompt="你是一只懒洋洋的鲸鱼娘。")
                return (r.completion_text or "").strip()

            text = asyncio.run(go())
            text = text.strip().strip("「」\"'")
            return text[:120] or material[:60]
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[qzone] 生成失败：{exc}")
            return random.choice([
                "今天不想动，窝着。",
                "屋里有点闷，开窗了。",
                "刚睡醒，头发翘着。",
            ])

    def _due(self) -> bool:
        cfg = _cfg()
        if not cfg.get("enabled", True) or int(cfg.get("auto_daily", 1)) <= 0:
            return False
        now = datetime.now()
        lo, hi = cfg.get("active_hours", [10, 23])
        if not (float(lo) <= now.hour + now.minute / 60 <= float(hi)):
            return False
        st = _state()
        today = now.strftime("%Y-%m-%d")
        if st.get("date") == today and int(st.get("count", 0)) >= int(cfg.get("auto_daily", 1)):
            return False
        # 随机窗口：一天里散开，避免整点发
        seed = f"{today}-{st.get('count', 0)}"
        random.seed(seed)
        target = random.uniform(float(lo), float(hi))
        random.seed()
        return (now.hour + now.minute / 60) >= target

    async def _auto_tick(self) -> None:
        if not self._due():
            return
        try:
            content = self._compose()
            data = self._post(content)
            st = _state()
            today = datetime.now().strftime("%Y-%m-%d")
            if st.get("date") != today:
                st = {"date": today, "count": 0}
            st["count"] = int(st.get("count", 0)) + 1
            _save_state(st)
            logger.info(f"[qzone] 自动发了说说：{content[:30]} tid={data.get('tid')}")
            cfg = _cfg()
            target = str(cfg.get("notify_qq") or "")
            if cfg.get("notify_qq") and target:
                _api("send_private_msg", {"user_id": int(target),
                                          "message": f"我刚在空间发了条说说：{content}"})
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[qzone] 自动发失败：{exc}")

    @filter.on_astrbot_loaded()
    async def on_loaded(self) -> None:
        """加载后启动每分钟检查。"""
        import asyncio

        async def loop() -> None:
            while True:
                await asyncio.sleep(60)
                try:
                    await self._auto_tick()
                except Exception as exc:  # noqa: BLE001
                    logger.info(f"[qzone] tick 出错：{exc}")

        asyncio.create_task(loop())

    # ---------------- 指令 / 工具 ----------------
    @filter.command("说说", alias={"发说说", "qzone"})
    async def cmd_post(self, event: AstrMessageEvent, content: GreedyStr):
        text = str(content or "").strip()
        if not text:
            yield event.plain_result("发什么？用法：/说说 今天不想动")
            return
        try:
            data = await __import__("asyncio").to_thread(self._post, text)
            yield event.plain_result(f"发好了（tid {data.get('tid', '')[:8]}）")
        except Exception as exc:  # noqa: BLE001
            yield event.plain_result(f"没发出去：{exc}")

    @filter.llm_tool(name="post_qzone")
    async def tool_post(self, event: AstrMessageEvent, content: str) -> str:
        """在自己的 QQ 空间发一条说说。用户让你"发个说说""发条动态"时使用。

        Args:
            content(string): 说说正文，30 字以内，用你自己的语气，不要标题和引号。
        """
        try:
            import asyncio

            data = await asyncio.to_thread(self._post, content)
            return f"（已发到空间，正文：{content}）"
        except Exception as exc:  # noqa: BLE001
            return f"没发出去：{exc}"

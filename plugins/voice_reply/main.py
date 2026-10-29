"""自动语音：在她回复之后，偶尔把这句话用她自己的声音再说一遍。

设计取舍：
- 只在私聊、只在文字回复、只挑「适合出声」的短句
- 有冷却时间（默认 15 分钟）和每日上限（默认 10 条），避免刷屏
- 合成在后台异步做，不拖慢文字回复（常驻 daemon 约 4-9 秒）
- `/语音` 开关；`/语音 状态` 看今天发了几条
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import date
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, MessageChain, filter

ROOT = Path(r"D:\QQrobot")
CFG = ROOT / "astrbot" / "data" / "config" / "feiyu_voice_config.json"
STATE = ROOT / "voice_reply_state.json"
MOOD_FILE = ROOT / "mood.json"

MAX_CHARS = 42
COOLDOWN_MIN = 15
DAILY_CAP = 10
PROBABILITY = 0.05   # 平时按这个概率随机出声
EXPLICIT_PAT = re.compile(
    r"说句话|说一句|说个话|说下话|说给我听|用语音|发语音|语音说|"
    r"让我听听|听听你|你的声音|念一下|读一下|说点什么|讲句话|开个口|说两句"
)
SUPPRESS_TEXT = True  # 决定发语音时，先扣住文字，语音成功后再丢掉
PLACEHOLDER = "…"      # 扣字后立刻发的占位，避免空窗
POST_VOICE_PAUSE = 1.5  # 语音发完后的留白（秒）

# 这些话值得用声音说
TRIGGERS = (
    "晚安", "早安", "早上好", "睡吧", "睡了", "想你了", "想你", "爱你", "抱抱",
    "乖", "别怕", "我在", "在呢", "回来", "亲口", "说句话", "说话", "叫我",
)

SKIP_PAT = re.compile(r"https?://|www\.|BV[0-9A-Za-z]{10}|```|\[B站|【B站|点开|链接")


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    try:
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _config() -> dict:
    try:
        return json.loads(CFG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _emotion() -> str:
    """按当天心情选情绪：心情好用开心，其余用奶音。"""
    try:
        mood = json.loads(MOOD_FILE.read_text(encoding="utf-8")).get("mood", "")
    except Exception:
        return "奶音"
    return "开心" if any(k in str(mood) for k in ("开心", "兴奋", "高兴", "雀跃")) else "奶音"


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._busy = False
        self._user_text = ""

    # ---------------- 触发判断 ----------------
    def _should_speak(self, text: str, umo: str) -> tuple[bool, str]:
        if not text or len(text) > MAX_CHARS:
            return False, "太长"
        if SKIP_PAT.search(text):
            return False, "含链接/代码"
        if "FriendMessage" not in umo and "webchat" not in umo:
            return False, "不是私聊"
        explicit = bool(self._user_text and EXPLICIT_PAT.search(self._user_text))
        st = _load_state()
        if st.get("disabled"):
            return False, "已关闭"
        today = date.today().isoformat()
        if st.get("date") != today:
            st = {"date": today, "count": 0, "last": 0}
        if int(st.get("count", 0)) >= DAILY_CAP:
            return False, "今日上限"
        if explicit:
            return True, "你要听"
        if time.time() - float(st.get("last") or 0) < COOLDOWN_MIN * 60:
            return False, "冷却中"
        import random

        if random.random() < PROBABILITY:
            return True, f"随机 {int(PROBABILITY * 100)}%"
        return False, "这次不出声"

    # ---------------- 合成 + 发送 ----------------
    async def _speak(self, umo: str, text: str, fallback_text: str = "") -> None:
        import urllib.request

        cfg = _config()

        # 走阿里云 cosyvoice-v2
        if str(cfg.get("provider") or "local").lower() in ("qwen", "qwen_rvc"):
            try:
                from .qwen_tts import synthesize

                voice = str(cfg.get("qwen_voice") or "longhua_v2")
                try:
                    speed = float(cfg.get("qwen_speed", 1.0))
                except (TypeError, ValueError):
                    speed = 1.0
                out = ROOT / "voice_out" / f"auto_{int(time.time())}.wav"
                await synthesize(text, out, voice=voice, speed=speed)
                if str(cfg.get("provider")).lower() == "qwen_rvc":
                    try:
                        from .rvc_client import convert

                        out = await asyncio.to_thread(
                            convert, out, ROOT / "voice_out" / f"her_{int(time.time())}.wav"
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.info(f"[voice_reply] RVC 失败，用 qwen 原音：{exc}")
                await self.context.send_message(
                    umo, MessageChain([Comp.Record.fromFileSystem(str(out))])
                )
                return
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[voice_reply] qwen 失败，转本地：{exc}")

        base = (cfg.get("base_url") or "http://127.0.0.1:9880").rstrip("/")
        key = cfg.get("api_key") or ""
        style = cfg.get("style") or "cute"

        def _post() -> dict:
            body = json.dumps({
                "voice": "feiyu", "text": text, "style": style,
                "emotion": _emotion(), "speed": 0.9, "liveliness": 0.35, "chunk": False,
            }).encode()
            req = urllib.request.Request(
                base + "/v1/speech", data=body, method="POST",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())

        try:
            job = await asyncio.to_thread(_post)
            job_id = job.get("id")
            if not job_id:
                logger.info(f"[voice_reply] 提交失败：{job}")
                await self._fallback_text(umo, fallback_text)
                return
            for _ in range(60):
                await asyncio.sleep(1.5)
                state = await asyncio.to_thread(self._job_state, base, key, job_id)
                if state.get("status") == "success":
                    break
                if state.get("status") == "failed":
                    logger.info(f"[voice_reply] 合成失败：{state.get('error')}")
                    await self._fallback_text(umo, fallback_text)
                    return
            else:
                logger.info("[voice_reply] 合成超时")
                await self._fallback_text(umo, fallback_text)
                return
            data = await asyncio.to_thread(self._download, base, key, job_id)
            out = ROOT / "voice_out" / f"auto_{job_id[:8]}.wav"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            ok = await self.context.send_message(
                umo, MessageChain([Comp.Record.fromFileSystem(str(out))])
            )
            if not ok:
                await self.context.send_message(
                    umo, MessageChain([Comp.File(name=out.name, file=str(out))])
                )
            await asyncio.sleep(POST_VOICE_PAUSE)
            st = _load_state()
            st["count"] = int(st.get("count", 0)) + 1
            st["last"] = time.time()
            st["date"] = date.today().isoformat()
            _save_state(st)
            logger.info(f"[voice_reply] 已发语音（{len(text)} 字，{style}）")
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[voice_reply] 异常：{exc}")
            await self._fallback_text(umo, fallback_text)

    async def _placeholder(self, umo: str) -> None:
        """扣字后先冒个泡，别让她那边空着。"""
        try:
            await self.context.send_message(umo, MessageChain([Comp.Plain(PLACEHOLDER)]))
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[voice_reply] 占位发送失败：{exc}")

    async def _fallback_text(self, umo: str, text: str) -> None:
        """语音没成功时，把扣住的文字补发出去。"""
        if not text:
            return
        try:
            await self.context.send_message(umo, MessageChain([Comp.Plain(text)]))
            logger.info("[voice_reply] 语音失败，已补发文字")
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[voice_reply] 补发文字失败：{exc}")

    @staticmethod
    def _job_state(base: str, key: str, job_id: str) -> dict:
        import urllib.request

        req = urllib.request.Request(f"{base}/v1/jobs/{job_id}",
                                     headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    @staticmethod
    def _download(base: str, key: str, job_id: str) -> bytes:
        import urllib.request

        req = urllib.request.Request(f"{base}/v1/jobs/{job_id}/audio",
                                     headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()

    # ---------------- 钩子 ----------------
    @filter.on_decorating_result()
    async def capture_text(self, event: AstrMessageEvent) -> None:
        """判定这一轮要不要出声。

        要出声：扣住文字（清空消息链），等语音合成好再发；
                合成失败就把文字补发出去，不能让她"失声"。
        不出声：什么都不做，文字照常发。
        """
        try:
            try:
                self._user_text = str(getattr(event, "message_str", "") or "")
            except Exception:
                self._user_text = ""
            result = event.get_result()
            if result is None:
                return
            parts = [c.text for c in result.chain if isinstance(c, Comp.Plain)]
            text = "\n".join(x for x in parts if x and x.strip()).strip()
            if not text:
                return
            ok, why = self._should_speak(text, event.unified_msg_origin)
            if not ok:
                return
            if not SUPPRESS_TEXT:
                event.set_extra("voice_reply_text", text)
                return
            # 扣住文字：清空消息链，respond 阶段会跳过发送
            result.chain = []
            event.set_extra("voice_consumed", True)
            logger.info(f"[voice_reply] 文字已扣住，转语音：{text[:24]}…（{why}）")
            umo = event.unified_msg_origin
            asyncio.create_task(self._placeholder(umo))
            asyncio.create_task(self._speak(umo, text, fallback_text=text))
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[voice_reply] 捕获阶段异常：{exc}")

    @filter.after_message_sent()
    async def maybe_speak(self, event: AstrMessageEvent) -> None:
        if self._busy or event.get_extra("voice_consumed"):
            return
        text = str(event.get_extra("voice_reply_text") or "").strip()
        if not text:
            return
        ok, _why = self._should_speak(text, event.unified_msg_origin)
        if not ok:
            return
        self._busy = True
        try:
            await self._speak(event.unified_msg_origin, text)
        finally:
            self._busy = False

    # ---------------- 指令 ----------------
    @filter.command("语音测试")
    async def test_cmd(self, event: AstrMessageEvent, text: str = ""):
        """立刻用当前声线说一句，用来验证链路"""
        say = (text or "").strip() or "这是语音测试，能听见就说明通了。"
        yield event.plain_result(f"去合成：{say}")
        await self._speak(event.unified_msg_origin, say[:60])

    @filter.command("语音")
    async def toggle(self, event: AstrMessageEvent, arg: str = ""):
        """开关自动语音：/语音 on|off|状态"""
        st = _load_state()
        a = (arg or "").strip().lower()
        if a in ("off", "关", "关闭"):
            st["disabled"] = True
            _save_state(st)
            yield event.plain_result("自动语音关了")
            return
        if a in ("on", "开", "开启"):
            st["disabled"] = False
            _save_state(st)
            yield event.plain_result("自动语音开了")
            return
        today = date.today().isoformat()
        count = int(st.get("count", 0)) if st.get("date") == today else 0
        flag = "关" if st.get("disabled") else "开"
        yield event.plain_result(
            f"自动语音：{flag}\n今天已发 {count}/{DAILY_CAP} 条\n"
            f"冷却 {COOLDOWN_MIN} 分钟 · 只在私聊 · 只挑带情绪的短句"
        )

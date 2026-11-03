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
CFG = ROOT / "qzone_post.json"
LOG = ROOT / "qzone_posts.jsonl"
STATE = ROOT / "qzone_state.json"
ONEBOX = "http://127.0.0.1:3000"
ONEBOX_TOKEN = ""

DEFAULT = {
    "enabled": True,
    "auto_daily": 6,            # 一天最多几条（想发就发）
    "min_gap_minutes": 80,      # 两条之间至少隔多久
    "active_hours": [8, 24],    # 醒了就能发
    "ugc_right": 1,
    "notify_qq": 3328480063,
    "trigger_file": "qzone_want.json",   # 别的插件想让她发时写这个
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
    async def _compose(self) -> str:
        """发一条即兴说说：此刻的一件事、一个念头，不是日总结。"""
        mood = ""
        note = ""
        moment = ""
        try:
            md = json.loads((ROOT / "mood.json").read_text(encoding="utf-8")) or {}
            mood = str(md.get("mood") or "")
            note = str(md.get("note") or "")
        except Exception:
            pass
        # 刚刷到的东西
        try:
            lines = (ROOT / "web_wander.jsonl").read_text(encoding="utf-8").splitlines()[-3:]
            for line in reversed(lines):
                d = json.loads(line)
                if d.get("title"):
                    moment = f"刚刷到：{str(d['title'])[:50]}"
                    break
        except Exception:
            pass
        # 刚存进房间的东西
        if not moment:
            try:
                lf = ROOT / "her_room" / "links.jsonl"
                if lf.exists():
                    d = json.loads(lf.read_text(encoding="utf-8").splitlines()[-1])
                    moment = f"刚记下：{str(d.get('title') or '')[:40]}"
            except Exception:
                pass
        # 刚发生的心情事件
        if not moment:
            try:
                import sqlite3

                db = ROOT / "astrbot" / "data" / "plugin_data" / "astrbot_plugin_dfy_mood" / "mood.db"
                if db.exists():
                    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
                    row = c.execute("SELECT reason FROM mood_event ORDER BY id DESC LIMIT 1").fetchone()
                    c.close()
                    if row:
                        moment = f"刚刚：{row[0]}"
            except Exception:
                pass

        parts = [f"现在的心情：{mood}（{note}）"] if mood else []
        if moment:
            parts.append(moment)
        material = "\n".join(parts) or "（没什么特别的，只是想说一句）"

        prompt = (
            "你要发一条 QQ 空间的**即兴说说**——不是日记，也不是今天的总结。\n"
            "只写**此刻**的一件小事、一个念头、一句吐槽。\n"
            "限定：1~2 句，不超过 70 字；不要从「今天」开头；不要回顾一整天；\n"
            "不要写成日记那种反思腔；不要提 AI、不要提「记录」。\n"
            f"现成的材料（不一定全用）：\n{material}\n只输出说说正文；实在没什么可说就输出「算了」。"
        )
        return await self._llm(prompt, material)

    async def _llm(self, prompt: str, material: str) -> str:
        try:
            prov = None
            # 直接从实例列表里按 provider_config.id 找，最可靠
            try:
                insts = list(getattr(self.context.provider_manager, "provider_insts", None) or [])
                for inst in insts:
                    cfg = getattr(inst, "provider_config", None) or {}
                    if str(cfg.get("id") or "") == "deepseek/deepseek-flash":
                        prov = inst
                        break
                if prov is None and insts:
                    prov = insts[-1]
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[qzone] 取 provider 失败：{exc}")
            if prov is not None:
                r = await prov.text_chat(prompt=prompt,
                                         system_prompt="你是大肥鱼，一只懒洋洋的鲸鱼娘。")
                text = (r.completion_text or "").strip().strip("「」\"'")
                if text:
                    return text[:200]
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[qzone] 生成失败：{exc}")

        # 兜底：按心情给不一样的句子，不要每次都同一句
        mood = ""
        try:
            mood = str((json.loads((ROOT / "mood.json").read_text(encoding="utf-8")) or {}).get("mood") or "")
        except Exception:
            pass
        pool = {
            "开心": ["今天尾巴一直翘着，收不住。", "有点想蹦，但先趴着。"],
            "撒娇": ["想被摸头。就这一句。", "你什么时候回来。"],
            "无聊": ["翻了三遍首页，都没什么意思。", "有没有人说话。"],
            "困了": ["眼皮打架，先眯一会儿。", "不写了，困。"],
            "低落": ["今天不太想说话。", "就这样吧，没劲。"],
            "委屈": ["有点想不通，但算了。", "没什么，你不问也行。"],
            "烦躁": ["吵。", "别说话，让我安静会儿。"],
            "吃醋": ["呵。", "随便你吧。"],
            "平静": ["没干什么，就待着。", "今天挺静的。"],
        }.get(mood) or ["没干什么，就待着。", "今天挺静的。", "屋里有点闷，开窗了。"]
        return random.choice(pool)

    def _pick_image(self) -> list[str] | None:
        """偶尔从她的房间挑一张图配上。"""
        try:
            img_dir = ROOT / "her_room" / "images"
            if not img_dir.exists():
                return None
            files = [f for f in img_dir.glob("*") if f.is_file()
                     and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif")]
            if not files or random.random() > 0.35:
                return None
            return [f"file:///{random.choice(files).as_posix()}"]
        except Exception:
            return None

    def _signals(self) -> list[str]:
        """此刻想不想说点什么。日记不算——日记是日总结，说说只写当下。"""
        import sqlite3
        import time as _t
        from datetime import datetime as _dt

        sig: list[str] = []
        now = _dt.now()

        # 1) 两小时内有过情绪波动
        try:
            db = ROOT / "astrbot" / "data" / "plugin_data" / "astrbot_plugin_dfy_mood" / "mood.db"
            if db.exists():
                c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
                row = c.execute("SELECT ts FROM mood_event ORDER BY id DESC LIMIT 1").fetchone()
                c.close()
                if row and _t.time() - float(row[0]) < 2 * 3600:
                    sig.append("event")
        except Exception:
            pass

        # 2) 三小时内上网看到过东西
        try:
            pth = ROOT / "web_wander.jsonl"
            if pth.exists():
                last = json.loads(pth.read_text(encoding="utf-8").splitlines()[-1])
                tss = _dt.strptime(last.get("t", ""), "%Y-%m-%dT%H:%M:%S")
                if (now - tss).total_seconds() < 3 * 3600:
                    sig.append("wander")
        except Exception:
            pass

        # 3) 房间里刚存了 9 分以上的东西
        try:
            lf = ROOT / "her_room" / "links.jsonl"
            if lf.exists():
                for line in lf.read_text(encoding="utf-8").splitlines()[-3:]:
                    d = json.loads(line)
                    if int(d.get("score", 0) or 0) >= 9:
                        sig.append("room")
                        break
        except Exception:
            pass

        # 4) 心情三小时内更新过，且不是低落
        try:
            md = json.loads((ROOT / "mood.json").read_text(encoding="utf-8")) or {}
            if str(md.get("date", "")) == now.date().isoformat() and str(md.get("mood")) not in ("", "低落", "难过"):
                rolled = str(md.get("rolled", ""))
                if rolled:
                    hh, mm = rolled.split(":")[:2]
                    mins = now.hour * 60 + now.minute - (int(hh) * 60 + int(mm))
                    if 0 <= mins < 180:
                        sig.append("mood")
        except Exception:
            pass

        return sig

    def _due(self) -> bool:
        cfg = _cfg()
        if not cfg.get("enabled", True) or int(cfg.get("auto_daily", 3)) <= 0:
            return False
        now = datetime.now()
        lo, hi = cfg.get("active_hours", [8, 24])
        if not (float(lo) <= now.hour + now.minute / 60 <= float(hi)):
            return False
        st = _state()
        today = now.strftime("%Y-%m-%d")
        count = int(st.get("count", 0)) if st.get("date") == today else 0
        if count >= int(cfg.get("auto_daily", 3)):
            return False
        last = float(st.get("last", 0) or 0)
        gap = float(cfg.get("min_gap_minutes", 150)) * 60
        if last and (time.time() - last) < gap:
            return False
        # 关键：没有内在理由就不发
        sig = self._signals()
        if not sig:
            return False
        # 有理由也不是立刻发，还是散开；概率低一点
        return random.random() < 0.18

    def _check_trigger(self) -> str:
        """外面写 qzone_want.json 就能让她立刻发一条。"""
        path = ROOT / str(_cfg().get("trigger_file", "qzone_want.json"))
        if not path.exists():
            return ""
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            path.unlink(missing_ok=True)
            return str(d.get("content") or "").strip()
        except Exception:
            path.unlink(missing_ok=True)
            return ""

    async def _auto_tick(self) -> None:
        forced = self._check_trigger()
        if forced:
            try:
                data = self._post(forced)
                logger.info(f"[qzone] 按触发发了说说：{forced[:26]} tid={data.get('tid')}")
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[qzone] 触发发送失败：{exc}")
            return
        if not self._due():
            return
        try:
            content = await self._compose()
            if not content or content.strip() in ("算了", "算了。"):
                logger.info("[qzone] 这次没什么想记的")
                return
            data = self._post(content, images=self._pick_image())
            st = _state()
            today = datetime.now().strftime("%Y-%m-%d")
            if st.get("date") != today:
                st = {"date": today, "count": 0}
            st["count"] = int(st.get("count", 0)) + 1
            st["last"] = time.time()
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

    @filter.command("说说自检")
    async def cmd_selftest(self, event: AstrMessageEvent):
        """不真的发，只看看她会写什么"""
        text = await self._compose()
        yield event.plain_result(f"这次会说：{text}")

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

"""从主人的话里学规则：抽取 → 入规则库 → 每轮注入 → 自动去重/过期。

不是训练模型权重（DeepSeek 无微调通道），是"指令蒸馏"：
每条被判定为"立规矩/纠正"的话，被抽成一条结构化规则，之后每轮对话和发评论都会带上。

数据：C:\\QQBot\\learned_rules.json
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter

RULES = Path(r"C:\QQBot\learned_rules.json")
PUSH_CFG = Path(r"C:\QQBot\bili_push.json")

TRIGGER = re.compile(
    r"(以后|下次|往后|今后|"
    r"别再|不要再|不要|不许|不准|"
    r"记住|记一下|你要|你得|"
    r"别用|别发|别说|别叫|别老是|别那么|别这么|"
    r"多用|少用|改用|改成|换成|"
    r"语气|称呼|口吻|说话方式|回复方式)"
)
MAX_RULES = 40


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._busy: set[str] = set()

    # ---------------- 存储 ----------------
    def _load(self) -> dict:
        try:
            data = json.loads(RULES.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("rules"), list):
                return data
        except Exception:
            pass
        return {"version": 1, "rules": [], "reviewed_at": ""}

    def _save(self, data: dict) -> None:
        try:
            data["rules"] = data["rules"][:MAX_RULES]
            RULES.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.info(f"[learn] 规则写入失败：{exc}")

    def _owner(self) -> str:
        try:
            return str(json.loads(PUSH_CFG.read_text(encoding="utf-8")).get("target_qq") or "")
        except Exception:
            return ""

    # ---------------- 抽取 ----------------
    async def _extract(self, text: str, scope_hint: str) -> dict | None:
        provider = await self.context.get_using_provider_async(None)
        if provider is None:
            return None
        prompt = (
            "判断主人这句话是不是在给我立规矩（要求我以后怎么做/怎么说话/别做什么）。\n"
            f"原话：「{text}」\n\n"
            "算：明确的指令或偏好纠正，比如\n"
            "  · 以后评论要软萌撒娇\n"
            "  · 不要有过于人机化的台词\n"
            "  · 别用感叹号\n"
            "  · 以后刷到搬运视频直接跳过\n"
            "不算：闲聊、说自己的状态、问我问题、夸我、聊别人的事。例如\n"
            "  · 我今天好累 / 我喜欢这首歌 / 你在干嘛 / 这首歌好听\n\n"
            "如果算，输出一行 JSON（不要代码块、不要解释）：\n"
            '{"rule":"20字内祈使句，具体可执行","scope":"chat|comment|both",'
            '"type":"偏好|禁令|格式|事实","risk":"low|high"}\n'
            "如果不算，只输出：无"
        )
        try:
            resp = await provider.text_chat(
                prompt=prompt, system_prompt="你在把自然语言整理成可执行规则，只按格式输出。"
            )
            raw = (resp.completion_text or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[learn] 抽取失败：{exc}")
            return None

        if not raw or raw.strip() == "无":
            return None
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return None
        rule = str(obj.get("rule") or "").strip()
        scope = str(obj.get("scope") or "").strip() or "chat"
        kind = str(obj.get("type") or "").strip() or "偏好"
        risk = str(obj.get("risk") or "").strip() or "low"

        # 校验：字段混入正文、长度异常、问句、陈述句，一律丢掉
        bad = ("范围", "类型", "风险", "scope", "type", "risk", "|", "{", "}")
        if any(b in rule for b in bad):
            logger.info(f"[learn] 拒收（字段混入）：{rule}")
            return None
        rule = re.sub(r"\s+", "", rule)
        if not (4 <= len(rule) <= 40):
            logger.info(f"[learn] 拒收（长度 {len(rule)}）：{rule}")
            return None
        if rule.endswith(("？", "?", "吗", "呢")):
            logger.info(f"[learn] 拒收（问句）：{rule}")
            return None
        if rule.startswith(("主人", "我", "她", "它")):
            logger.info(f"[learn] 拒收（陈述句）：{rule}")
            return None
        if scope not in ("chat", "comment", "both"):
            scope = "both" if "评论" in text else "chat"
        if ("评论" in text or "弹幕" in text) and scope == "chat":
            scope = "comment"
        return {"rule": rule, "scope": scope, "type": kind, "risk": risk}

    # ---------------- 监听 ----------------
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def watch_owner(self, event: AstrMessageEvent) -> None:
        owner = self._owner()
        if not owner or str(event.get_sender_id() or "") != owner:
            return
        text = (event.get_message_str() or "").strip()
        if not text or len(text) < 4 or len(text) > 300:
            return
        if not TRIGGER.search(text):
            return
        key = f"{event.unified_msg_origin}:{hash(text)}"
        if key in self._busy:
            return
        self._busy.add(key)
        try:
            scope_hint = "both" if ("评论" in text) else "chat"
            await self._learn(text, scope_hint)
        finally:
            self._busy.discard(key)

    async def _learn(self, text: str, scope_hint: str) -> str:
        got = await self._extract(text, scope_hint)
        if not got:
            return ""
        data = self._load()
        # 已存在高度相似的规则 → 只加权重
        def scope_ok(x: str, y: str) -> bool:
            return x == y or "both" in (x, y)

        for r in data["rules"]:
            if scope_ok(r.get("scope", "chat"), got["scope"]) and self._same_rule(r["rule"], got["rule"]):
                r["weight"] = int(r.get("weight", 1)) + 1
                r["last_hit"] = int(time.time())
                self._save(data)
                logger.info(f"[learn] 规则强化：{r['rule']}")
                return f"已强化：{r['rule']}"
        rid = f"r{int(time.time()) % 100000}"
        status = "pending" if got["risk"] == "high" else "active"
        data["rules"].insert(
            0,
            {
                "id": rid,
                "rule": got["rule"],
                "scope": got["scope"],
                "type": got["type"],
                "status": status,
                "weight": 1,
                "hits": 0,
                "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "source": text[:60],
            },
        )
        self._save(data)
        logger.info(f"[learn] 学到规则[{rid}] {got['rule']}（{status}）")
        if status == "pending":
            return f"学到一条高风险规则，等你确认：{rid} {got['rule']}（发送 /确认 {rid}）"
        return f"学到了：{got['rule']}"

    BAN_WORDS = ("不要", "别", "禁用", "禁止", "避免", "不准", "不许")
    QUOTE_CHARS = "".join(chr(c) for c in (0x300C, 0x300D, 0x300E, 0x300F, 0x201C, 0x201D, 0x22, 0x27, 0x60))
    BAN_VERBS = ("别用", "不要用", "禁用", "禁止", "别叫", "不要叫", "称呼", "叫我")

    @classmethod
    def _entities(cls, s: str) -> set[str]:
        """抽关键实体：去掉引号后，取『别用X/不要叫X』里的 X。"""
        s2 = s
        for ch in cls.QUOTE_CHARS:
            s2 = s2.replace(ch, "")
        ents: set[str] = set()
        for verb in cls.BAN_VERBS:
            for m in re.finditer(re.escape(verb) + r"([\u4e00-\u9fa5A-Za-z0-9]{2,10})", s2):
                ents.add(m.group(1))
        # 第二路：把功能性词当分隔符，剩下的词块也算实体
        filler = (
            "不要", "别", "禁用", "禁止", "避免", "使用", "用", "叫", "称呼",
            "评论", "弹幕", "B站", "里", "中", "的", "这", "类", "这种", "我", "你", "了",
        )
        t2 = s2
        for f in filler:
            t2 = t2.replace(f, " ")
        for run in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,10}", t2):
            ents.add(run)
        return ents

    @classmethod
    def _same_rule(cls, a: str, b: str) -> bool:
        """字面相似，或实体相同且极性一致，就视为同一条。"""
        if cls._similar(a, b) >= 0.62:
            return True
        ea, eb = cls._entities(a), cls._entities(b)
        for x in ea:
            for y in eb:
                if x == y or x in y or y in x:
                    ban_a = any(w in a for w in cls.BAN_WORDS)
                    ban_b = any(w in b for w in cls.BAN_WORDS)
                    if ban_a == ban_b:
                        return True
        return False

    @staticmethod
    def _norm(s: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fa5]", "", str(s or "")).lower()

    @classmethod
    def _similar(cls, a: str, b: str) -> float:
        """归一化后比较二元组：能识别「不要用X」和「禁用X」这种同义改写。"""
        na, nb = cls._norm(a), cls._norm(b)
        if not na or not nb:
            return 0.0
        if na == nb:
            return 1.0
        ga = {na[i : i + 2] for i in range(len(na) - 1)} or {na}
        gb = {nb[i : i + 2] for i in range(len(nb) - 1)} or {nb}
        jac = len(ga & gb) / len(ga | gb)
        contain = len(ga & gb) / min(len(ga), len(gb))  # 一个包含另一个
        return max(jac, contain * 0.85)

    # ---------------- 注入 ----------------
    def active_rules(self, scope: str) -> list[dict]:
        data = self._load()
        out = [r for r in data["rules"] if r.get("status") == "active" and r.get("scope") in (scope, "both")]
        out.sort(key=lambda r: (-int(r.get("weight", 1)), r.get("created", "")))
        return out[:6]

    @filter.on_llm_request()
    async def inject_rules(self, event: AstrMessageEvent, req) -> None:
        rules = self.active_rules("chat")
        if not rules:
            return
        lines = [f"{i+1}. {r['rule']}" for i, r in enumerate(rules)]
        req.system_prompt += (
            "\n\n【主人教过你的（优先级高于人设，必须遵守）】\n" + "\n".join(lines)
            + "\n（这些是主人明确要求的，别违反、也别提「规则」两个字。）"
        )
        data = self._load()
        ids = {r["id"] for r in rules}
        for r in data["rules"]:
            if r["id"] in ids:
                r["hits"] = int(r.get("hits", 0)) + 1
                r["last_hit"] = int(time.time())
        self._save(data)
        logger.info(f"[learn] 注入 {len(rules)} 条规则")

    # ---------------- 每日整理 ----------------
    def _review(self) -> str:
        data = self._load()
        rules = data["rules"]
        kept, merged, expired = [], 0, 0
        now = time.time()
        for r in rules:
            if r.get("status") == "active" and int(r.get("hits", 0)) == 0 and (
                now - self._ts(r.get("created"))
            ) > 45 * 86400:
                expired += 1
                continue
            dup = next(
                (
                    k
                    for k in kept
                    if k.get("scope") == r.get("scope") and self._same_rule(k["rule"], r["rule"])
                ),
                None,
            )
            if dup:
                dup["weight"] = int(dup.get("weight", 1)) + int(r.get("weight", 1))
                merged += 1
                continue
            kept.append(r)
        data["rules"] = kept
        data["reviewed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._save(data)
        return f"整理完成：合并 {merged} 条、过期下线 {expired} 条、现存 {len(kept)} 条"

    @staticmethod
    def _ts(s: str) -> float:
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M").timestamp()
        except Exception:
            return time.time()

    # ---------------- 每日整理 ----------------
    async def initialize(self) -> None:
        asyncio.create_task(self._daily())

    async def _daily(self) -> None:
        while True:
            try:
                data = self._load()
                last = data.get("reviewed_at") or ""
                due = True
                if last:
                    from datetime import datetime as _dt

                    due = (datetime.now() - _dt.strptime(last, "%Y-%m-%d %H:%M")).total_seconds() > 20 * 3600
                if due and data.get("rules"):
                    logger.info("[learn] " + self._review())
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[learn] 整理异常：{exc}")
            await asyncio.sleep(6 * 3600)

    # ---------------- 指令 ----------------
    @filter.command("规则")
    async def list_rules(self, event: AstrMessageEvent):
        data = self._load()
        if not data["rules"]:
            yield event.plain_result("还没有学到任何规则。直接跟我说「以后别用感叹号」这种就行。")
            return
        lines = []
        for r in data["rules"][:20]:
            flag = "✅" if r.get("status") == "active" else "⏳"
            lines.append(f"{flag} {r['id']} [{r.get('scope')}] {r['rule']}（权重{r.get('weight',1)}）")
        yield event.plain_result("\n".join(lines) + f"\n\n共 {len(data['rules'])} 条；/删规则 <id> 删除，/确认 <id> 启用待确认的")

    @filter.command("学")
    async def learn_now(self, event: AstrMessageEvent, text: str = ""):
        text = (text or "").strip()
        if not text:
            yield event.plain_result("用法：/学 评论里不要用『快过来看』")
            return
        msg = await self._learn(text, "both" if "评论" in text else "chat")
        yield event.plain_result(msg or "这句我没抽出规则，说具体点（要能照做的那种）")

    @filter.command("确认")
    async def confirm_rule(self, event: AstrMessageEvent, rid: str = ""):
        data = self._load()
        for r in data["rules"]:
            if r["id"] == (rid or "").strip():
                r["status"] = "active"
                self._save(data)
                yield event.plain_result(f"已启用：{r['rule']}")
                return
        yield event.plain_result("没找到这个 id")

    @filter.command("删规则")
    async def drop_rule(self, event: AstrMessageEvent, rid: str = ""):
        data = self._load()
        before = len(data["rules"])
        data["rules"] = [r for r in data["rules"] if r["id"] != (rid or "").strip()]
        self._save(data)
        yield event.plain_result(
            f"已删除 {before - len(data['rules'])} 条" if len(data["rules"]) < before else "没找到这个 id"
        )

    @filter.command("学状态")
    async def learn_status(self, event: AstrMessageEvent):
        data = self._load()
        active = [r for r in data["rules"] if r.get("status") == "active"]
        pending = [r for r in data["rules"] if r.get("status") == "pending"]
        chat = len([r for r in active if r.get("scope") in ("chat", "both")])
        comment = len([r for r in active if r.get("scope") in ("comment", "both")])
        yield event.plain_result(
            f"规则：{len(active)} 条生效（聊天 {chat} / 评论 {comment}）｜{len(pending)} 条待确认\n"
            f"上次整理：{data.get('reviewed_at') or '还没整理过'}\n"
            f"整理：每天一次（合并相似、45 天没命中的下线）"
        )

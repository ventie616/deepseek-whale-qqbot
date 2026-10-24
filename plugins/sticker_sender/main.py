import base64
import hashlib
import json
import random
import time
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain

STICKER_DIR = Path(r"C:\QQBot\stickers")
MISC_DIR = STICKER_DIR / "misc"
INDEX_PATH = STICKER_DIR / "index.json"
PENDING_DIR = Path(r"C:\QQBot\sticker_inbox\_pending")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_BYTES = 3 * 1024 * 1024
PROBABILITY = 0.35
# 优先用「大肥鱼」本体表情包（kiriyuki.com，CC BY-NC 4.0），她自己的形象更贴人设
PREFER_PACK = "nekowhale"
PREFER_WEIGHT = 0.8
# 语义检索：用回复文本和 use/tags/emotion 的字符二元组重合度加权
SEMANTIC_ON = True
SEMANTIC_WEIGHT = 8.0
SEMANTIC_TOP = 8
# 回复里出现这些词 -> 优先找带对应标签的图（短句对不上长 use，靠这个补）
SYNONYMS = {
    "谢谢": ["感谢", "道谢", "谢谢", "谢啦"],
    "感谢": ["感谢", "道谢", "谢谢"],
    "夸": ["求夸", "被夸", "夸我", "表扬"],
    "骂": ["挨骂", "被骂", "骂我", "道歉", "对不起"],
    "对不起": ["道歉", "对不起", "认错"],
    "困": ["困", "睡觉", "晚安", "累"],
    "睡": ["睡觉", "困", "晚安"],
    "累": ["累", "疲惫", "摆烂", "摸鱼"],
    "难过": ["难过", "委屈", "安慰", "抱抱"],
    "委屈": ["委屈", "难过", "安慰"],
    "哭": ["哭", "含泪", "委屈"],
    "生气": ["生气", "不爽", "炸毛", "记仇"],
    "喜欢": ["喜欢", "贴贴", "爱心", "比心"],
    "生气": ["生气", "不爽", "炸毛"],
    "吃": ["吃", "干饭", "白饭", "饿了"],
    "饿": ["吃", "干饭", "饿了"],
    "加班": ["加班", "上班", "赶稿", "社畜"],
    "摸鱼": ["摸鱼", "偷懒", "摆烂", "上班"],
}
PENDING_TTL = 24 * 3600  # 暂存 24 小时：避免重启/隔天就丢
AUTO_TAG = True  # 新存的图用视觉模型自动打标

MAGIC = [
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"BM", ".bmp"),
]

# 回复情绪 -> (候选主情绪, 关键词, 基础强度)
FAMILIES = {
    "angry": {
        "moods": ["生气", "暴怒", "抓狂", "嘲讽", "吐槽", "摆烂", "嚣张", "拒绝"],
        "kw": ["哼", "生气", "讨厌", "烦", "可恶", "闭嘴", "揍", "再说一遍", "叫谁", "不服", "欠", "滚",
               "气死", "烦死", "受不了", "讨厌死", "臭", "傻", "骂", "无赖", "欠揍"],
        "base": 3,
    },
    "sad": {
        "moods": ["难过", "委屈", "破防", "卖惨", "含泪", "忧郁", "尴尬", "慌张", "紧张", "道歉", "安慰"],
        "kw": ["呜", "难过", "伤心", "哭", "委屈", "算了", "没吃饱", "可怜", "不要"],
        "base": 2,
    },
    "shy": {
        "moods": ["害羞", "含泪", "喜欢"],
        "kw": ["害羞", "好羞", "脸红", "才不", "真的吗", "不好意思", "讨厌啦", "比心", "心动"],
        "base": 3,
    },
    "sleepy": {
        "moods": ["困", "累", "无语", "疲惫", "呆滞"],
        "kw": ["困", "晚安", "睡", "累了", "躺", "眯", "哈欠"],
        "base": 2,
    },
    "happy": {
        "moods": ["开心", "大笑", "满足", "得意", "卖萌", "搞笑", "沙雕", "平静",
                  "疑惑", "调皮", "乖巧", "自信", "期待", "偷瞄",
                  "憨", "搞怪", "兴奋", "思考", "打招呼", "感谢"],
        "kw": ["好耶", "哈哈", "嘿嘿", "开心", "太好了", "笑", "耶", "棒", "收到", "吃饱"],
        "base": 2,
    },
}
HARSH = ["气死", "滚", "烦死", "受不了", "讨厌死", "闭嘴", "臭婊", "傻逼", "妈的", "贱"]


def _guess_ext(data: bytes) -> str:
    for magic, ext in MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._pending: dict[str, dict] = {}
        self._cache_mtime = 0.0
        self._cache_items: list[dict] = []
        self._cleanup_pending()

    def _cleanup_pending(self) -> None:
        try:
            if not PENDING_DIR.is_dir():
                return
            cutoff = time.time() - PENDING_TTL
            for p in PENDING_DIR.glob("*"):
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
        except OSError:
            pass

    # ---------------- 索引 ----------------
    def _index(self) -> list[dict]:
        try:
            mtime = INDEX_PATH.stat().st_mtime
        except OSError:
            return []
        if mtime != self._cache_mtime:
            try:
                data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
                self._cache_items = data.get("items") or []
                self._cache_mtime = mtime
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[sticker] 索引读取失败：{exc}")
                return []
        return self._cache_items

    def _save_index(self, items: list[dict]) -> None:
        try:
            INDEX_PATH.write_text(
                json.dumps({"version": 1, "items": items}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._cache_items = items
            self._cache_mtime = INDEX_PATH.stat().st_mtime
        except OSError as exc:
            logger.info(f"[sticker] 索引写入失败：{exc}")

    # ---------------- 选图：主情绪 -> 安全 -> 强度 -> 场景 ----------------
    @staticmethod
    def _family(text: str) -> str | None:
        for fam, cfg in FAMILIES.items():
            if any(w in text for w in cfg["kw"]):
                return fam
        return None

    @staticmethod
    def _intensity(text: str, base: int) -> int:
        v = base
        if any(w in text for w in HARSH):
            v += 2
        if "！" in text or "!" in text:
            v += 1
        if len(text) <= 5 and not any(w in text for w in HARSH):
            v -= 1
        return max(1, min(5, v))

    @staticmethod
    def _bigrams(s: str) -> set[str]:
        s = "".join(ch for ch in s if ch.strip())
        return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}

    def _semantic_pool(self, text: str, items: list[dict]) -> list[tuple[float, dict]]:
        """按 use/tags/emotion 的字符重合度给候选打分（中文不需要分词）。"""
        q = self._bigrams(text)
        if not q:
            return []
        out = []
        for e in items:
            blob = " ".join([
                str(e.get("use") or ""),
                str(e.get("desc") or "")[:40],
                " ".join(str(x) for x in (e.get("tags") or [])),
                str(e.get("emotion") or ""),
                str(e.get("text") or ""),
            ])
            b = self._bigrams(blob)
            if not b:
                continue
            inter = len(q & b)
            jac = inter / (len(q) + len(b) - inter) if inter else 0.0
            hit = sum(1 for tg in (e.get("tags") or []) if tg and str(tg) in text)
            score = jac * 12 + hit * 3
            # 同义词：回复里有"谢谢"就找 tags 带"感谢"的图（不受二元组交集限制）
            blob2 = blob + " " + str(e.get("emotion") or "")
            for kw, wants in SYNONYMS.items():
                if kw in text and any(w in blob2 for w in wants):
                    score += 6.0
            if score > 0.4:
                out.append((score, e))
        out.sort(key=lambda x: -x[0])
        return out[:SEMANTIC_TOP]

    def _pick(self, text: str, is_private: bool = True) -> Path | None:
        items = self._index()
        if not items:
            return self._pick_fallback()

        fam = self._family(text)
        target_moods = FAMILIES[fam]["moods"] if fam else None
        target_int = self._intensity(text, FAMILIES[fam]["base"] if fam else 2)

        # 1) 安全等级：群里不出 risky
        pool = [e for e in items if is_private or e.get("safety") != "risky"]
        if not pool:
            pool = items

        # 2) 主情绪
        by_mood = [e for e in pool if target_moods and e.get("mood") in target_moods]
        if by_mood:
            pool = by_mood

        # 3) 强度：先 ±1，不行再放宽
        for tol in (1, 2, 5):
            narrowed = [e for e in pool if abs(int(e.get("intensity", 3)) - target_int) <= tol]
            if narrowed:
                pool = narrowed
                break

        # 4) 场景加权（+ 优先本体表情包）
        def weight(e: dict) -> float:
            w = 1.0
            for scene in e.get("scenes") or []:
                if scene and scene in text:
                    w += 2.0
            if e.get("pack") == PREFER_PACK:
                w *= 1.0 + PREFER_WEIGHT * 4  # 本体表情权重高 5 倍左右
            return w

        # 5) 语义加权：把 use/tags 跟回复文本对得上的图提上来
        sem: dict[str, float] = {}
        if SEMANTIC_ON:
            for sc, e in self._semantic_pool(text, pool):
                sem[str(e.get("file"))] = sc
            if not sem:
                for sc, e in self._semantic_pool(text, items):
                    sem[str(e.get("file"))] = sc
                    if e not in pool:
                        pool.append(e)

        def weight2(e: dict) -> float:
            return weight(e) + SEMANTIC_WEIGHT * sem.get(str(e.get("file")), 0.0)

        # 语义强命中：70% 直接在最像的 3 张里挑，避免被 500 张的基数稀释
        strong = [e for sc, e in self._semantic_pool(text, items)[:3] if sc >= 6.0]
        if strong and random.random() < 0.7:
            chosen = random.choice(strong)
        else:
            try:
                chosen = random.choices(pool, weights=[max(0.01, weight2(e)) for e in pool], k=1)[0]
            except Exception:
                chosen = random.choice(pool)
        path = STICKER_DIR / str(chosen.get("file") or "")
        if not path.exists():
            return self._pick_fallback()
        logger.info(
            f"[sticker] {fam or '任意'} 强度{target_int} -> {chosen.get('mood')}/{chosen.get('pack') or 'meme'}"
            f" 语义{sem.get(str(chosen.get('file')), 0):.1f}｜{str(chosen.get('use') or '')[:24]}"
            f"/{(chosen.get('tags') or [''])[0]} 强度{chosen.get('intensity')} "
            f"{chosen.get('safety')}"
        )
        return path

    def _pick_fallback(self) -> Path | None:
        pool = [
            p
            for p in STICKER_DIR.rglob("*")
            if p.is_file()
            and p.suffix.lower() in IMAGE_EXT
            and "_imported" not in p.parts
            and not p.name.startswith("_")
            and p.stat().st_size <= MAX_BYTES
        ]
        return random.choice(pool) if pool else None

    @filter.on_decorating_result()
    async def add_sticker(self, event: AstrMessageEvent) -> None:
        """按五维索引给回复配一张表情包（独立成一条消息）。"""
        try:
            result = event.get_result()
            if result is None or not result.chain:
                return
            plains = [c for c in result.chain if isinstance(c, Plain)]
            if not plains:
                return
            text = "".join(c.text for c in plains).strip()
            if not text:
                return
            if random.random() > PROBABILITY:
                return
            try:
                is_private = bool(event.is_private_chat())
            except Exception:
                is_private = True
            path = self._pick(text, is_private=is_private)
            if path is None:
                return
            result.chain.append(Image.fromFileSystem(str(path)))
        except Exception:
            pass

    # ---------------- 收图：暂存，等主人发话再入库 ----------------
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def stage_incoming_images(self, event: AstrMessageEvent) -> None:
        try:
            images = [c for c in event.get_messages() if isinstance(c, Image)]
        except Exception:
            return
        if not images:
            return

        logger.info(
            "[看图诊断] 收到 %d 张图 | 引用: %s",
            len(images),
            [str(getattr(c, "file", "") or getattr(c, "url", ""))[:48] for c in images],
        )

        paths: list[str] = []
        for img in images:
            try:
                src = Path(await img.convert_to_file_path())
                data = src.read_bytes()
            except Exception:
                continue
            if len(data) < 512:
                continue
            digest = hashlib.sha256(data).hexdigest()[:16]
            ext = _guess_ext(data)
            try:
                PENDING_DIR.mkdir(parents=True, exist_ok=True)
                dst = PENDING_DIR / f"p_{int(time.time() * 1000)}_{digest}{ext}"
                if not dst.exists():
                    dst.write_bytes(data)
                paths.append(str(dst))
            except OSError:
                continue

        if paths:
            umo = event.unified_msg_origin
            old = self._pending.get(umo) or {}
            merged = list(dict.fromkeys([*(old.get("paths") or []), *paths]))
            self._pending[umo] = {"paths": merged, "at": time.time()}
            logger.info(
                f"[sticker] 暂存 {len(paths)} 张（该会话待保存 {len(merged)} 张），等主人发话再入库"
            )

    @filter.on_llm_request()
    async def hint_pending_images(self, event: AstrMessageEvent, req) -> None:
        item = self._pending.get(event.unified_msg_origin)
        if not item or not item.get("paths"):
            return
        n = len(item["paths"])
        req.system_prompt += (
            f"\n\n【系统提示·表情包】主人刚发来 {n} 张图片，已经暂存但**还没有**存进表情包库。\n"
            "- 不要自动保存，也不要声称已经保存。\n"
            "- 只有当主人明确表示要存（例如「保存」「收下」「存着」「收藏一下」）时，"
            "才调用 save_sticker 工具入库，然后回一句简短确认。\n"
            "- 主人没提保存，就当普通图片正常聊；想不起来可以顺口问一句要不要收。"
        )

    # ---------------- 入库 + 自动打标 ----------------
    async def _auto_tag(self, path: Path) -> dict:
        """让视觉模型给新表情包打五维标签，失败就用默认值。"""
        fallback = {
            "mood": "开心",
            "tags": ["未分类"],
            "intensity": 3,
            "scenes": ["闲聊"],
            "safety": "safe",
        }
        if not AUTO_TAG:
            return fallback
        try:
            provider = await self.context.get_using_provider_async(None)
            if provider is None:
                return fallback
            b64 = base64.b64encode(path.read_bytes()).decode()
            ext = path.suffix.lower().lstrip(".") or "png"
            prompt = (
                "这是一张 QQ 表情包。按这个 JSON 格式输出，不要解释：\n"
                '{"mood":"主情绪，中文两字，如 开心/生气/难过/害羞/困/嘲讽/无语",'
                '"tags":["2到4个中文短标签"],"intensity":1到5的整数,'
                '"scenes":["1到3个使用场景，如 被夸/被骂/晚安/群聊玩梗/撒娇"],'
                '"safety":"safe 或 mild 或 risky（含脏话、擦边、攻击性写 risky）"}'
            )
            resp = await provider.text_chat(
                prompt=prompt,
                system_prompt="你在给表情包打标签，只输出 JSON。",
                image_urls=[f"data:image/{ext};base64,{b64}"],
            )
            raw = (resp.completion_text or "").strip()
            start, end = raw.find("{"), raw.rfind("}")
            if start < 0 or end < 0:
                return fallback
            data = json.loads(raw[start:end + 1])
            safety = str(data.get("safety") or "safe")
            return {
                "mood": str(data.get("mood") or fallback["mood"])[:6],
                "tags": [str(t)[:8] for t in (data.get("tags") or [])][:4] or ["未分类"],
                "intensity": max(1, min(5, int(data.get("intensity") or 3))),
                "scenes": [str(s)[:10] for s in (data.get("scenes") or [])][:3] or ["闲聊"],
                "safety": safety if safety in ("safe", "mild", "risky") else "safe",
            }
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[sticker] 自动打标失败，用默认值：{exc}")
            return fallback

    @filter.llm_tool(name="save_sticker")
    async def save_sticker(self, event: AstrMessageEvent, count: int = 0):
        """把主人刚发来的图片保存进表情包库。仅当主人明确说要保存/收藏时才调用。

        Args:
            count(number): 保存几张，0 表示全部
        """
        item = self._pending.get(event.unified_msg_origin) or {}
        paths = list(item.get("paths") or [])
        if not paths:
            return "现在没有待保存的图片"
        if count and count > 0:
            paths = paths[:count]

        saved = 0
        stay: list[str] = []
        items = self._index()
        for p in paths:
            src = Path(p)
            try:
                data = src.read_bytes()
            except OSError:
                continue
            digest = hashlib.sha256(data).hexdigest()[:16]
            target = MISC_DIR / f"qq_{digest}{_guess_ext(data)}"
            try:
                MISC_DIR.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.write_bytes(data)
                    saved += 1
                    meta = await self._auto_tag(target)
                    items.append({"file": f"misc/{target.name}", **meta})
                src.unlink(missing_ok=True)
            except OSError:
                stay.append(p)

        if saved:
            self._save_index(items)
        left = [x for x in (item.get("paths") or []) if x not in paths] + stay
        if left:
            self._pending[event.unified_msg_origin] = {"paths": left, "at": time.time()}
        else:
            self._pending.pop(event.unified_msg_origin, None)

        if saved:
            logger.info(f"[sticker] 入库 {saved} 张并打标 -> {INDEX_PATH}")
            return f"收进库里了 {saved} 张，标签也打好了"
        return "这些图库里已经都有了"

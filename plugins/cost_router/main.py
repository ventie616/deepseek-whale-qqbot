"""省钱路由：简单闲聊走 flash、不相关时裁掉工具，并把易变注入挪到用户消息后。

三条一起做：
1. 简单消息（问候/短回应）→ req.model 换 flash，工具清空（少发几千 token 的 schema）
2. 普通消息 → 按关键词裁工具（没提 B站 就不发 bili_* 那堆）
3. 易变注入（记忆/心情/日记/兴趣）挂到 extra_user_content_parts，
   让 system_prompt 保持稳定，前缀缓存命中率更高
"""

from __future__ import annotations

import json
from pathlib import Path
import re

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.agent.tool import ToolSet

CFG_PATH = Path(r"D:\\QQrobot\\cost_router.json")


def _cfg():
    try:
        return json.loads(CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


FLASH_MODEL = "deepseek-flash"
PRO_MODEL = None          # None = 用配置里的默认（v4-pro）

SIMPLE_PAT = re.compile(
    r"^(在吗|在么|在不在|你好|您好|嗨|hi|hello|早|早上好|早安|晚安|晚安啦|睡了|我睡了|"
    r"拜拜|再见|哈+|嗯+|哦+|好|好的|行|ok|谢谢|多谢|收到|知道了|吃了吗|吃没|干嘛呢|"
    r"困了|累了|想你|想我了吗)[呀啊哈呢吧。！!?？~～\s]*$",
    re.I,
)

# 工具名（或前缀）-> 命中这些词才发
TOOL_TRIGGERS: dict[str, tuple[str, ...]] = {
    "bili_": ("B站", "b站", "视频", "刷", "热门", "排行", "up主", "UP主", "推荐", "投稿"),
    "anime_lookup": ("番", "剧", "动画", "动漫"),
    "sing_a_clip": ("唱", "歌"),
    "write_diary": ("日记",),
    "read_diary": ("日记",),
    "my_interests": ("兴趣", "喜欢", "爱看"),
    "keep_video": ("留着", "记住这个", "收藏", "记下"),
    "save_sticker": ("表情", "贴图", "这个图"),
    "timeline": ("时间线", "今天干", "上午", "下午都", "昨晚"),
    "browse_history": ("浏览记录",),
    "group_recap": ("群里", "大家", "总结"),
    "web_": ("搜", "查一下", "查查", "查查资料", "帮我查", "新闻", "最新", "官网", "网址", "链接", "是什么", "谁是", "怎么样", "为什么", "多少", "哪年", "什么时候", "百科", "资料", "了解", "介绍", "出处", "来源"),
}

CORE_KEEP = {"search_chat", "who_is", "remember_member"}


def _tool_name(tool) -> str:
    return str(getattr(tool, "name", "") or "")


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._last_model = None

    @filter.on_waiting_llm_request()
    async def force_provider(self, event) -> None:
        """force_flash 时，在选模型之前就把 provider 换掉。"""
        if _cfg().get("force_flash"):
            event.set_extra("selected_provider", "deepseek/deepseek-flash")

    @filter.on_llm_request()
    async def route(self, event: AstrMessageEvent, req) -> None:
        text = str(getattr(req, "prompt", "") or "").strip()
        has_media = bool(getattr(req, "image_urls", None)) or bool(getattr(req, "audio_urls", None))

        # 0) 全部走 flash（试线路用）
        if _cfg().get("force_flash"):
            req.model = FLASH_MODEL
            if self._last_model != "flash-forced":
                logger.info(f"[cost] 强制 flash（{len(text)} 字）")
                self._last_model = "flash-forced"
            return

        # 1) 简单闲聊 -> flash，且不需要工具
        simple = (
            len(text) <= 16
            and not has_media
            and (SIMPLE_PAT.match(text) is not None or len(text) <= 3)
        )
        if simple:
            req.model = FLASH_MODEL
            if getattr(req, "func_tool", None):
                req.func_tool = ToolSet()
            if self._last_model != "flash":
                logger.info(f"[cost] 简单消息走 flash（{text[:12]}）工具已清空")
                self._last_model = "flash"
            return

        # 2) 普通消息 -> pro，按关键词裁工具
        req.model = PRO_MODEL
        keep, dropped = [], []
        tools = list(getattr(getattr(req, "func_tool", None), "tools", []) or [])
        for tool in tools:
            name = _tool_name(tool)
            need = name in CORE_KEEP
            if not need:
                for prefix, keys in TOOL_TRIGGERS.items():
                    if name == prefix or name.startswith(prefix):
                        need = any(k.lower() in text.lower() for k in keys)
                        break
                else:
                    need = True      # 没配规则的工具默认保留
            (keep if need else dropped).append(tool)
        if dropped and keep:
            req.func_tool = ToolSet(tools=keep)
        sys_len = len(str(getattr(req, "system_prompt", "") or ""))
        extra = len(getattr(req, "extra_user_content_parts", None) or [])
        if self._last_model != "pro":
            logger.info(
                f"[cost] 走 pro；工具保留 {len(keep)}/{len(tools)}；"
                f"system={sys_len} 字 / 额外块 {extra} 个 / 消息 {len(text)} 字"
                + (f"；裁掉 {[_tool_name(x) for x in dropped][:6]}" if dropped else "")
            )
            self._last_model = "pro"

"""记忆备份：每天把关键记忆文件复制到 backup/日期/，保留 14 天。

对着"数字生命清单"的第六条（可靠性：别让一次故障轻易抹去经历）。

- 每 6 小时检查一次，当天还没备份就备一份
- 保留 14 天，自动清理更早的
- 指令：/备份 立刻备一份并报告
补回来：把 backup/日期/ 里的文件拷回 C:\\QQBot 对应位置，重启 AstrBot。
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter

ROOT = Path(r"C:\QQBot")
BACKUP = ROOT / "backup"
KEEP_DAYS = 14

TARGETS = [
    ("bili_memory.json", "B站长期记忆"),
    ("bili_recent.json", "B站短期池"),
    ("bili_watched.jsonl", "看过的视频"),
    ("bili_activity.jsonl", "B站往来"),
    ("group_memory.json", "群/私聊摘要与人物卡"),
    ("learned_rules.json", "学到的规则"),
    ("mood.json", "当天心情"),
    ("persona/whale-girl.md", "人格真源"),
    ("voice_clips/index.json", "唱歌素材索引"),
    ("stickers/index.json", "表情索引"),
    ("proactive_dm_state.json", "主动私聊状态"),
]


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._stop = False
        self._task: asyncio.Task | None = None

    # ---------------- 备份 ----------------
    def _run_backup(self, force: bool = False) -> dict:
        day = date.today().isoformat()
        dst = BACKUP / day
        if dst.exists() and not force:
            return {"skipped": True, "day": day, "dir": str(dst)}

        dst.mkdir(parents=True, exist_ok=True)
        copied, missing, total = [], [], 0
        for rel, label in TARGETS:
            src = ROOT / rel
            if not src.is_file():
                missing.append(rel)
                continue
            out = dst / rel.replace("/", "__")
            try:
                shutil.copy2(src, out)
                copied.append({"file": rel, "label": label, "kb": round(out.stat().st_size / 1024, 1)})
                total += out.stat().st_size
            except OSError as exc:
                missing.append(f"{rel}({exc})")

        manifest = {
            "day": day,
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "files": copied,
            "missing": missing,
            "kb": round(total / 1024, 1),
        }
        (dst / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 清理过期
        cutoff = date.today() - timedelta(days=KEEP_DAYS)
        for d in sorted(BACKUP.iterdir()):
            if not d.is_dir():
                continue
            try:
                if date.fromisoformat(d.name) < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
            except ValueError:
                continue

        logger.info(f"[backup] 已备份 {len(copied)} 个文件 / {manifest['kb']} KB -> {dst}")
        return manifest

    def _overview(self) -> dict:
        dirs = sorted([d for d in BACKUP.iterdir() if d.is_dir()]) if BACKUP.exists() else []
        size = sum(f.stat().st_size for f in BACKUP.rglob("*") if f.is_file()) if BACKUP.exists() else 0
        return {"count": len(dirs), "latest": dirs[-1].name if dirs else "", "mb": round(size / 1024 / 1024, 2)}

    # ---------------- 循环 ----------------
    async def _loop(self) -> None:
        await asyncio.sleep(60)
        while not self._stop:
            try:
                await asyncio.to_thread(self._run_backup)
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[backup] 备份失败：{exc}")
            await asyncio.sleep(6 * 3600)

    async def initialize(self) -> None:
        self._task = asyncio.create_task(self._loop())
        try:
            info = await asyncio.to_thread(self._run_backup)
            logger.info(f"[backup] 启动即备份：{info.get('dir') or '当天已有'}")
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[backup] 首次备份失败：{exc}")

    async def terminate(self) -> None:
        self._stop = True
        if self._task:
            self._task.cancel()

    # ---------------- 指令 ----------------
    @filter.command("备份")
    async def backup_cmd(self, event: AstrMessageEvent, act: str = ""):
        """立刻备份记忆（/备份 可强制重备当天）"""
        force = (act or "").strip() in ("现在", "重备", "force")
        info = await asyncio.to_thread(self._run_backup, force)
        if info.get("skipped"):
            ov = self._overview()
            yield event.plain_result(
                f"今天已经备过：{info['dir']}\n共 {ov['count']} 份备份 / {ov['mb']} MB\n"
                "要重备就说 /备份 重备"
            )
            return
        ov = self._overview()
        miss = info.get("missing") or []
        text = (
            f"备份完成：{len(info.get('files') or [])} 个文件 / {info.get('kb')} KB\n"
            f"位置：{info.get('day')}\\ ｜ 现有 {ov['count']} 份 / {ov['mb']} MB\n"
            f"保留 {KEEP_DAYS} 天"
        )
        if miss:
            text += f"\n（缺 {len(miss)} 个：{', '.join(miss[:3])}）"
        yield event.plain_result(text)

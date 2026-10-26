"""补齐视频分区信息：bvid -> {tname, up, tid}，缓存到 bili_meta.json。

浏览记录里只有标题和 UP，兴趣画像要按「分区」切，所以把 tname 补出来。
只补缺的，已有缓存不重复请求。
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(r"C:\QQBot")
WATCHED = ROOT / "bili_watched.jsonl"
MEMORY = ROOT / "bili_memory.json"
RECENT = ROOT / "bili_recent.json"
META = ROOT / "bili_meta.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def collect_bvids() -> list[str]:
    out: list[str] = []
    if WATCHED.exists():
        for line in WATCHED.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("bvid"):
                out.append(str(r["bvid"]))
    for path in (MEMORY, RECENT):
        for m in _load(path, []):
            if isinstance(m, dict) and m.get("bvid"):
                out.append(str(m["bvid"]))
    seen, uniq = set(), []
    for b in out:
        if b not in seen:
            seen.add(b)
            uniq.append(b)
    return uniq


def fetch(bvid: str) -> dict:
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": f"https://www.bilibili.com/video/{bvid}",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read())
    if d.get("code") != 0:
        return {}
    data = d["data"]
    tags = []
    try:
        tr = urllib.request.Request(
            f"https://api.bilibili.com/x/tag/archive/tags?bvid={bvid}",
            headers={"User-Agent": UA, "Referer": f"https://www.bilibili.com/video/{bvid}"},
        )
        td = json.loads(urllib.request.urlopen(tr, timeout=20).read())
        if td.get("code") == 0:
            tags = [str(x.get("tag_name") or "") for x in (td.get("data") or [])][:8]
    except Exception:
        pass
    time.sleep(0.15)
    return {
        "tname": data.get("tname") or "",
        "tid": data.get("tid") or 0,
        "up": (data.get("owner") or {}).get("name") or "",
        "tags": [x for x in tags if x],
    }


def main() -> int:
    cache = _load(META, {})
    bvids = collect_bvids()
    missing = [b for b in bvids if b not in cache or not cache[b].get("tags")]
    print(f"共 {len(bvids)} 个 video，缺 {len(missing)} 个")
    ok = 0
    for i, b in enumerate(missing, 1):
        try:
            info = fetch(b)
            if info:
                cache[b] = info
                ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  {b} 失败：{exc}")
        if i % 20 == 0:
            print(f"  {i}/{len(missing)}")
            META.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        time.sleep(0.25)
    META.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完成：新补 {ok} 条，缓存共 {len(cache)} 条 -> {META}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

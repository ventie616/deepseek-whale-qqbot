"""通过 GitHub REST API 把本地 git 历史完整重放到远程仓库。

本机连不上 github.com:443（只能连 api.github.com），token 又没有 admin:public_key，
所以不能用 git push、也加不了 SSH key。这里改成：
  blob（按内容 sha，天然去重）→ tree（逐层）→ commit（带原始作者/时间/说明）→ ref

用法：
    python tools/push_via_api.py ventie616 deepseek-whale-qqbot --branch main
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

TOKEN_FILE = pathlib.Path(os.environ.get("TEMP", ".")) / "gh_token.txt"
API = "https://api.github.com"


def token() -> str:
    return TOKEN_FILE.read_text(encoding="ascii").strip()


def api(path: str, method: str = "GET", body: dict | None = None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"token {token()}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "qqbot-push",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"{method} {path} -> {e.code}: {detail}") from None


def git(*args: str, cwd: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout


def seed_empty_repo(cwd: str, repo: str, branch: str) -> str:
    """空仓库 Git Data API 会 409，先提交一个 .gitignore 把仓库变成"非空"。"""
    src = pathlib.Path(cwd) / ".gitignore"
    content = src.read_text(encoding="utf-8") if src.is_file() else "# init\n"
    api(f"/repos/{repo}/contents/.gitignore", "PUT", {
        "message": "chore: initialize repository",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    })
    head = api(f"/repos/{repo}/commits/{branch}")
    print(f"已建初始提交 {head['sha'][:8]}（用于解锁 Git Data API）")
    return head["sha"]


def commits(cwd: str) -> list[dict]:
    out = git(
        "log", "--reverse", "--format=%H%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cn%x1f%ce%x1f%cI%x1f%B%x1e",
        cwd=cwd,
    )
    rows = []
    for chunk in out.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        parts = chunk.split("\x1f")
        if len(parts) < 9:
            continue
        rows.append({
            "sha": parts[0], "parents": parts[1].split(), "an": parts[2], "ae": parts[3],
            "ad": parts[4], "cn": parts[5], "ce": parts[6], "cd": parts[7],
            "message": parts[8].rstrip("\n"),
        })
    return rows


def blob_shas(cwd: str, commit: str) -> dict[str, str]:
    """path -> blob sha（内容寻址，和 API 返回的 sha 一致）"""
    out = git("ls-tree", "-r", "-z", commit, cwd=cwd)
    mapping = {}
    for item in out.split("\0"):
        if not item:
            continue
        meta, path = item.split("\t", 1)
        mode, typ, sha = meta.split()
        if typ == "blob":
            mapping[path] = sha
    return mapping


def upload_blob(cwd: str, sha: str, cache: set[str], repo: str) -> None:
    if sha in cache:
        return
    content = subprocess.run(["git", "cat-file", "blob", sha], cwd=cwd, capture_output=True, check=True).stdout
    api(f"/repos/{repo}/git/blobs", "POST",
        {"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"})
    cache.add(sha)


def build_tree(cwd: str, files: dict[str, str], repo: str, cache: dict[str, str]) -> str:
    """files: path -> blob sha；返回根 tree sha。"""
    root: dict = {}
    for path, sha in files.items():
        parts = path.split("/")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = sha

    def make(node: dict) -> str:
        entries = []
        for name, val in node.items():
            if isinstance(val, dict):
                entries.append({"path": name, "mode": "040000", "type": "tree", "sha": make(val)})
            else:
                entries.append({"path": name, "mode": "100644", "type": "blob", "sha": val})
        key = json.dumps(sorted(e["path"] for e in entries)) + "".join(
            e["sha"] for e in sorted(entries, key=lambda x: x["path"])
        )
        if key in cache:
            return cache[key]
        d = api(f"/repos/{repo}/git/trees", "POST", {"tree": entries})
        cache[key] = d["sha"]
        return d["sha"]

    return make(root)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("owner")
    ap.add_argument("repo")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--cwd", default=str(pathlib.Path(__file__).resolve().parent.parent))
    args = ap.parse_args()
    repo = f"{args.owner}/{args.repo}"
    cwd = args.cwd

    repo_info = api(f"/repos/{repo}")
    print(f"目标仓库: {repo_info['full_name']} ({'private' if repo_info['private'] else 'public'})")
    if repo_info.get("size", 0) > 0:
        print("⚠️ 仓库不是空的，先停一下")
        return 1

    rows = commits(cwd)
    print(f"本地提交 {len(rows)} 个，开始重放…")

    base = None
    if repo_info.get("size", 0) == 0:
        try:
            base = seed_empty_repo(cwd, repo, args.branch)
            args.branch = api(f"/repos/{repo}").get("default_branch") or args.branch
        except RuntimeError as exc:
            print("初始化失败:", exc)
            return 1

    blobs: set[str] = set()
    trees: dict[str, str] = {}
    sha_map: dict[str, str] = {}
    for i, c in enumerate(rows, 1):
        files = blob_shas(cwd, c["sha"])
        for sha in set(files.values()):
            upload_blob(cwd, sha, blobs, repo)
        tree = build_tree(cwd, files, repo, trees)
        parents = [sha_map[p] for p in c["parents"] if p in sha_map]
        if not parents and base:
            parents = [base]
        body = {
            "message": c["message"],
            "tree": tree,
            "parents": parents,
            "author": {"name": c["an"], "email": c["ae"], "date": c["ad"]},
            "committer": {"name": c["cn"], "email": c["ce"], "date": c["cd"]},
        }
        d = api(f"/repos/{repo}/git/commits", "POST", body)
        sha_map[c["sha"]] = d["sha"]
        if i % 5 == 0 or i == len(rows):
            print(f"  {i}/{len(rows)}  {d['sha'][:8]}  {c['message'].splitlines()[0][:46]}")

    head = sha_map[rows[-1]["sha"]]
    ref = f"refs/heads/{args.branch}"
    try:
        api(f"/repos/{repo}/git/refs", "POST", {"ref": ref, "sha": head})
        print(f"分支 {args.branch} 已创建 -> {head[:8]}")
    except RuntimeError as exc:
        print("创建分支失败，尝试更新:", exc)
        api(f"/repos/{repo}/git/refs/heads/{args.branch}", "PATCH", {"sha": head, "force": True})
    api(f"/repos/{repo}", "PATCH", {"default_branch": args.branch})

    # 标签
    for tag in git("tag", cwd=cwd).split():
        target = git("rev-list", "-n", "1", tag, cwd=cwd).strip()
        if target not in sha_map:
            continue
        try:
            api(f"/repos/{repo}/git/refs", "POST",
                {"ref": f"refs/tags/{tag}", "sha": sha_map[target]})
            print(f"标签 {tag} 已推")
        except RuntimeError as exc:
            print(f"标签 {tag} 跳过: {exc}")

    print("完成。仓库地址:", repo_info["html_url"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

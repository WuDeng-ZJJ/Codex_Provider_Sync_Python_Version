#!/usr/bin/env python3
"""codex_provider_sync.py

让 Codex 切换 model_provider 后, 历史会话在 Desktop / resume 列表里重新可见。

原理: Codex Desktop 的会话列表按当前 model_provider 过滤。切换 provider 后,
旧会话的 provider 标签和新 provider 不一致, 就被隐藏。本工具把会话的 provider
标签同步到目标 provider, 同时改两处:
  1. 数据库 threads 表 (~/.codex/sqlite/state_5.sqlite) 的 model_provider 字段
  2. sessions/**/*.jsonl 首条 session_meta 的 payload.model_provider 字段

能力边界:
  - 只改 provider 可见性 metadata, 不改对话内容、标题、排序。
  - 含 encrypted_content 的会话跨 provider/account 后, 列表能恢复可见,
    但点进去继续聊可能报 invalid_encrypted_content (Codex 加密机制决定, 无解)。

用法:
  python codex_provider_sync.py --status                 查看现状
  python codex_provider_sync.py --sync --dry-run         预览要改什么 (不写)
  python codex_provider_sync.py --sync                    同步到当前 config 的 provider
  python codex_provider_sync.py --sync --provider openai  同步到指定 provider
  python codex_provider_sync.py --restore <backup_dir>    从备份还原

默认会在改动前自动备份到 ~/.codex/backups_state/py-provider-sync/<时间戳>/
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

CODEX_HOME = Path.home() / ".codex"
DB_PATH = CODEX_HOME / "sqlite" / "state_5.sqlite"
CONFIG_PATH = CODEX_HOME / "config.toml"
SESSIONS_DIR = CODEX_HOME / "sessions"
ARCHIVED_DIR = CODEX_HOME / "archived_sessions"
BACKUP_ROOT = CODEX_HOME / "backups_state" / "py-provider-sync"
DEFAULT_PROVIDER = "openai"


def _log(msg: str) -> None:
    print(msg, flush=True)


def detect_provider(explicit: str = "") -> str:
    """读 config.toml 的根级 model_provider; --provider 优先。"""
    if explicit:
        return explicit
    if not CONFIG_PATH.exists():
        return DEFAULT_PROVIDER
    text = CONFIG_PATH.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'^\s*model_provider\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else DEFAULT_PROVIDER


def iter_session_files(include_archived: bool = False):
    roots = [SESSIONS_DIR]
    if include_archived and ARCHIVED_DIR.exists():
        roots.append(ARCHIVED_DIR)
    for root in roots:
        if root.exists():
            yield from root.rglob("*.jsonl")


def prune_backups(keep: int = 3) -> None:                                                   # def prune_backups(keep: int = 5) -> None:
    """只保留最近 keep 个备份目录, 超出的删最早的。
    备份目录名是 %Y%m%d-%H%M%S 时间戳, 按名字排序即按时间排序。"""
    if not BACKUP_ROOT.exists():
        return
    dirs = sorted(
        [d for d in BACKUP_ROOT.iterdir() if d.is_dir()],
        key=lambda d: d.name,
    )
    while len(dirs) > keep:
        oldest = dirs.pop(0)
        try:
            shutil.rmtree(oldest)
            _log(f"[prune] 已删除最早的备份: {oldest.name}")
        except OSError as exc:
            _log(f"[prune] 删除失败 {oldest.name}: {exc}")


def make_backup() -> Path:
    """备份 db + sessions 到带时间戳目录, 返回目录路径。"""
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_ROOT / stamp
    dest.mkdir(parents=True, exist_ok=True)
    # db 三件套 (含 -wal -shm)
    if DB_PATH.exists():
        for f in DB_PATH.parent.glob("state_5.sqlite*"):
            shutil.copy2(f, dest / f.name)
    # sessions 全量复制
    if SESSIONS_DIR.exists():
        shutil.copytree(SESSIONS_DIR, dest / "sessions", dirs_exist_ok=True)
    _log(f"[backup] 已备份到 {dest}")
    prune_backups(keep=3)                                                                   #     prune_backups(keep=5)
    return dest


def read_session_meta(path: Path):
    """返回 (raw_first_line, meta_obj) 或 (None, None)。"""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
                    return line, obj
                return None, None  # 首条非 session_meta
    except (OSError, json.JSONDecodeError):
        pass
    return None, None


def rewrite_session_provider(path: Path, target: str, dry_run: bool) -> bool:
    """把 jsonl 首条 session_meta 的 payload.model_provider 改成 target。
    返回 True 表示发生(或将发生)改动。"""
    raw, meta = read_session_meta(path)
    if meta is None:
        return False
    payload = meta["payload"]
    if payload.get("model_provider") == target:
        return False
    if dry_run:
        return True
    payload["model_provider"] = target
    new_first = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    # 替换第一条非空行
    for i, ln in enumerate(lines):
        if ln.strip():
            lines[i] = new_first
            break
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    return True


def _connect_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise SystemExit(f"[错误] 找不到数据库: {DB_PATH}\n请确认 Codex 已安装并至少建过一次会话。")
    con = sqlite3.connect(str(DB_PATH), timeout=5.0)
    con.row_factory = sqlite3.Row
    return con


def cmd_status(target: str) -> None:
    _log(f"Codex Home : {CODEX_HOME}")
    _log(f"数据库      : {DB_PATH}  ({'存在' if DB_PATH.exists() else '缺失'})")
    _log(f"当前 config provider : {detect_provider()}")
    _log(f"目标 provider        : {target}")
    _log("")
    if not DB_PATH.exists():
        return
    con = _connect_db()
    rows = con.execute(
        "SELECT model_provider, archived, COUNT(*) n FROM threads "
        "GROUP BY model_provider, archived ORDER BY model_provider, archived"
    ).fetchall()
    _log("数据库 threads 表按 provider 统计:")
    for r in rows:
        flag = "已归档" if r["archived"] else "活跃"
        match = " <- 目标(已可见)" if r["model_provider"] == target else ""
        _log(f"  provider={r['model_provider']:<12} {flag:<6} {r['n']} 条{match}")
    hidden = con.execute(
        "SELECT COUNT(*) FROM threads WHERE model_provider != ? AND archived = 0",
        (target,),
    ).fetchone()[0]
    _log("")
    _log(f"切到 '{target}' 后, 当前被隐藏的活跃会话: {hidden} 条 (sync 可让它们重新可见)")
    con.close()


def cmd_sync(target: str, dry_run: bool, include_archived: bool) -> None:
    if not dry_run:
        make_backup()
    # 1. 数据库
    con = _connect_db()
    where = "model_provider != ?" + ("" if include_archived else " AND archived = 0")
    to_change = con.execute(
        f"SELECT id, model_provider, title FROM threads WHERE {where}", (target,)
    ).fetchall()
    _log(f"[db] 需要改 provider 的 threads: {len(to_change)} 条")
    for r in to_change:
        t = (r["title"] or "")[:30].replace("\n", " ")
        _log(f"  {'[预览]' if dry_run else '[改]'} {r['id']}  {r['model_provider']} -> {target}  | {t}")
    if not dry_run and to_change:
        con.execute(f"UPDATE threads SET model_provider = ? WHERE {where}", (target, target))
        con.commit()
    con.close()
    # 2. sessions 文件
    changed = 0
    for f in iter_session_files(include_archived=include_archived):
        if rewrite_session_provider(f, target, dry_run):
            changed += 1
    _log(f"[sessions] {'将改' if dry_run else '已改'} {changed} 个 jsonl 文件的 provider 标签")
    _log("")
    if dry_run:
        _log("== DRY RUN 预览结束, 未写入任何改动。去掉 --dry-run 才会真正执行。 ==")
    else:
        _log("== 同步完成。重开 Codex 查看列表。==")
        _log("注意: 含加密内容的旧会话可能只恢复可见, 点开续聊仍可能报 invalid_encrypted_content。")


def cmd_restore(backup_dir: str) -> None:
    src = Path(backup_dir)
    if not src.exists():
        raise SystemExit(f"[错误] 备份目录不存在: {src}")
    # 还原 db
    for f in src.glob("state_5.sqlite*"):
        shutil.copy2(f, DB_PATH.parent / f.name)
        _log(f"[还原] {f.name}")
    # 还原 sessions
    bs = src / "sessions"
    if bs.exists():
        shutil.copytree(bs, SESSIONS_DIR, dirs_exist_ok=True)
        _log("[还原] sessions/")
    _log("== 还原完成 ==")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Codex provider 可见性同步工具 (Python)")
    p.add_argument("--status", action="store_true", help="查看现状, 不改任何东西")
    p.add_argument("--sync", action="store_true", help="把会话同步到目标 provider")
    p.add_argument("--provider", default="", help="目标 provider (默认读 config.toml)")
    p.add_argument("--dry-run", action="store_true", help="只预览不写入")
    p.add_argument("--include-archived", action="store_true", help="同时处理已归档会话")
    p.add_argument("--restore", metavar="DIR", default="", help="从备份目录还原")
    args = p.parse_args(argv)

    if args.restore:
        cmd_restore(args.restore)
        return 0
    target = detect_provider(args.provider)
    if args.status or not (args.sync or args.restore):
        cmd_status(target)
        if not args.sync:
            return 0
    if args.sync:
        cmd_sync(target, args.dry_run, args.include_archived)
    return 0


if __name__ == "__main__":
    sys.exit(main())

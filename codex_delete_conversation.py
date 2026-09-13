# -*- coding: utf-8 -*-
"""Exact-ID, confirmed local conversation deletion. Never follows linked threads."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import ExitStack, closing
from pathlib import Path
from urllib.parse import urlsplit, unquote
from uuid import UUID


def parse_link(value: str) -> str:
    link = urlsplit(value.strip())
    if link.scheme.lower() != "codex" or link.netloc.lower() != "threads" or link.query or link.fragment:
        raise ValueError("请输入本机对话深度链接：codex://threads/完整对话ID；不接受分享链接、远程链接或模糊匹配。")
    identifier = unquote(link.path).strip("/")
    if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", identifier):
        raise ValueError("链接中的对话 ID 格式无效。")
    return str(UUID(identifier))


def connect(path: Path, readonly: bool = False):
    c = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10) if readonly else sqlite3.connect(path, timeout=10)
    c.execute("PRAGMA busy_timeout=10000")
    return c


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def safe_path(home: Path, path: Path) -> Path:
    # Reject junctions and symlinks, even when they point back inside the home.
    absolute = path.absolute()
    absolute.relative_to(home)
    for component in [absolute, *absolute.parents]:
        if component == home:
            break
        if component.is_symlink() or (hasattr(component, "is_junction") and component.is_junction()):
            raise RuntimeError(f"路径包含链接，不能安全删除：{path}")
    resolved = absolute.resolve()
    resolved.relative_to(home)
    if resolved == home:
        raise RuntimeError("拒绝操作整个数据目录。")
    return resolved


def data_files(home: Path):
    # Only app data and local recovery snapshots, never projects, plugins or skills.
    for path in home.iterdir():
        if path.is_file():
            yield safe_path(home, path)
        elif path.name in {"sessions", "archived_sessions", "sqlite", "backups", "backups_state", "automations", "thread-writer-locks"} or "backup" in path.name or "before" in path.name or path.name.startswith("global_state_fix_"):
            safe_path(home, path)
            for root, dirs, files in os.walk(path, followlinks=False):
                for name in dirs:
                    safe_path(home, Path(root) / name)
                for name in files:
                    yield safe_path(home, Path(root) / name)


def validate_operation_triggers(c, sql, args):
    # EXPLAIN compiles without executing writes. SQLite itself resolves which
    # triggers fire for this operation (including nested trigger invocations).
    definitions = dict(c.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'"))
    approved = """CREATE TRIGGER thread_realtime_items_projection_cleanup
        AFTER DELETE ON thread_history_projection_state
        BEGIN
        DELETE FROM thread_realtime_items WHERE thread_id = OLD.thread_id;
        END"""
    normalize = lambda value: " ".join(value.split()).rstrip(";").casefold()
    allowed = {name for name, definition in definitions.items()
               if normalize(definition or "") == normalize(approved)}
    blocked = set()

    def authorize(action, arg1, arg2, database, source):
        if action in (sqlite3.SQLITE_DELETE, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_INSERT) and source and source not in allowed:
            blocked.add(source)
        return sqlite3.SQLITE_OK

    c.set_authorizer(authorize)
    try:
        c.execute("EXPLAIN " + sql, args).fetchall()
    finally:
        c.set_authorizer(None)
    if blocked:
        raise RuntimeError("本次操作会执行未核准的写入触发器，停止删除：" + "、".join(sorted(blocked)))


def table_ops(c, identifier: str):
    """Delete owned rows; remove edges, but never delete their other endpoint."""
    operations = []
    for (table,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({quote(table)})")}
        keys = [k for k in ("thread_id", "session_id", "conversation_id") if k in cols]
        if table == "threads" and "id" in cols:
            keys.append("id")
        if table == "thread_spawn_edges":
            keys.extend(k for k in ("parent_thread_id", "child_thread_id") if k in cols)
        if table == "automations" and "target_thread_id" in cols:
            keys.append("target_thread_id")
        if table == "jobs" and "job_key" in cols:
            keys.append("job_key")
        if keys:
            where = "(" + " OR ".join(f"{quote(k)} = ?" for k in keys) + ")"
            args = [identifier] * len(keys)
            if "host_id" in cols:
                where += " AND host_id = 'local'"
            rows = c.execute(f"SELECT * FROM {quote(table)} WHERE {where}", args).fetchall()
            if rows:
                operations.append((f"DELETE FROM {quote(table)} WHERE {where}", args, table, rows))
        # A shared job/migration cursor is retained with only the exact reference cleared.
        for key in ("assigned_thread_id", "last_checked_thread_id"):
            if key in cols:
                rows = c.execute(f"SELECT * FROM {quote(table)} WHERE {quote(key)} = ?", (identifier,)).fetchall()
                if rows:
                    operations.append((f"UPDATE {quote(table)} SET {quote(key)} = NULL WHERE {quote(key)} = ?", [identifier], table, rows))
    # Delete realtime rows first; the approved projection cleanup then finds no
    # remaining rows, so explicit row-count checks stay accurate.
    operations.sort(key=lambda op: op[2] == "thread_history_projection_state")
    for sql, args, _, _ in operations:
        validate_operation_triggers(c, sql, args)
    return operations


def remove_ref(value, identifier):
    """Used only inside known thread-index fields; never rewrites message strings."""
    if isinstance(value, dict):
        return {k: remove_ref(v, identifier) for k, v in value.items() if k != identifier}
    if isinstance(value, list):
        return [remove_ref(v, identifier) for v in value if v != identifier and not (isinstance(v, dict) and any(v.get(k) == identifier for k in ("threadId", "thread_id", "id")))]
    return None if value == identifier else value


def global_state_without_thread(state, identifier):
    fields = {"projectless-thread-ids", "thread-workspace-root-hints", "thread-projectless-output-directories", "queued-follow-ups", "pinned-thread-ids", "electron-thread-read-state-v1", "sidebar-project-thread-orders"}
    result = dict(state)
    for key in fields & state.keys():
        result[key] = remove_ref(state[key], identifier)
    key = "app-server-migrated-pinned-thread-ids-by-host"
    if isinstance(state.get(key), dict):
        result[key] = {host: remove_ref(v, identifier) if host == "local" or host.startswith("local:") else v for host, v in state[key].items()}
    return result


def message_text(payload):
    value = payload.get("message", payload.get("text", payload.get("content", "")))
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(x.get("text", "[非文本内容]") if isinstance(x, dict) else str(x) for x in value)
    return "[非文本内容]"


def recent_turn(path: Path):
    # Prefer canonical message records to duplicated event notifications.
    messages, events = [], []
    with path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            payload = record.get("payload", {})
            if not isinstance(payload, dict):
                continue
            kind = record.get("type")
            role = payload.get("role")
            if kind == "response_item" and payload.get("type") == "message" and role in ("user", "assistant"):
                messages.append((role, message_text(payload), record.get("timestamp", "")))
            elif kind == "event_msg" and payload.get("type") in ("user_message", "agent_message"):
                events.append(("user" if payload["type"] == "user_message" else "assistant", message_text(payload), record.get("timestamp", "")))
    # Newer event-only messages must not be hidden by old response_item records.
    if events and (not messages or events[-1][2] > messages[-1][2]):
        messages = events
    if not messages:
        return []
    start = next((i for i in range(len(messages) - 1, -1, -1) if messages[i][0] == "user"), len(messages) - 1)
    return messages[start:]


def history_preview(c, identifier):
    cols = {r[1] for r in c.execute('PRAGMA table_info("thread_items")')}
    if not {"thread_id", "item_json", "rollout_ordinal"}.issubset(cols):
        return []
    messages = []
    for (raw,) in c.execute("SELECT item_json FROM thread_items WHERE thread_id=? ORDER BY rollout_ordinal", (identifier,)):
        item = json.loads(raw)
        kind = item.get("type")
        role = item.get("role")
        if kind in ("userMessage", "user_message"):
            role = "user"
        elif kind in ("agentMessage", "agent_message"):
            role = "assistant"
        if role in ("user", "assistant"):
            messages.append((role, message_text(item), ""))
    start = next((i for i in range(len(messages) - 1, -1, -1) if messages[i][0] == "user"), max(0, len(messages) - 1))
    return messages[start:]


def build_plan(home: Path, identifier: str, allow_missing: bool = False):
    home = home.resolve()
    files, databases, previews, titles = {}, {}, [], []
    found = False
    for path in data_files(home):
        name = path.name
        if name.endswith((".sqlite", ".db")):
            with closing(connect(path, True)) as c:
                ops = table_ops(c, identifier)
                if ops:
                    databases[path] = ops
                tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "thread_items" in tables:
                    turn = history_preview(c, identifier)
                    if turn:
                        previews.append(turn)
                if "threads" in tables:
                    cols = {r[1] for r in c.execute('PRAGMA table_info("threads")')}
                    if "id" in cols:
                        rows = c.execute("SELECT * FROM threads WHERE id = ?", (identifier,)).fetchall()
                        if rows:
                            found = True
                            names = [r[1] for r in c.execute('PRAGMA table_info("threads")')]
                            for row in rows:
                                item = dict(zip(names, row))
                                if item.get("title"):
                                    titles.append(item["title"])
                                if item.get("rollout_path"):
                                    rollout = Path(item["rollout_path"])
                                    # Never trust a stale database pointer as deletion authority.
                                    if rollout.exists():
                                        safe_path(home, rollout)
                                        with rollout.open(encoding="utf-8-sig") as handle:
                                            meta = json.loads(handle.readline())
                                        if meta.get("type") != "session_meta" or meta.get("payload", {}).get("id") != identifier:
                                            raise RuntimeError(f"数据库路径指向另一对话或无效文件，停止删除：{rollout}")
                                        files[rollout] = (rollout.read_bytes(), None)
                                        turn = recent_turn(rollout)
                                        if turn:
                                            previews.append(turn)
        elif name.startswith("rollout-") and name.endswith(".jsonl"):
            with path.open(encoding="utf-8-sig") as handle:
                first = handle.readline()
            try:
                record = json.loads(first)
            except ValueError:
                if identifier in name:
                    raise RuntimeError(f"目标会话文件无法解析，停止删除：{path}")
                continue
            if record.get("type") == "session_meta" and record.get("payload", {}).get("id") == identifier:
                found = True
                files[path] = (path.read_bytes(), None)
                turn = recent_turn(path)
                if turn:
                    previews.append(turn)
        elif name in {"session_index.jsonl", "history.jsonl", "transcription-history.jsonl"}:
            before = path.read_bytes()
            kept = []
            for line in before.splitlines(keepends=True):
                try:
                    obj = json.loads(line)
                except ValueError:
                    if identifier.encode() in line:
                        raise RuntimeError(f"索引含无法解析的目标记录：{path}")
                    kept.append(line)
                    continue
                keys = ("id",) if name == "session_index.jsonl" else ("session_id", "thread_id", "threadId")
                if not isinstance(obj, dict) or not any(obj.get(k) == identifier for k in keys):
                    kept.append(line)
            after = b"".join(kept)
            if after != before:
                files[path] = (before, after)
        elif ".codex-global-state.json" in name:
            before = path.read_bytes()
            try:
                obj = json.loads(before)
            except ValueError:
                if identifier.encode() in before:
                    raise RuntimeError(f"桌面缓存无法解析：{path}")
                continue
            after = global_state_without_thread(obj, identifier)
            if obj != after:
                files[path] = (before, (json.dumps(after, ensure_ascii=False, indent=2) + "\n").encode())
        elif name == "manifest.json":
            before = path.read_bytes()
            obj = json.loads(before)
            after = dict(obj)
            for key in ("changedSessionFiles", "conversations", "sessions", "threads"):
                if isinstance(after.get(key), list):
                    after[key] = remove_ref(after[key], identifier)
            if after != obj:
                files[path] = (before, (json.dumps(after, ensure_ascii=False, indent=2) + "\n").encode())
        elif name == identifier + ".lock" and path.parent == home / "thread-writer-locks":
            files[path] = (path.read_bytes(), None)
        elif name == "automation.toml":
            import tomllib
            before = path.read_bytes()
            config = tomllib.loads(before.decode("utf-8-sig"))
            if config.get("target_thread_id") == identifier and config.get("kind") == "heartbeat":
                files[path] = (before, None)
    if not found and not allow_missing:
        raise RuntimeError("没有找到该 ID 的本机对话记录；未执行删除。请确认链接属于这台电脑。")
    preview = max(previews, key=lambda turn: turn[-1][2]) if previews else []
    fingerprint = hashlib.sha256()
    for path, (before, after) in sorted(files.items()):
        fingerprint.update(str(path).encode()); fingerprint.update(before)
    for path, ops in sorted(databases.items()):
        fingerprint.update(str(path).encode()); fingerprint.update(repr(ops).encode())
    return {"home": home, "id": identifier, "files": files, "databases": databases, "preview": preview,
            "title": titles[0] if titles else "（无标题记录）", "fingerprint": fingerprint.hexdigest()}


def purge_staging(home, staging):
    resolved = safe_path(home, staging)
    if resolved.parent != home or not resolved.name.startswith(".thread-delete-"):
        raise RuntimeError("拒绝清理非删除事务目录。")
    shutil.rmtree(resolved)


def execute_plan(plan):
    home, identifier = plan["home"], plan["id"]
    current = build_plan(home, identifier)
    if current["fingerprint"] != plan["fingerprint"]:
        raise RuntimeError("预览后对话数据发生变化，未删除。请重新输入 A 并确认最新内容。")
    staging = Path(tempfile.mkdtemp(prefix=".thread-delete-", dir=home))
    (staging / "transaction.json").write_text(json.dumps({"thread_id": identifier, "status": "prepared"}), encoding="utf-8")
    snapshots, changed_files = {}, []
    def save_journal():
        (staging / "transaction.json").write_text(json.dumps({
            "thread_id": identifier, "status": "in_progress",
            "databases": {str(path): str(saved) for path, saved in snapshots.items()},
            "files": {str(path): str(saved) for path, saved in changed_files},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        with ExitStack() as stack:
            connections = {}
            for index, path in enumerate(plan["databases"]):
                c = stack.enter_context(closing(connect(path)))
                c.execute("BEGIN IMMEDIATE")
                connections[path] = c
                snapshot = staging / f"db-{index}.sqlite"
                with closing(connect(path, True)) as source, closing(sqlite3.connect(snapshot)) as dest:
                    source.backup(dest)
                snapshots[path] = snapshot
                save_journal()
            try:
                for path, c in connections.items():
                    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    other_threads = c.execute("SELECT * FROM threads WHERE id <> ?", (identifier,)).fetchall() if "threads" in tables else None
                    other_catalog = c.execute("SELECT * FROM local_thread_catalog WHERE host_id <> 'local' OR thread_id <> ?", (identifier,)).fetchall() if "local_thread_catalog" in tables else None
                    fk_before = c.execute("PRAGMA foreign_key_check").fetchall()
                    ops = table_ops(c, identifier)
                    if ops != plan["databases"][path]:
                        raise RuntimeError("数据库在确认后发生变化，已停止删除。")
                    for sql, args, _, rows in ops:
                        if c.execute(sql, args).rowcount != len(rows):
                            raise RuntimeError("删除数量不符合预览，已停止。")
                    if "local_thread_catalog_metadata" in tables:
                        validate_operation_triggers(c, "UPDATE local_thread_catalog_metadata SET catalog_revision = catalog_revision + 1 WHERE id=1", [])
                        c.execute("UPDATE local_thread_catalog_metadata SET catalog_revision = catalog_revision + 1 WHERE id=1")
                    if other_threads is not None and c.execute("SELECT * FROM threads WHERE id <> ?", (identifier,)).fetchall() != other_threads:
                        raise RuntimeError("其他对话记录发生变化，已停止并回滚。")
                    if other_catalog is not None and c.execute("SELECT * FROM local_thread_catalog WHERE host_id <> 'local' OR thread_id <> ?", (identifier,)).fetchall() != other_catalog:
                        raise RuntimeError("其他对话索引发生变化，已停止并回滚。")
                    if any(row not in fk_before for row in c.execute("PRAGMA foreign_key_check").fetchall()):
                        raise RuntimeError("删除引入了未处理的关联引用，已停止并回滚。")
                    if table_ops(c, identifier):
                        raise RuntimeError("仍有目标对话关联记录，已停止。")
                    if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise RuntimeError("数据库完整性校验失败。")
                for index, (path, (before, after)) in enumerate(plan["files"].items()):
                    safe_path(home, path)
                    if path.read_bytes() != before:
                        raise RuntimeError("会话文件在确认后发生变化，已停止。")
                    saved = staging / f"file-{index}"
                    shutil.copy2(path, saved)
                    changed_files.append((path, saved))
                    save_journal()
                    if after is None:
                        path.unlink()
                    else:
                        temp = staging / f"new-{index}"
                        temp.write_bytes(after)
                        os.replace(temp, path)
                for c in connections.values():
                    c.commit()
            except BaseException:
                for c in connections.values():
                    c.rollback()
                raise
        # Verification includes snapshots in the normal backup folders.
        remaining = build_plan(home, identifier, allow_missing=True)
        if remaining["files"] or remaining["databases"]:
            raise RuntimeError("删除后仍发现目标对话数据。")
    except BaseException:
        # Restore *all* snapshots, including databases committed before a later failure.
        try:
            for path, snapshot in snapshots.items():
                with closing(connect(snapshot, True)) as source, closing(connect(path)) as dest:
                    source.backup(dest)
            for path, saved in changed_files:
                shutil.copy2(saved, path)
        except BaseException as error:
            raise RuntimeError(f"回滚未完成。请保留恢复目录并停止使用应用：{staging}；{error}") from error
        purge_staging(home, staging)
        raise
    # Delete temporary rollback copies after success; no permanent copy of the deleted conversation.
    try:
        purge_staging(home, staging)
    except OSError as error:
        raise RuntimeError(f"对话已删除，但临时恢复副本清理失败：{staging}；{error}") from error


def interactive_delete(home: Path):
    pending = list(home.glob(".thread-delete-*"))
    if pending:
        raise RuntimeError(f"发现未完成的删除事务，请先处理恢复目录：{pending[0]}")
    identifier = parse_link(input("需要删除的对话深度链接是什么？\n> "))
    print("正在按完整对话 ID 扫描本机记录及本地备份，请稍候……", flush=True)
    plan = build_plan(home, identifier)
    print(f"\n指定对话：{plan['title']}\n对话 ID：{identifier}\n最近一次对话内容：")
    if plan["preview"]:
        for role, text, timestamp in plan["preview"]:
            print(f"\n{'用户' if role == 'user' else '助手'}（{timestamp or '无时间记录'}）：\n{text}")
        if plan["preview"][-1][0] == "user":
            print("\n最近一条是用户消息，尚未发现对应助手回复。")
    else:
        print("未找到可读取的消息正文；不能凭标题推断最近内容。")
    rows = sum(len(op[3]) for ops in plan["databases"].values() for op in ops)
    print(f"\n将清理 {len(plan['files'])} 个文件中的目标记录，以及 {len(plan['databases'])} 个数据库中的 {rows} 条目标记录/引用。")
    print("包括本机历史备份里的该对话副本；不删除其他对话、子对话或项目文件。成功后不保留该对话的恢复副本。")
    for path in [*plan["files"], *plan["databases"]]:
        print(f"  {path.relative_to(plan['home'])}")
    answer = input(f"\n是否删除上述指定对话？输入 Y 确认；其它输入取消：")
    if answer.strip().upper() != "Y":
        print("已取消，未删除或修改任何对话记录。")
        return
    execute_plan(plan)
    print(f"已删除指定本机对话及已识别的关联记录：{identifier}。校验通过。")

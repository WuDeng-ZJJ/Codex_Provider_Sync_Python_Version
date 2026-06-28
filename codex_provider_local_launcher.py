# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_PROVIDER = "openai"
SESSION_DIRS = ("sessions", "archived_sessions")
DB_FILE_BASENAME = "state_5.sqlite"
SQLITE_DIR_BASENAME = "sqlite"
PROVIDER_BACKUP_NAMESPACE = "provider-sync"
CONVERSATION_BACKUP_NAMESPACE = "py-provider-sync"


def default_codex_home() -> Path:
    return Path.home() / ".codex"


def normalize_codex_home(value: str | None) -> Path:
    return Path(value or os.environ.get("CODEX_HOME") or default_codex_home()).expanduser().resolve()


def provider_backup_root(codex_home: Path) -> Path:
    return codex_home / "backups_state" / PROVIDER_BACKUP_NAMESPACE


def conversation_backup_root(codex_home: Path) -> Path:
    return codex_home / "backups_state" / CONVERSATION_BACKUP_NAMESPACE


def timestamp_for_path() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def unique_timestamp_dir(root: Path) -> Path:
    base = timestamp_for_path()
    candidate = root / base
    if not candidate.exists():
        return candidate
    for index in range(1, 100):
        candidate = root / f"{base}-{index:02d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法创建唯一备份目录：{root}")


def clean_text(value: Any, fallback: str = "未命名") -> str:
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return text or fallback


def parse_time_ms(value: Any) -> int:
    if not value:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return 0
    try:
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except ValueError:
        return 0


def to_unix_seconds(value: Any) -> int:
    ms = parse_time_ms(value)
    return int(ms / 1000) if ms else int(datetime.now(tz=timezone.utc).timestamp())


def to_unix_ms(value: Any) -> int:
    ms = parse_time_ms(value)
    return ms if ms else int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def char_width(char: str) -> int:
    code = ord(char)
    return 2 if (
        0x1100 <= code <= 0x115F
        or code in (0x2329, 0x232A)
        or 0x2E80 <= code <= 0xA4CF
        or 0xAC00 <= code <= 0xD7A3
        or 0xF900 <= code <= 0xFAFF
        or 0xFE10 <= code <= 0xFE19
        or 0xFE30 <= code <= 0xFE6F
        or 0xFF00 <= code <= 0xFF60
        or 0xFFE0 <= code <= 0xFFE6
    ) else 1


def display_width(value: Any) -> int:
    return sum(char_width(char) for char in str(value if value is not None else ""))


def shorten(value: Any, max_width: int) -> str:
    text = clean_text(value, "")
    if display_width(text) <= max_width:
        return text
    result = ""
    width = 0
    for char in text:
        next_width = char_width(char)
        if width + next_width > max_width - 1:
            break
        result += char
        width += next_width
    return f"{result}…"


def pad_display(value: Any, width: int) -> str:
    text = str(value if value is not None else "")
    return text + (" " * max(0, width - display_width(text)))


def sum_counts(counts: dict[str, int] | None) -> int:
    return sum(int(value or 0) for value in (counts or {}).values())


def format_counts(counts: dict[str, int] | None) -> str:
    if not counts:
        return "无"
    return "，".join(f"{provider} {count} 条" for provider, count in counts.items())


def count_hidden(counts: dict[str, int], current_provider: str | None) -> int:
    return sum(int(count or 0) for provider, count in counts.items() if provider != current_provider)


def read_config_text(codex_home: Path) -> str:
    return (codex_home / "config.toml").read_text(encoding="utf-8")


def read_current_provider_from_config_text(config_text: str) -> tuple[str, bool]:
    for line in re.split(r"\r?\n", config_text):
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue
        if trimmed.startswith("["):
            break
        match = re.match(r'^model_provider\s*=\s*"([^"]+)"\s*$', trimmed)
        if match:
            return match.group(1), False
    return DEFAULT_PROVIDER, True


def list_configured_provider_ids(config_text: str) -> list[str]:
    providers = {DEFAULT_PROVIDER}
    providers.update(re.findall(r"^\[model_providers\.([A-Za-z0-9_.-]+)]\s*$", config_text, flags=re.M))
    return sorted(providers)


def read_first_line_record(file_path: Path) -> tuple[str, bytes, bytes, bytes]:
    data = file_path.read_bytes()
    newline_index = data.find(b"\n")
    if newline_index < 0:
        return data.decode("utf-8"), b"", b"", data
    if newline_index > 0 and data[newline_index - 1:newline_index] == b"\r":
        first_bytes = data[: newline_index - 1]
        separator = b"\r\n"
    else:
        first_bytes = data[:newline_index]
        separator = b"\n"
    rest = data[newline_index + 1:]
    return first_bytes.decode("utf-8"), separator, rest, data


def parse_session_meta(first_line: str) -> dict[str, Any] | None:
    if not first_line:
        return None
    try:
        record = json.loads(first_line)
    except json.JSONDecodeError:
        return None
    if record.get("type") != "session_meta" or not isinstance(record.get("payload"), dict):
        return None
    return record


def list_rollout_files(root_dir: Path) -> list[Path]:
    if not root_dir.exists():
        return []
    return sorted(path for path in root_dir.rglob("rollout-*.jsonl") if path.is_file())


def record_has_user_event(record: dict[str, Any]) -> bool:
    if record.get("type") == "event_msg" and (record.get("payload") or {}).get("type") == "user_message":
        return True
    for key in ("payload", "item", "msg"):
        value = record.get(key)
        if isinstance(value, dict) and value.get("type") == "message" and value.get("role") == "user":
            return True
    return False


def file_has_user_event(file_path: Path) -> bool:
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    if record_has_user_event(json.loads(line)):
                        return True
                except json.JSONDecodeError:
                    continue
    except OSError:
        return False
    return False


def file_has_encrypted_content(file_path: Path) -> bool:
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if '"encrypted_content"' in line:
                    return True
    except OSError:
        return False
    return False


def to_desktop_workspace_path(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    if not trimmed:
        return value
    match = re.match(r"^\\\\\?\\UNC\\(.+)$", trimmed, flags=re.I)
    if match:
        return ("\\\\" + match.group(1)).replace("/", "\\")
    match = re.match(r"^\\\\\?\\([A-Za-z]:)(?:[\\/](.*))?$", trimmed)
    if match:
        drive, rest = match.groups()
        return f"{drive}\\{rest.replace('/', '\\')}" if rest else f"{drive}\\"
    if trimmed.startswith("\\\\?\\"):
        return trimmed[4:].replace("/", "\\")
    return value


def normalize_comparable_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    match = re.match(r"^\\\\\?\\UNC\\(.+)$", normalized, flags=re.I)
    normalized = "\\\\" + match.group(1) if match else re.sub(r"^\\\\\?\\", "", normalized)
    normalized = normalized.replace("/", "\\").rstrip("\\")
    if re.match(r"^[A-Za-z]:$", normalized):
        normalized += "\\"
    return normalized.lower()


def collect_rollout_status(codex_home: Path, target_provider: str | None = None) -> dict[str, Any]:
    provider_counts = {name: Counter() for name in SESSION_DIRS}
    encrypted_counts = {name: Counter() for name in SESSION_DIRS}
    user_event_thread_ids: set[str] = set()
    thread_cwd_by_id: dict[str, str] = {}
    changes: list[dict[str, Any]] = []
    locked_paths: list[str] = []

    for dir_name in SESSION_DIRS:
        root_dir = codex_home / dir_name
        for rollout_path in list_rollout_files(root_dir):
            try:
                first_line, separator, rest, original_data = read_first_line_record(rollout_path)
            except OSError:
                locked_paths.append(str(rollout_path))
                continue
            record = parse_session_meta(first_line)
            if not record:
                continue
            payload = record["payload"]
            provider = payload.get("model_provider", "(missing)")
            provider_counts[dir_name][provider] += 1
            thread_id = payload.get("id")
            cwd = payload.get("cwd")
            if isinstance(thread_id, str) and thread_id and isinstance(cwd, str) and cwd.strip():
                thread_cwd_by_id[thread_id] = to_desktop_workspace_path(cwd)
            if file_has_encrypted_content(rollout_path):
                encrypted_counts[dir_name][provider] += 1
            if isinstance(thread_id, str) and thread_id and file_has_user_event(rollout_path):
                user_event_thread_ids.add(thread_id)
            if target_provider is not None and target_provider != "__status_only__" and payload.get("model_provider") != target_provider:
                next_record = json.loads(json.dumps(record))
                next_record["payload"]["model_provider"] = target_provider
                stat = rollout_path.stat()
                changes.append({
                    "path": rollout_path,
                    "thread_id": thread_id,
                    "directory": dir_name,
                    "original_first_line": first_line,
                    "separator": separator,
                    "rest": rest,
                    "original_data": original_data,
                    "mtime": stat.st_mtime,
                    "updated_first_line": json.dumps(next_record, ensure_ascii=False, separators=(",", ":")),
                    "original_provider": provider,
                })

    return {
        "provider_counts": {key: dict(value) for key, value in provider_counts.items()},
        "encrypted_counts": {key: dict(value) for key, value in encrypted_counts.items()},
        "user_event_thread_ids": user_event_thread_ids,
        "thread_cwd_by_id": thread_cwd_by_id,
        "changes": changes,
        "locked_paths": locked_paths,
    }


def state_db_candidates(codex_home: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": codex_home / SQLITE_DIR_BASENAME / DB_FILE_BASENAME,
            "relative_path": str(Path(SQLITE_DIR_BASENAME) / DB_FILE_BASENAME),
            "source": "sqlite-dir",
        },
        {
            "path": codex_home / DB_FILE_BASENAME,
            "relative_path": DB_FILE_BASENAME,
            "source": "legacy-root",
        },
    ]


def detect_state_db(codex_home: Path) -> dict[str, Any] | None:
    for candidate in state_db_candidates(codex_home):
        if candidate["path"].exists():
            return candidate
    return None


def existing_state_db_path(codex_home: Path) -> Path | None:
    detected = detect_state_db(codex_home)
    return detected["path"] if detected else None


def sqlite_connect(db_path: Path, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    else:
        conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row["name"] for row in conn.execute(f'PRAGMA table_info("{table_name}")')}


def read_sqlite_provider_counts(codex_home: Path) -> dict[str, Any] | None:
    db_path = existing_state_db_path(codex_home)
    if not db_path:
        return None
    try:
        with sqlite_connect(db_path, read_only=True) as conn:
            columns = table_columns(conn, "threads")
            if "model_provider" not in columns:
                return {"sessions": {}, "archived_sessions": {}}
            archived_expression = "archived" if "archived" in columns else "0 AS archived"
            rows = conn.execute(f"""
                SELECT
                  CASE
                    WHEN model_provider IS NULL OR model_provider = '' THEN '(missing)'
                    ELSE model_provider
                  END AS model_provider,
                  {archived_expression},
                  COUNT(*) AS count
                FROM threads
                GROUP BY model_provider, archived
                ORDER BY archived, model_provider
            """).fetchall()
            result = {"sessions": {}, "archived_sessions": {}}
            for row in rows:
                bucket = result["archived_sessions"] if row["archived"] else result["sessions"]
                bucket[row["model_provider"]] = int(row["count"])
            return result
    except sqlite3.Error as error:
        message = str(error).lower()
        if "malformed" in message or "not a database" in message or "database is locked" in message:
            return {
                "sessions": {},
                "archived_sessions": {},
                "unreadable": True,
                "error": "state_5.sqlite is currently in use" if "locked" in message else "state_5.sqlite is malformed or unreadable",
            }
        raise


def read_sqlite_repair_stats(
    codex_home: Path,
    user_event_thread_ids: set[str],
    thread_cwd_by_id: dict[str, str],
) -> dict[str, int] | None:
    db_path = existing_state_db_path(codex_home)
    if not db_path:
        return None
    with sqlite_connect(db_path, read_only=True) as conn:
        columns = table_columns(conn, "threads")
        user_event_rows = 0
        if "has_user_event" in columns and user_event_thread_ids:
            for thread_id in user_event_thread_ids:
                row = conn.execute("SELECT has_user_event FROM threads WHERE id = ?", (thread_id,)).fetchone()
                if row and int(row["has_user_event"] or 0) != 1:
                    user_event_rows += 1

        cwd_rows = 0
        if "cwd" in columns and thread_cwd_by_id:
            for thread_id, cwd in thread_cwd_by_id.items():
                if not thread_id or not cwd.strip():
                    continue
                row = conn.execute("SELECT cwd FROM threads WHERE id = ?", (thread_id,)).fetchone()
                if row and row["cwd"] != cwd:
                    cwd_rows += 1
        return {"userEventRowsNeedingRepair": user_event_rows, "cwdRowsNeedingRepair": cwd_rows}


def read_sqlite_thread_rows(codex_home: Path) -> dict[str, dict[str, Any]]:
    db_path = existing_state_db_path(codex_home)
    if not db_path:
        return {}
    try:
        with sqlite_connect(db_path, read_only=True) as conn:
            columns = table_columns(conn, "threads")
            wanted = ["id", "title", "model_provider", "archived", "cwd"]
            selected = [column for column in wanted if column in columns]
            if "id" not in selected:
                return {}
            rows = conn.execute(f"SELECT {', '.join(selected)} FROM threads").fetchall()
            return {row["id"]: dict(row) for row in rows}
    except sqlite3.Error:
        return {}


def read_rollout_sessions(codex_home: Path) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    for file_path in list_rollout_files(codex_home / "sessions"):
        try:
            first_line, _, _, _ = read_first_line_record(file_path)
        except OSError:
            continue
        record = parse_session_meta(first_line)
        if not record:
            continue
        payload = record["payload"]
        sessions.append({
            "id": payload.get("id") or payload.get("session_id") or file_path.stem.removeprefix("rollout-"),
            "provider": payload.get("model_provider", "(missing)"),
            "cwd": payload.get("cwd", ""),
            "timestamp": payload.get("timestamp") or record.get("timestamp") or "",
            "source": payload.get("source") or "vscode",
            "thread_source": payload.get("thread_source") or "user",
            "cli_version": payload.get("cli_version") or "",
            "has_user_event": file_has_user_event(file_path),
            "file_path": file_path,
        })
    return sessions


def read_session_index_names(codex_home: Path) -> dict[str, dict[str, str]]:
    index_path = codex_home / "session_index.jsonl"
    names: dict[str, dict[str, str]] = {}
    if not index_path.exists():
        return names
    with index_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            thread_id = item.get("id")
            if not thread_id:
                continue
            previous = names.get(thread_id)
            if previous is None or parse_time_ms(item.get("updated_at")) >= parse_time_ms(previous.get("updated_at")):
                names[thread_id] = {
                    "name": item.get("thread_name") or "",
                    "updated_at": item.get("updated_at") or "",
                }
    return names


def build_conversation_details(codex_home: Path) -> list[dict[str, Any]]:
    rollouts = read_rollout_sessions(codex_home)
    index_names = read_session_index_names(codex_home)
    sqlite_rows = read_sqlite_thread_rows(codex_home)
    details: list[dict[str, Any]] = []
    for session in rollouts:
        thread_id = str(session["id"])
        index_name = (index_names.get(thread_id) or {}).get("name")
        sqlite_row = sqlite_rows.get(thread_id)
        detail = dict(session)
        detail.update({
            "name": clean_text(index_name or (sqlite_row or {}).get("title") or thread_id),
            "sqlite_title": clean_text((sqlite_row or {}).get("title"), "") if sqlite_row else "",
            "in_sqlite": sqlite_row is not None,
            "archived": "是" if sqlite_row and sqlite_row.get("archived") else "否",
            "index_updated_at": (index_names.get(thread_id) or {}).get("updated_at", ""),
        })
        details.append(detail)
    details.sort(key=lambda item: parse_time_ms(item.get("index_updated_at") or item.get("timestamp")), reverse=True)
    return details


def to_path_array(value: Any) -> list[str]:
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, str) and entry.strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def dedupe_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in paths:
        comparable = normalize_comparable_path(value)
        if not comparable or comparable in seen:
            continue
        seen.add(comparable)
        result.append(value)
    return result


def read_workspace_roots_from_global_state(state: dict[str, Any]) -> list[str]:
    saved_roots = to_path_array(state.get("electron-saved-workspace-roots"))
    project_order = to_path_array(state.get("project-order"))
    active_roots = to_path_array(state.get("active-workspace-roots"))
    source = [*project_order, *saved_roots, *active_roots] if project_order else [*saved_roots, *active_roots]
    return dedupe_paths([to_desktop_workspace_path(path) for path in source])


def build_time_expression(columns: set[str]) -> str:
    expressions = []
    if "updated_at_ms" in columns:
        expressions.append("updated_at_ms")
    if "updated_at" in columns:
        expressions.append("updated_at * 1000")
    if "created_at_ms" in columns:
        expressions.append("created_at_ms")
    if "created_at" in columns:
        expressions.append("created_at * 1000")
    expressions.append("0")
    return f"COALESCE({', '.join(expressions)})"


def format_rank_preview(ranks: list[int], max_count: int = 12) -> str:
    preview = ", ".join(str(rank) for rank in ranks[:max_count])
    remaining = len(ranks) - min(len(ranks), max_count)
    return f"{preview} (+{remaining} more)" if remaining > 0 else preview


def read_project_thread_visibility(codex_home: Path, page_size: int = 50) -> list[dict[str, Any]]:
    global_state_path = codex_home / ".codex-global-state.json"
    if not global_state_path.exists():
        return []
    state = json.loads(global_state_path.read_text(encoding="utf-8"))
    roots = read_workspace_roots_from_global_state(state)
    if not roots:
        return []
    db_path = existing_state_db_path(codex_home)
    if not db_path:
        return [{
            "root": root,
            "interactiveThreads": 0,
            "firstPageThreads": 0,
            "exactCwdMatches": 0,
            "verbatimCwdRows": 0,
            "ranks": [],
            "rankPreview": "",
            "providerCounts": {},
        } for root in roots]

    with sqlite_connect(db_path, read_only=True) as conn:
        columns = table_columns(conn, "threads")
        if "cwd" not in columns:
            return []
        source_filter = "AND source IN ('cli', 'vscode')" if "source" in columns else ""
        archived_filter = "AND archived = 0" if "archived" in columns else ""
        first_user_filter = "AND first_user_message <> ''" if "first_user_message" in columns else ""
        provider_expression = "model_provider" if "model_provider" in columns else "'' AS model_provider"
        time_expression = build_time_expression(columns)
        rows = conn.execute(f"""
            SELECT id, cwd, {provider_expression}, {time_expression} AS sort_ts
            FROM threads
            WHERE cwd IS NOT NULL AND cwd <> ''
              {archived_filter}
              {first_user_filter}
              {source_filter}
            ORDER BY sort_ts DESC, id DESC
        """).fetchall()

    ranked_rows = []
    for index, row in enumerate(rows, start=1):
        ranked_rows.append({
            **dict(row),
            "rank": index,
            "normalized_cwd": normalize_comparable_path(row["cwd"]),
            "desktop_cwd": to_desktop_workspace_path(row["cwd"]),
        })

    result = []
    for root in roots:
        normalized_root = normalize_comparable_path(root)
        exact_root = to_desktop_workspace_path(root)
        matching_rows = [row for row in ranked_rows if row["normalized_cwd"] == normalized_root]
        ranks = [int(row["rank"]) for row in matching_rows]
        provider_counts: Counter[str] = Counter()
        exact_cwd_matches = 0
        verbatim_cwd_rows = 0
        for row in matching_rows:
            provider_counts[row.get("model_provider") or "(missing)"] += 1
            if row.get("cwd") == exact_root:
                exact_cwd_matches += 1
            if isinstance(row.get("cwd"), str) and row["cwd"].startswith("\\\\?\\"):
                verbatim_cwd_rows += 1
        result.append({
            "root": exact_root,
            "interactiveThreads": len(matching_rows),
            "firstPageThreads": len([rank for rank in ranks if rank <= page_size]),
            "exactCwdMatches": exact_cwd_matches,
            "verbatimCwdRows": verbatim_cwd_rows,
            "topRank": ranks[0] if ranks else None,
            "ranks": ranks,
            "rankPreview": format_rank_preview(ranks),
            "providerCounts": dict(provider_counts),
        })
    return result


def format_project_visibility(projects: list[dict[str, Any]]) -> str:
    if not projects:
        return "无项目可见性诊断"
    return "\n  ".join(
        f"{project['root']}：交互 {project['interactiveThreads']} 条，首页 {project['firstPageThreads']}/50，"
        f"排名 {project.get('rankPreview') or '无'}，精确路径 {project['exactCwdMatches']}/{project['interactiveThreads']}"
        for project in projects
    )


def get_status(codex_home: Path) -> dict[str, Any]:
    if not codex_home.exists():
        raise FileNotFoundError(f"Codex home 不存在：{codex_home}")
    config_text = read_config_text(codex_home)
    current_provider, current_provider_implicit = read_current_provider_from_config_text(config_text)
    configured_providers = list_configured_provider_ids(config_text)
    rollout = collect_rollout_status(codex_home, "__status_only__")
    sqlite_counts = read_sqlite_provider_counts(codex_home)
    sqlite_repair_stats = None
    if sqlite_counts and not sqlite_counts.get("unreadable"):
        sqlite_repair_stats = read_sqlite_repair_stats(
            codex_home,
            rollout["user_event_thread_ids"],
            rollout["thread_cwd_by_id"],
        )
    project_thread_visibility = [] if sqlite_counts and sqlite_counts.get("unreadable") else read_project_thread_visibility(codex_home)
    backup_root = provider_backup_root(codex_home)
    return {
        "codexHome": str(codex_home),
        "currentProvider": current_provider,
        "currentProviderImplicit": current_provider_implicit,
        "configuredProviders": configured_providers,
        "rolloutCounts": rollout["provider_counts"],
        "lockedRolloutFiles": rollout["locked_paths"],
        "encryptedContentCounts": rollout["encrypted_counts"],
        "sqliteCounts": sqlite_counts,
        "stateDbLocation": detect_state_db(codex_home),
        "sqliteRepairStats": sqlite_repair_stats,
        "projectThreadVisibility": project_thread_visibility,
        "backupRoot": str(backup_root),
    }


def get_sqlite_session_counts(status: dict[str, Any]) -> dict[str, int]:
    sqlite_counts = status.get("sqliteCounts")
    if sqlite_counts and not sqlite_counts.get("unreadable"):
        return sqlite_counts.get("sessions") or {}
    return {}


def get_repair_total(status: dict[str, Any], hidden_count: int) -> dict[str, int]:
    repair_stats = status.get("sqliteRepairStats") or {}
    user_event = int(repair_stats.get("userEventRowsNeedingRepair") or 0)
    cwd = int(repair_stats.get("cwdRowsNeedingRepair") or 0)
    return {"userEvent": user_event, "cwd": cwd, "total": hidden_count + user_event + cwd}


def print_status(codex_home: Path) -> None:
    status = get_status(codex_home)
    conversations = build_conversation_details(codex_home)
    rollout_session_counts = status["rolloutCounts"].get("sessions") or {}
    sqlite_session_counts = get_sqlite_session_counts(status)
    rollout_total = sum_counts(rollout_session_counts)
    sqlite_total = sum_counts(sqlite_session_counts)
    not_in_sqlite = max(0, rollout_total - sqlite_total)
    hidden_threads = count_hidden(rollout_session_counts, status["currentProvider"])
    repair = get_repair_total(status, hidden_threads)
    sqlite_database = str(status["stateDbLocation"]["path"]) if status.get("stateDbLocation") else "未找到"
    if status.get("sqliteCounts", {}).get("unreadable"):
        sqlite_counts_text = f"不可读取：{status['sqliteCounts'].get('error') or '数据库异常'}"
    else:
        sqlite_counts_text = (
            f"数据库 {sqlite_database}；入库对话 {sqlite_total} 条（{format_counts(sqlite_session_counts)}）；"
            f"未入库对话文件 {not_in_sqlite} 条；user-event 待修 {repair['userEvent']} 条；cwd 路径待修 {repair['cwd']} 条"
        )

    print(f"对话仓库：{status['codexHome']}")
    print()
    print(f"本电脑有 {len(status['configuredProviders'])} 个 provider：{'，'.join(status['configuredProviders'])}；当前的 provider 是：{status['currentProvider']}")
    print()
    print(f"对话记录有 {rollout_total} 条；分别是：{format_counts(rollout_session_counts)}；隐藏的对话有：{hidden_threads} 条")
    print()
    print("对话明细：")
    for index, conversation in enumerate(conversations, start=1):
        sqlite_status = "已入库" if conversation["in_sqlite"] else "未入库"
        id_label = pad_display(f"{index}. ID：{conversation['id']}", 50)
        name_label = pad_display(f"Desktop 命名：{shorten(conversation['name'], 30)}", 52)
        cwd_label = pad_display(f"cwd：{shorten(conversation.get('cwd') or '无', 52)}", 58)
        print(f"  {id_label}{name_label}{cwd_label}（{sqlite_status}）")
        print()
    print()
    print(f"SQLite state：{sqlite_counts_text}")
    print()
    print("Project visibility：")
    print(f"  {format_project_visibility(status['projectThreadVisibility'])}")
    print()
    print(f"同步备份在：{status['backupRoot']}")
    print(f"对话恢复备份在：{conversation_backup_root(codex_home)}")
    print()
    print(f"预计恢复/修复的条数：{repair['total']} 条（隐藏 {hidden_threads}，user-event {repair['userEvent']}，cwd {repair['cwd']}）")
    print()
    print(f"未入库对话：{not_in_sqlite} 条。输入 B 可将未入库对话写入 SQLite。")
    print()


def copy_directory(source_dir: Path, target_dir: Path) -> None:
    if target_dir.exists():
        shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)
    else:
        shutil.copytree(source_dir, target_dir)


def list_conversation_backups(codex_home: Path) -> list[dict[str, Any]]:
    root = conversation_backup_root(codex_home)
    if not root.exists():
        return []
    backups = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        sessions_path = entry / "sessions"
        if not sessions_path.exists():
            continue
        backups.append({
            "name": entry.name,
            "full_path": entry,
            "sessions_path": sessions_path,
            "count": len(list_rollout_files(sessions_path)),
            "mtime": entry.stat().st_mtime,
        })
    return sorted(backups, key=lambda item: item["name"], reverse=True)


def prune_conversation_backups(codex_home: Path, keep: int = 5) -> dict[str, int]:
    backups = list_conversation_backups(codex_home)
    to_delete = backups[keep:]
    for backup in to_delete:
        shutil.rmtree(backup["full_path"], ignore_errors=True)
    return {"deleted": len(to_delete), "remaining": max(0, len(backups) - len(to_delete))}


def create_conversation_backup(codex_home: Path) -> dict[str, Any]:
    source_sessions = codex_home / "sessions"
    if not source_sessions.exists():
        return {"created": False, "reason": f"sessions 目录不存在：{source_sessions}"}
    root = conversation_backup_root(codex_home)
    backup_dir = unique_timestamp_dir(root)
    backup_dir.mkdir(parents=True, exist_ok=True)
    copy_directory(source_sessions, backup_dir / "sessions")
    prune_result = prune_conversation_backups(codex_home, 5)
    return {
        "created": True,
        "backupDir": backup_dir,
        "deletedOldBackups": prune_result["deleted"],
        "remainingBackups": prune_result["remaining"],
    }


def read_rollout_meta(file_path: Path) -> dict[str, Any] | None:
    first_line, _, _, _ = read_first_line_record(file_path)
    record = parse_session_meta(first_line)
    if not record:
        return None
    payload = record["payload"]
    return {
        "id": payload.get("id") or payload.get("session_id") or file_path.stem.removeprefix("rollout-"),
        "timestamp": payload.get("timestamp") or record.get("timestamp") or "",
        "source_path": file_path,
    }


def list_backup_conversation_files(backup: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for file_path in list_rollout_files(backup["sessions_path"]):
        try:
            meta = read_rollout_meta(file_path)
        except (OSError, json.JSONDecodeError):
            meta = None
        if meta:
            result.append(meta)
    result.sort(key=lambda item: parse_time_ms(item.get("timestamp")), reverse=True)
    return result


def target_path_for_backup_rollout(codex_home: Path, backup: dict[str, Any], source_path: Path) -> Path:
    relative = os.path.relpath(source_path, backup["sessions_path"])
    if relative.startswith("..") or os.path.isabs(relative):
        raise RuntimeError(f"备份文件路径异常：{source_path}")
    return codex_home / "sessions" / relative


def restore_conversation_files(codex_home: Path, backup: dict[str, Any], requested_count: int) -> dict[str, Any]:
    conversations = list_backup_conversation_files(backup)
    count = len(conversations) if requested_count >= len(conversations) else max(0, requested_count)
    selected = conversations[:count]
    index_names = read_session_index_names(codex_home)
    for item in selected:
        target_path = target_path_for_backup_rollout(codex_home, backup, item["source_path"])
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item["source_path"], target_path)
    return {
        "restored": len(selected),
        "available": len(conversations),
        "items": [
            {
                "id": item["id"],
                "name": clean_text((index_names.get(item["id"]) or {}).get("name") or item["id"]),
            }
            for item in selected
        ],
    }


def delete_conversation_backups(codex_home: Path) -> dict[str, Any]:
    root = conversation_backup_root(codex_home)
    backups = list_conversation_backups(codex_home)
    if backups:
        shutil.rmtree(root, ignore_errors=True)
    return {"deleted": len(backups), "root": root}


def parse_positive_integer(value: str) -> int | None:
    text = str(value or "").strip()
    if not re.match(r"^\d+$", text):
        return None
    parsed = int(text, 10)
    return parsed if parsed > 0 else None


def run_restore_flow(codex_home: Path) -> None:
    initial_backups = list_conversation_backups(codex_home)
    print(f"可恢复对话文件备份：{len(initial_backups)} 个")
    if not initial_backups:
        print(f"恢复选项不可用：{conversation_backup_root(codex_home)} 下没有包含 sessions/ 的备份。")
        return

    delete_answer = input("是否删除所有对话文件备份？是请输入 YES，不是任意键：")
    if delete_answer.strip() == "YES":
        result = delete_conversation_backups(codex_home)
        print(f"已删除 {result['deleted']} 个对话文件备份：{result['root']}")

    backups = list_conversation_backups(codex_home)
    if not backups:
        print("没有可用于恢复的对话文件备份。")
        return

    target_backup = backups[0]
    latest_answer = input("在备份中选择最新的备份进行恢复吗？是：Y，不是：N：")
    if latest_answer.strip().upper() != "Y":
        print("所有可恢复备份：")
        for index, backup in enumerate(backups, start=1):
            print(f"  {index}. {backup['name']}（{backup['count']} 条对话）")
        while True:
            index_answer = input("以第几个为恢复目标：")
            backup_index = parse_positive_integer(index_answer)
            if backup_index and backup_index <= len(backups):
                target_backup = backups[backup_index - 1]
                break
            print(f"请输入 1 到 {len(backups)} 之间的编号。")

    backup_files = list_backup_conversation_files(target_backup)
    while True:
        count_answer = input(f"想要恢复的对话数（该备份共有 {len(backup_files)} 条）：")
        requested_count = parse_positive_integer(count_answer)
        if requested_count is not None:
            break
        print("请输入正整数。")

    actual_count = len(backup_files) if requested_count >= len(backup_files) else requested_count
    print(f"将从备份 {target_backup['name']} 恢复最近 {actual_count} 条对话。")
    confirm = input("确认恢复吗？Y：确认；N：退出：")
    if confirm.strip().upper() != "Y":
        print("已退出，未恢复任何内容。")
        return

    result = restore_conversation_files(codex_home, target_backup, requested_count)
    print(f"恢复完成：{result['restored']}/{result['available']} 条。")
    if result["items"]:
        print("恢复 ID 列表：")
        for index, item in enumerate(result["items"], start=1):
            id_label = pad_display(f"{index}. ID：{item['id']}", 50)
            print(f"  {id_label}Desktop 命名：{shorten(item['name'], 50)}")


def backup_sqlite_files(codex_home: Path, db_path: Path) -> Path:
    backup_dir = codex_home / "backups_state" / "provider-sync-local-backfill" / timestamp_for_path()
    backup_dir.mkdir(parents=True, exist_ok=True)
    for source_path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        if source_path.exists():
            shutil.copy2(source_path, backup_dir / source_path.name)
    return backup_dir


def build_insert_row(conversation: dict[str, Any]) -> dict[str, Any]:
    title = clean_text(conversation.get("name") or conversation.get("id"))
    created_at = to_unix_seconds(conversation.get("timestamp"))
    created_at_ms = to_unix_ms(conversation.get("timestamp"))
    index_updated_ms = parse_time_ms(conversation.get("index_updated_at"))
    updated_at_ms = index_updated_ms if index_updated_ms else created_at_ms
    updated_at = int(updated_at_ms / 1000)
    return {
        "id": conversation["id"],
        "rollout_path": str(conversation["file_path"]),
        "created_at": created_at,
        "updated_at": updated_at,
        "source": conversation.get("source") or "vscode",
        "model_provider": conversation.get("provider") or "custom",
        "cwd": conversation.get("cwd") or "",
        "title": title,
        "sandbox_policy": '{"type":"disabled"}',
        "approval_mode": "never",
        "tokens_used": 0,
        "has_user_event": 1 if conversation.get("has_user_event") else 0,
        "archived": 0,
        "archived_at": None,
        "git_sha": None,
        "git_branch": None,
        "git_origin_url": None,
        "cli_version": conversation.get("cli_version") or "",
        "first_user_message": title,
        "agent_nickname": None,
        "agent_role": None,
        "memory_mode": "enabled",
        "model": None,
        "reasoning_effort": None,
        "agent_path": None,
        "created_at_ms": created_at_ms,
        "updated_at_ms": updated_at_ms,
        "thread_source": conversation.get("thread_source") or "user",
        "preview": title,
    }


def backfill_missing_threads(codex_home: Path) -> dict[str, Any]:
    db_path = existing_state_db_path(codex_home)
    if not db_path:
        raise RuntimeError("找不到 state_5.sqlite，无法入库。")
    conversations = build_conversation_details(codex_home)
    missing = [conversation for conversation in conversations if not conversation["in_sqlite"]]
    backup_dir = backup_sqlite_files(codex_home, db_path)
    if not missing:
        return {"inserted": 0, "backupDir": backup_dir, "ids": []}

    with sqlite_connect(db_path) as conn:
        columns = table_columns(conn, "threads")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for conversation in missing:
                row = {key: value for key, value in build_insert_row(conversation).items() if key in columns}
                names = list(row.keys())
                placeholders = ", ".join(f":{name}" for name in names)
                conn.execute(
                    f"INSERT INTO threads ({', '.join(names)}) VALUES ({placeholders})",
                    row,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {
        "inserted": len(missing),
        "backupDir": backup_dir,
        "ids": [conversation["id"] for conversation in missing],
    }


def backfill(codex_home: Path) -> None:
    result = backfill_missing_threads(codex_home)
    print("入库完成。")
    print(f"本次备份：{result['backupDir']}")
    print(f"新增 SQLite threads：{result['inserted']} 条")
    if result["ids"]:
        print(f"新增 ID：{'，'.join(result['ids'])}")


def copy_provider_sync_backup(codex_home: Path, target_provider: str, changes: list[dict[str, Any]]) -> Path:
    backup_dir = unique_timestamp_dir(provider_backup_root(codex_home))
    backup_dir.mkdir(parents=True, exist_ok=True)
    config_path = codex_home / "config.toml"
    if config_path.exists():
        shutil.copy2(config_path, backup_dir / "config.toml")
    db_path = existing_state_db_path(codex_home)
    if db_path:
        sqlite_backup = backup_dir / "sqlite"
        sqlite_backup.mkdir(parents=True, exist_ok=True)
        for source_path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            if source_path.exists():
                shutil.copy2(source_path, sqlite_backup / source_path.name)
    session_manifest = []
    for change in changes:
        source_path = change["path"]
        relative = os.path.relpath(source_path, codex_home)
        target_path = backup_dir / relative
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        session_manifest.append({
            "path": str(source_path),
            "backupPath": str(target_path),
            "threadId": change.get("thread_id"),
            "originalProvider": change.get("original_provider"),
        })
    (backup_dir / "manifest.json").write_text(
        json.dumps({
            "createdAt": datetime.now(tz=timezone.utc).isoformat(),
            "targetProvider": target_provider,
            "changedSessionFiles": session_manifest,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return backup_dir


def prune_provider_backups(codex_home: Path, keep: int = 5) -> dict[str, int]:
    root = provider_backup_root(codex_home)
    if not root.exists():
        return {"deletedCount": 0, "remaining": 0}
    backups = sorted([entry for entry in root.iterdir() if entry.is_dir()], key=lambda item: item.name, reverse=True)
    to_delete = backups[keep:]
    for entry in to_delete:
        shutil.rmtree(entry, ignore_errors=True)
    return {"deletedCount": len(to_delete), "remaining": max(0, len(backups) - len(to_delete))}


def rewrite_rollout_first_line(change: dict[str, Any]) -> bool:
    file_path: Path = change["path"]
    try:
        first_line, separator, rest, _ = read_first_line_record(file_path)
    except OSError:
        return False
    if first_line != change["original_first_line"]:
        return False
    tmp_path = file_path.with_name(f"{file_path.name}.provider-sync.{os.getpid()}.{int(datetime.now().timestamp() * 1000)}.tmp")
    try:
        tmp_path.write_bytes(change["updated_first_line"].encode("utf-8") + separator + rest)
        os.replace(tmp_path, file_path)
        os.utime(file_path, (file_path.stat().st_atime, change["mtime"]))
        return True
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def restore_rollout_first_lines(changes: list[dict[str, Any]]) -> None:
    for change in changes:
        file_path: Path = change["path"]
        try:
            file_path.write_bytes(change["original_data"])
            os.utime(file_path, (file_path.stat().st_atime, change["mtime"]))
        except OSError:
            pass


def read_thread_cwd_stats(codex_home: Path) -> list[dict[str, Any]]:
    db_path = existing_state_db_path(codex_home)
    if not db_path:
        return []
    with sqlite_connect(db_path, read_only=True) as conn:
        columns = table_columns(conn, "threads")
        if "cwd" not in columns:
            return []
        if "updated_at_ms" in columns and "updated_at" in columns:
            updated_expr = "COALESCE(MAX(updated_at_ms), MAX(updated_at) * 1000, 0)"
        elif "updated_at_ms" in columns:
            updated_expr = "COALESCE(MAX(updated_at_ms), 0)"
        elif "updated_at" in columns:
            updated_expr = "COALESCE(MAX(updated_at) * 1000, 0)"
        else:
            updated_expr = "0"
        rows = conn.execute(f"""
            SELECT cwd, COUNT(*) AS count, {updated_expr} AS updated_at_ms
            FROM threads
            WHERE cwd IS NOT NULL AND cwd <> ''
            GROUP BY cwd
            ORDER BY count DESC, updated_at_ms DESC, cwd
        """).fetchall()
    result = []
    for row in rows:
        normalized = normalize_comparable_path(row["cwd"])
        if normalized:
            result.append({
                "cwd": row["cwd"],
                "normalizedCwd": normalized,
                "count": int(row["count"] or 0),
                "updatedAtMs": int(row["updated_at_ms"] or 0),
            })
    return result


def resolve_stored_path(value: Any, cwd_stats: list[dict[str, Any]]) -> Any:
    comparable = normalize_comparable_path(value)
    if not comparable:
        return value
    matches = [entry for entry in cwd_stats if entry["normalizedCwd"] == comparable]
    if not matches:
        return to_desktop_workspace_path(value)
    matches.sort(key=lambda entry: (-entry["count"], -entry["updatedAtMs"], entry["cwd"]))
    return to_desktop_workspace_path(matches[0]["cwd"])


def copy_resolved_object_keys(value: Any, cwd_stats: list[dict[str, Any]]) -> Any:
    if not isinstance(value, dict):
        return value
    result = {}
    for key, nested_value in value.items():
        resolved = resolve_stored_path(key, cwd_stats)
        if resolved not in result or resolved == key:
            result[resolved] = nested_value
    return result


def count_array_changes(previous: list[str], next_values: list[str]) -> int:
    compared = max(len(previous), len(next_values))
    return sum(1 for index in range(compared) if (previous[index] if index < len(previous) else None) != (next_values[index] if index < len(next_values) else None))


def sync_workspace_roots(codex_home: Path) -> dict[str, Any]:
    file_path = codex_home / ".codex-global-state.json"
    backup_path = codex_home / ".codex-global-state.json.bak"
    if not file_path.exists():
        return {"present": False, "updated": False, "updatedWorkspaceRoots": 0, "savedWorkspaceRootCount": 0}

    original_text = file_path.read_text(encoding="utf-8")
    state = json.loads(original_text)
    cwd_stats = read_thread_cwd_stats(codex_home)
    existing_saved_roots = to_path_array(state.get("electron-saved-workspace-roots"))
    existing_project_order = to_path_array(state.get("project-order"))
    existing_active_roots = to_path_array(state.get("active-workspace-roots"))

    source_roots = [*existing_project_order, *existing_saved_roots, *existing_active_roots] if existing_project_order else [*existing_saved_roots, *existing_active_roots]
    next_saved_roots = dedupe_paths([resolve_stored_path(value, cwd_stats) for value in source_roots])
    project_source = [*existing_project_order, *existing_saved_roots] if existing_project_order else [*next_saved_roots]
    next_project_order = dedupe_paths([resolve_stored_path(value, cwd_stats) for value in project_source])
    next_active_roots = dedupe_paths([resolve_stored_path(value, cwd_stats) for value in existing_active_roots])
    next_labels = copy_resolved_object_keys(state.get("electron-workspace-root-labels"), cwd_stats)
    next_open_targets = state.get("open-in-target-preferences")
    if isinstance(next_open_targets, dict):
        next_open_targets = dict(next_open_targets)
        next_open_targets["perPath"] = copy_resolved_object_keys(next_open_targets.get("perPath"), cwd_stats)

    original_active_value = state.get("active-workspace-roots")
    next_active_value = next_active_roots if isinstance(original_active_value, list) else (next_active_roots[0] if next_active_roots else original_active_value)

    state["electron-saved-workspace-roots"] = next_saved_roots
    state["project-order"] = next_project_order
    state["active-workspace-roots"] = next_active_value
    if next_labels is not None:
        state["electron-workspace-root-labels"] = next_labels
    if next_open_targets is not None:
        state["open-in-target-preferences"] = next_open_targets

    next_text = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    updated = original_text != next_text or not backup_path.exists()
    if updated:
        file_path.write_text(next_text, encoding="utf-8")
        backup_path.write_text(next_text, encoding="utf-8")
    return {
        "present": True,
        "updated": updated,
        "updatedWorkspaceRoots": count_array_changes(existing_saved_roots, next_saved_roots),
        "savedWorkspaceRootCount": len(next_saved_roots),
    }


def update_sqlite_provider(
    codex_home: Path,
    target_provider: str,
    user_event_thread_ids: set[str],
    thread_cwd_by_id: dict[str, str],
    after_update,
) -> dict[str, Any]:
    db_path = existing_state_db_path(codex_home)
    if not db_path:
        after_update()
        return {
            "updatedRows": 0,
            "providerRowsUpdated": 0,
            "userEventRowsUpdated": 0,
            "cwdRowsUpdated": 0,
            "databasePresent": False,
        }

    with sqlite_connect(db_path) as conn:
        columns = table_columns(conn, "threads")
        conn.execute("BEGIN IMMEDIATE")
        try:
            provider_rows = 0
            if "model_provider" in columns:
                provider_rows = conn.execute(
                    "UPDATE threads SET model_provider = ? WHERE COALESCE(model_provider, '') <> ?",
                    (target_provider, target_provider),
                ).rowcount

            user_event_rows = 0
            if "has_user_event" in columns:
                for thread_id in user_event_thread_ids:
                    user_event_rows += conn.execute(
                        "UPDATE threads SET has_user_event = 1 WHERE id = ? AND COALESCE(has_user_event, 0) <> 1",
                        (thread_id,),
                    ).rowcount

            cwd_rows = 0
            if "cwd" in columns:
                for thread_id, cwd in thread_cwd_by_id.items():
                    if not thread_id or not cwd.strip():
                        continue
                    cwd_rows += conn.execute(
                        "UPDATE threads SET cwd = ? WHERE id = ? AND COALESCE(cwd, '') <> ?",
                        (cwd, thread_id, cwd),
                    ).rowcount

            after_update()
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {
        "updatedRows": provider_rows + user_event_rows + cwd_rows,
        "providerRowsUpdated": provider_rows,
        "userEventRowsUpdated": user_event_rows,
        "cwdRowsUpdated": cwd_rows,
        "databasePresent": True,
    }


def sync(codex_home: Path) -> None:
    config_text = read_config_text(codex_home)
    target_provider, _ = read_current_provider_from_config_text(config_text)
    rollout = collect_rollout_status(codex_home, target_provider)
    changes = rollout["changes"]

    print("正在扫描对话文件...")
    print("正在创建备份...")
    backup_dir = copy_provider_sync_backup(codex_home, target_provider, changes)

    applied_changes: list[dict[str, Any]] = []
    workspace_result = {"updatedWorkspaceRoots": 0, "savedWorkspaceRootCount": 0}

    def apply_files_and_workspace() -> None:
        nonlocal applied_changes, workspace_result
        print("正在更新对话文件...")
        for change in changes:
            if rewrite_rollout_first_line(change):
                applied_changes.append(change)
        workspace_result = sync_workspace_roots(codex_home)

    try:
        print("正在更新 SQLite...")
        sqlite_result = update_sqlite_provider(
            codex_home,
            target_provider,
            rollout["user_event_thread_ids"],
            rollout["thread_cwd_by_id"],
            apply_files_and_workspace,
        )
    except Exception:
        restore_rollout_first_lines(applied_changes)
        raise

    print("正在清理旧备份...")
    prune_provider_backups(codex_home, 5)

    print("保存完成。")
    print(f"目标 provider：{target_provider}")
    print(f"本次备份：{backup_dir}")
    print(f"已更新对话文件：{len(applied_changes)} 个")
    print(f"已更新 SQLite provider 行：{sqlite_result['providerRowsUpdated']} 行")
    print(f"已修复 user-event：{sqlite_result['userEventRowsUpdated']} 行")
    print(f"已修复 cwd 路径：{sqlite_result['cwdRowsUpdated']} 行")
    print(f"已更新项目路径缓存：{workspace_result.get('updatedWorkspaceRoots') or 0} 个")


def interactive(codex_home: Path) -> None:
    print_status(codex_home)
    print()
    print("选择：")
    backup_answer = input("是否进行对话恢复备份？Y：备份；其它键跳过：")
    if backup_answer.strip().upper() == "Y":
        backup_result = create_conversation_backup(codex_home)
        if backup_result.get("created"):
            print(f"对话恢复备份完成：{backup_result['backupDir']}")
            print(f"备份保留：{backup_result['remainingBackups']} 份；本次清理旧备份：{backup_result['deletedOldBackups']} 份")
        else:
            print(f"对话恢复备份跳过：{backup_result['reason']}")
    else:
        print("已跳过对话恢复备份。")
    print()
    first_answer = input("输入 B：将未入库的对话入库；其它键继续：")
    if first_answer.strip().upper() == "B":
        backfill(codex_home)
        return

    run_restore_flow(codex_home)

    final_answer = input("输入 Y 进行保存；其它键取消：")
    if final_answer.strip().upper() == "Y":
        sync(codex_home)
    else:
        print("已取消，未修改任何内容。")


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    codex_home = normalize_codex_home(argv[2] if len(argv) > 2 else None)
    try:
        if command == "status":
            print_status(codex_home)
        elif command == "sync":
            sync(codex_home)
        elif command == "backfill":
            backfill(codex_home)
        elif command == "interactive":
            interactive(codex_home)
        else:
            raise RuntimeError(f"未知命令：{command}")
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

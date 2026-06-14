# codex-provider-sync

A small Python tool that makes your old Codex conversations visible again after switching `model_provider` (e.g. account login ↔ API login).

让 Codex 切换 `model_provider`（账号登录 ↔ API 登录）后，消失的历史对话在列表里重新可见。

---

## The problem / 解决什么问题

Codex Desktop filters the conversation list by the **current `model_provider`**. When you switch provider (e.g. from account-based `openai` to API-based `custom`), old conversations tagged with the previous provider get hidden — they look like they "disappeared".

Codex Desktop 的对话列表**按当前 `model_provider` 过滤**。切换 provider 后，旧对话的 provider 标签和当前不一致，就被隐藏，看起来像“消失了”。

This tool syncs the provider tag of your conversations to the target provider, in **two places**:

本工具把会话的 provider 标签同步到目标 provider，改**两处**：

1. The `model_provider` field in the `threads` table of `~/.codex/sqlite/state_5.sqlite`
2. The `payload.model_provider` of the first `session_meta` line in `~/.codex/sessions/**/*.jsonl`

---

## Requirements / 环境要求

- Python 3.8+
- Codex Desktop installed (database at `~/.codex/sqlite/state_5.sqlite`)

> Note / 注意: This tool targets the **new** Codex DB layout where the database lives in the `~/.codex/sqlite/` subdirectory. Older tools that look in `~/.codex/` root will not work.

---

## Usage / 用法

**Close Codex completely first** (including background processes), or the database may be locked.

**先彻底关闭 Codex**（含后台进程），否则数据库被锁可能写失败。

```bash
# Enter the folder where the files are / 先进入文件所在的文件夹
cd "path/to/this/folder"

# Show current status (read-only) / 查看现状（只读）
python codex_provider_sync.py --status

# Preview what would change (no write) / 预览改动（不写入）
python codex_provider_sync.py --sync --dry-run

# Sync to the provider in config.toml (auto backup) / 同步到 config 的 provider（自动备份）
python codex_provider_sync.py --sync

# Sync to a specific provider / 同步到指定 provider
python codex_provider_sync.py --sync --provider openai

# Restore from a backup / 从备份还原
python codex_provider_sync.py --restore <backup_dir>
```

Windows users can just double-click **`sync.bat`** — it runs status → dry-run preview → asks for confirmation → syncs.

Windows 用户直接双击 **`sync.bat`**：依次跑现状 → 预览 → 确认 → 同步。

---

## Options / 参数

| Option | Description |
|--------|-------------|
| `--status` | Show status, change nothing (read-only) / 查看现状，只读 |
| `--sync` | Sync conversations to the target provider / 同步到目标 provider |
| `--provider <name>` | Target provider (default: read from `config.toml`) / 指定目标 provider |
| `--dry-run` | Preview only, no write / 只预览不写入 |
| `--include-archived` | Also process archived conversations / 同时处理已归档会话 |
| `--restore <dir>` | Restore DB and sessions from a backup dir / 从备份还原 |

---

## Safety / 安全说明

- **Auto backup**: every `--sync` first backs up to `~/.codex/backups_state/py-provider-sync/<timestamp>/` (keeps the latest few, prunes the rest). 每次同步前自动备份。
- ✅ Restores **visibility** of conversations in the list. 恢复列表可见性。
- ⚠️ Does **not** guarantee readability: conversations with `encrypted_content` may show in the list after a cross-provider switch but fail to open with `invalid_encrypted_content`. This is determined by Codex's encryption mechanism and cannot be bypassed by any tool. 含加密内容的会话可恢复可见，但点开续聊可能报 `invalid_encrypted_content`，无解。
- ❌ Does not touch conversation content, titles, ordering, or login/auth. 不改内容、标题、排序、登录。

---

## License

[MIT](LICENSE)

# codex-provider-sync

A small Python tool that makes old Codex Desktop conversations visible again after switching `model_provider` (for example: account login ↔ API login), with local backup/restore helpers.

一个 Python 小工具：当 Codex Desktop 切换 `model_provider`（例如账号登录 ↔ API 登录）后，让“消失”的历史对话重新在列表里可见，并提供本地备份与恢复辅助。

## The Problem / 解决什么问题

Codex Desktop filters the conversation list by the current `model_provider`. When you switch provider, old conversations tagged with the previous provider can be hidden. They may look like they disappeared, while the rollout files are still on disk.

Codex Desktop 的对话列表会按当前 `model_provider` 过滤。切换 provider 后，旧对话的 provider 标签和当前不一致，就可能被隐藏，看起来像“消失了”，但对话文件通常还在本地。

This tool can sync the provider tag of your conversations to the current provider in two places:

本工具可以把会话的 provider 标签同步到当前 provider，主要处理两处：

1. The `model_provider` field in the `threads` table of `~/.codex/sqlite/state_5.sqlite`
2. The `payload.model_provider` field in the first `session_meta` line of `~/.codex/sessions/**/*.jsonl`

It also repairs several visibility-related fields such as `has_user_event`, `cwd`, and workspace root cache where possible.

同时，它还会尽量修复影响可见性的 `has_user_event`、`cwd` 和项目路径缓存。

## Requirements / 环境要求

- Python 3.10+ recommended
- Codex Desktop installed
- New Codex DB layout:

```text
~/.codex/sqlite/state_5.sqlite
```

Note / 注意：

This tool targets the newer Codex database layout where the database lives in the `~/.codex/sqlite/` subdirectory. Older tools that only look in the `~/.codex/` root may not work.

本工具面向新的 Codex 数据库布局，也就是数据库位于 `~/.codex/sqlite/` 子目录。只查找 `~/.codex/` 根目录的旧工具可能无法工作。

## Files / 文件

```text
codex_provider_local_launcher.py      # Main Python tool / Python 主程序
同步恢复对话.bat                        # Windows launcher / Windows 启动脚本
Linux/同步恢复对话.sh                   # Linux launcher / Linux 启动脚本
Macos/同步恢复对话.command              # macOS launcher / macOS 启动脚本
github/                               # Upstream reference code / 上游参考代码
```

The current version uses the Python launcher only.

当前版本只调用 Python 文件。

## Usage / 用法

Close Codex completely first, including background processes, or the database may be locked.

先彻底关闭 Codex（包括后台进程），否则 SQLite 数据库可能被锁定，导致写入失败。

Enter the folder where the files are:

先进入文件所在目录：

```bash
cd "path/to/this/folder"
```

Show current status (read-only):

查看现状（只读）：

```bash
python codex_provider_local_launcher.py status
```

Start the interactive recovery/sync workflow:

启动交互式恢复/同步流程：

```bash
python codex_provider_local_launcher.py interactive
```

Backfill missing rollout conversations into SQLite:

将未入库的 rollout 对话写入 SQLite：

```bash
python codex_provider_local_launcher.py backfill
```

Sync conversations to the provider configured in `config.toml`:

同步到 `config.toml` 当前配置的 provider：

```bash
python codex_provider_local_launcher.py sync
```

Use a custom Codex home:

指定 Codex home：

```bash
python codex_provider_local_launcher.py status "/path/to/.codex"
python codex_provider_local_launcher.py interactive "/path/to/.codex"
```

## Windows / Windows 用户

Double-click:

直接双击：

```text
同步恢复对话.bat
```

The batch file runs the Python interactive workflow:

这个 bat 会启动 Python 交互流程：

```text
status display -> optional conversation backup -> optional backfill -> optional restore -> confirm sync
状态展示 -> 可选对话备份 -> 可选入库 -> 可选恢复 -> 确认同步
```

## Linux

```bash
chmod +x ./Linux/同步恢复对话.sh
./Linux/同步恢复对话.sh
```

Optional commands:

```bash
./Linux/同步恢复对话.sh status
./Linux/同步恢复对话.sh backfill
./Linux/同步恢复对话.sh sync
```

## macOS

```bash
chmod +x ./Macos/同步恢复对话.command
./Macos/同步恢复对话.command
```

You can also double-click the `.command` file.

也可以直接双击 `.command` 文件。

## Commands / 命令

| Command | 描述 |
|---|---|
| `status` | Show status, change nothing. / 查看现状，只读，不修改数据。 |
| `interactive` | Run the guided workflow. / 运行交互式流程。 |
| `backfill` | Insert rollout files that exist on disk but are missing from SQLite. / 将未入库对话写入 SQLite。 |
| `sync` | Sync provider metadata and repair visibility-related state. / 同步 provider 并修复可见性相关状态。 |

## Interactive Flow / 交互流程

The interactive mode prints:

交互模式会先显示：

```text
对话仓库
provider 数量和当前 provider
对话数量、隐藏数量
每条对话 ID / Desktop 命名 / cwd
SQLite state
Project visibility
同步备份目录
对话恢复备份目录
```

Then it asks:

然后询问：

```text
是否进行对话恢复备份？Y：备份；其它键跳过
输入 B：将未入库的对话入库；其它键继续
是否删除所有对话文件备份？
是否选择最新备份恢复？
想要恢复的对话数
确认恢复吗？
输入 Y 进行保存
```

Conversation backup is optional. It is created only when you answer `Y`.

对话恢复备份是可选的，只有输入 `Y` 才会创建。

## Backup / 备份

### Provider Sync Backup / 同步备份

Path:

```text
~/.codex/backups_state/provider-sync
```

Created before `sync`. It backs up SQLite/config/global-state related data before provider metadata is changed.

在执行 `sync` 前创建，用于保护 SQLite、配置和全局状态等数据。

Default retention:

```text
5 backups
```

### Conversation Restore Backup / 对话恢复备份

Path:

```text
~/.codex/backups_state/py-provider-sync
```

Created only when you choose `Y` in interactive mode. It backs up `~/.codex/sessions`.

只在交互模式中输入 `Y` 时创建，用于备份 `~/.codex/sessions`。

Default retention:

```text
5 backups
```

## Safety / 安全说明

- `status` is read-only. / `status` 只读。
- `backfill`, `sync`, and restore actions modify local Codex state. / `backfill`、`sync` 和恢复操作会修改本地 Codex 状态。
- Backups are created before sync/backfill operations where applicable. / 执行同步、入库等写操作前会尽量创建备份。
- This tool does not change login/auth. / 本工具不修改登录或认证信息。
- This tool does not intentionally rewrite conversation content. / 本工具不主动改写对话正文内容。
- Conversations containing `encrypted_content` may become visible after a cross-provider switch but still fail to open with `invalid_encrypted_content`. That is determined by Codex encryption and cannot be bypassed by this tool. / 含 `encrypted_content` 的会话可能恢复列表可见，但点开或续聊仍可能报 `invalid_encrypted_content`。这是 Codex 加密机制决定的，本工具无法绕过。

## Verify / 验证

```bash
python -m py_compile codex_provider_local_launcher.py
python codex_provider_local_launcher.py status
```

For Windows launcher:

验证 Windows 启动脚本：

```powershell
rg -n "codex_provider_local_launcher|PYTHON_EXE" ".\同步恢复对话.bat"
```

Expected: `.py` and `PYTHON_EXE` should appear.

正常情况下应看到 `.py` 和 `PYTHON_EXE`。

## Suggested .gitignore / 建议 .gitignore

```gitignore
__pycache__/
*.pyc
.DS_Store
Thumbs.db
```

## License

MIT

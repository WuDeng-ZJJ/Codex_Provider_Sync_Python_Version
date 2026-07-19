# Codex Provider Sync (Python Edition)

一个面向 **Codex Desktop 本地数据维护** 的 Python 工具。  
A Python tool for **maintaining local Codex Desktop data**.

当前版本定位为一个面向本地会话状态检查、备份、恢复、入库与修复的实用工具。  
The current version is positioned as a practical tool for local session inspection, backup, restore, backfill, and repair.

当前这版更像一个本地会话维护工具，重点是：

- 查看当前本机 Codex 对话状态
- 识别哪些对话已入库、哪些未入库
- 备份对话文件与本地状态
- 从备份中恢复最近若干条对话
- 将未入库对话补写入 SQLite
- 预览并清理没有对应 rollout 文件的 SQLite 异常记录
- 同步 provider 相关元数据
- 修复一部分影响列表可见性的本地状态

## What This Tool Does / 这工具现在能做什么

This project works on local Codex data under `~/.codex`.

本项目直接处理本机 `~/.codex` 目录中的数据，主要能力如下：

1. `status`
   - Read current local state only
   - 查看当前本地状态，不写入任何数据
2. `interactive`
   - Run the guided workflow for backup, restore, backfill, cleanup, and final save
   - 进入交互流程，按步骤执行备份、恢复、入库、异常清理、保存
3. `backfill`
   - Insert conversations that exist in rollout/session files but are missing from SQLite
   - 将磁盘上存在、但 SQLite 里没有记录的对话写回 SQLite
4. `cleanup`
   - Preview and delete SQLite thread rows whose rollout files no longer exist
   - 预览并删除数据库中存在、但 rollout 对话文件已经不存在的异常记录
5. `sync`
   - Sync provider-related metadata and repair related local state
   - 同步 provider 相关元数据，并修复相关本地状态

## Why This Exists / 为什么会有这个工具

In Codex Desktop, conversations may appear to "disappear" for several different local-state reasons.

在 Codex Desktop 里，对话看起来“消失了”，很多时候并不是真的没了，而是本地状态有问题。常见情况包括：

- the current `model_provider` does not match older conversation metadata  
  当前 `model_provider` 和旧对话记录中的 provider 不一致
- conversation files still exist, but matching rows are missing in SQLite  
  对话文件还在，但 SQLite 里的 `threads` 记录缺失
- SQLite rows still exist, but their rollout files no longer exist  
  SQLite 记录仍存在，但对应的 rollout 对话文件已经不存在
- `cwd` or workspace-root cache is stale  
  `cwd` 或工作区缓存过期
- related local visibility state is inconsistent  
  一些和可见性相关的本地状态不一致

So this tool is meant to inspect, back up, restore, backfill, sync, and repair local conversation state.

## Core Local Data / 涉及的本地数据

Main paths used by this tool:

```text
~/.codex/config.toml
~/.codex/sqlite/state_5.sqlite
~/.codex/session_index.jsonl
~/.codex/sessions/**/*.jsonl
~/.codex/.codex-global-state.json
```

When `sync` runs, the code currently updates or repairs data related to:

- `threads.model_provider` in SQLite
- the first `session_meta` line in conversation rollout files
- `has_user_event`
- `cwd`
- workspace root cache

这些能力都来自当前本地 Python 程序本身，不是 README 里额外虚构出来的说明。

## Project Files / 项目文件

Current local structure:

```text
codex_provider_local_launcher.py
close_codex_desktop.ps1
README.md
同步恢复对话.bat
Linux/
  同步恢复对话.sh
Macos/
  同步恢复对话.command
```

All launchers call the same Python program. The Windows launcher first calls `close_codex_desktop.ps1` to close the installed `OpenAI.Codex` desktop package:

- Windows: `同步恢复对话.bat`
- Linux: `Linux/同步恢复对话.sh`
- macOS: `Macos/同步恢复对话.command`

## Requirements / 环境要求

- Python 3.10+ recommended
- Codex Desktop local data available
- Newer Codex layout expected:

```text
~/.codex/sqlite/state_5.sqlite
```

## Quick Start / 快速开始

On Windows, `同步恢复对话.bat` closes Codex Desktop and its package-owned background processes before entering the maintenance workflow. It first requests a normal window close, then force-stops package processes only if they remain after five seconds. Independently installed Codex CLI processes outside the `OpenAI.Codex` package directory are not targeted.

在 Windows 上，`同步恢复对话.bat` 会先关闭 Codex Desktop 及其属于安装包的后台进程，再进入维护流程。脚本先请求正常关窗；五秒后仍未退出时才强制结束。安装在 `OpenAI.Codex` 包目录之外的独立 Codex CLI 不在关闭范围内。

On Linux and macOS, close Codex Desktop completely before any write operation.

### Windows

Double-click:

```text
同步恢复对话.bat
```

The Windows launcher verifies its helper and Python first, closes Codex Desktop, and then starts the existing interactive workflow.

or run:

```powershell
.\同步恢复对话.bat
```

### Linux

```bash
chmod +x ./Linux/同步恢复对话.sh
./Linux/同步恢复对话.sh
```

### macOS

```bash
chmod +x ./Macos/同步恢复对话.command
./Macos/同步恢复对话.command
```

## Command Usage / 命令用法

Enter the folder first:

```bash
cd "path/to/this/folder"
```

Show current status:

```bash
python codex_provider_local_launcher.py status
```

Run interactive mode:

```bash
python codex_provider_local_launcher.py interactive
```

Backfill missing SQLite thread rows:

```bash
python codex_provider_local_launcher.py backfill
```

Preview and clean SQLite rows whose rollout files are missing:

```bash
python codex_provider_local_launcher.py cleanup
```

`cleanup` lists every candidate first and only writes after an explicit `Y` confirmation.

Sync provider metadata and related local state:

```bash
python codex_provider_local_launcher.py sync
```

Use another Codex home if needed:

```bash
python codex_provider_local_launcher.py status "/path/to/.codex"
python codex_provider_local_launcher.py interactive "/path/to/.codex"
```

## What Interactive Mode Shows / 交互模式会显示什么

The current interactive flow prints local diagnostics first, including:

- 对话仓库
- 本机 provider 数量与当前 provider
- 对话记录数量
- 内部子线程数量（不计入 Desktop 侧栏对话）
- 隐藏对话数量
- 每条对话的 `ID`
- Codex Desktop 中的命名
- `cwd`
- SQLite state
- Project visibility
- 同步备份位置
- 对话恢复备份位置

This matches the current local script behavior.

这部分是按当前本地脚本的实际输出逻辑整理的，不是旧版 README 的泛化描述。

## Interactive Workflow / 交互流程

The current interactive workflow is:

1. Ask whether to create a conversation-restore backup first  
   先询问是否创建“对话恢复备份”
2. Choose `B` to backfill conversations missing from SQLite, or `C` to preview database cleanup candidates  
   可输入 `B` 将未入库对话写入 SQLite，或输入 `C` 预览数据库异常记录
3. Cleanup requires a second explicit `Y` confirmation and creates a full SQLite backup first  
   异常清理要求再次输入 `Y`，并先创建完整 SQLite 备份
4. Enter restore flow when neither `B` nor `C` is selected  
   进入恢复流程
5. Optionally delete all restore backups  
   可选删除全部恢复备份
6. Choose latest backup or manually choose one backup  
   选择最新备份，或手动选择某一份备份
7. Enter how many conversations to restore  
   输入要恢复多少条对话
8. Confirm restore  
   确认是否恢复
9. Ask whether to save at the end  
   最后询问是否保存
10. If confirmed, run `sync`  
   若确认保存，则执行同步

## Backup Types / 备份类型

### 1. Provider Sync Backup / 同步备份

Path:

```text
~/.codex/backups_state/provider-sync
```

Used before `sync` writes local provider-related state.

用于 `sync` 写入前，备份相关本地状态。

Current retention in code:

```text
5 backups
```

This is implemented in:

- `prune_provider_backups(codex_home, 5)`

### 2. Conversation Restore Backup / 对话恢复备份

Path:

```text
~/.codex/backups_state/py-provider-sync
```

Used to back up conversation/session rollout files before restore operations.

用于在恢复前备份对话 rollout/session 文件。

The backup keeps both top-level conversation files and their internal subagent files for completeness. Counts and restore selection only treat top-level conversations as conversations; related subagent files are restored automatically with their selected parent conversation.

备份会同时保留顶层对话文件和关联的内部子线程文件，以保证数据完整。界面计数和恢复数量只按顶层对话计算；选择恢复父对话时，关联子线程会自动随父对话恢复，不会单独占用对话数量。

Current retention in code:

```text
5 backups
```

This is implemented in:

- `prune_conversation_backups(codex_home, 5)`

### 3. SQLite Cleanup Backup / SQLite 异常清理备份

Path:

```text
~/.codex/backups_state/manual-db-cleanup/<timestamp>/
```

Each confirmed cleanup creates:

- `state_5.sqlite`: a consistent SQLite online backup taken before deletion
- `deleted_threads.json`: the full rows selected for deletion

每次确认清理都会先创建一致性的 SQLite 在线备份，并保存待删除记录的完整清单。清理过程使用单个事务，同时检查数据库完整性和外键状态。

## Important Notes / 重要说明

- `status` is read-only  
  `status` 只读
- `backfill`, `cleanup`, restore actions, and `sync` modify local data  
  `backfill`、`cleanup`、恢复、`sync` 都会改动本地数据
- `cleanup` only targets `threads` rows whose `rollout_path` is not an existing file, and requires explicit confirmation  
  `cleanup` 只处理 `rollout_path` 文件不存在的 `threads` 记录，并且必须明确确认
- the tool does not manage login or authentication  
  本工具不处理登录或认证
- it does not intentionally rewrite normal conversation body content  
  不会主动重写正常对话正文
- conversations with `encrypted_content` may become visible again but still fail to open after cross-provider switching  
  带 `encrypted_content` 的对话有可能重新可见，但跨 provider 后仍可能无法正常打开

## Verify / 验证

You can verify the current program like this:

```bash
python -m py_compile codex_provider_local_launcher.py
python codex_provider_local_launcher.py status
```

Check the Windows launcher is calling the Python file:

```powershell
rg -n "codex_provider_local_launcher|PYTHON_EXE" ".\\同步恢复对话.bat"
```

Expected:

- it should point to `codex_provider_local_launcher.py`
- it should detect `python` or `py`

## Suggested .gitignore / 建议 .gitignore

```gitignore
__pycache__/
*.pyc
.DS_Store
Thumbs.db
```

## License

MIT

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
- 同步 provider 相关元数据
- 修复一部分影响列表可见性的本地状态

## What This Tool Does / 这工具现在能做什么

This project works on local Codex data under `~/.codex`.

本项目直接处理本机 `~/.codex` 目录中的数据，主要能力如下：

1. `status`
   - Read current local state only
   - 查看当前本地状态，不写入任何数据
2. `interactive`
   - Run the guided workflow for backup, restore, backfill, and final save
   - 进入交互流程，按步骤执行备份、恢复、入库、保存
3. `backfill`
   - Insert conversations that exist in rollout/session files but are missing from SQLite
   - 将磁盘上存在、但 SQLite 里没有记录的对话写回 SQLite
4. `sync`
   - Sync provider-related metadata and repair related local state
   - 同步 provider 相关元数据，并修复相关本地状态

## Why This Exists / 为什么会有这个工具

In Codex Desktop, conversations may appear to "disappear" for several different local-state reasons.

在 Codex Desktop 里，对话看起来“消失了”，很多时候并不是真的没了，而是本地状态有问题。常见情况包括：

- the current `model_provider` does not match older conversation metadata  
  当前 `model_provider` 和旧对话记录中的 provider 不一致
- conversation files still exist, but matching rows are missing in SQLite  
  对话文件还在，但 SQLite 里的 `threads` 记录缺失
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
README.md
同步恢复对话.bat
Linux/
  同步恢复对话.sh
Macos/
  同步恢复对话.command
```

All launchers call the same Python program:

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

Before any write operation, close Codex Desktop completely, including background processes.

在执行写操作前，先彻底关闭 Codex Desktop，包括后台进程。

### Windows

Double-click:

```text
同步恢复对话.bat
```

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
2. Allow backfilling conversations missing from SQLite  
   可选择将未入库对话写入 SQLite
3. Enter restore flow  
   进入恢复流程
4. Optionally delete all restore backups  
   可选删除全部恢复备份
5. Choose latest backup or manually choose one backup  
   选择最新备份，或手动选择某一份备份
6. Enter how many conversations to restore  
   输入要恢复多少条对话
7. Confirm restore  
   确认是否恢复
8. Ask whether to save at the end  
   最后询问是否保存
9. If confirmed, run `sync`  
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

Current retention in code:

```text
5 backups
```

This is implemented in:

- `prune_conversation_backups(codex_home, 5)`

## Important Notes / 重要说明

- `status` is read-only  
  `status` 只读
- `backfill`, restore actions, and `sync` modify local data  
  `backfill`、恢复、`sync` 都会改动本地数据
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

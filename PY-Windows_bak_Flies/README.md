# Windows 纯 Python 版本

这个目录可以单独复制到另一台 Windows 电脑使用。它包含：

- `S.py`：Windows 启动器和 Codex Desktop 进程维护。
- `codex_provider_local_launcher.py`：同步、备份、恢复、补录和清理逻辑。
- `codex_delete_conversation.py`：按完整深度链接预览并精确删除单个对话。

## 使用

需要 Python 3.11 或更新版本。安装 Python 时若已关联 `.py` 文件，可以直接双击 `S.py`；也可以从资源管理器或独立的 PowerShell/命令提示符运行。不要从 Codex Desktop 的集成终端启动：

```powershell
cd "E:\.AI\.sessions_sync\PY-Windows"
python .\S.py
```

默认进入原来的交互菜单：输入 `S` 同步当前 provider，输入 `A` 按深度链接删除指定对话，其他输入进入备份、恢复、补录和异常清理流程。Codex 原子写入产生的 `..codex-global-state.json.tmp-*` 临时文件可能是零字节或截断的 JSON，按链接删除时会跳过它们，只处理正式状态文件及其 `.bak` 备份。

也支持直接指定命令和 `.codex` 目录：

```powershell
python .\S.py status
python .\S.py interactive "C:\Users\你的用户名\.codex"
python .\S.py sync
python .\S.py backfill
python .\S.py cleanup
```

启动器会在写入前检查业务模块和 `.codex` 目录，然后用 Windows 原生 API 向 `OpenAI.Codex` 进程发送正常关闭请求；等待五秒后仍有残留才会强制结束，并且会追踪该桌面进程的子进程。若启动器自身已经是 Codex 进程树的子进程，会停止并提示从独立终端重新启动，以免关闭自己的父进程。

运行结束会询问是否打开 ChatGPT。输入 `N` 不打开，其他输入（包括直接回车）打开本机应用。自动化检查可使用：

```powershell
python .\S.py --dry-run-close --no-open --no-pause
```

`--dry-run-close` 只枚举将处理的进程，不会关闭进程，也不会修改会话数据。

如果所在发行版的 Microsoft Store 包标识不同，可以在运行前设置 `CODEX_PACKAGE_FAMILY` 和 `CODEX_APP_USER_MODEL_ID`；默认值分别是 `OpenAI.Codex_2p2nqsd0c76g0` 和 `shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App`。

## 复制更新

当 `PY` 目录中的业务模块有更新时，请将下面两个文件一起复制到本目录，保持三个 Python 文件来自同一版本：

```text
codex_provider_local_launcher.py
codex_delete_conversation.py
```

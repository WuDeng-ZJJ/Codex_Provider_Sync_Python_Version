# -*- coding: utf-8 -*-
"""Portable Windows entry point for Codex session recovery and provider sync.

This file deliberately keeps the orchestration layer in Python.  The two
business modules next to it contain the existing backup, restore, sync,
backfill, cleanup, and exact-link deletion workflows.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import importlib
import os
from pathlib import Path
import subprocess
import sys
import time
from dataclasses import dataclass


MIN_PYTHON = (3, 11)
DEFAULT_PACKAGE_FAMILY = "OpenAI.Codex_2p2nqsd0c76g0"
DEFAULT_APP_USER_MODEL_ID = r"shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App"
# Store package identifiers can change between distributions.  Environment
# overrides keep the copied folder usable without editing the program.
PACKAGE_FAMILY = os.environ.get("CODEX_PACKAGE_FAMILY", DEFAULT_PACKAGE_FAMILY).strip() or DEFAULT_PACKAGE_FAMILY
APP_USER_MODEL_ID = os.environ.get("CODEX_APP_USER_MODEL_ID", DEFAULT_APP_USER_MODEL_ID).strip() or DEFAULT_APP_USER_MODEL_ID
PACKAGE_PROCESS_QUERY = 0x1000
PROCESS_TERMINATE = 0x0001
PROCESS_SYNCHRONIZE = 0x00100000
SNAPSHOT_PROCESSES = 0x00000002
WM_CLOSE = 0x0010
STILL_ACTIVE = 259
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_INVALID_PARAMETER = 87
ERROR_NO_MORE_FILES = 18

SCRIPT_DIR = Path(__file__).resolve().parent


def configure_output() -> None:
    """Keep Chinese and provider errors readable in a Windows console."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def recognizable_codex_home(path: Path) -> bool:
    """Return whether *path* looks like an existing Codex data directory."""
    if not path.is_dir():
        return False
    markers = (
        "config.toml",
        "sessions",
        "archived_sessions",
        "sqlite",
        "state_5.sqlite",
        ".codex-global-state.json",
    )
    return any((path / marker).exists() for marker in markers)


def absolute_path(value: str | os.PathLike[str]) -> Path:
    text = os.fspath(value).strip().strip('"')
    if not text:
        raise ValueError("路径不能为空。")
    return Path(text).expanduser().resolve()


def resolve_codex_home(command_line_value: str | None) -> tuple[Path, str]:
    """Resolve the data directory with the same fallback as the Windows BAT.

    An explicit command-line path is strict.  An invalid CODEX_HOME environment
    variable falls back to the user's normal ``.codex`` directory when that
    directory is recognizable, which prevents the common ``C:\\Users\\MI.codex``
    typo from redirecting maintenance to an empty directory.
    """
    user_root = os.environ.get("USERPROFILE") or os.environ.get("HOMEDRIVE", "") + os.environ.get("HOMEPATH", "")
    default = absolute_path(Path(user_root) / ".codex") if user_root else (Path.home() / ".codex").resolve()

    if command_line_value:
        selected = absolute_path(command_line_value)
        if not recognizable_codex_home(selected):
            raise RuntimeError(f"指定的 Codex 数据目录不存在或无法识别：{selected}")
        return selected, "命令行参数"

    configured = os.environ.get("CODEX_HOME")
    if configured:
        selected = absolute_path(configured)
        if recognizable_codex_home(selected):
            return selected, "CODEX_HOME 环境变量"
        if recognizable_codex_home(default):
            print(f"[WARN] CODEX_HOME 无效，回退到默认目录：{default}")
            return default, "用户默认目录（已忽略无效 CODEX_HOME）"
        raise RuntimeError(
            f"CODEX_HOME 指向不可识别的目录：{selected}\n"
            f"默认目录也不存在或不可识别：{default}\n"
            "请先启动一次 Codex Desktop，或修正 CODEX_HOME。"
        )

    if recognizable_codex_home(default):
        return default, "用户默认目录"
    raise RuntimeError(
        f"找不到 Codex 数据目录：{default}\n"
        "请先启动一次 Codex Desktop，或通过命令行传入 .codex 目录。"
    )


def load_business_modules():
    """Import both bundled modules before any desktop process is closed."""
    provider_path = SCRIPT_DIR / "codex_provider_local_launcher.py"
    delete_path = SCRIPT_DIR / "codex_delete_conversation.py"
    for path in (provider_path, delete_path):
        if not path.is_file():
            raise RuntimeError(f"缺少业务模块：{path}")

    # The interactive A branch imports codex_delete_conversation by module name.
    # Putting the bundled directory first makes the copied Windows folder
    # self-contained and prevents an unrelated module elsewhere on PYTHONPATH.
    for path in (provider_path, delete_path):
        source = path.read_bytes()
        try:
            compile(source, str(path), "exec")
        except SyntaxError as error:
            raise RuntimeError(f"业务模块无法在当前 Python 中解析：{path}\n{error}") from error
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    importlib.invalidate_caches()
    modules = []
    for name, path in (
        ("codex_provider_local_launcher", provider_path),
        ("codex_delete_conversation", delete_path),
    ):
        existing = sys.modules.get(name)
        existing_file = getattr(existing, "__file__", None) if existing else None
        if existing_file and Path(existing_file).resolve() != path:
            del sys.modules[name]
        try:
            module = importlib.import_module(name)
        except Exception as error:
            raise RuntimeError(f"加载业务模块失败：{path}\n{error}") from error
        modules.append(module)
    return modules[0], modules[1]


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    parent_pid: int
    name: str
    package_family: str | None = None


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class WindowsProcessController:
    """Close only the packaged Codex process tree using Win32 APIs."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("此启动器只能在 Windows 上运行。")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._configure_api()

    def _configure_api(self) -> None:
        k = self.kernel32
        u = self.user32
        k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k.Process32FirstW.restype = wintypes.BOOL
        k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k.Process32NextW.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.GetExitCodeProcess.restype = wintypes.BOOL
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateProcess.restype = wintypes.BOOL
        k.GetPackageFamilyName.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPWSTR,
        ]
        k.GetPackageFamilyName.restype = wintypes.LONG
        u.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.c_void_p), ctypes.c_void_p]
        u.EnumWindows.restype = wintypes.BOOL
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.PostMessageW.restype = wintypes.BOOL

    def _package_family(self, pid: int) -> str | None:
        handle = self.kernel32.OpenProcess(PACKAGE_PROCESS_QUERY, False, pid)
        if not handle:
            return None
        try:
            length = wintypes.DWORD(0)
            result = self.kernel32.GetPackageFamilyName(handle, ctypes.byref(length), None)
            if result != ERROR_INSUFFICIENT_BUFFER or length.value == 0:
                return None
            buffer = ctypes.create_unicode_buffer(length.value)
            result = self.kernel32.GetPackageFamilyName(handle, ctypes.byref(length), buffer)
            if result != 0:
                return None
            return buffer.value
        finally:
            self.kernel32.CloseHandle(handle)

    def snapshot(self, include_package_family: bool = False) -> list[ProcessInfo]:
        snapshot = self.kernel32.CreateToolhelp32Snapshot(SNAPSHOT_PROCESSES, 0)
        invalid = ctypes.c_void_p(-1).value
        if not snapshot or snapshot == invalid:
            raise ctypes.WinError(ctypes.get_last_error())
        result: list[ProcessInfo] = []
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not self.kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error == ERROR_NO_MORE_FILES:
                    return result
                raise ctypes.WinError(error)
            while True:
                family = self._package_family(entry.th32ProcessID) if include_package_family else None
                result.append(
                    ProcessInfo(
                        pid=int(entry.th32ProcessID),
                        parent_pid=int(entry.th32ParentProcessID),
                        name=str(entry.szExeFile),
                        package_family=family,
                    )
                )
                if not self.kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    error = ctypes.get_last_error()
                    if error not in (ERROR_NO_MORE_FILES, 0):
                        raise ctypes.WinError(error)
                    break
        finally:
            self.kernel32.CloseHandle(snapshot)
        return result

    @staticmethod
    def descendant_ids(processes: list[ProcessInfo], roots: set[int]) -> set[int]:
        children: dict[int, list[int]] = {}
        for process in processes:
            children.setdefault(process.parent_pid, []).append(process.pid)
        result = set(roots)
        pending = list(roots)
        while pending:
            parent = pending.pop()
            for child in children.get(parent, ()):
                if child not in result:
                    result.add(child)
                    pending.append(child)
        return result

    def _alive(self, pid: int) -> bool:
        handle = self.kernel32.OpenProcess(
            PACKAGE_PROCESS_QUERY | PROCESS_SYNCHRONIZE,
            False,
            pid,
        )
        if not handle:
            error = ctypes.get_last_error()
            # Access denied means the process may still be running; fail safe.
            return error != ERROR_INVALID_PARAMETER
        try:
            exit_code = wintypes.DWORD()
            if not self.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == STILL_ACTIVE
        finally:
            self.kernel32.CloseHandle(handle)

    def _expand_targets(self, targets: set[int]) -> set[int]:
        try:
            return self.descendant_ids(self.snapshot(False), targets)
        except OSError:
            return targets

    def _wait_for_exit(self, targets: set[int], seconds: float) -> set[int]:
        deadline = time.monotonic() + seconds
        current = set(targets)
        while time.monotonic() < deadline:
            current = self._expand_targets(current)
            alive = {pid for pid in current if self._alive(pid)}
            if not alive:
                return set()
            time.sleep(0.25)
        return {pid for pid in self._expand_targets(current) if self._alive(pid)}

    def _request_graceful_close(self, root_ids: set[int]) -> None:
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.c_void_p)

        @callback_type
        def callback(hwnd, _lparam):
            pid = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) in root_ids:
                self.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            return True

        if not self.user32.EnumWindows(callback, None):
            raise ctypes.WinError(ctypes.get_last_error())

    def _force_stop(self, targets: set[int]) -> list[int]:
        failed: list[int] = []
        for pid in sorted(targets):
            if not self._alive(pid):
                continue
            handle = self.kernel32.OpenProcess(PROCESS_TERMINATE | PROCESS_SYNCHRONIZE, False, pid)
            if not handle:
                failed.append(pid)
                continue
            try:
                if not self.kernel32.TerminateProcess(handle, 1):
                    failed.append(pid)
            finally:
                self.kernel32.CloseHandle(handle)
        return failed

    def close(self, dry_run: bool = False) -> None:
        processes = self.snapshot(True)
        roots = {
            process.pid
            for process in processes
            if process.package_family and process.package_family.lower() == PACKAGE_FAMILY.lower()
        }
        if not roots:
            print("[INFO] OpenAI.Codex 未运行，或当前用户未安装该桌面包。")
            return

        targets = self.descendant_ids(processes, roots)
        if os.getpid() in targets:
            raise RuntimeError(
                "当前启动器是 Codex Desktop 的子进程，不能安全地关闭其父进程。"
                "请从资源管理器或独立的 PowerShell/命令提示符运行此文件。"
            )

        print(f"[INFO] 找到 {len(targets)} 个 Codex Desktop 进程（包含子进程）。")
        for process in processes:
            if process.pid in targets:
                print(f"  PID {process.pid}: {process.name}")
        if dry_run:
            print("[DRY RUN] 未关闭进程。")
            return

        self._request_graceful_close(roots)
        remaining = self._wait_for_exit(targets, 5.0)
        if remaining:
            print(f"[INFO] 正在强制结束 {len(remaining)} 个残留进程。")
            self._force_stop(remaining)
            remaining = self._wait_for_exit(remaining, 2.0)
            if remaining:
                raise RuntimeError(f"Codex Desktop 无法完全关闭，残留 PID：{sorted(remaining)}")
        print("[OK] Codex Desktop 已关闭。")


def open_chatgpt() -> None:
    try:
        subprocess.Popen(
            ["explorer.exe", APP_USER_MODEL_ID],
            close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
    except OSError as error:
        raise RuntimeError(f"无法打开 ChatGPT：{error}") from error


def ask_to_open(no_open: bool) -> None:
    if no_open:
        return
    try:
        answer = input("是否打开 ChatGPT？N：不打开，其余键或直接回车：打开：")
    except EOFError:
        answer = ""
    if answer.strip().upper() != "N":
        open_chatgpt()


def pause_after_failure(no_pause: bool) -> None:
    if no_pause:
        return
    try:
        input("按回车键关闭窗口……")
    except EOFError:
        pass


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Windows Codex 会话同步、备份、恢复、清理和按链接删除工具。"
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("interactive", "status", "sync", "backfill", "cleanup"),
        default="interactive",
        help="业务命令，默认 interactive。",
    )
    parser.add_argument("home", nargs="?", help="可选的 Codex .codex 数据目录。")
    parser.add_argument("--no-open", action="store_true", help="结束后不启动 ChatGPT。")
    parser.add_argument("--no-pause", action="store_true", help="失败时不等待回车。")
    parser.add_argument("--dry-run-close", action="store_true", help="只枚举将关闭的进程，不写入数据。")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    configure_output()
    args = parse_arguments(argv)
    if os.name != "nt":
        print("[ERROR] 此文件只能在 Windows 上运行。", file=sys.stderr)
        pause_after_failure(args.no_pause)
        return 1
    if sys.version_info < MIN_PYTHON:
        print(
            f"[ERROR] 需要 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 或更新版本，"
            f"当前是 {sys.version.split()[0]}。",
            file=sys.stderr,
        )
        pause_after_failure(args.no_pause)
        return 1

    operation_code = 1
    try:
        print("============================================")
        print(" Codex Session Recovery / Provider Sync")
        print("============================================\n")
        home, source = resolve_codex_home(args.home)
        print(f"[INFO] Codex home source: {source}")
        print(f"[INFO] Codex home: {home}")
        provider_module, _delete_module = load_business_modules()
        print(f"[INFO] Python: {sys.version.split()[0]} | {sys.executable}")
        print("[INFO] 已在关闭 Desktop 前完成业务模块预检。")
        controller = WindowsProcessController()
        controller.close(dry_run=args.dry_run_close)
        if args.dry_run_close:
            operation_code = 0
        else:
            print("[INFO] 开始运行维护菜单……")
            operation_code = int(provider_module.main([str(SCRIPT_DIR / "codex_provider_local_launcher.py"), args.command, str(home)]))
            if operation_code:
                print(f"[ERROR] Python 业务程序退出码：{operation_code}", file=sys.stderr)
    except Exception as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        operation_code = 1

    try:
        ask_to_open(args.no_open)
    except Exception as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        operation_code = operation_code or 1
    if operation_code:
        pause_after_failure(args.no_pause)
    return operation_code


if __name__ == "__main__":
    raise SystemExit(run())

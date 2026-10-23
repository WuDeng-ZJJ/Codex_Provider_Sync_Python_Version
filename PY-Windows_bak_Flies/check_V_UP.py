#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_V_UP.py  ——  检查当前 Python 版本，若低于 3.11 则询问是否升级

运行逻辑：
    1) 检查当前运行脚本的 Python 解释器版本
    2) 若版本 >= 3.11，直接结束
    3) 若低于 3.11，显示当前版本，询问是否升级
       - 输入 N → 退出
       - 输入 Y → 继续
    4) 提供 10 个版本号供选择
    5) 根据操作系统尝试自动升级（Windows 用 winget，macOS 用 Homebrew）
       若自动升级不可用，则打开官方下载页面供手动安装

依赖：仅标准库
"""

import sys
import subprocess
import platform
import webbrowser
from typing import List


# ============ 可供选择的升级版本（10 个） ============
VERSIONS: List[str] = [
    "3.11.16",
    "3.11.15",
    "3.11.14",
    "3.12.14",
    "3.12.13",
    "3.12.12",
    "3.13.15",
    "3.13.14",
    "3.14.7",
    "3.14.6",
]


def get_current_version() -> sys.version_info:
    """返回当前脚本运行的 Python 解释器版本信息。"""
    return sys.version_info


def version_str(info: sys.version_info) -> str:
    """把 version_info 转成 '3.11.16' 这样的字符串。"""
    return f"{info.major}.{info.minor}.{info.micro}"


def ask_yes_no(prompt: str) -> bool:
    """询问 y/n，不区分大小写。"""
    while True:
        try:
            ans = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            sys.exit(0)
        if ans in ("y", "yes", "是"):
            return True
        if ans in ("n", "no", "否"):
            return False
        print("请输入 y（是）或 n（否）。")


def open_download_page(version: str) -> None:
    """打开指定版本的官方下载页面。"""
    # 3.11.16 → 31116
    nodots = version.replace(".", "")
    url = f"https://www.python.org/downloads/release/python-{nodots}/"
    print(f"\n正在打开下载页面：{url}")
    webbrowser.open(url)


def upgrade_windows(version: str) -> bool:
    """Windows：尝试通过 winget 安装指定主要版本的最新小版本。"""
    major_minor = ".".join(version.split(".")[:2])  # 例如 "3.11"
    package_id = f"Python.Python.{major_minor}"

    # 检查 winget 是否存在
    try:
        result = subprocess.run(
            ["winget", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            print("winget 不可用。")
            return False
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("未找到 winget。")
        return False

    print(f"\n正在通过 winget 安装 {package_id} ...")
    try:
        subprocess.run(
            [
                "winget", "install",
                "--id", package_id,
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ],
            check=True,
            timeout=600,
        )
        print("✓ winget 安装完成。")
        return True
    except subprocess.CalledProcessError as e:
        print(f"winget 安装失败（返回码 {e.returncode}）。")
        return False
    except subprocess.TimeoutExpired:
        print("winget 安装超时。")
        return False


def upgrade_macos(version: str) -> bool:
    """macOS：尝试通过 Homebrew 安装。"""
    major_minor = ".".join(version.split(".")[:2])  # "3.11"
    formula = f"python@{major_minor}"

    try:
        subprocess.run(
            ["brew", "--version"],
            capture_output=True,
            check=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("未找到 Homebrew。")
        return False

    print(f"\n正在通过 Homebrew 安装 {formula} ...")
    try:
        subprocess.run(["brew", "install", formula], check=True, timeout=600)
        print("✓ Homebrew 安装完成。")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Homebrew 安装失败（返回码 {e.returncode}）。")
        return False
    except subprocess.TimeoutExpired:
        print("Homebrew 安装超时。")
        return False


def upgrade_linux(version: str) -> bool:
    """Linux：因发行版差异较大，仅给出提示。"""
    major_minor = ".".join(version.split(".")[:2])
    print("\nLinux 系统请使用发行版包管理器手动安装，例如：")
    print(f"  sudo apt install python{major_minor}    # Debian/Ubuntu")
    print(f"  sudo dnf install python{major_minor}    # Fedora")
    print("或访问官方下载页面手动编译。")
    return False


def main() -> None:
    print("=" * 60)
    print("      check_V_UP.py  ——  Python 版本检查与升级")
    print("=" * 60)

    current = get_current_version()
    current_str = version_str(current)

    print(f"\n当前 Python 版本：{current_str}")
    print(f"解释器路径：{sys.executable}")

    # ---------- 版本判断 ----------
    if current.major > 3 or (current.major == 3 and current.minor >= 11):
        print("\n✓ 当前版本已 >= 3.11，无需升级。")
        return

    print(f"\n⚠ 当前版本 {current_str} 低于 3.11。")
    if not ask_yes_no("是否需要升级 Python？(y/n) > "):
        print("已取消升级。")
        return

    # ---------- 显示 10 个版本选项 ----------
    print("\n请选择要升级到的版本：")
    for i, ver in enumerate(VERSIONS, 1):
        print(f"  {i:>2}. {ver}")

    while True:
        try:
            choice = input(f"\n请输入选项编号 (1-{len(VERSIONS)})： > ").strip()
            idx = int(choice)
            if 1 <= idx <= len(VERSIONS):
                selected = VERSIONS[idx - 1]
                break
            print(f"请输入 1 到 {len(VERSIONS)} 之间的数字。")
        except ValueError:
            print("请输入数字。")

    print(f"\n已选择：{selected}")

    # ---------- 按操作系统执行升级 ----------
    system = platform.system()
    success = False

    if system == "Windows":
        success = upgrade_windows(selected)
    elif system == "Darwin":
        success = upgrade_macos(selected)
    elif system == "Linux":
        success = upgrade_linux(selected)
    else:
        print(f"不支持的系统：{system}")

    if not success:
        print("\n自动升级未成功，正在打开官方下载页面 ...")
        open_download_page(selected)
        print("请手动下载并安装。")
    else:
        print("\n升级操作已完成，请重新打开终端验证版本。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消。")
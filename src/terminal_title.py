"""終端機視窗標題的跨平台小工具。"""

import os


DEFAULT_TITLE = "telegram-game-bot"
RUNNING_TITLE = "MOMOBearBot - main"


def set_title(title: str) -> None:
    """在 Windows 主控台設定標題；其他平台安靜略過。

    專案原本用 ``os.system('title ...')``，在非 Windows shell 會輸出錯誤，
    且不利於保證結束時復原。Windows API 不需要經過 shell，也不會有引號
    或命令注入問題。
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except OSError:
        # 沒有附著主控台時（例如背景服務）不影響 bot 本身。
        pass


def restore_default_title() -> None:
    set_title(DEFAULT_TITLE)

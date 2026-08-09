"""集中管理路徑與環境相關常數。"""
from pathlib import Path


def _find_project_root(start: Path) -> Path:
    """從目前檔案往上找，第一個同時包含 src/ 與 data/ 的資料夾就是專案根目錄。
    比起寫死 .parent.parent 這種算法，之後套件搬到更深的資料夾也不用跟著改
    （這裡曾經因為硬算層數，套件多包一層資料夾後路徑就算錯了，改成這種方式
    避免同樣的問題再發生一次）。
    """
    current = start.resolve()
    for candidate in [current] + list(current.parents):
        if (candidate / "src").is_dir() and (candidate / "data").is_dir():
            return candidate
    # 找不到就退回舊算法當保底，並印警告，至少不會直接炸掉
    fallback = start.resolve().parent.parent.parent
    print(f"[WARN] config.py 找不到同時包含 src/ 與 data/ 的專案根目錄，"
          f"退回猜測路徑：{fallback}")
    return fallback


BASE_DIR = _find_project_root(Path(__file__))
REGISTRY_FILE = BASE_DIR / "config" / "command_registry.json"

# 專門「廣播」遊戲事件的公告頻道，目前確認只有一個，且固定不變
ANNOUNCEMENT_CHAT_ID = -1004431989174  # 摸摸熊戰鬥陀螺

# raw 資料中實際觀察到的 event_type 值（注意是 "edited"，不是 "edit"）
EVENT_TYPE_NEW = "new"
EVENT_TYPE_EDITED = "edited"
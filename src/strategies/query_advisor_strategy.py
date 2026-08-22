# -*- coding: utf-8 -*-
"""
query_advisor_strategy.py

負責「查詢類指令的回覆，要不要自動附加出戰建議」——這是 strategy 模組：
決定要不要附加、附加什麼，讀 roster（跨模組資料），不直接改 shape 的
顯示文字產生方式。parser（response_shapes/top_record.py 等）保持無狀態，
只管文字→structured→顯示文字；這裡才是「讀最新 roster、算建議」的地方。
跟 market_tracking_strategy.py 是同一種分工，接的模式也一樣。

=== 沿革 ===
這段邏輯原本分散在兩個地方：
  1. recommendation_footer.py：讀 roster、呼叫 query_reactor.handle_query_reply()
  2. response_shapes/top_record.py：在 format_for_display() 裡呼叫 (1)
第 2 點是條隱藏路徑——response_parser.py 對所有 shape 一律單參數呼叫
format_for_display(structured)，導致 top_record.py 多要的 account_id
永遠傳不進去，這條路徑事實上從沒被觸發過。統一收斂到這裡：shape 檔案
一律保持單參數介面，建議 footer 改成 pipeline 後段處理。

=== 涵蓋範圍 ===
handle_query_reply() 本身直接吃「訊息原文」判斷是陀螺戰績／世界王／
護衛／清護衛哪一種，不依賴 parsed['shape']——所以護衛/清護衛就算還沒有
正式的 response_shapes 檔案（目前只有陀螺戰績 top_record.py 跟世界王
world_boss_status.py 進了 _KNOWN_SHAPES），這裡一樣能附加建議，不用等
那兩個 shape 檔案先做完。之後 guard_status.py shape 做出來，這裡完全
不用改——只是那時 parsed['shape'] 會多一種可能值，不影響這裡的判斷。

用法（main.py 裡，每筆 MessageRouter.parse() 的結果都餵一次，
跟 market_tracking 併在同一個 StrategyPipeline 裡）：

    query_advisor = QueryAdvisorStrategy(
        account_data_dir=BASE_DIR / "data" / str(ACCOUNT_ID),
        common_data_dir=BASE_DIR / "data" / "common",
    )
    parsed = query_advisor.observe(parsed, record)
"""

import json
import logging
from pathlib import Path

from query_reactor import handle_query_reply, resolve_roster
from battle_status import load_element_catalog

logger = logging.getLogger(__name__)


class QueryAdvisorStrategy:
    def __init__(self, account_data_dir, common_data_dir):
        self.account_data_dir = Path(account_data_dir)
        self.common_data_dir = Path(common_data_dir)

    # ---------- roster 載入 ----------
    def _load_roster(self):
        """每次都重新從硬碟讀，之後陀螺強化/鑄造/分解不用重開程式就會反映最新狀態。
        跟 recommendation_footer.py 原本的 _load_roster() 邏輯相同，搬過來而已。"""
        tops_path = self.account_data_dir / "tops.json"
        cast_path = self.account_data_dir / "cast_tops_catalog.json"
        special_path = self.common_data_dir / "special_tops_catalog.json"

        with tops_path.open(encoding="utf-8") as f:
            raw_roster = json.load(f)["detailed"]

        cast_catalog = {}
        if cast_path.exists():
            with cast_path.open(encoding="utf-8") as f:
                cast_catalog = json.load(f)

        special_catalog = {}
        if special_path.exists():
            with special_path.open(encoding="utf-8") as f:
                special_catalog = json.load(f)

        catalog = load_element_catalog(special_catalog, cast_catalog)
        return resolve_roster(raw_roster, catalog)

    # ---------- 對外入口（符合 strategy pipeline 的統一介面） ----------
    def observe(self, parsed, record):
        """pipeline 每筆訊息都會呼叫一次。

        只針對「有 display_text 可以附加建議」的訊息動作——不限定
        parsed['shape'] 一定要是已知值，因為護衛/清護衛還沒有正式 shape，
        判斷交給 handle_query_reply() 自己看訊息原文決定要不要處理。

        回傳 {"display_text": ...} 會被 pipeline 合併回 parsed，附加建議
        時整段覆蓋（原本的 display_text + 建議段落）；沒有建議時回傳
        None，不影響原本的 display_text。
        """
        raw_text = parsed.get("raw_text") or record.get("text") or ""
        display_text = parsed.get("display_text") or raw_text
        if not raw_text:
            return None

        try:
            roster = self._load_roster()
        except FileNotFoundError as e:
            logger.warning(
                "[出戰建議] 找不到 roster 檔案，略過建議（account_data_dir=%s, 錯誤=%s）",
                self.account_data_dir, e,
            )
            return None

        suggestion = handle_query_reply(raw_text, roster)
        if not suggestion:
            return None

        return {"display_text": f"{display_text}\n\n[出戰建議]\n{suggestion}"}


if __name__ == "__main__":
    import shutil

    test_base = Path(__file__).parent / "_test_base"
    account_dir = test_base / "data" / "envpop"
    common_dir = test_base / "data" / "common"
    account_dir.mkdir(parents=True, exist_ok=True)
    common_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy("/mnt/user-data/uploads/tops.json", account_dir / "tops.json")

    strategy = QueryAdvisorStrategy(account_data_dir=account_dir, common_data_dir=common_dir)

    raw_message = """📊 @envpop 的陀螺戰績
──────────────
目前關卡：第 100 階・摸摸熊・原初真神 🌟神位
最高通關：第 100 階　連勝：0
出戰：曜金神熊・摸摸太白GO（神・平衡型・戰力 535）
收藏：38 顆
⏳ 戰敗冷卻：還 18 分"""

    parsed = {"shape": "top_record", "raw_text": raw_message, "display_text": raw_message}
    result = strategy.observe(parsed, {"text": raw_message})
    print(result["display_text"] if result else "（無建議）")

    shutil.rmtree(test_base)

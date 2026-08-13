"""
contract_tracking_strategy.py

負責追蹤契約所（星環契約所）透露出的道具指數資訊，跨訊息持久化。
跟 market_tracking_strategy.py 是同樣的模式，但資料完全分開存——
契約所是另一套經濟體系（道具指數選擇權），不是股票型商品，兩邊的
「代稱」不會重複，但語意上是不同的東西，混在同一份檔案裡會讓之後
做分析時搞不清楚這筆資料到底是哪個市場的。

data/common/contract_prices.jsonl   全服共通的道具指數時間序列，
                                     跟帳號無關

目前只做「記錄」，不像 market_tracking_strategy 那樣有個人持倉/
交易確認的部分——契約所的「訂契」「我的契約」還沒有對應的 shape，
等那些做出來，看要獨立成交易紀錄還是併進這裡再說，不要現在就先猜
格式硬做。
"""
import json
from pathlib import Path


class ContractTrackingStrategy:
    def __init__(self, common_data_dir):
        self.common_data_dir = Path(common_data_dir)
        self.common_data_dir.mkdir(parents=True, exist_ok=True)
        self.contract_prices_file = self.common_data_dir / "contract_prices.jsonl"

    def _append_jsonl(self, entry):
        with self.contract_prices_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def observe(self, parsed, record):
        shape = parsed.get("shape")
        recorded_at = record.get("recorded_at") or record.get("message_date")

        if shape == "contract_overview":
            structured = parsed.get("structured") or {}
            for it in structured.get("items", []):
                self._append_jsonl({
                    "recorded_at": recorded_at,
                    "name": it["name"],
                    "price": it["price"],
                    "round_pct": it["round_pct"],
                    "day_pct": it["day_pct"],
                    "premium": it["premium"],
                })
            return None

        if shape == "contract_quote":
            structured = parsed.get("structured") or {}
            if structured.get("price") is not None:
                self._append_jsonl({
                    "recorded_at": recorded_at,
                    "name": structured["name"],
                    "price": structured["price"],
                    "round_pct": structured["round_pct"],
                    "day_pct": structured["day_pct"],
                    "premium": structured.get("premium"),
                })
            return None

        return None

"""
market_tracking_strategy.py

負責追蹤商契透露出的行情資訊，跨訊息、跨指令持久化。這是 strategy
模組：決定「要不要記」、「記什麼」，自己持久化，不直接呼叫 executor。
parser（response_shapes/market_contract.py）保持無狀態，只管文字→
結構化；這裡才是「記得之前發生過什麼」的地方。

三份檔案，市場資料跟個人資料分開存，不混在一起：
    data/common/market_prices.jsonl        全服共通的商品現價時間序列，
                                            跟帳號無關，純市場資料
    data/{帳號ID}/portfolio_history.jsonl  你的持倉時間序列，只留真正
                                            屬於「你」的三個欄位：股數
                                            (shares)、均價(cost)、
                                            帳面盈虧(pnl)——現價不重複
                                            存在這裡，那是市場資料，
                                            已經存在 market_prices.jsonl
    data/{帳號ID}/market_snapshot.json     每個商品的最新狀態 + 跟上一次
                                            比較的漲跌，供跨指令的「市場
                                            脈動」提示使用

用法（main.py 裡，每筆 MessageRouter.parse() 的結果都餵一次）：

    market_tracking = MarketTrackingStrategy(
        account_data_dir=BASE_DIR / "data" / str(ACCOUNT_ID),
        common_data_dir=BASE_DIR / "data" / "common",
    )
    parsed = market_tracking.observe(parsed, record)  # 回傳值可能加了 market_pulse
"""
import json
from pathlib import Path


class MarketTrackingStrategy:
    def __init__(self, account_data_dir, common_data_dir, enable_pulse=True):
        self.account_data_dir = Path(account_data_dir)
        self.account_data_dir.mkdir(parents=True, exist_ok=True)
        self.common_data_dir = Path(common_data_dir)
        self.common_data_dir.mkdir(parents=True, exist_ok=True)

        self.snapshot_file = self.account_data_dir / "market_snapshot.json"
        self.portfolio_history_file = self.account_data_dir / "portfolio_history.jsonl"
        self.market_prices_file = self.common_data_dir / "market_prices.jsonl"

        # 要不要在非市場類指令的顯示下面附加「📈 市場｜...」那行提示，
        # 關掉不影響存檔（market_prices.jsonl／portfolio_history.jsonl
        # 照樣寫），只是不再附加在畫面上。
        self.enable_pulse = enable_pulse

        self.snapshot = self._load_snapshot()
        self.name_map_file = self.common_data_dir / "name_map.json"
        self.name_map = self._load_name_map()  # {全名: 短稱}

    def _load_name_map(self):
        if self.name_map_file.exists():
            with self.name_map_file.open("r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_name_map(self):
        with self.name_map_file.open("w", encoding="utf-8") as f:
            json.dump(self.name_map, f, ensure_ascii=False, indent=2)

    def _remember_name_mapping(self, short_name, full_name):
        if self.name_map.get(full_name) != short_name:
            self.name_map[full_name] = short_name
            self._save_name_map()

    def _resolve_short_name(self, item_label):
        """item_label 是「emoji+全名」黏在一起的原始字串（例如
        "🛰️群星通訊"），從已知的全名對照表裡找出對應的短稱。
        找不到就回傳 None，呼叫端要自己決定放棄還是怎麼處理，
        不要硬猜一個可能是錯的短稱去污染 snapshot。"""
        for full_name, short_name in self.name_map.items():
            if full_name in item_label:
                return short_name
        return None

    # ---------- 持久化 ----------
    def _load_snapshot(self):
        if self.snapshot_file.exists():
            with self.snapshot_file.open("r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_snapshot(self):
        with self.snapshot_file.open("w", encoding="utf-8") as f:
            json.dump(self.snapshot, f, ensure_ascii=False, indent=2)

    def _append_jsonl(self, file_path, entry):
        with file_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ---------- 對外入口(符合 strategy_pipeline 的統一介面) ----------
    def observe(self, parsed, record):
        """pipeline 每筆訊息都會呼叫一次。

        回傳值會被 pipeline 合併進 parsed，讓 display_formatter 等其他
        呼叫端可以直接從 parsed['market_pulse'] 讀到，不用知道這是
        market_tracking_strategy 產生的、也不用另外呼叫任何函式。
        """
        shape = parsed.get("shape")

        if shape == "market_contract":
            self._persist_market_contract(parsed, record)
            return None  # 商契自己就有完整資訊，不需要額外附加東西

        if shape == "market_overview":
            self._persist_market_overview(parsed, record)
            enriched = self._enrich_overview_display(parsed)
            return {"display_text": enriched} if enriched else None

        if shape == "market_quote":
            structured = parsed.get("structured") or {}
            name, full_name = structured.get("name"), structured.get("full_name")
            if name and full_name:
                self._remember_name_mapping(name, full_name)
            return None  # 這則本身就有完整的現價/漲跌資訊，不重複附加脈動提示

        if shape == "trade_confirmation":
            self._persist_trade(parsed, record)
            return None  # 交易確認本身內容就很清楚，不需要額外附加東西

        if not self.enable_pulse:
            return None

        pulse = self._compute_market_pulse_text()
        if pulse:
            return {"market_pulse": pulse}
        return None

    def _persist_market_contract(self, parsed, record):
        structured = parsed.get("structured") or {}
        holdings = structured.get("holdings", [])
        summary = structured.get("summary", {})
        recorded_at = record.get("recorded_at") or record.get("message_date")

        # 1) 市場資料：現價，全服共通，跟帳號無關
        for h in holdings:
            self._append_jsonl(self.market_prices_file, {
                "recorded_at": recorded_at,
                "name": h["name"],
                "price": h["price"],
            })

        # 2) 個人資料：只留真正屬於你的三個欄位——股數、均價、帳面盈虧。
        #    現價不重複存在這裡，市場資料已經存過一份了。
        self._append_jsonl(self.portfolio_history_file, {
            "recorded_at": recorded_at,
            "message_id": record.get("message_id"),
            "holdings": [
                {"name": h["name"], "shares": h["shares"], "cost": h["cost"], "pnl": h["pnl"]}
                for h in holdings
            ],
            "summary": summary,
        })

        # 3) snapshot：這次現價 vs 上次現價，算出「這段期間漲跌多少」
        #    （跟 pct_vs_cost 不一樣：pct_vs_cost 是相對於你的均價，這裡是
        #    相對於上一次查詢，對「行情最近好不好」這個問題來說，這個
        #    才是真正有意義的數字）
        for h in holdings:
            name = h["name"]
            prev = self.snapshot.get(name)
            prev_price = prev.get("price") if prev else None
            price_change_pct = None
            if prev_price:
                price_change_pct = round((h["price"] - prev_price) / prev_price * 100, 2)
            existing = self.snapshot.get(name, {})
            existing.update({
                "price": h["price"],
                "cost": h["cost"],
                "shares": h["shares"],
                "pct_vs_cost": h["pct"],
                "price_change_pct": price_change_pct,
                "last_seen": recorded_at,
            })
            self.snapshot[name] = existing
        self._save_snapshot()

    def _persist_market_overview(self, parsed, record):
        """市集：一次涵蓋全部商品（不限持有），而且遊戲直接給「本盤」漲跌，
        比商契那邊土法煉鋼比較兩次查詢還準——這裡優先採用本盤%當
        price_change_pct，不用等下一次查詢才有比較基準。"""
        structured = parsed.get("structured") or {}
        items = structured.get("items", [])
        recorded_at = record.get("recorded_at") or record.get("message_date")

        for it in items:
            self._append_jsonl(self.market_prices_file, {
                "recorded_at": recorded_at,
                "name": it["name"],
                "price": it["price"],
                "round_pct": it["round_pct"],
                "day_pct": it["day_pct"],
            })
            self._remember_name_mapping(it["name"], it["full_name"])
            # 用 existing.update 而不是整個覆蓋：如果之前商契已經記過
            # cost/pct_vs_cost（你的持倉資訊），這裡不要把它洗掉，
            # 只更新市集這邊真正知道的欄位（價格、本盤、今日）。
            existing = self.snapshot.get(it["name"], {})
            existing.update({
                "price": it["price"],
                "price_change_pct": it["round_pct"],
                "day_pct": it["day_pct"],
                "last_seen": recorded_at,
            })
            self.snapshot[it["name"]] = existing
        self._save_snapshot()

    def _enrich_overview_display(self, parsed):
        """市集原本一個商品占兩行（名稱一行、價格一行），這裡改成一行；
        並且對照 snapshot 裡的持倉資料（來自之前查過的商契），如果這個
        商品你有持有，用市集當下的現價重新估算盈虧附加在行尾。標
        「(估)」是因為這個盈虧不是伺服器這次回應直接給的數字，是用
        上次商契查到的股數/均價 × 這次市集的現價自己算出來的，均價
        跟股數如果之後又變動（買賣過）而你還沒重新查一次商契，這個
        估算就會失準。"""
        structured = parsed.get("structured") or {}
        items = structured.get("items", [])
        if not items:
            return None

        lines = ["🏮 星環市集", "──────────────"]
        for it in items:
            arrow = "🔻" if it["round_pct"] < 0 else ("🔺" if it["round_pct"] > 0 else "─")
            line = (
                f"{it['index']}. {it['emoji']}【{it['name']}】{it['full_name']}　"
                f"{arrow} {it['price']}　本盤 {it['round_pct']:+.1f}%　今日 {it['day_pct']:+.1f}%"
            )
            pos = self.snapshot.get(it["name"], {})
            shares = pos.get("shares")
            cost = pos.get("cost")
            if shares and cost:
                pnl_now = round(shares * (it["price"] - cost))
                line += f"　💰 {pnl_now:+}(估)"
            lines.append(line)
        lines.append("──────────────")
        if structured.get("news"):
            lines.append(f"🗞️ {structured['news']}")
        if structured.get("points") is not None:
            lines.append(f"💰 點數 {structured['points']}")
        return "\n".join(lines)

    def _compute_market_pulse_text(self):
        """給 display_formatter 當共用 trailer 用的一行摘要，
        不管這次打的是什麼指令都能附上，讓你不用特地查商契也能感覺到
        目前行情大概好壞。沒有資料時回傳 None，呼叫端要自己判斷要不要顯示。"""
        if not self.snapshot:
            return None

        up = down = flat = unknown = 0
        for info in self.snapshot.values():
            change = info.get("price_change_pct")
            if change is None:
                unknown += 1
            elif change > 0:
                up += 1
            elif change < 0:
                down += 1
            else:
                flat += 1

        parts = []
        if up:
            parts.append(f"▲{up}")
        if down:
            parts.append(f"▼{down}")
        if flat:
            parts.append(f"─{flat}")

        if not parts:
            return None  # 全部都是第一次看到、沒有比較基準，先不顯示

        return "📈 市場｜" + " ".join(parts) + "（依最近一次商契／市集查詢）"

    def _persist_trade(self, parsed, record):
        """入資/撤資確認是即時、權威的交易結果，比商契/市集/行情的
        「查詢當下快照」更準——直接覆蓋 snapshot 裡的股數/均價，
        不用等下一次剛好查商契才發現變了。"""
        structured = parsed.get("structured") or {}
        item_label = structured.get("item_label")
        short_name = self._resolve_short_name(item_label) if item_label else None

        if short_name is None:
            print(f"[market_tracking_strategy] 交易確認「{item_label}」找不到對應的短稱，"
                  f"可能是還沒查過市集/行情看過這個商品的全名，這筆交易先不更新 snapshot")
            return

        recorded_at = record.get("recorded_at") or record.get("message_date")

        existing = self.snapshot.get(short_name, {})
        existing["shares"] = structured.get("shares_after")
        existing["last_seen"] = recorded_at
        # 只有入資才更新均價——撤資不影響剩餘股數的均價，這裡沒有值
        # 可以更新（avg_cost_after 是 None），維持 snapshot 裡原本記的均價。
        if structured.get("avg_cost_after") is not None:
            existing["cost"] = structured["avg_cost_after"]
        self.snapshot[short_name] = existing
        self._save_snapshot()

        # 個人交易紀錄，跟商契的持倉快照分開存，這筆是「有交易發生」
        # 的明確事件，不是定期查詢的快照
        self._append_jsonl(self.portfolio_history_file, {
            "recorded_at": recorded_at,
            "message_id": record.get("message_id"),
            "event": "trade",
            "action": structured.get("action"),
            "name": short_name,
            "traded_shares": structured.get("traded_shares"),
            "trade_price": structured.get("trade_price"),
            "net_change": structured.get("net_change"),
            "shares_after": structured.get("shares_after"),
            "avg_cost_after": structured.get("avg_cost_after"),   # 撤資是 None
            "realized_pnl": structured.get("realized_pnl"),       # 入資是 None
            "remaining_points": structured.get("remaining_points"),
        })
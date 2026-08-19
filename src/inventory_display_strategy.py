# -*- coding: utf-8 -*-
"""
inventory_display_strategy.py

負責「我的陀螺顯示裡要不要插入五行屬性」——這是 strategy 模組：需要讀
帳號存檔（tops.json）跟外部對照表（special_tops_catalog.json /
cast_tops_catalog.json），shape 層（parsing/response_shapes/my_tops.py）
拿不到這些，只能在這裡（有 base_dir/account_id 可用的 pipeline 後段）做。

=== 查詢順序（熊確認）===
    1. tops.json（帳號存檔，已經是「綁定一覽」合併回來的結果，
       每筆 detailed 都可能帶 element 欄位）── 優先查這裡
    2. special_tops_catalog.json / cast_tops_catalog.json（旋王/旋神/
       UR精選/鑄造陀螺對照表，用 inventory_parsers.annotate_special_source()）
       ── 上面查不到才 fallback 到這裡
    3. 查不到就是沒有，不再往下猜（不用 binding 反查、不用其他 fallback
       鏈——熊說「這裡如果找不到就是沒有了」）

=== 比對 key：match_key，不是 index ===
    index 是「依戰力排序」的名次，兩次查詢之間如果有陀螺強化/新增，
    排序會跳動，用它跨查詢比對會配錯。match_key（正規化後的完整名字，
    見 inventory_parsers._normalize_key）才是專案裡一貫用來跨查詢配對
    同一顆陀螺的 key，跟 carry_over_enrichment() / merge_bindings_into_tops()
    用的是同一套邏輯，這裡沿用，不要另外發明比對方式。

=== 插入方式：逐行插入，不整份重組 ===
    只針對能查到五行的那幾行，在原本的「稀有度・類型・戰力 N」插入
    「・屬性」；查不到的（一般 UR、未收錄的鑄造）維持原樣，不顯示錯誤
    資訊。跟 market_tracking_strategy._enrich_overview_display() 是
    同一種「在既有 display_text 基礎上補強」模式。

    只套用在單體列出的神/UR/王（parsed['structured']['detailed']）——
    SSR 以下只列數量，沒有單獨的行可以插入，也不需要（熊確認「統計數量
    的就不管它」）。
"""

import json
import re
import logging
from pathlib import Path

from inventory_parsers import annotate_special_source

logger = logging.getLogger(__name__)

_INDEX_PREFIX_RE = re.compile(r"^(\d+)\.")


def _inject_element(line: str, element: str) -> str:
    if "｜" not in line:
        return line
    prefix, tail = line.split("｜", 1)
    parts = tail.split("・")
    if len(parts) != 3:
        return line  # 格式跟預期不符，不硬改，保留原樣比顯示錯誤資訊安全
    rarity, top_type, power_part = parts
    return f"{prefix}｜{rarity}・{top_type}・{element}屬性・{power_part}"


class InventoryDisplayStrategy:
    def __init__(self, base_dir, account_id_getter):
        self.base_dir = Path(base_dir)
        self.account_id_getter = account_id_getter

    # ---------- 步驟1：tops.json（帳號存檔，match_key 比對） ----------
    def _load_tops_element_by_matchkey(self, account_id):
        """
        element 的查詢優先序（沿用專案既有的 UR/神階屬性 fallback 規則，
        見 memory：「top-level element 欄位經常是 null，要 fallback 到
        binding.element_stage.element」）：
            1. top-level 的 element 欄位
            2. binding.element_stage.element
                （旋王／UR精選這類屬性隨機取得的分類，模板本身不記錄
                固定屬性，只有實際綁定過後 binding 資料才知道抽到哪個
                五行——這一步漏掉就是這次熊回報「tops.json 明明有資料
                卻沒接上」的根因）
            3. 兩者都沒有 → 這顆真的沒有可用的屬性資料，跳過不處理
        """
        tops_path = self.base_dir / "data" / str(account_id) / "tops.json"
        if not tops_path.exists():
            return {}
        try:
            with tops_path.open(encoding="utf-8") as f:
                saved = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[我的陀螺屬性] tops.json 讀取失敗，略過（%s）", e)
            return {}

        result = {}
        for t in saved.get("detailed", []):
            match_key = t.get("match_key")
            if not match_key:
                continue

            element = t.get("element")
            if not element:
                binding = t.get("binding") or {}
                element_stage = binding.get("element_stage") or {}
                element = element_stage.get("element")

            if element:
                result[match_key] = element

        return result

    def observe(self, parsed, record):
        if parsed.get("shape") != "my_tops":
            return None

        structured = parsed.get("structured") or {}
        detailed = structured.get("detailed") or []
        if not detailed:
            return None

        account_id = self.account_id_getter()
        if not account_id:
            return None

        # 步驟1：先查 tops.json（match_key 比對）
        tops_element_by_key = self._load_tops_element_by_matchkey(account_id)

        working = [dict(t) for t in detailed]  # 複製，不動 parsed['structured'] 本身
        still_missing = []
        for t in working:
            element = tops_element_by_key.get(t.get("match_key"))
            if element:
                t["element"] = element
            else:
                still_missing.append(t)

        # 步驟2：tops.json 查不到的，fallback 到 special/cast 對照表
        if still_missing:
            annotate_special_source(still_missing, self.base_dir, account_id)
            # annotate_special_source 就地修改 still_missing 裡的 dict，
            # 這些 dict 物件跟 working 裡的是同一份參照，working 會一併更新

        element_by_index = {t["index"]: t["element"] for t in working if t.get("element")}
        if not element_by_index:
            return None  # 步驟3：都查不到，維持原樣

        display_text = parsed.get("display_text") or structured.get("raw_text") or ""
        new_lines = []
        changed = False
        for line in display_text.splitlines():
            m = _INDEX_PREFIX_RE.match(line.strip())
            if m:
                idx = int(m.group(1))
                element = element_by_index.get(idx)
                if element:
                    new_line = _inject_element(line, element)
                    if new_line != line:
                        changed = True
                    line = new_line
            new_lines.append(line)

        if not changed:
            return None

        return {"display_text": "\n".join(new_lines)}


if __name__ == "__main__":
    import shutil

    test_base = Path(__file__).parent / "_test_base"
    common_dir = test_base / "data" / "common"
    account_dir = test_base / "data" / "envpop"
    common_dir.mkdir(parents=True, exist_ok=True)
    account_dir.mkdir(parents=True, exist_ok=True)

    # 模擬 tops.json 已經有一筆從綁定一覽合併回來的 element
    (account_dir / "tops.json").write_text(
        json.dumps({
            "detailed": [
                {"match_key": "☆元氣之始・太初神熊・摸摸原初GO+17🌀回歸綁定IV", "element": "木"},
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    # 第二顆只能靠 cast_tops_catalog 查到
    (common_dir / "special_tops_catalog.json").write_text("{}", encoding="utf-8")
    (account_dir / "cast_tops_catalog.json").write_text(
        json.dumps({"🧰": {"element": "金"}}, ensure_ascii=False), encoding="utf-8"
    )

    strategy = InventoryDisplayStrategy(base_dir=test_base, account_id_getter=lambda: "envpop")

    sample_display = """🧰 你的陀螺收藏（共 131 顆）
──────────────
1. ✦✦✦✦✦🔱 ☆元氣之始・太初神熊・摸摸原初GO +17🌀回歸綁定IV｜神・平衡型・戰力 670
31. ✦✦✦✦ 🧰｜UR・持久型・戰力 212
32. ✦✦✦✦ 星碎｜UR・持久型・戰力 212"""

    parsed = {
        "shape": "my_tops",
        "display_text": sample_display,
        "structured": {
            "raw_text": sample_display,
            "detailed": [
                {"index": 1, "name": "☆元氣之始・太初神熊・摸摸原初GO",
                 "match_key": "☆元氣之始・太初神熊・摸摸原初GO+17🌀回歸綁定IV",
                 "rarity": "神", "type": "平衡型", "power": 670},
                {"index": 31, "name": "🧰", "match_key": "🧰",
                 "rarity": "UR", "type": "持久型", "power": 212},
                {"index": 32, "name": "星碎", "match_key": "星碎",
                 "rarity": "UR", "type": "持久型", "power": 212},
            ],
        },
    }

    result = strategy.observe(parsed, {})
    print(result["display_text"] if result else "（無變化）")
    print()
    print("預期：#1 從 tops.json 查到「木」，#31 從 cast_tops_catalog 查到「金」，#32 兩邊都查不到維持原樣")

    shutil.rmtree(test_base)
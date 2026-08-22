# -*- coding: utf-8 -*-
"""
inventory_display_strategy.py

負責「我的陀螺顯示裡要不要插入五行屬性」——這是 strategy 模組：需要讀
帳號存檔（tops.json）跟外部對照表（special_tops_catalog.json /
cast_tops_catalog.json），shape 層（parsing/response_shapes/my_tops.py）
拿不到這些，只能在這裡（有 base_dir/account_id 可用的 pipeline 後段）做。

=== 查詢順序（熊確認，2026-08-19 修正過優先序）===
    1. tops.json（帳號存檔，已經是「綁定一覽」合併回來的結果）── 優先查這裡，
       實際判斷交給 battle_status.resolve_element()：binding.element_stage
       優先於 top-level element（binding 是玩家實際投入的地面真相，比
       catalog 猜測值準——見 battle_status.py 的 2026-08-19 修正說明，
       實測 #15 森羅✦2／#24 翠森旋王・盤根GO 兩顆證實 top-level 有時是
       catalog 的錯誤猜測值，不能無條件信任）。這裡不再重複寫一份優先序
       邏輯，直接呼叫 battle_status.resolve_element()，只在這一個地方
       修就全部同步套用。
    2. special_tops_catalog.json / cast_tops_catalog.json（旋王/旋神/
       UR精選/鑄造陀螺對照表，用 inventory_parsers.annotate_special_source()）
       ── 上面查不到才 fallback 到這裡
    3. 查不到就是沒有，不再往下猜（不用其他 fallback 鏈——熊說「這裡
       如果找不到就是沒有了」）

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
from battle_status import resolve_element

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
        """優先序判斷交給 battle_status.resolve_element()（binding 優先，
        top-level 次之），這裡只負責讀檔跟用 match_key 建索引。"""
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
            element = resolve_element(t)
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

        # 步驟1：先查 tops.json（match_key 比對，binding 優先於 top-level）
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

    # 模擬 tops.json：#15 情境重現——top-level=土（catalog 猜測值，錯的），
    # binding.element_stage=金（真正的），修正後應該顯示「金」
    (account_dir / "tops.json").write_text(
        json.dumps({
            "detailed": [
                {"match_key": "☆黑獄・森羅✦2+18🛡️護盾綁定III", "element": "土",
                 "binding": {"element_stage": {"element": "金", "stage": 3}}},
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (common_dir / "special_tops_catalog.json").write_text("{}", encoding="utf-8")
    (account_dir / "cast_tops_catalog.json").write_text("{}", encoding="utf-8")

    strategy = InventoryDisplayStrategy(base_dir=test_base, account_id_getter=lambda: "envpop")

    sample_display = """🧰 你的陀螺收藏（共 38 顆）
──────────────
15. ✦✦✦✦ ☆黑獄・森羅✦2 +18🛡️護盾綁定III｜UR・防禦型・戰力 415"""

    parsed = {
        "shape": "my_tops",
        "display_text": sample_display,
        "structured": {
            "raw_text": sample_display,
            "detailed": [
                {"index": 15, "name": "☆黑獄・森羅✦2",
                 "match_key": "☆黑獄・森羅✦2+18🛡️護盾綁定III",
                 "rarity": "UR", "type": "防禦型", "power": 415},
            ],
        },
    }

    result = strategy.observe(parsed, {})
    print(result["display_text"] if result else "（無變化）")
    print()
    print("預期：顯示「金屬性」（binding 的真實值），不是「土屬性」（catalog 的錯誤舊值）")

    shutil.rmtree(test_base)
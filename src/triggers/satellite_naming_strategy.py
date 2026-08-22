# -*- coding: utf-8 -*-
"""
triggers/satellite_naming_strategy.py —— 「群星計畫結業」自動命名

跟 satellite_training_strategy.py（每回合選按鈕）是完全獨立的觸發模組，
用獨立的 SYSTEM_KEY（satellite_naming），這樣可以只關掉自動命名、
但保留每回合自動點按鈕，或反過來（熊 2026-08-22 指定）。

=== 命名規則 ===
從這次培育習得的技能裡，依優先序選一個當「主要技能」：
    1. 特殊金技（星隕／山嶽／汲魂／過載，只能隨機事件取得，最稀有）
    2. 普通技能（不帶 🏅 的一般技能）
    3. 進階金技（王之餘威／爐心壁壘／靈焰回生／雷核連旋）
    4. 普通金技／基礎金技（其餘帶 🏅 的金技）
（熊 2026-08-22 指定的優先序；判斷依據是技能種類，不是稀有度標籤——
熊原話「不用管稀有度，數據才是我選擇的唯一標準」。目前同一優先序內
如果有多個候選技能，規則是取文字裡最先出現的那個，還沒做「依實際數值
挑最強」的比較邏輯——不同效果類型（追加一擊／生成護盾／回血／數值%）
沒有共通的比較基準，熊如果想要更精細的規則，要先講清楚怎麼跨類型比較。）

技能名稱去掉 emoji 符號跟結尾數字（例如「🗡️銳擊2」→「銳擊」），只留
純文字，接上一個全局共用的流水號（例如「靈焰回生3」），格式模仿遊戲
本身「同技能疊加顯示成『技能名+數字』」的呈現方式，不加分隔符號。

=== 流水號 ===
落地存在 data/common/satellite_naming_sequence.json（不是 runtime_state，
因為要跨重啟持續遞增）。要重置，刪掉這個檔案，或呼叫 reset_sequence()
（例如熊之後想在終端機加一個 /sat_reset_seq 指令，直接呼叫這個函式）。
"""
import json
import re
from pathlib import Path

from triggers import actions

SYSTEM_KEY = "satellite_naming"

# 只能靠隨機事件取得的特殊金技，跟 satellite_catalog_display.py 共用同一份
# 清單（那邊是「衛星圖鑑」畫面在用，這裡是結業命名在用，兩個用途不同但
# 分級標準應該一致，所以直接 import 共用，不重新宣告一份，避免兩邊之後
# 各自改到不同步）。
from satellite_catalog_display import SPECIAL_GOLD_SKILLS

# 進階金技（熊 2026-08-22 提供的完整技能圖鑑訊息裡標註「以上為進階金技」
# 那四個）。普通技能／普通金技不需要維護清單：普通技能用「沒有🏅字首」
# 判斷，普通金技用「有🏅字首、但不在特殊/進階清單裡」判斷（見
# classify_skill_tier），這樣之後遊戲新增技能，只要不是特殊或進階金技，
# 都會自動落在正確的分級，不用回來維護清單。
ADVANCED_GOLD_SKILLS = ["王之餘威", "爐心壁壘", "靈焰回生", "雷核連旋"]

_SEQUENCE_FILENAME = "satellite_naming_sequence.json"

# 只保留中文字元，用來從「🏅🔮靈焰回生」「🗡️銳擊2」這類原始文字裡
# 抽出純技能名稱（emoji、結尾數字都會被去掉）。
_CJK_ONLY_PATTERN = re.compile(r"[^\u4e00-\u9fff]")


def classify_skill_tier(skill_text: str) -> int:
    """數字越小優先序越高：1=特殊金技 2=普通技能 3=進階金技 4=普通金技。"""
    if any(name in skill_text for name in SPECIAL_GOLD_SKILLS):
        return 1
    if "🏅" not in skill_text:
        return 2
    if any(name in skill_text for name in ADVANCED_GOLD_SKILLS):
        return 3
    return 4


def extract_skill_core_name(skill_text: str) -> str:
    """去掉 emoji 符號跟結尾數字，只留純中文技能名稱。"""
    return _CJK_ONLY_PATTERN.sub("", skill_text)


def choose_primary_skill(skills):
    """依優先序選一個技能當命名主體；同一優先序有多個候選時，取文字裡
    最先出現的那個（見檔頭說明，尚未做數值強度比較）。沒有任何技能時
    回傳 None。"""
    if not skills:
        return None
    return min(skills, key=lambda s: (classify_skill_tier(s), skills.index(s)))


def _sequence_path(base_dir) -> Path:
    return Path(base_dir) / "data" / "common" / _SEQUENCE_FILENAME


def next_sequence_number(base_dir) -> int:
    """取下一個流水號並落地存檔（每次呼叫都會遞增，不是唯讀查詢）。"""
    path = _sequence_path(base_dir)
    last_used = 0
    if path.exists():
        try:
            last_used = json.loads(path.read_text(encoding="utf-8")).get("last_used", 0)
        except json.JSONDecodeError:
            last_used = 0
    new_value = last_used + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_used": new_value}, ensure_ascii=False, indent=2), encoding="utf-8")
    return new_value


def reset_sequence(base_dir, start_before: int = 0) -> None:
    """重置流水號。start_before 是重置後「最後用掉的號碼」，
    下一次 next_sequence_number() 會回傳 start_before + 1
    （預設重置成下一個是 1）。"""
    path = _sequence_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_used": start_before}, ensure_ascii=False, indent=2), encoding="utf-8")


def generate_completion_name(skills, base_dir) -> str:
    """回傳完整要送出的名字字串（純技能文字 + 流水號，無分隔符）。"""
    primary = choose_primary_skill(skills)
    core_name = extract_skill_core_name(primary) if primary else "衛星"
    if not core_name:
        # 極端情況：extract 完是空字串（例如技能文字裡完全沒有中文字元），
        # 保底用「衛星」，不要送出空字串當名字的一部分。
        core_name = "衛星"
    seq = next_sequence_number(base_dir)
    return f"{core_name}{seq}"


def decide(ctx):
    if ctx.shape != "satellite_training_complete":
        return None

    if not ctx.is_enabled(SYSTEM_KEY):
        return actions.none(
            log="[群星計畫·結業] 🔕 自動命名已關閉（終端機輸入 /auto 查看開關狀態），"
                "已收到結業畫面但不會自動命名，請自行手動打「結業 你想取的名字」",
            stop=True,
        )

    skills = ctx.structured.get("skills") or []
    primary = choose_primary_skill(skills)
    name = generate_completion_name(skills, ctx.base_dir)

    return actions.send_now(
        f"結業 {name}",
        chat_id=ctx.chat_id,
        reason=(f"群星計畫結業，習得技能：{'、'.join(skills) if skills else '（無）'}，"
                f"依優先序選出主要技能「{primary or '無'}」，自動命名為「{name}」"),
        log=f"[群星計畫·結業] ✅ 自動命名「{name}」",
    )

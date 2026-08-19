"""
battle_status.py
出戰狀態列格式化 —— 以少控多：一個函式處理單顆陀螺的簡寫，
上層負責組裝「主/副/衛星/編隊」的排列方式。

設計依據真實資料核對（2026-08-14 熊提供樣本，含 tops.json 片段、
陀螺收藏、綁定天賦一覽、副陀螺查詢回覆）：
- 陀螺的 top-level "element" 欄位常為 null（尤其 UR/神階，天賦未點時），
  真正屬性要 fallback 到 binding.element_stage.element；
  跟 talent_overview.build_unified_view() 的 unified_view row 相容——
  該格式 element 已攤平到 top-level，resolve_element() 直接命中。
- "type" 欄位固定四選一：攻擊型／防禦型／持久型／平衡型，不會缺值。
- 短稱規則取「・」分隔的最後兩段，不是第一段——由兩個獨立指令的真實
  回覆互相印證：「陀螺戰績」把「坤元・裂地震・磐岩旋王・絕盾GO」顯示
  成「磐岩旋王・絕盾GO」；「副陀螺」查詢把「坤元鎮界・崩嶽神熊・
  摸摸撼地GO」顯示成「崩嶽神熊・摸摸撼地GO」。段數 <=2 就整串照用。

用法：
    from battle_status import build_status_data, format_status_line

    data = build_status_data(
        main_top=main_row,        # 來自 talent_overview unified_view 或 tops.json
        sub_top=sub_row,          # 來自 parse_sub_top_query() 或 unified_view
        satellite_name=extract_active_satellite(raw_衛星圖鑑文字),
        formation_tops=[...],     # 編隊三隻，來自 unified_view 依名字查
    )
    print(format_status_line(data))
"""

import re
from typing import Optional

# 五行 -> 顏色圓點
_ELEMENT_COLOR = {
    "火": "🔴",
    "水": "🔵",
    "木": "🟢",
    "金": "🟡",
    "土": "🟤",
}
_NO_ELEMENT = "⬜"

# 類型 -> 單字簡寫
_TYPE_ABBR = {
    "攻擊型": "攻",
    "防禦型": "防",
    "持久型": "持",
    "平衡型": "衡",
}


def resolve_element(top: dict) -> Optional[str]:
    """
    取得陀螺的真實五行屬性。
    優先看 top-level "element"；為 null 時 fallback 到
    binding.element_stage.element（實測資料顯示這是常態，不是例外）。
    """
    elem = top.get("element")
    if elem:
        return elem
    binding = top.get("binding") or {}
    stage = binding.get("element_stage") or {}
    return stage.get("element")


def catalog_key(top: dict) -> str:
    """
    查 catalog（special_tops_catalog.json / cast_tops_catalog.json）用的 key。
    優先用 tops.json 原生的 "base_name" 欄位——這是遊戲資料本身的欄位，
    段數不固定，不是靠切字串猜的（例如「極・天熊・滅卻牙」是 3 段）。
    "base_name" 拿不到時（例如 talent_overview 的 unified_view row 目前
    沒有帶這個欄位）才退回 short_name() 的「最後兩段」猜測，準確度較低，
    只在沒有更好資料時當備援用。
    """
    base = top.get("base_name")
    if base:
        return base
    return short_name(top.get("name", ""))


def load_element_catalog(special_catalog: dict, cast_catalog: Optional[dict] = None) -> dict:
    """
    把 special_tops_catalog.json（旋王/旋神/UR精選/鑄造 四個分類）攤平成
    一個 {base_name: {"element":..., "build":...}} 的查表 dict，並用
    cast_tops_catalog.json（個人鑄造陀螺）覆蓋/補充——cast 是熊自己的
    鑄造紀錄，比共用 catalog 更新，優先權比較高。

    catalog 的 key 是遊戲的 base_name，查表時要用 catalog_key()，
    不要用 short_name()（兩者只在部分樣本剛好一致，不能當通用規則）。

    注意：catalog 裡的 element 有些本身就是 null（例如「赤焰旋王・狂牙GO」
    這種屬性隨機的旋王基礎版），代表這個 catalog 條目本來就沒有固定屬性，
    不是查表失敗——查到 None 就是 None，不用再往下猜。
    """
    flat = {}
    for category in special_catalog.values():
        flat.update(category)
    if cast_catalog:
        flat.update(cast_catalog)
    return flat


def resolve_element_any(top: dict, catalog: Optional[dict] = None) -> Optional[str]:
    """
    屬性解析的完整優先序：
    1. 目前實際裝備/綁定狀態（resolve_element：top-level 或 binding fallback）
       —— 這是「現在真的長怎樣」，最準。
    2. catalog 查表（用 catalog_key，優先 base_name）—— 給還沒綁定/沒點天賦、
       resolve_element 拿不到值的陀螺當備援。
    catalog 沒傳就只做第 1 步，行為跟舊版 resolve_element 一致，不影響既有呼叫端。
    """
    elem = resolve_element(top)
    if elem:
        return elem
    if not catalog:
        return None
    entry = catalog.get(catalog_key(top))
    return entry.get("element") if entry else None


def short_name(full_name: str) -> str:
    """
    給人看的顯示用短稱（不是查表用的 key，查表要用 catalog_key）。
    取「・」分隔的最後兩段，這是從 2 個真實查詢回覆反推的猜測規則：
    「陀螺戰績」把「坤元・裂地震・磐岩旋王・絕盾GO」顯示成
    「磐岩岩旋王・絕盾GO」；「副陀螺」查詢把「坤元鎮界・崩嶽神熊・
    摸摸撼地GO」顯示成「崩嶽神熊・摸摸撼地GO」——兩個都剛好是 2 段。

    已知這規則不一定通用：catalog 資料證實「☆聖氣盾・極・天熊・滅卻牙」
    的真實 base_name 是 3 段「極・天熊・滅卻牙」，用這個函式會切成
    2 段「天熊・滅卻牙」，跟遊戲的真實短稱未必一致（目前沒有聖氣盾的
    真實查詢短稱樣本可以核對）。純顯示排版用途可以接受這個誤差；
    需要準確比對（查表、跨來源 join）一律用 catalog_key()。
    段數 <=2 就整串照用。
    """
    parts = full_name.split("・")
    if len(parts) <= 2:
        return full_name
    return "・".join(parts[-2:])


def format_top(top: dict, *, show_name: bool = True) -> str:
    """
    格式化單顆陀螺為 "簡稱 顏色類型簡寫"，例如 "磐岩旋王 🟤防"。
    show_name=False 時只回傳 "顏色類型簡寫"（給編隊之類已經知道名字的情境用）。
    """
    elem = resolve_element(top)
    color = _ELEMENT_COLOR.get(elem, _NO_ELEMENT)
    type_abbr = _TYPE_ABBR.get(top.get("type"), "?")
    tag = f"{color}{type_abbr}"
    if not show_name:
        return tag
    name = short_name(top.get("name", "未知"))
    return f"{name} {tag}"


_ACTIVE_SATELLITE_RE = re.compile(r"⚔️出戰中\s+(\S+?)(?:\s*\+\d+)?（")


def extract_active_satellite(raw_text: str) -> Optional[str]:
    """
    從「衛星圖鑑」原始訊息文字裡抓出目前裝備（⚔️出戰中）的衛星名字。
    不查表，純文字截取。找不到就回傳 None。
    """
    m = _ACTIVE_SATELLITE_RE.search(raw_text)
    return m.group(1) if m else None


# 「🌗 副陀螺：崩嶽神熊・摸摸撼地GO（副屬性 🟡土屬性・爆擊率分你一半）」
# 副陀螺查詢本身就直接標出屬性，是比 unified_view 更即時的來源（不用等
# talent_overview 重新查一次天賦）。遊戲在這裡自己標了顏色（此樣本 🟡=土），
# 先原樣保留在 game_color，不做二次轉換，避免跟我方自訂配色打架；
# 未來蒐集到其他屬性的顏色樣本再統一比對、決定要不要改用遊戲配色。
# 「目前沒有副陀螺」的回覆格式尚無真實樣本，_SUB_TOP_UNSET_RE 未驗證。
_SUB_TOP_QUERY_RE = re.compile(
    r"🌗\s*副陀螺[：:]\s*(?P<name>.+?)(?:（|\()\s*副屬性\s*(?P<game_color>\S)?(?P<element>[火水木金土])屬性"
)
_SUB_TOP_UNSET_RE = re.compile(r"(尚未設定|沒有設定|卸下)")


def parse_sub_top_query(raw_text: str) -> Optional[dict]:
    """
    解析「副陀螺」查詢指令（無參數，查目前設定）的原始文字。
    回傳 {"name": 短稱, "element": 五行, "game_color": 遊戲標的顏色emoji}
    或 None（沒設定副陀螺，或格式未命中）。
    不含 type——這則訊息沒給類型，要靠 name 回頭比對 unified_view 補上。
    """
    m = _SUB_TOP_QUERY_RE.search(raw_text)
    if m:
        return {
            "name": short_name(m.group("name")),
            "element": m.group("element"),
            "game_color": m.group("game_color"),
        }
    if _SUB_TOP_UNSET_RE.search(raw_text):
        return None
    return None


def top_status_data(top: dict, catalog: Optional[dict] = None) -> dict:
    """
    結構化版本，給其他模組（例如 main_tower_advisor 的屬性/類型比對）
    直接重複使用，不必再解析一次原始資料。

    相容性說明：這個函式吃三種來源都不用改寫——
    1. tops.json 原始格式：element 常為 top-level null，
       resolve_element_any() 會 fallback 到 binding.element_stage.element。
    2. talent_overview.build_unified_view() 的 unified_view row：
       element 已經是攤平後的 top-level 值，第一步就直接命中。
    3. 前兩者都拿不到值時（例如未綁定、天賦未點的 UR），
       傳入 catalog（load_element_catalog() 的結果）就會再用短稱查
       special_tops_catalog.json / cast_tops_catalog.json 當備援；
       不傳 catalog 就跳過這步，行為不變。
    """
    return {
        "name": top.get("name", "未知"),
        "short_name": short_name(top.get("name", "未知")),
        "element": resolve_element_any(top, catalog),
        "type": top.get("type"),
    }


def build_status_data(
    main_top: Optional[dict] = None,
    sub_top: Optional[dict] = None,
    satellite_name: Optional[str] = None,
    formation_tops: Optional[list] = None,
    catalog: Optional[dict] = None,
) -> dict:
    """結構化總覽，供程式邏輯（例如比對推薦）取用，不是給人看的字串。"""
    return {
        "main": top_status_data(main_top, catalog) if main_top else None,
        "sub": top_status_data(sub_top, catalog) if sub_top else None,
        "satellite": satellite_name,
        "formation": [top_status_data(t, catalog) for t in formation_tops] if formation_tops else [],
    }


def format_status_line(data: dict) -> str:
    """把 build_status_data() 的結果轉成人看的一行式顯示。"""
    lines = []

    if data.get("main") or data.get("sub"):
        parts = []
        m = data.get("main")
        if m:
            color = _ELEMENT_COLOR.get(m["element"], _NO_ELEMENT)
            parts.append(f"{m['short_name']} {color}{_TYPE_ABBR.get(m['type'], '?')}")
        s = data.get("sub")
        if s:
            color = _ELEMENT_COLOR.get(s["element"], _NO_ELEMENT)
            parts.append(f"副{s['short_name']} {color}{_TYPE_ABBR.get(s['type'], '?')}")
        lines.append("出戰｜" + " ".join(parts))

    if data.get("satellite"):
        lines.append(f"衛星｜{data['satellite']}")

    if data.get("formation"):
        parts = []
        for t in data["formation"]:
            color = _ELEMENT_COLOR.get(t["element"], _NO_ELEMENT)
            parts.append(f"{t['short_name']} {color}{_TYPE_ABBR.get(t['type'], '?')}")
        lines.append("編隊｜" + "／".join(parts))

    return "\n".join(lines)


# status 欄位只會是 "出戰" / "副陀螺" / None 這三種值——已對照
# top_collection_snapshot.py 原始碼確認：_STATUS_MAP = {"⭐":"出戰","🌗":"副陀螺"}，
# GodTop/URLightEntry 存的是轉換後的中文字，不是原始 emoji，talent_overview.py
# 合併 UR 資料時也是直接沿用這個轉換後的字串。
_MAIN_STATUS = "出戰"
_SUB_STATUS = "副陀螺"


def find_active_tops(unified: list) -> dict:
    """
    從 talent_overview.build_unified_view() 的結果裡找出目前主陀螺/副陀螺。
    回傳 {"main": dict或None, "sub": dict或None}。
    """
    main = next((r for r in unified if r.get("status") == _MAIN_STATUS), None)
    sub = next((r for r in unified if r.get("status") == _SUB_STATUS), None)
    return {"main": main, "sub": sub}


def build_status_line(
    main_top: Optional[dict] = None,
    sub_top: Optional[dict] = None,
    satellite_name: Optional[str] = None,
    formation_tops: Optional[list] = None,
) -> str:
    """
    組裝完整出戰狀態列，只印出有提供的區塊，一律一行一區塊。
    """
    lines = []

    if main_top or sub_top:
        parts = []
        if main_top:
            parts.append(format_top(main_top))
        if sub_top:
            parts.append("副" + format_top(sub_top))
        lines.append("出戰｜" + " ".join(parts))

    if satellite_name:
        lines.append(f"衛星｜{satellite_name}")

    if formation_tops:
        parts = [format_top(t) for t in formation_tops]
        lines.append("編隊｜" + "／".join(parts))

    return "\n".join(lines)


if __name__ == "__main__":
    # 用熊提供的真實樣本快速驗證
    sample_main = {
        "name": "磐岩旋王・絕盾GO",
        "type": "防禦型",
        "element": None,
        "binding": {"element_stage": {"element": "土", "stage": 3}},
    }
    sample_formation = [
        {
            "name": "碧蒼裂空・蒼穹神熊・摸摸盤龍GO",
            "type": "持久型",
            "element": "木",
            "binding": {"element_stage": {"element": "木", "stage": 3}},
        },
        {
            "name": "龍淵・千重浪・蒼海旋王・不滅GO",
            "type": "持久型",
            "element": None,
            "binding": {"element_stage": {"element": "土", "stage": 3}},
        },
    ]
    print(build_status_line(
        main_top=sample_main,
        satellite_name="轟鳴皇",
        formation_tops=sample_formation,
    ))
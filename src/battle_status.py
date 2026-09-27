"""
battle_status.py
出戰狀態列格式化 —— 以少控多：一個函式處理單顆陀螺的簡寫，
上層負責組裝「主/副/衛星/編隊」的排列方式。

設計依據真實資料核對（2026-08-14 熊提供樣本，含 tops.json 片段、
陀螺收藏、綁定天賦一覽、副陀螺查詢回覆）：
- 陀螺的 top-level "element" 欄位常為 null（尤其 UR/神階，天賦未點時），
  真正屬性要 fallback 到 binding.element_stage.element。
- "type" 欄位固定四選一：攻擊型／防禦型／持久型／平衡型，不會缺值。
- 短稱規則取「・」分隔的最後兩段，不是第一段——由兩個獨立指令的真實
  回覆互相印證：「陀螺戰績」把「坤元・裂地震・磐岩旋王・絕盾GO」顯示
  成「磐岩旋王・絕盾GO」；「副陀螺」查詢把「坤元鎮界・崩嶽神熊・
  摸摸撼地GO」顯示成「崩嶽神熊・摸摸撼地GO」。段數 <=2 就整串照用。

=== 2026-08-19 修正：優先序反過來，binding 要蓋過 top-level ===
實測發現（熊提供的真實樣本，#15 森羅✦2、#24 北漠黃・翠森旋王・盤根GO）：
top-level "element" 欄位在「旋王／特殊 UR」這類屬性隨機取得的分類上，
是 annotate_special_source() 從 catalog 查出來的「這個名字模板可能的
屬性」，不是「這顆實際綁定投入的屬性」——一旦玩家綁定過、
binding.element_stage 有了真正的值，這個 catalog 猜測值卻沒有被清掉，
留在 top-level 繼續存在。原本的優先序（先信 top-level，查不到才 fallback
binding）在這種情況下會顯示錯誤：明明有更準的綁定資料，卻被更舊的
catalog 猜測值蓋過去。

binding 是玩家實際投入的地面真相，永遠比 catalog 猜測值準——優先序
改成「有 binding.element_stage 就優先用它，沒有才退回 top-level」。
這個函式被 resolve_element_any() 間接呼叫，而 resolve_element_any() 又
被 query_reactor.resolve_roster() 用在所有讀 roster 做判斷的功能上
（query_advisor_strategy／guard_clear_strategy 等），這裡修好，全部
下游呼叫端自動受惠，不用個別修改。

=== 2026-09 清理：刪掉一批孤兒函式 ===
talent_overview.py／top_collection_snapshot.py 確認是死碼（見
decisions-and-learnings）之後，回頭盤點發現這支檔案裡有將近一半的函式
只服務那條死路徑、或已經被其他地方取代，一併清掉：
    top_status_data() / build_status_data() / format_status_line()
        —— docstring 宣稱給 main_tower_advisor.py 用，實際查證那支根本
           沒有 import battle_status，這個關聯關係本身就是假的。
    find_active_tops()
        —— 明確依賴 talent_overview.build_unified_view() 的輸出格式。
    build_status_line()
        —— battle_status_line_strategy.py 自己內聯重寫了一份
           _compute_status_line()，從來沒有呼叫這裡的版本。
    extract_active_satellite()
        —— profile_sync_strategy.py 有自己獨立的
           _extract_active_satellite_name()，兩者只是名字相似，無關聯。
    parse_sub_top_query()
        —— docstring 自己承認已被 parsing/response_shapes/sub_top_status.py
           正式接手。
留下來的都是實測有真正外部呼叫端的：resolve_element／resolve_element_any／
load_element_catalog（roster_loader.py）、format_top（battle_status_line_strategy.py）。
"""

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
    "平衡型": "平",
}


def resolve_element(top: dict) -> Optional[str]:
    """
    取得陀螺的真實五行屬性。
    優先看 binding.element_stage.element（玩家實際綁定投入的地面真相），
    沒有才 fallback 到 top-level "element"（可能是 catalog 猜測值，
    對「屬性隨機取得」的分類來說不一定準——見 2026-08-19 修正說明）。
    """
    binding = top.get("binding") or {}
    stage = binding.get("element_stage") or {}
    elem = stage.get("element")
    if elem:
        return elem
    return top.get("element")


def catalog_key(top: dict) -> str:
    """
    查 catalog（special_tops_catalog.json / cast_tops_catalog.json）用的 key。
    優先用 tops.json 原生的 "base_name" 欄位——這是遊戲資料本身的欄位，
    段數不固定，不是靠切字串猜的（例如「極・天熊・滅卻牙」是 3 段）。
    "base_name" 拿不到時才退回 short_name() 的「最後兩段」猜測，準確度
    較低，只在沒有更好資料時當備援用。
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

    注意：這個函式不做任何檔案讀取，兩個參數都要是呼叫端已經讀好的 dict——
    名字裡的 "load" 容易讓人誤會會自己開檔，之後有機會的話可以考慮改名成
    更準確的 flatten_element_catalog() 或 merge_element_catalogs()（會牽動
    roster_loader.py 的呼叫端，這裡先不動，只在註解說明清楚）。

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
    1. 目前實際裝備/綁定狀態（resolve_element：binding.element_stage 優先，
       沒有才 fallback top-level）—— 這是「現在真的長怎樣」，最準。
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
    「磐岩旋王・絕盾GO」；「副陀螺」查詢把「坤元鎮界・崩嶽神熊・
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


if __name__ == "__main__":
    # 用熊提供的真實樣本快速驗證：#15 森羅✦2（top-level=土，binding=金），
    # 修正後應該回傳「金」，不是「土」
    sample_15 = {
        "name": "☆黑獄・森羅✦2",
        "type": "防禦型",
        "element": "土",  # catalog 猜測值（錯的）
        "binding": {"element_stage": {"element": "金", "stage": 3}},  # 真正的
    }
    print("修正驗證 #15 森羅✦2：", resolve_element(sample_15), "（預期：金）")

    sample_24 = {
        "name": "北漠黃・翠森旋王・盤根GO",
        "type": "持久型",
        "element": "木",  # catalog 猜測值（錯的）
        "binding": {"element_stage": {"element": "火", "stage": 1}},  # 真正的
    }
    print("修正驗證 #24 翠森旋王：", resolve_element(sample_24), "（預期：火）")

    # 沒有 binding 資料時，維持 fallback 到 top-level（不影響既有行為）
    sample_no_binding = {"name": "測試", "type": "攻擊型", "element": "水", "binding": None}
    print("無 binding 時 fallback：", resolve_element(sample_no_binding), "（預期：水）")
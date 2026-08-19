"""
inventory_parsers.py —— 「我的陀螺」「衛星圖鑑」伺服器回應 → 結構化資料庫

設計原則（跟 command_registry.json / parser.py 一致）：
  - 這裡只負責「把一段文字，解析成結構化 dict」跟「存檔」，不負責判斷什麼時候該呼叫，
    呼叫時機（比對觸發文字）由 main.py 的 dispatch_action 決定。
  - 陀螺清單／衛星清單屬於個人資料（不同帳號進度不同），存在 data/{帳號ID}/ 底下，
    路徑規則統一透過 data_store 取得。
"""

import json
import logging
import re
from pathlib import Path

from data_store import account_dir

logger = logging.getLogger(__name__)


# ============================================================
# 我的陀螺
# ============================================================
#
# 真實格式（比最早的示範複雜很多，範例）：
#   1. ✦✦✦✦✦🔱 炎焱燚明・焚天神熊・摸摸赤焱GO +17💥爆擊綁定IV｜神・攻擊型・戰力 631
#   16. 🌗✦✦✦✦ ⚡・⚡ +15🌀回歸綁定III｜UR・持久型・戰力 372
#   38. ✦✦✦ 紫熊・絕命喰｜SSR・平衡型・戰力 145
#   123.  雪熊・影縫破｜N・防禦型・戰力 55
#
# 組成：編號. [⭐出戰/🌗副陀螺]? [✦星等]* [🔱神/👑旋王]? [綁定冠名・]*本名 [+強化值]? [綁定標籤]? ｜稀有度・類型・戰力 N
#
# 只有 神／UR 這兩種稀有度才留個別細節（含 神旗下的旋神、UR 旗下的旋王／UR精選／鑄造），
# SSR 以下只累加進 rarity_summary，不留逐筆資料——這是刻意的取捨（見設計討論：
# SSR 以下數量龐大且用途低，逐筆存反而拖累效能跟可讀性）。

_TOP_LINE_PATTERN = re.compile(
    r"^(\d+)\.\s*(⭐|🌗)?(✦*)(🔱|👑)?\s*(.+?)｜(\S+)・(\S+)・戰力\s*(\d+)$"
)

_ENHANCEMENT_PATTERN = re.compile(r"\+(\d+)")
_BIND_TAG_PATTERN = re.compile(r"(💥爆擊綁定|🛡️護盾綁定|🌀回歸綁定)(IV|III|II|I)?")

_DETAILED_RARITIES = {"神", "UR"}


def _normalize_key(s):
    """去除所有空白字元（含全形空白），用來比對「陀螺清單」跟「綁定一覽」
    兩份回應對同一顆陀螺描述的完整名字段落——兩邊格式極相似，唯一差異是
    強化值跟綁定標籤之間偶爾多一個空格，正規化後即可穩定比對。"""
    return re.sub(r"\s+", "", s or "")


def _strip_enh_and_bind(name_raw):
    """從「完整名字段落」裡拆出強化值／綁定標籤，剩下的當作顯示名字
    （仍可能帶綁定冠名前綴，這裡不處理——配對兩份清單不需要拆到本名，
    見 merge_bindings_into_tops 的說明）。"""
    enhancement = None
    m = _ENHANCEMENT_PATTERN.search(name_raw)
    if m:
        enhancement = int(m.group(1))

    bind_type = None
    bind_tier = None
    bm = _BIND_TAG_PATTERN.search(name_raw)
    if bm:
        bind_type = bm.group(1)
        bind_tier = bm.group(2)  # 沒有羅馬數字代表最初階（尚未升階），保留 None 不硬填

    clean = _ENHANCEMENT_PATTERN.sub("", name_raw)
    clean = _BIND_TAG_PATTERN.sub("", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean, enhancement, bind_type, bind_tier


def parse_my_tops(text):
    """解析「我的陀螺」的伺服器回應文字。

    回傳的 detailed 只包含 神／UR 兩種稀有度（含旗下的旋神／旋王／UR精選／鑄造），
    其餘稀有度（SSR/SR/R/N）只計入 rarity_summary，不留逐筆資料。
    """
    detailed = []
    rarity_summary = {}
    total_matched = 0

    for line in text.splitlines():
        line = line.strip()
        m = _TOP_LINE_PATTERN.match(line)
        if not m:
            continue

        index, marker, stars, evo_marker, name_raw, rarity, top_type, power = m.groups()
        total_matched += 1
        rarity_summary[rarity] = rarity_summary.get(rarity, 0) + 1

        if rarity not in _DETAILED_RARITIES:
            continue

        if marker == "⭐":
            status = "active"       # 出戰中
        elif marker == "🌗":
            status = "secondary"    # 副陀螺
        else:
            status = "bench"        # 板凳（沒上場）

        clean_name, enhancement, bind_type, bind_tier = _strip_enh_and_bind(name_raw)

        detailed.append({
            "index": int(index),
            "name": clean_name,
            "match_key": _normalize_key(name_raw),
            "rarity": rarity,
            "type": top_type,
            "power": int(power),
            "stars": len(stars),
            "status": status,
            "evolution_marker": evo_marker,   # 🔱=神／旋神，👑=旋王，None=一般 UR 或鑄造
            "enhancement": enhancement,
            "bind_type": bind_type,
            "bind_tier": bind_tier,
        })

    declared_count, is_complete = check_completeness(
        text, total_matched, list_label="我的陀螺"
    )

    return {
        "total_count": total_matched,
        "declared_count": declared_count,
        "is_complete": is_complete,
        "detailed": detailed,
        "rarity_summary": rarity_summary,
    }


# ============================================================
# 衛星圖鑑（我的衛星 是這個的 alias，回應格式相同）
# ============================================================

_SATELLITE_HEADER_PATTERN = re.compile(
    r"^#(\d+)\s+(?:⚔️出戰中\s+)?(.+?)（([A-Z])\s*級·綜合\s*(\d+)\s*分·(.+?)·(.+?)）"
    r"攻(\d+)/防(\d+)/耐(\d+)$"
)
_SATELLITE_BONUS_PATTERN = re.compile(
    r"加成\s*攻\+(\d+)%/防\+(\d+)%/耐\+(\d+)%｜技能\s*(.+)$"
)
_SATELLITE_SLOT_PATTERN = re.compile(
    r"槽位\s*一般\s*(\d+)/(\d+)・金技\s*(\d+)/(\d+)"
)


def parse_satellite_catalog(text):
    """解析「衛星圖鑑」（含 alias「我的衛星」）的伺服器回應文字，
    回傳結構化的衛星清單。三行一組（標題／加成技能／槽位），逐組解析。
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    satellites = []
    current = None

    for line in lines:
        header_match = _SATELLITE_HEADER_PATTERN.match(line)
        if header_match:
            if current is not None:
                satellites.append(current)

            (index, name, grade, score, build, trait,
             atk, defe, hp) = header_match.groups()

            current = {
                "index": int(index),
                "name": name.strip(),
                "is_active": "⚔️出戰中" in line,
                "grade": grade,
                "score": int(score),
                "build": build,
                "trait": trait,
                "base_stats": {
                    "attack": int(atk),
                    "defense": int(defe),
                    "hp": int(hp),
                },
            }
            continue

        bonus_match = _SATELLITE_BONUS_PATTERN.search(line)
        if bonus_match and current is not None:
            atk_pct, def_pct, hp_pct, skills_raw = bonus_match.groups()
            current["bonus"] = {
                "attack_pct": int(atk_pct),
                "defense_pct": int(def_pct),
                "hp_pct": int(hp_pct),
            }
            current["skills"] = [s.strip() for s in skills_raw.split("、") if s.strip()]
            continue

        slot_match = _SATELLITE_SLOT_PATTERN.search(line)
        if slot_match and current is not None:
            normal_used, normal_total, gold_used, gold_total = slot_match.groups()
            current["slots"] = {
                "normal_used": int(normal_used),
                "normal_total": int(normal_total),
                "gold_used": int(gold_used),
                "gold_total": int(gold_total),
            }
            continue

    if current is not None:
        satellites.append(current)

    # 衛星是三行一組（標題／加成技能／槽位），如果最後一筆缺「槽位」那行，
    # 代表這組在解析完標題（甚至加成技能）後，訊息就被切斷了——
    # 這比對「宣告數量」更直接，能抓到「最後一則剛好卡在某顆衛星中間」的情況。
    last_entry_incomplete = bool(satellites) and "slots" not in satellites[-1]
    if last_entry_incomplete:
        logger.warning(
            "[衛星圖鑑] 疑似被 TG 分則截斷：最後一筆「%s」缺少槽位資訊（可能整段被腰斬）",
            satellites[-1]["name"],
        )

    declared_count, declared_match = check_completeness(
        text, len(satellites), list_label="衛星圖鑑"
    )
    is_complete = False if last_entry_incomplete else declared_match

    return {
        "total_count": len(satellites),
        "declared_count": declared_count,
        "is_complete": is_complete,
        "equip_limit": _extract_equip_limit(text),
        "satellites": satellites,
    }


_SPECIAL_CATALOG_FILE = "special_tops_catalog.json"   # 全帳號共通：旋王／旋神／UR精選（遊戲寫死，不會變）
_CAST_CATALOG_FILE = "cast_tops_catalog.json"          # 帳號專屬：鑄造陀螺（玩家自訂名字，只屬於這個帳號）

_special_catalog_cache = None  # 共通資料很少變，快取沒關係

_EQUIP_LIMIT_PATTERN = re.compile(r"目前裝備上限\s*(\d+)")


def _extract_equip_limit(text):
    m = _EQUIP_LIMIT_PATTERN.search(text)
    return int(m.group(1)) if m else None


def carry_over_enrichment(new_detailed, old_detailed):
    """把舊快照裡已有的 binding（天賦養成資料）依 match_key 接回新解析出的清單。

    只有「綁定一覽」查詢會產生 binding 資料（annotate_special_source() 標記的
    element/base_name/source_category 不需要靠這個，見下方呼叫端說明）。
    「陀螺收藏」查詢本身完全不提供天賦資訊，如果存檔時不做這一步接回，
    每次查一次陀螺收藏，之前綁定一覽合併進去的天賦資料就會被整份蓋掉
    （這是 2026-08-15 抓到的實際 bug：save_tops_snapshot 覆蓋存檔前，
    _handle_tops_start 沒有做這個接回，導致 element/binding 消失）。

    比對邏輯跟 merge_bindings_into_tops 一致，用 match_key（正規化後的
    完整名字段落），不用戰力，因為戰力會浮動、不適合當比對 key。
    找不到對應舊資料的（真正新增的陀螺），binding 保持 None，這是正確的，
    不是漏接——新陀螺本來就還沒有天賦資料。
    """
    old_by_key = {t["match_key"]: t for t in old_detailed if t.get("match_key")}

    for top in new_detailed:
        old = old_by_key.get(top.get("match_key"))
        top["binding"] = old.get("binding") if old else None

    return new_detailed


def _load_special_catalog(base_dir):
    """載入全帳號共通的旋王／旋神／UR精選對照表（data/common/ 底下）。"""
    global _special_catalog_cache
    if _special_catalog_cache is not None:
        return _special_catalog_cache
    f = Path(base_dir) / "data" / "common" / _SPECIAL_CATALOG_FILE
    if not f.exists():
        _special_catalog_cache = {}
        return _special_catalog_cache
    with f.open("r", encoding="utf-8") as fp:
        _special_catalog_cache = json.load(fp)
    return _special_catalog_cache


def _load_cast_catalog(base_dir, account_id):
    """載入這個帳號自己的鑄造陀螺對照表（data/{帳號ID}/ 底下）。

    故意不快取——鑄造是會持續發生的事件，之後如果接了鑄造訊息自動寫入
    這份檔案（見討論），快取會讓當次流程讀到舊資料，不快取的成本在這個
    使用頻率（每次「綁定一覽」查詢才讀一次）可以忽略。
    """
    f = account_dir(base_dir, account_id) / _CAST_CATALOG_FILE
    if not f.exists():
        return {}
    with f.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def annotate_special_source(tops_detailed, base_dir, account_id):
    """幫 detailed 清單裡的每一顆，比對特殊陀螺對照表，標記來源分類
    （旋神／旋王／UR精選／鑄造／None=一般 UR）跟五行屬性。

    用「顯示名字是否以對照表裡的本名結尾」判斷——不管前面疊了幾層綁定冠名
    都抓得到，因為本名固定在最後、冠名只會往前加。對照表沒有的（一般 UR、
    未收錄的鑄造）保留 source_category=None、element=None，不算錯誤。

    旋王／旋神／UR精選是全帳號共通資料（data/common/），鑄造陀螺是這個
    帳號自己的（data/{帳號ID}/）——兩份分開存放、分開讀取，這裡合併成
    同一份查詢清單使用，呼叫端不用關心底層是兩份檔案。
    """
    common_catalog = _load_special_catalog(base_dir)
    cast_catalog = _load_cast_catalog(base_dir, account_id)

    # 攤平成 [(本名, 分類, 資料), ...]，本名長的排前面，避免短名字誤先比對到
    # （目前資料集沒有這種案例，但保留這個排序原則比較穩健）
    flat = []
    for category, entries in common_catalog.items():
        for base_name, info in entries.items():
            flat.append((base_name, category, info))
    for base_name, info in cast_catalog.items():
        flat.append((base_name, "鑄造", info))
    flat.sort(key=lambda x: len(x[0]), reverse=True)

    for top in tops_detailed:
        name = top.get("name", "")
        for base_name, category, info in flat:
            if name.endswith(base_name):
                top["source_category"] = category
                top["base_name"] = base_name
                top["element"] = info.get("element")
                break
        else:
            top["source_category"] = None
            top["base_name"] = None
            top["element"] = None

    return tops_detailed


# ============================================================
# 綁定一覽（陀螺天賦養成資料，只列出「有綁定靈魂石」的陀螺）
# ============================================================
#
# 真實格式（兩行一組：標題／熟練＋天賦）：
#   #1 炎焱燚明・焚天神熊・摸摸赤焱GO +17 💥爆擊綁定IV　攻擊型・戰力 631
#   　熟練 420/420・可兌換 0 次｜五行火3階・破軍3・會心3・昏蝕3・噬血1・極意2・✨共鳴:連斬/蝕滅
#   #13 萬象歸一・原初旗艦・摸摸GO +15 💥爆擊綁定III　平衡型・戰力 396
#   　熟練 360/360・可兌換 12 次｜尚未點天賦
#
# 「尚未點天賦」代表這顆熟練度已滿但還沒點過天賦樹（跟「五行X1階但沒有
# 任何天賦項目」不同，後者是剛換上、還在練熟練度，元素階級是隨裝備自動
# 顯示的基礎資訊，不代表已經投入天賦點數）。

_BINDING_HEADER_PATTERN = re.compile(
    r"^#(\d+)\s+(?:⚔️出戰中\s+)?(.+?)　(\S+)・戰力\s*(\d+)$"
)
_BINDING_DETAIL_PATTERN = re.compile(
    r"熟練\s*(\d+)/(\d+)・可兌換\s*(\d+)\s*次｜(.+)$"
)
_ELEMENT_STAGE_PATTERN = re.compile(r"^五行(.)(\d+)階$")
_RESONANCE_PATTERN = re.compile(r"^✨共鳴[:：](.+)$")
_TALENT_LEVEL_PATTERN = re.compile(r"^(.+?)(\d+)?$")


def _parse_binding_tail(tail):
    """解析熟練那行「｜」後面的天賦段落。"""
    if tail.strip() == "尚未點天賦":
        return {
            "talents_allocated": False,
            "element_stage": None,
            "talents": [],
            "resonance": [],
        }

    element_stage = None
    talents = []
    resonance = []

    for part in tail.split("・"):
        part = part.strip()
        if not part:
            continue

        es_match = _ELEMENT_STAGE_PATTERN.match(part)
        if es_match:
            element_stage = {"element": es_match.group(1), "stage": int(es_match.group(2))}
            continue

        res_match = _RESONANCE_PATTERN.match(part)
        if res_match:
            resonance = [r.strip() for r in res_match.group(1).split("/") if r.strip()]
            continue

        tm = _TALENT_LEVEL_PATTERN.match(part)
        talent_name = tm.group(1)
        talent_level = int(tm.group(2)) if tm.group(2) else 1
        talents.append({"name": talent_name, "level": talent_level})

    return {
        "talents_allocated": True,
        "element_stage": element_stage,
        "talents": talents,
        "resonance": resonance,
    }


def parse_bindings(text):
    """解析「你的綁定陀螺天賦一覽」的伺服器回應文字。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    bindings = []
    current = None

    for line in lines:
        header_match = _BINDING_HEADER_PATTERN.match(line)
        if header_match:
            if current is not None:
                bindings.append(current)

            index, name_raw, build, power = header_match.groups()
            clean_name, enhancement, bind_type, bind_tier = _strip_enh_and_bind(name_raw)

            current = {
                "index": int(index),
                "name": clean_name,
                "match_key": _normalize_key(name_raw),
                "is_active": "⚔️出戰中" in line,
                "build": build,
                "power": int(power),
                "enhancement": enhancement,
                "bind_type": bind_type,
                "bind_tier": bind_tier,
            }
            continue

        detail_match = _BINDING_DETAIL_PATTERN.match(line)
        if detail_match and current is not None:
            cur_mastery, max_mastery, exchange, tail = detail_match.groups()
            current["mastery"] = {"current": int(cur_mastery), "max": int(max_mastery)}
            current["exchange_available"] = int(exchange)
            current.update(_parse_binding_tail(tail))
            continue

    if current is not None:
        bindings.append(current)

    # 兩行一組，如果最後一筆缺「熟練」那行，代表切在標題跟熟練行中間。
    last_entry_incomplete = bool(bindings) and "mastery" not in bindings[-1]
    if last_entry_incomplete:
        logger.warning(
            "[綁定一覽] 疑似被截斷：最後一筆「%s」缺少熟練度資訊",
            bindings[-1]["name"],
        )

    declared_count, declared_match = check_completeness(
        text, len(bindings), list_label="綁定一覽"
    )
    is_complete = False if last_entry_incomplete else declared_match

    return {
        "total_count": len(bindings),
        "declared_count": declared_count,
        "is_complete": is_complete,
        "bindings": bindings,
    }

def merge_bindings_into_tops(tops_result, bindings_result):
    """把「綁定一覽」的天賦養成資料，合併進「我的陀螺」detailed 清單裡對應的那顆。

    配對 key 只用 match_key（正規化後的完整名字段落：綁定冠名＋本名＋強化值＋
    綁定標籤），不把戰力算進去——戰力後續版本會被額外機制影響、不是穩定值，
    兩次查詢之間浮動就會讓配對失效。match_key 本身已經有足夠的唯一性：
    冠名是玩家自訂、本名依遊戲規則不重複、強化值/綁定標籤再疊加一層區分度，
    不需要靠戰力輔助。
    """
    binding_index = {b["match_key"]: b for b in bindings_result["bindings"]}

    matched_count = 0
    for top in tops_result["detailed"]:
        binding = binding_index.pop(top.get("match_key"), None)
        if binding is None:
            top["binding"] = None
            continue

        top["binding"] = {
            "mastery": binding["mastery"],
            "exchange_available": binding["exchange_available"],
            "talents_allocated": binding["talents_allocated"],
            "element_stage": binding["element_stage"],
            "talents": binding["talents"],
            "resonance": binding["resonance"],
        }
        matched_count += 1

    # 2026-08-19 補上：綁定一覽本身就知道「哪顆⚔️出戰中」（is_active），
    # 之前完全沒同步回 tops.json 的 status 欄位——這是「陀螺清單/status」
    # 跟「綁定一覽/is_active」兩套出戰追蹤沒串起來的實際缺口。
    # 只做「正面確認」：找到綁定一覽裡標出戰中的那顆才動作，同時把
    # 「之前被標成 active、但這次不是它」的那顆改回 bench；如果這批
    # 綁定一覽裡完全沒有任何一筆是 is_active（可能真正出戰的那顆沒綁定
    # 過，不會出現在綁定一覽裡），就不碰 status，維持原樣，避免誤刪
    # 正確的 active 標記。
    active_binding = next((b for b in bindings_result["bindings"] if b.get("is_active")), None)
    if active_binding is not None:
        active_key = active_binding["match_key"]
        for top in tops_result["detailed"]:
            if top.get("match_key") == active_key:
                top["status"] = "active"
            elif top.get("status") == "active":
                top["status"] = "bench"

    if binding_index:
        # 理論上不該發生：綁定一覽只會列出確實綁定過的陀螺，一定存在於陀螺清單裡。
        # 出現代表兩份清單擷取時間點不同步（例如中途強化了、或改了綁定冠名），
        # 記下來方便追查，不擋住其餘已配對成功的結果。
        logger.warning(
            "[綁定合併] 有 %d 筆綁定紀錄找不到對應的陀螺，可能兩份清單擷取時間不同步：%s",
            len(binding_index),
            list(binding_index.keys()),
        )

    return {
        "matched_count": matched_count,
        "total_bindings": len(bindings_result["bindings"]),
        "unmatched_binding_count": len(binding_index),
    }


def is_bindings_message(text):
    return (text or "").strip().startswith("🔧 你的綁定陀螺天賦一覽")


# ============================================================
# 完整性檢查（偵測 TG 自動分則造成的截斷）
# ============================================================
#
# 圖鑑類回應開頭通常會宣告「共 N 顆／N 顆」，但訊息過長時 TG 會自動拆成多則
# 送達。若呼叫端沒有把所有分則接起來就丟進 parser，解析出的筆數會少於宣告值。
# 這裡只做「比對＋記錄」，不負責決定何時該合併多則訊息（那是 monitor 層的事），
# 純粹是最後一道防線：算出來的筆數對不上宣告值，就記一筆 warning，並把
# declared_count / is_complete 放進解析結果，讓上層（executor／查詢指令）
# 自行決定要不要重試、提示使用者，或標記這份快照不可信。

_DECLARED_COUNT_PATTERN = re.compile(r"(?:共\s*)?(\d+)\s*(?:顆|隻|個|支)")


def check_completeness(text, actual_count, count_pattern=_DECLARED_COUNT_PATTERN, list_label="清單"):
    """比對文字開頭宣告的數量跟實際解析出的筆數。

    count_pattern 可自訂，方便之後其他清單型式（宣告格式不是「N 顆」）沿用
    同一套機制，不用重寫比對邏輯。

    回傳 (declared_count, is_complete)：
      - declared_count 為 None：文字裡沒抓到宣告數字，視為無法判斷（不記警告）。
      - is_complete 為 False：抓到宣告數字但跟實際筆數對不上，視為疑似截斷。
    """
    m = count_pattern.search(text)
    if not m:
        return None, None

    declared = int(m.group(1))
    is_complete = declared == actual_count

    if not is_complete:
        logger.warning(
            "[%s] 疑似被 TG 分則截斷：宣告 %d 筆，實際解析出 %d 筆",
            list_label, declared, actual_count,
        )

    return declared, is_complete


def merge_message_parts(parts):
    """把同一批被 TG 拆成多則的原始文字，依送達順序串接成一段完整文字。

    parser 是逐行比對（衛星三行一組／陀螺一行一筆），不依賴「訊息邊界」，
    所以只要 parts 順序正確，單純串接即可餵給 parse_my_tops / parse_satellite_catalog。
    實際要在什麼時機把多則訊息判定為「同一批、該合併」（例如同一 chat_id、
    短時間內連續到達、且不是新的觸發指令），交給 monitor 層處理。
    """
    return "\n".join(p.strip("\n") for p in parts if p)


# ============================================================
# 觸發判斷 + 存檔（個人資料，依帳號隔離）
# ============================================================

def is_my_tops_message(text):
    return (text or "").strip().startswith("🧰 你的陀螺收藏")


def is_satellite_catalog_message(text):
    return (text or "").strip().startswith("🛰️ 衛星圖鑑")


def save_tops_snapshot(base_dir, account_id, tops_result):
    """陀螺清單整份覆蓋存檔（回應本身就是當下完整快照，覆蓋最準確）。"""
    f = account_dir(base_dir, account_id) / "tops.json"
    with f.open("w", encoding="utf-8") as fp:
        json.dump(tops_result, fp, ensure_ascii=False, indent=2)
    return tops_result


def load_tops_snapshot(base_dir, account_id):
    """讀回上次存的陀螺清單快照；還沒存過就回傳 None。"""
    f = account_dir(base_dir, account_id) / "tops.json"
    if not f.exists():
        return None
    with f.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def save_satellites_snapshot(base_dir, account_id, satellite_result):
    f = account_dir(base_dir, account_id) / "satellites.json"
    with f.open("w", encoding="utf-8") as fp:
        json.dump(satellite_result, fp, ensure_ascii=False, indent=2)
    return satellite_result


if __name__ == "__main__":
    import json

    my_tops_text = """🧰 你的陀螺收藏（共 3 顆,⭐=出戰　🌗=副陀螺,依戰力排序）
──────────────
1. ⭐✦✦✦✦ 泥巴星球｜UR・防禦型・戰力 208
2. ✦✦✦✦ 光熊・幻滅爪｜UR・平衡型・戰力 206
3. ✦✦✦✦ 絕・煉熊・碎星牙｜UR・防禦型・戰力 200
──────────────
換出戰：打「出戰 編號」｜🌗 副陀螺：打「副陀螺 編號」｜🔧 綁定過的打「我的天賦」
👉 下一步：打「綁定 名字」把出戰的 泥巴星球 綁定,解鎖熟練度＋天賦養成 💎"""

    satellite_text = """🛰️ 衛星圖鑑（6 顆,目前裝備上限 1）
──────────────
#1 ⚔️出戰中 強衛星（A 級·綜合 77 分·攻擊流·💡靈感）攻20/防12/耐12
　加成 攻+10%/防+15%/耐+2%｜技能 ✦旋星閃3、🛡️旋盾展開、💧旋能回充2、⚔️雙刃3、🧱硬化2、🎯銳芒3、🏔山嶽
　槽位 一般 6/6・金技 1/3
#2 亂點（C 級·綜合 40 分·鐵壁流·🪨堅韌）攻25/防38/耐20
　加成 攻+11%/防+8%/耐+9%｜技能 ✦旋星閃、💪韌體、🔺共鳴增幅、🌠援護射擊、🎯銳芒
　槽位 一般 5/6・金技 0/3
#3 繁技（B 級·綜合 60 分·攻擊流·🪨堅韌）攻25/防10/耐16
　加成 攻+14%/防+10%/耐+13%｜技能 🔺共鳴增幅2、✦旋星閃2、💪韌體3、🧿迴旋盾3、🌱續航核、🧱硬化2
　槽位 一般 6/6・金技 0/3
#4 援護射擊（D 級·綜合 33 分·均衡型·💡靈感）攻60/防50/耐66
　加成 攻+12%/防+10%/耐+13%｜技能 🌠援護射擊
　槽位 一般 1/6・金技 0/3
#5 韌體（D 級·綜合 32 分·均衡型·🧊沉著）攻65/防22/耐66
　加成 攻+13%/防+4%/耐+18%｜技能 💪韌體
　槽位 一般 1/6・金技 0/3
#6 迴旋盾（A 級·綜合 81 分·連技流·😪慵懶）攻53/防56/耐50
　加成 攻+23%/防+11%/耐+13%｜技能 🧿迴旋盾、🔺共鳴增幅2、🛡️旋盾展開、💠星核共振、🌀連旋亂舞、☄️流星追擊
　槽位 一般 4/6・金技 2/3
──────────────
「裝備衛星 編號」換裝；「培育」養新的一顆；「繼承 編號」獻祭一顆,下次培育帶血統(數值頭期款+金技傳承)
「融合衛星 主編號 素材編號」吃掉一顆,主星有機會習得牠的一個技能(先不加「確認」會顯示機率)
「強化衛星」點數強化裝備中那顆(+4 前必成)｜「靈魂合體 編號」讓一顆衛星常駐靈魂(需 🌠合體石,霸主限定)"""

    print("=== 我的陀螺 解析結果 ===")
    print(json.dumps(parse_my_tops(my_tops_text), ensure_ascii=False, indent=2))

    print("\n=== 衛星圖鑑 解析結果 ===")
    print(json.dumps(parse_satellite_catalog(satellite_text), ensure_ascii=False, indent=2))
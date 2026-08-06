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

from data_store import account_dir

logger = logging.getLogger(__name__)


# ============================================================
# 我的陀螺
# ============================================================

_TOP_LINE_PATTERN = re.compile(
    r"^(\d+)\.\s*(⭐|🌗)?(✦*)\s*(.+?)｜(\S+)・(\S+)・戰力\s*(\d+)$"
)


def parse_my_tops(text):
    """解析「我的陀螺」的伺服器回應文字，回傳結構化的陀螺清單。"""
    tops = []
    for line in text.splitlines():
        line = line.strip()
        m = _TOP_LINE_PATTERN.match(line)
        if not m:
            continue

        index, marker, stars, name, rarity, top_type, power = m.groups()

        if marker == "⭐":
            status = "active"       # 出戰中
        elif marker == "🌗":
            status = "secondary"    # 副陀螺
        else:
            status = "bench"        # 板凳（沒上場）

        tops.append({
            "index": int(index),
            "name": name.strip(),
            "rarity": rarity,
            "type": top_type,
            "power": int(power),
            "stars": len(stars),
            "status": status,
        })

    declared_count, is_complete = check_completeness(
        text, len(tops), list_label="我的陀螺"
    )

    return {
        "total_count": len(tops),
        "declared_count": declared_count,
        "is_complete": is_complete,
        "tops": tops,
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


_EQUIP_LIMIT_PATTERN = re.compile(r"目前裝備上限\s*(\d+)")


def _extract_equip_limit(text):
    m = _EQUIP_LIMIT_PATTERN.search(text)
    return int(m.group(1)) if m else None


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
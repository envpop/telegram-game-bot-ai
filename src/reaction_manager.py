"""/react terminal command: manage text reaction rules (check -> live lifecycle).

設計說明
========
職責切分（避免與 /auto 重複）：
  - /auto  ：「大串」自動化的開關入口——多個程式、多種判斷、需要前後關係或有變動資料的
             整套功能（世界王、主塔戰鬥、群星計畫…）合成一個 system key，由 auto_toggle 管理；
             需要切換選項時另有專用指令（例如 /wbmode、/sakura）。
  - /react ：「小」的觸發即反應規則（reaction_rules.json 的文字規則）的管理入口，
             各規則自己的狀態（檢查中／上線）在這裡切換。
  - /react 對 auto_toggle 只做唯讀列出，不提供任何改動它的指令，避免兩個入口各自切換、狀態亂掉。

Reaction 規則的兩種狀態（只用既有欄位，不新增 schema，reaction_rules.py 不需修改）：
  - 檢查中：risk_level=safe 且 auto_execute=false
            → 命中時只出現提示文字，不執行動作（新增規則的預設狀態）。
  - 上線  ：risk_level=safe 且 auto_execute=true
            → 命中時真的執行動作。

流程：
  /react add    建立規則（強制為「檢查中」）
  /react test   用範例文字離線測試規則會不會命中（不送出任何東西）
  /react live   確認後上線（需再輸入 yes 才會生效；非 safe 規則不允許上線）
  /react check  上線規則退回「檢查中」

已知限制：
  - /react test 的比對方式需與 reaction_rules.py 保持一致（目前是子字串比對）；
    若該檔的比對方式日後改變，這裡的 _matches() 要同步。
  - /react test 只測關鍵字，不模擬 watch_chat 與冷卻；規則若有 watch_chat 會另外提示。
  - 規則 id 以外的欄位（cooldown_seconds 等）不在此指令修改範圍，請直接編輯 JSON。
"""
import json
import shlex

import auto_toggle
import data_store


# 「模組 → system key」對照，只用於 /react list 的唯讀顯示。
# 以 key 的常數名稱（字串）登記，用 getattr 解析：常數已從 auto_toggle 移除時自動略過，
# 不會因為舊模組（例如已移除的 furnace 開關）而在 import 時崩潰。
_TRIGGER_MODULES = (
    ("guard_clear_strategy", "GUARD_CLEAR"),
    ("furnace_cycle_strategy", "WORLD_BOSS"),
    ("furnace_loop_strategy", "WORLD_BOSS"),
    ("world_boss_strategy", "WORLD_BOSS"),
    ("main_tower_battle_strategy", "MAIN_TOWER_BATTLE"),
    ("satellite_training_strategy", "SATELLITE_TRAINING"),
    ("satellite_naming_strategy", "SATELLITE_NAMING"),
    ("sakura_strategy (公告策略)", "SAKURA_AUTO_CHALLENGE"),
)

STATE_LIVE = "上線"
STATE_CHECK = "檢查中"
STATE_MANUAL = "手動確認"


# ---------------------------------------------------------------- 規則檔 I/O

def _rules_path(base_dir):
    # 路徑統一由 data_store 提供（單一來源）。
    return data_store.config_dir(base_dir) / "reaction_rules.json"


def _load_rules(base_dir):
    with _rules_path(base_dir).open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_rules(base_dir, data):
    path = _rules_path(base_dir)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _find_rule(data, rule_id):
    for rule in data.get("rules", []):
        if rule.get("id") == rule_id:
            return rule
    return None


def _rule_state(rule):
    """回傳規則目前狀態：上線 / 檢查中 / 手動確認（非 safe，不會自動執行）。"""
    safe = rule.get("risk_level") == "safe"
    auto = bool(rule.get("auto_execute"))
    if safe and auto:
        return STATE_LIVE
    if not auto:
        return STATE_CHECK
    return STATE_MANUAL


def _matches(pattern, text):
    """與 reaction_rules.py 一致：match_pattern 是「子字串」比對，不是 regex。"""
    return pattern in text


# ---------------------------------------------------------------- 說明

def _usage():
    print(
        "/react list                      列出反應規則與自動化開關狀態（唯讀）\n"
        "/react add <id> <關鍵字> <回應>   新增規則，預設「檢查中」：命中只提示、不動作\n"
        "/react test <id> <文字>           用範例文字測試規則是否命中（不送出任何東西）\n"
        "/react live <id>                  檢視規則並要求確認；/react live <id> yes 才會上線\n"
        "/react check <id>                 把上線規則退回「檢查中」\n"
        "自動化開關（trigger / strategy）請改用 /auto <key> on|off，/react 不再切換。"
    )


# ---------------------------------------------------------------- list

def _print_rules(data):
    print("[Reaction 規則]")
    rules = data.get("rules", [])
    if not rules:
        print("  （尚無規則）")
    for rule in rules:
        action = rule.get("action") or "（無動作）"
        print(f"  {rule.get('id')}: {rule.get('match_pattern')} → {action} [{_rule_state(rule)}]")


def _print_switches(base_dir):
    """自動化開關（唯讀）。同一個 system key 只列一次，並附上共用它的模組。"""
    modules_by_key = {}
    for module_name, const_name in _TRIGGER_MODULES:
        key = getattr(auto_toggle, const_name, None)
        if key is not None:
            modules_by_key.setdefault(key, []).append(module_name)

    print("[自動化開關]（唯讀；變更請用 /auto <key> on|off）")
    for key, label in auto_toggle.SYSTEM_KEYS.items():
        state = "開啟" if auto_toggle.is_enabled(base_dir, key) else "關閉"
        modules = modules_by_key.get(key)
        suffix = f" ← {', '.join(modules)}" if modules else ""
        print(f"  {label} ({key}): {state}{suffix}")


# ---------------------------------------------------------------- 各子指令

def _cmd_list(base_dir):
    _print_rules(_load_rules(base_dir))
    _print_switches(base_dir)


def _cmd_add(base_dir, rule_id, pattern, action):
    data = _load_rules(base_dir)
    rules = data.setdefault("rules", [])
    if _find_rule(data, rule_id):
        print(f"[react] 規則 id 已存在：{rule_id}")
        return
    if not pattern.strip() or not action.strip():
        print("[react] 關鍵字和回應不可空白。")
        return
    rules.append({
        "id": rule_id,
        "match_pattern": pattern,
        "description": "由 /react 建立",
        "risk_level": "safe",
        "auto_execute": False,
        "action": action,
        "cooldown_seconds": 60,
    })
    _save_rules(base_dir, data)
    print(f"[react] 已新增「{rule_id}」[{STATE_CHECK}]：命中只提示、不動作。\n"
          f"        可用 /react test {rule_id} <文字> 測試；確認沒問題再 /react live {rule_id}。")


def _cmd_test(base_dir, rule_id, sample):
    rule = _find_rule(_load_rules(base_dir), rule_id)
    if rule is None:
        print(f"[react] 找不到規則：{rule_id}")
        return
    hit = _matches(rule.get("match_pattern", ""), sample)
    state = _rule_state(rule)
    if not hit:
        print(f"[react] 「{rule_id}」未命中（關鍵字是子字串比對，需完全包含）。")
        return
    if rule.get("watch_chat"):
        print(f"[react] 注意：此規則限定聊天室「{rule['watch_chat']}」才會觸發。")
    if state == STATE_LIVE:
        print(f"[react] 「{rule_id}」命中；目前為上線，實際發生時會執行：{rule.get('action')}")
    else:
        print(f"[react] 「{rule_id}」命中；目前為{state}，實際發生時只提示、不執行：{rule.get('action')}")


def _cmd_live(base_dir, rule_id, confirmed):
    data = _load_rules(base_dir)
    rule = _find_rule(data, rule_id)
    if rule is None:
        print(f"[react] 找不到規則：{rule_id}")
        return
    if rule.get("risk_level") != "safe":
        print(f"[react] 「{rule_id}」risk_level 不是 safe，不允許用 /react 上線；請手動審查 JSON。")
        return
    if _rule_state(rule) == STATE_LIVE:
        print(f"[react] 「{rule_id}」已經是上線狀態。")
        return
    if not confirmed:
        print(f"[react] 即將上線：{rule_id}\n"
              f"        關鍵字：{rule.get('match_pattern')}\n"
              f"        動作  ：{rule.get('action')}\n"
              f"        冷卻  ：{rule.get('cooldown_seconds')} 秒\n"
              f"        確認請輸入：/react live {rule_id} yes")
        return
    rule["auto_execute"] = True
    _save_rules(base_dir, data)
    print(f"[react] 「{rule_id}」已上線；命中時會自動執行。退回請用 /react check {rule_id}")


def _cmd_check(base_dir, rule_id):
    data = _load_rules(base_dir)
    rule = _find_rule(data, rule_id)
    if rule is None:
        print(f"[react] 找不到規則：{rule_id}")
        return
    if not rule.get("auto_execute"):
        print(f"[react] 「{rule_id}」本來就是{STATE_CHECK}。")
        return
    rule["auto_execute"] = False
    _save_rules(base_dir, data)
    print(f"[react] 「{rule_id}」已退回{STATE_CHECK}；命中只提示、不動作。")


# ---------------------------------------------------------------- 入口

async def handle_command(text, base_dir, account_id):
    try:
        parts = shlex.split(text)
    except ValueError as e:
        print(f"[react] 指令引號格式錯誤：{e}")
        _usage()
        return

    n = len(parts)
    sub = parts[1] if n > 1 else ""

    if n == 2 and sub == "list":
        _cmd_list(base_dir)
    elif n == 5 and sub == "add":
        _cmd_add(base_dir, parts[2], parts[3], parts[4])
    elif n == 4 and sub == "test":
        _cmd_test(base_dir, parts[2], parts[3])
    elif n == 3 and sub == "live":
        _cmd_live(base_dir, parts[2], confirmed=False)
    elif n == 4 and sub == "live" and parts[3] == "yes":
        _cmd_live(base_dir, parts[2], confirmed=True)
    elif n == 3 and sub == "check":
        _cmd_check(base_dir, parts[2])
    elif sub == "trigger":
        print("[react] 自動化開關已統一由 /auto <key> on|off 管理；/react 只能用 list 查看。")
    else:
        _usage()
"""/react terminal command for managing safe text reactions and automation switches."""
import json
import shlex
from pathlib import Path

import auto_toggle


_ALIASES = {
    "mtb": auto_toggle.MAIN_TOWER_BATTLE,
    "wb": auto_toggle.WORLD_BOSS,
    "sat": auto_toggle.SATELLITE_TRAINING,
    "gc": auto_toggle.GUARD_CLEAR,
    "satname": auto_toggle.SATELLITE_NAMING,
    "sakura": auto_toggle.SAKURA_AUTO_CHALLENGE,
    "furnace": auto_toggle.FURNACE_AUTO,
}

_TRIGGER_MODULES = (
    ("guard_clear_strategy", auto_toggle.GUARD_CLEAR),
    ("furnace_cycle_strategy", auto_toggle.WORLD_BOSS),
    ("furnace_loop_strategy", auto_toggle.WORLD_BOSS),
    ("world_boss_strategy", auto_toggle.WORLD_BOSS),
    ("main_tower_battle_strategy", auto_toggle.MAIN_TOWER_BATTLE),
    ("satellite_training_strategy", auto_toggle.SATELLITE_TRAINING),
    ("satellite_naming_strategy", auto_toggle.SATELLITE_NAMING),
    ("sakura_strategy (公告策略)", auto_toggle.SAKURA_AUTO_CHALLENGE),
)


def _rules_path(base_dir):
    return Path(base_dir) / "config" / "reaction_rules.json"


def _load_rules(base_dir):
    with _rules_path(base_dir).open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_rules(base_dir, data):
    path = _rules_path(base_dir)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _usage():
    print("/react list | /react add <id> <關鍵字> <回應> | /react trigger <key> on|off\n"
          "新增反應預設為 safe + auto_execute=false，只提示你手動處理。\n"
          "trigger key 可用 mtb/wb/sat/gc/satname/sakura/furnace 或完整 system key。")


async def handle_command(text, base_dir, account_id):
    try:
        parts = shlex.split(text)
    except ValueError as e:
        print(f"[react] 指令引號格式錯誤：{e}")
        _usage()
        return

    if len(parts) == 2 and parts[1] == "list":
        data = _load_rules(base_dir)
        print("[Reaction 規則]")
        for rule in data.get("rules", []):
            state = "自動" if rule.get("risk_level") == "safe" and rule.get("auto_execute") else "手動確認"
            print(f"  {rule.get('id')}: {rule.get('match_pattern')} → {rule.get('action') or '（無動作）'} [{state}]")
        print("[Trigger 模組]（同一 system key 共用開關）")
        for module_name, key in _TRIGGER_MODULES:
            state = "開啟" if auto_toggle.is_enabled(base_dir, key) else "關閉"
            print(f"  {module_name} [{key}]: {state}")
        print("[其他自動化開關]")
        for key, label in auto_toggle.SYSTEM_KEYS.items():
            state = "開啟" if auto_toggle.is_enabled(base_dir, key) else "關閉"
            print(f"  {label} ({key}): {state}")
        return

    if len(parts) == 5 and parts[1] == "add":
        _, _, rule_id, pattern, action = parts
        data = _load_rules(base_dir)
        rules = data.setdefault("rules", [])
        if any(rule.get("id") == rule_id for rule in rules):
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
        print(f"[react] 已新增「{rule_id}」；預設只提示、不自動執行。")
        return

    if len(parts) == 4 and parts[1] == "trigger" and parts[3] in ("on", "off"):
        key = _ALIASES.get(parts[2], parts[2])
        if key not in auto_toggle.SYSTEM_KEYS:
            print(f"[react] 不認得 trigger key：{parts[2]}")
            _usage()
            return
        enabled = parts[3] == "on"
        auto_toggle.set_enabled(base_dir, key, enabled)
        print(f"[react] {auto_toggle.SYSTEM_KEYS[key]}：{'開啟' if enabled else '關閉'}")
        return

    _usage()

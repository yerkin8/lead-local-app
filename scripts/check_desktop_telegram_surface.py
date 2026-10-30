from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "app" / "desktop.py"
UI_PATHS = [
    ROOT / "app" / "desktop_ui" / "index.html",
    ROOT / "app" / "desktop_ui" / "app.js",
    ROOT / "app" / "desktop_ui" / "flows.js",
    ROOT / "app" / "desktop_ui" / "stage9.js",
]
RETIRED_UI_MARKERS = (
    "telegram_state", "telegram_configure", "telegram_sign_in", "telegram_request_code",
    "telegram_check_phone", "campaign_start", "campaign_create", "contact_permissions",
    "telegram_policy_save", "send-policy-form",
    "script-for-business", "data-save-suppression", "do-not-contact", "first-run-form",
)
RETIRED_BRIDGE_METHODS = {
    "telegram_state", "telegram_configure", "telegram_sign_in", "telegram_request_code",
    "telegram_check_phone", "campaign_start", "campaign_create", "campaign_check",
    "campaign_permission", "telegram_policy_save",
    "template_render", "business_suppress",
}


def main() -> int:
    failures: list[str] = []
    ui = "\n".join(path.read_text(encoding="utf-8") for path in UI_PATHS)
    for marker in RETIRED_UI_MARKERS:
        if marker in ui:
            failures.append(f"retired UI marker remains: {marker}")

    if "data-show-telegram-links" not in ui:
        failures.append("manual Telegram phone-link action is missing")
    if "data-phone-open" not in ui or "data-phone-copy" not in ui or "data-copy-all-telegram-links" not in ui:
        failures.append("phone-link open/copy actions are incomplete")
    if "Если номер не открывается в Telegram, это может означать и скрытую видимость номера у владельца, а не отсутствие аккаунта" not in ui:
        failures.append("phone privacy explanation is missing")

    source = DESKTOP.read_text(encoding="utf-8")
    tree = ast.parse(source)
    bridge = next((node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Bridge"), None)
    methods = {node.name for node in bridge.body if isinstance(node, ast.FunctionDef)} if bridge else set()
    if not bridge:
        failures.append("desktop Bridge class is missing")
    for name in sorted(RETIRED_BRIDGE_METHODS & methods):
        failures.append(f"retired bridge method remains: {name}")
    if "telegram_sender" in source:
        failures.append("desktop entry point still references telegram_sender")
    if "TELEGRAM_MTPROTO_ENABLED = False" not in source:
        failures.append("MTProto default-off flag is missing")
    if 'data-view="scripts"' not in ui or not {"templates", "template_save"} <= methods:
        failures.append("local scripts editor is missing")

    phone_module = ast.parse((ROOT / "app" / "phone_links.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(phone_module):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    network_modules = {"httpx", "requests", "urllib", "socket", "telethon"}
    if imported & network_modules:
        failures.append(f"phone normalization imports network modules: {sorted(imported & network_modules)}")

    if failures:
        print("Desktop Telegram surface check: FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Desktop Telegram surface check: PASS")
    print(f"UI files checked: {len(UI_PATHS)}")
    print("MTProto default-off; authorization, sessions, campaigns, consent and send-limit UI absent")
    print("Phone links are generated locally; external opening is routed through the desktop bridge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

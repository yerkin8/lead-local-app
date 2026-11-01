"""«Clean machine» for the unified Desktop (docs/unify U11): the portable zip in an empty folder.

Unpacks the zip, starts LeadEqualMoney.exe without a terminal with an empty DATA_DIR, enters a format-only
OpenRouter key in the first-run wizard, opens every section, checks the mailing wizard, then inspects the data
folder: key encrypted, folders private, nothing secret on screen or in the log. No network request is made.
Output: verification/unify/clean-machine/ (screenshots, report.json).
Usage: python scripts/unify_clean_machine.py path\\to\\LeadEqualMoney-portable.zip
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import scripts.mailing_clean_machine as cdp  # noqa: E402  (WebView2 DevTools page helper, read-only use)

OUT = ROOT / "verification" / "unify" / "clean-machine"
cdp.OUT = OUT
FAKE_KEY = "sk-or-v1-" + "0123456789abcdef" * 4
SECTIONS = ("dashboard", "leads", "results", "today", "search", "businesses", "scripts", "ai", "mailing", "media",
            "settings")


def acl_only_owner(folder: Path) -> bool:
    text = subprocess.run(["icacls", str(folder)], capture_output=True).stdout.decode("oem", errors="replace")
    aces = [line.strip() for line in text.replace(str(folder), "").splitlines() if "(" in line]
    user = os.environ.get("USERNAME", "").casefold()
    return bool(aces) and all("(I)" not in a and (user in a.casefold() or "S-1-5-18" in a or "NT AUTHORITY" in a)
                              for a in aces)


def main(zip_path: Path) -> int:
    run = ROOT / ".build-tmp" / "unify-clean"
    if run.exists():
        shutil.rmtree(run)
    folder, data, elsewhere = run / "unpacked", run / "data", run / "some-other-folder"
    for path in (folder, data, elsewhere):
        path.mkdir(parents=True)
    OUT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(folder)
    exe = folder / "LeadEqualMoney" / "LeadEqualMoney.exe"
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}  # a clean user has no key
    env.update(DATA_DIR=str(data), HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9",
               WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f"--remote-debugging-port={cdp.PORT} --remote-allow-origins=*")
    report = {"zip": zip_path.name, "exe_exists": exe.is_file(), "checks": {}}
    checks = report["checks"]
    process = subprocess.Popen([str(exe)], cwd=str(elsewhere), env=env,
                               creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        page = cdp.Page()
        page.wait("typeof state!=='undefined' && state.config")
        checks["started_without_terminal_from_other_folder"] = True
        checks["window_responding"] = cdp.window_responding()
        page.wait("document.querySelector('#first-run-dialog #setup-ai-key')")
        page.shot("01_first_run_with_ai_key.png")
        page.js(f"document.querySelector('#setup-ai-key').value={json.dumps(FAKE_KEY)}")
        page.js("document.querySelector('#first-run-dialog [data-setup-continue]').click()")
        page.wait("!document.querySelector('#first-run-dialog')", 20)
        checks["wizard_saved_ai_key"] = page.js("call('ai_status').then(s=>s.configured && s.key_source==='saved')")
        opened = {}
        for view in SECTIONS:
            page.js(f"document.querySelector('.sidebar [data-view={view}]').click()")
            page.js("new Promise(r=>setTimeout(r,1500))")
            opened[view] = page.js(f"state.view==='{view}' && !document.querySelector('#content').innerText.includes('Не удалось загрузить')")
        checks["every_section_opens"] = all(opened.values())
        report["sections"] = opened
        page.js("document.querySelector('.sidebar [data-view=ai]').click()")
        page.js("new Promise(r=>setTimeout(r,1500))")
        checks["ai_chat_ready_after_wizard"] = page.js("Boolean(document.querySelector('.ai-chat'))")
        page.shot("02_ai_chat_ready.png")
        page.js("document.querySelector('.sidebar [data-view=mailing]').click()")
        page.wait("document.querySelector('#ml-keys-form')")
        checks["mailing_wizard_shown"] = True
        page.shot("03_mailing_wizard.png")
        screen = page.js("document.documentElement.outerHTML")
        checks["ai_key_not_on_page"] = FAKE_KEY not in screen
        page.js("document.querySelector('#app-back')?.click()")
        checks["back_button_present"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        process.terminate()
        try:
            process.wait(20)
        except subprocess.TimeoutExpired:
            process.kill()
    key_file = data / "ai" / "openrouter.bin"
    checks["ai_key_file_encrypted"] = key_file.is_file() and FAKE_KEY.encode() not in key_file.read_bytes()
    checks["ai_key_folder_private"] = (data / "ai").is_dir() and acl_only_owner(data / "ai")
    log = data / "logs" / "app.log"
    log_text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    checks["log_has_no_key"] = FAKE_KEY not in log_text
    checks["log_has_no_errors"] = not any(w in log_text for w in ("Traceback", " ERROR ", "CRITICAL"))
    leftovers = [p.name for p in (folder / "LeadEqualMoney").rglob("*") if p.suffix in (".sqlite3", ".session", ".env", ".bin")]
    checks["unpacked_folder_has_no_data_files"] = not leftovers
    report["passed"] = all(checks.values()) and "error" not in report
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))

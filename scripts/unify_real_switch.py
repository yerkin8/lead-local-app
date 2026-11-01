"""U13: one start of the installed build on the owner's real data (migration to v7), then a short UI check.

Opens every sidebar section in the window and closes it normally. Reads nothing secret; prints
only section checks. Usage: python scripts/unify_real_switch.py C:\\path\\to\\LeadEqualMoney.exe
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import scripts.mailing_clean_machine as cdp  # noqa: E402
from scripts.unify_e2e import close_app, wait_window  # noqa: E402

cdp.OUT = ROOT / "verification" / "unify" / "real-switch"


def main(exe: Path) -> int:
    cdp.OUT.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "DATA_DIR"}  # the real %APPDATA%\LeadEqualMoney
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"--remote-debugging-port={cdp.PORT} --remote-allow-origins=*"
    process = subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env,
                               creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    report = {"window": wait_window()}
    try:
        page = cdp.Page()
        page.wait("typeof state!=='undefined' && state.config", 90)
        report["responding"] = cdp.window_responding()
        report["businesses"] = page.js("call('businesses',{}).then(x=>x.length)")
        report["first_run_wizard_shown"] = page.js("Boolean(document.querySelector('#first-run-dialog'))")
        for view in ("leads", "today", "results", "search", "scripts", "ai", "media", "settings", "mailing"):
            page.js(f"document.querySelector('.sidebar [data-view={view}]').click()")
            page.js("new Promise(r=>setTimeout(r,2000))")
            report[f"opens_{view}"] = page.js(
                f"state.view==='{view}' && !document.querySelector('#content').innerText.includes('Не удалось загрузить')")
        page.shot("mailing.png")
        report["toast"] = page.js("document.querySelector('#toast')?.textContent || ''")
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        report["closed"] = close_app()
        try:
            process.wait(30)
        except subprocess.TimeoutExpired:
            process.kill()
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))

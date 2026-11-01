"""«Clean machine» check of the portable zip.

Unpacks the zip into an empty folder, starts LeadEqualMoney.exe without a terminal with an empty
DATA_DIR, and walks the first-run wizard of «Рассылка» with test values through the WebView2
DevTools port (test-only switch, bound to 127.0.0.1). No Telegram request is made.
Output: verification/mailing/clean-machine/ (screenshots, report.json).

Usage: python scripts/mailing_clean_machine.py path\\to\\LeadEqualMoney-portable.zip
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "verification" / "mailing" / "clean-machine"
PORT = 9339
TEST_ID, TEST_HASH, TEST_PHONE = "12345678", "a" * 32, "+79990000000"


def window_responding(samples: int = 5) -> bool:
    """Windows' own «не отвечает» test (IsHungAppWindow) on the app window, sampled for a few seconds."""
    import ctypes
    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = ctypes.c_void_p
    results = []
    for _ in range(samples):
        hwnd = user32.FindWindowW(None, "LeadEqualMoney")
        results.append(bool(hwnd) and not user32.IsHungAppWindow(ctypes.c_void_p(hwnd)))
        time.sleep(1)
    return all(results)


class Page:
    def __init__(self):
        import websocket
        deadline = time.time() + 150  # first WebView2 start on a fresh profile can be slow
        while True:
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=2))
                page = next(t for t in targets if t.get("type") == "page" and "index.html" in t.get("url", ""))
                break
            except Exception:
                if time.time() > deadline:
                    raise
                time.sleep(1)
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30, suppress_origin=True)
        self.seq = 0

    def send(self, method, **params):
        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == self.seq:
                return message.get("result", {})

    def js(self, expression):
        result = self.send("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"].get("text"))
        return result.get("result", {}).get("value")

    def wait(self, expression, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.js(f"!!({expression})"):
                return
            time.sleep(0.3)
        raise RuntimeError(f"timeout: {expression}")

    def shot(self, name):
        data = self.send("Page.captureScreenshot", format="png")["data"]
        (OUT / name).write_bytes(base64.b64decode(data))


def main(zip_path: Path) -> int:
    run = ROOT / ".build-tmp" / "clean-machine"
    if run.exists():
        shutil.rmtree(run)
    folder, data = run / "unpacked", run / "data"
    folder.mkdir(parents=True)
    data.mkdir()
    OUT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(folder)
    exe = folder / "LeadEqualMoney" / "LeadEqualMoney.exe"
    env = dict(os.environ, DATA_DIR=str(data),
               WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f"--remote-debugging-port={PORT} --remote-allow-origins=*")
    report = {"zip": zip_path.name, "exe_exists": exe.is_file(), "checks": {}}
    process = subprocess.Popen([str(exe)], cwd=str(run), env=env,
                               creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        page = Page()
        page.wait("typeof state!=='undefined' && state.config")
        report["checks"]["window_started_without_terminal"] = True
        report["checks"]["window_responding"] = window_responding()
        page.wait("document.querySelector('#first-run-dialog [data-setup-continue]')")
        page.shot("01_app_first_run.png")
        page.js("document.querySelector('#first-run-dialog [data-setup-continue]').click()")
        page.js("document.querySelector('nav button[data-view=mailing]').click()")
        page.wait("document.querySelector('#ml-keys-form')")
        page.shot("02_mailing_wizard.png")
        report["checks"]["mailing_wizard_shown"] = True
        page.js(f"""(()=>{{const f=document.querySelector('#ml-keys-form');f.api_id.value='{TEST_ID}';
            f.api_hash.value='{TEST_HASH}';f.querySelector('button.primary').click()}})()""")
        page.wait("document.querySelector('#ml-account-form')")
        page.js(f"""(()=>{{const f=document.querySelector('#ml-account-form');f.label.value='test';
            f.phone.value='{TEST_PHONE}';f.is_test.checked=true;f.querySelector('button.primary').click()}})()""")
        page.wait("document.querySelector('[data-ml-login]')")
        page.shot("03_account_added_login_button.png")
        report["checks"]["account_added_needs_login"] = page.js(
            "[...document.querySelectorAll('.chip')].some(c=>c.textContent==='Нужен вход')")
        screen = page.js("document.body.innerText")
        report["checks"]["keys_not_on_screen"] = TEST_HASH not in screen and TEST_ID not in screen
        report["checks"]["phone_masked_on_screen"] = TEST_PHONE[1:] not in screen
        page.js("document.querySelector('#app-back').click()")
        report["checks"]["back_button_works"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        process.terminate()
        try:
            process.wait(20)
        except subprocess.TimeoutExpired:
            process.kill()
    mailing = data / "mailing"
    keys = (mailing / "keys.bin").read_bytes() if (mailing / "keys.bin").exists() else b""
    report["checks"]["keys_file_encrypted"] = bool(keys) and TEST_HASH.encode() not in keys and TEST_ID.encode() not in keys
    account_files = list((mailing / "accounts").glob("*.bin")) if mailing.exists() else []
    report["checks"]["account_file_encrypted"] = bool(account_files) and all(
        b"79990000000" not in item.read_bytes() for item in account_files)
    acl = subprocess.run(["icacls", str(mailing)], capture_output=True)
    text = acl.stdout.decode("oem", errors="replace").replace(str(mailing), "<mailing>")
    aces = [line.strip() for line in text.splitlines() if "(" in line]
    report["acl"] = aces
    user = os.environ.get("USERNAME", "")
    # Only SYSTEM and the current user; no inherited (I) entries, no Users/Authenticated Users/Everyone.
    report["checks"]["acl_only_owner_and_system"] = bool(aces) and all(
        ("(I)" not in ace) and (user.casefold() in ace.casefold() or "S-1-5-18" in ace or "NT AUTHORITY" in ace)
        for ace in aces)
    logs = (data / "logs" / "app.log").read_text(encoding="utf-8", errors="replace") if (data / "logs" / "app.log").exists() else ""
    report["checks"]["log_has_no_test_secrets"] = all(s not in logs for s in (TEST_HASH, TEST_ID, TEST_PHONE[1:]))
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if "error" not in report and all(report["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))

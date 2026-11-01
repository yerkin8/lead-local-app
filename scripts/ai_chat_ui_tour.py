"""«ИИ-чат» redesign check in the real pywebview window (docs/unify/redesign/04-ai-chat.md).

Runs from sources with a throw-away DATA_DIR; the AI calls are stubbed in the page (no OpenRouter request, no key).
Saves screenshots light/dark × no key/empty/with messages/narrow into verification/redesign/04 and report.json.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = Path(tempfile.mkdtemp(prefix="lem-ai-tour-"))
os.environ["DATA_DIR"] = str(DATA)
(DATA / "first_run_complete").write_text("1.0.0", encoding="utf-8")

import webview  # noqa: E402
from app.desktop import Bridge, LocalWorker, resource_path  # noqa: E402

import ctypes  # noqa: E402
from ctypes import wintypes  # noqa: E402

OUT = ROOT / "verification/redesign/04"
OUT.mkdir(parents=True, exist_ok=True)
_user32 = ctypes.WinDLL("user32")
_user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.FindWindowW.restype = wintypes.HWND
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.PrintWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.UINT]


class tour_base:  # same PrintWindow capture as scripts/unify_ui_tour.py, without its DATA_DIR side effects
    @staticmethod
    def capture(name: str) -> None:
        hwnd = _user32.FindWindowW(None, "LeadEqualMoney")
        rect = wintypes.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(rect))
        import clr
        clr.AddReference("System.Drawing")
        from System.Drawing import Bitmap, Graphics
        from System.Drawing.Imaging import ImageFormat
        bitmap = Bitmap(rect.right - rect.left, rect.bottom - rect.top)
        graphics = Graphics.FromImage(bitmap)
        hdc = graphics.GetHdc()
        try:
            _user32.PrintWindow(hwnd, ctypes.c_void_p(hdc.ToInt64()), 2)
        finally:
            graphics.ReleaseHdc(hdc)
        bitmap.Save(str(OUT / name), ImageFormat.Png)
        graphics.Dispose()
        bitmap.Dispose()
REPORT: dict = {"data_dir": "temporary", "checks": {}}

STUB = r"""
window.__aiConfigured = %s;
window.__realCall = window.__realCall || call;
window.call = async function (method, ...args) {
  if (method === 'ai_status') return { configured: window.__aiConfigured, key_source: window.__aiConfigured ? 'saved' : null,
    categories: [{key:'general',label:'Общие'},{key:'first',label:'Первое сообщение'},{key:'objection',label:'Возражения'}] };
  if (method === 'ai_chat_start') { window.__lastStart = args; return { id: 'stub-1' }; }
  if (method === 'ai_chat_result') return { status: 'done', model: 'stub/free', seconds: 3,
    text: 'Здравствуйте! Посмотрел вашу карточку на Картах: отзывы отличные, а сайта нет.\n\nМогу за неделю сделать простой сайт с записью онлайн — показать пример?' };
  if (method === 'businesses') return [{id:'1', name:'Автосервис «Мотор»', address:'ул. Ленина, 5'}];
  return window.__realCall(method, ...args);
};
"""


def js(window, code):
    return window.evaluate_js(code)


def check(name, ok, detail=""):
    REPORT["checks"][name] = {"ok": bool(ok), "detail": detail}


def shot(window, name, theme):
    js(window, f"document.documentElement.dataset.theme={json.dumps(theme)}")
    time.sleep(0.6)
    tour_base.capture(f"{name}-{theme}.png")


def open_ai(window, configured):
    js(window, STUB % ("true" if configured else "false"))
    js(window, "setView('ai')")
    time.sleep(1.5)


def run(window):
    try:
        time.sleep(2)
        open_ai(window, False)
        check("no_key_prompt", js(window, "Boolean(document.querySelector('[data-ai-settings]'))"))
        for theme in ("light", "dark"):
            shot(window, "nokey", theme)

        open_ai(window, True)
        time.sleep(2.5)  # let the typewriter type something
        check("no_describe_plate", not js(window, "document.querySelector('#content').innerText.includes('Опишите, какое сообщение нужно')"))
        check("typewriter_runs", js(window, "document.querySelector('#ai-typer span').textContent.length > 0"))
        check("selectors_present", js(window, "Boolean(document.querySelector('#ai-category') && document.querySelector('[data-ai-lead-open]'))"))
        for theme in ("light", "dark"):
            shot(window, "empty", theme)

        js(window, "document.documentElement.dataset.theme='light'")
        js(window, "document.querySelector('[data-ai-lead-open]').click()")
        js(window, "const s=document.querySelector('#ai-lead-search');s.value='Мотор';s.dispatchEvent(new Event('input',{bubbles:true}))")
        time.sleep(1)
        tour_base.capture("lead-picker-light.png")
        js(window, "document.querySelector('.ai-lead-option').click()")
        check("lead_picked", js(window, "document.querySelector('.ai-chip.is-set')?.innerText.includes('Мотор')"))

        js(window, "const i=document.querySelector('#ai-input');i.focus();i.value='Первое сообщение автосервису без сайта';"
                   "i.dispatchEvent(new Event('input',{bubbles:true}));"
                   "i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',shiftKey:true,bubbles:true}))")
        check("shift_enter_does_not_send", js(window, "!window.__lastStart"))
        js(window, "document.querySelector('#ai-input').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))")
        time.sleep(0.4)
        check("typing_indicator", js(window, "Boolean(document.querySelector('.ai-typing'))"))
        tour_base.capture("typing-light.png")
        time.sleep(3)
        check("reply_shown", js(window, "document.querySelectorAll('.ai-bubble.bot').length === 1"))
        check("lead_sent", js(window, "window.__lastStart && window.__lastStart[2] === '1'"))
        for theme in ("light", "dark"):
            shot(window, "messages", theme)

        js(window, "window.call = async (m, ...a) => m === 'ai_chat_start' ? (()=>{throw Error('Лимит бесплатных запросов на сегодня исчерпан')})() : window.__realCall(m, ...a)")
        js(window, "const i=document.querySelector('#ai-input');i.value='Сделай короче';i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))")
        time.sleep(1)
        check("error_in_chat", js(window, "Boolean(document.querySelector('.ai-bubble.error'))"))
        shot(window, "error", "dark")

        window.resize(560, 900)
        time.sleep(1)
        check("no_horizontal_scroll_narrow", js(window, "document.querySelector('.main').scrollWidth - document.querySelector('.main').clientWidth") <= 1)
        shot(window, "narrow", "light")
        shot(window, "narrow", "dark")
    except Exception as error:  # noqa: BLE001
        REPORT["error"] = repr(error)
    finally:
        (OUT / "report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
        window.destroy()


def main():
    worker = LocalWorker()
    window = webview.create_window("LeadEqualMoney", resource_path("app/desktop_ui/index.html").resolve().as_uri(), js_api=Bridge(),
                                   width=1280, height=860)
    threading.Thread(target=lambda: (time.sleep(3), run(window)), daemon=True).start()
    webview.start(worker.start, gui="edgechromium", private_mode=True)


if __name__ == "__main__":
    main()

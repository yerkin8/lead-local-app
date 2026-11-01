"""Exercise the desktop WebView against only synthetic .build-tmp/ui-tour-data."""
from __future__ import annotations
import os, sys, time, threading, ctypes, json
from ctypes import wintypes
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
DATA=(ROOT/'.build-tmp/ui-tour-data').resolve()
os.environ['DATA_DIR']=str(DATA)
if not (DATA/'first_run_complete').exists():
    raise SystemExit('Run scripts/seed_demo_data.py first')
import webview
from app.desktop import APP_NAME, Bridge, LocalWorker
OUT=ROOT/'verification/ui_tour'; OUT.mkdir(parents=True,exist_ok=True)
LOG=[]
user32=ctypes.windll.user32
user32.FindWindowW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR]
user32.FindWindowW.restype=wintypes.HWND
class RECT(ctypes.Structure):
    _fields_=[('left',ctypes.c_long),('top',ctypes.c_long),('right',ctypes.c_long),('bottom',ctypes.c_long)]

def capture(name):
    hwnd=user32.FindWindowW(None,APP_NAME)
    if not hwnd: raise RuntimeError('Window handle unavailable')
    rect=RECT()
    if not user32.GetWindowRect(hwnd,ctypes.byref(rect)): raise RuntimeError('GetWindowRect failed')
    path=OUT/name
    width,height=rect.right-rect.left,rect.bottom-rect.top
    import clr
    clr.AddReference('System.Drawing')
    from System.Drawing import Bitmap, Graphics
    from System.Drawing.Imaging import ImageFormat
    bitmap=Bitmap(width,height)
    graphics=Graphics.FromImage(bitmap); hdc=graphics.GetHdc()
    try:
        if not user32.PrintWindow(hwnd,hdc,2):
            raise RuntimeError('PrintWindow failed')
        graphics.ReleaseHdc(hdc); hdc=None
        bitmap.Save(str(path),ImageFormat.Png)
    finally:
        if hdc: graphics.ReleaseHdc(hdc)
        graphics.Dispose(); bitmap.Dispose()
    LOG.append(f'{name}: {(width,height)}, hwnd={hwnd}')

def tour(window):
    try:
        time.sleep(5)
        LOG.append('initial '+str(window.evaluate_js('document.title + "; " + document.readyState')))
        window.evaluate_js('window.__qaErrors=[];window.addEventListener("error",e=>window.__qaErrors.push(e.message));const old=console.error;console.error=(...x)=>{window.__qaErrors.push(x.join(" "));old(...x)}')
        capture('09_01_panel.png')
        for name,selector in [('09_02_search.png','[data-view="search"]'),('09_03_scripts.png','[data-view="scripts"]'),('09_04_base.png','[data-view="businesses"]'),('09_05_media.png','[data-view="media"]'),('09_06_settings.png','[data-view="settings"]')]:
            found=window.evaluate_js(f'Boolean(document.querySelector({json.dumps(selector)}))')
            LOG.append(f'{selector}: {found}')
            if not found: continue
            window.evaluate_js(f'document.querySelector({json.dumps(selector)}).click()')
            time.sleep(2)
            capture(name)
            if name=='09_04_base.png':
                LOG.append('folders '+str(window.evaluate_js('document.querySelectorAll(".glass-folder-card").length')))
                window.evaluate_js('document.querySelector(".glass-folder-card")?.setAttribute("data-hover","true")')
                time.sleep(.5); capture('09_04a_base_hover.png')
                window.evaluate_js('document.querySelector("[data-folder-toggle]")?.click()')
                time.sleep(.6); capture('09_04b_base_open.png')
                window.evaluate_js('document.querySelector("[data-folder-menu-key]")?.click()')
                time.sleep(.3); capture('09_04c_base_menu.png')
                window.evaluate_js('document.querySelector("[data-folder-menu-key]")?.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}))')
                window.evaluate_js('document.querySelector(".glass-folder-open")?.click()')
                time.sleep(1.5); capture('09_04d_cities.png')
                window.evaluate_js('document.querySelector(".glass-folder-open")?.click()')
                time.sleep(1.2); capture('09_04e_runs.png')
                window.evaluate_js('document.querySelector("[data-base-open-run]")?.click()')
                time.sleep(1.2); capture('09_04f_organizations.png')
                window.evaluate_js('document.querySelector("[data-business-card]")?.click()')
                time.sleep(1.2); capture('09_04g_card.png')
                window.evaluate_js('document.querySelector("[data-open-telegram]")?.click()')
                time.sleep(.7); capture('09_04h_telegram.png')
                for step in range(5):
                    window.evaluate_js('document.querySelector("#app-back")?.click()')
                    time.sleep(.6); capture(f'09_04_back_{step+1}.png')
        window.evaluate_js('document.querySelector("[data-view=businesses]")?.click()');time.sleep(.5)
        window.resize(1024,700); time.sleep(1); capture('09_07_small.png')
        LOG.append('small_overflow '+str(window.evaluate_js('document.documentElement.scrollWidth-document.documentElement.clientWidth')))
        window.resize(1920,1080); time.sleep(1); capture('09_08_large.png')
        LOG.append('js_errors '+str(window.evaluate_js('JSON.stringify(window.__qaErrors)')))
    except Exception as exc:
        LOG.append(type(exc).__name__+': '+str(exc))
    finally:
        (OUT/'tour.log').write_text('\n'.join(LOG),encoding='utf-8')
        window.destroy()

worker=LocalWorker(); bridge=Bridge()
window=webview.create_window(APP_NAME,(ROOT/'app/desktop_ui/index.html').resolve().as_uri(),js_api=bridge,width=1366,height=768,min_size=(1024,700))
bridge._window=window

def start():
    worker.start()
    threading.Thread(target=tour,args=(window,),daemon=True).start()
webview.start(start,gui='edgechromium',private_mode=True,storage_path=str(DATA/'webview-tour'))
worker.stop.set(); worker.shutdown(timeout=10)


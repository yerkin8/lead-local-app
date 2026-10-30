from pathlib import Path
import os,subprocess,time,ctypes,sys
from ctypes import wintypes
root=Path(__file__).resolve().parents[1]
exe=root/'.build-tmp/stage8-dist/LeadEqualMoney/LeadEqualMoney.exe'
data=root/'.build-tmp/stage8-exe-data'
if data.exists(): raise SystemExit('Test profile exists; refusing to reuse')
data.mkdir(parents=True)
env=os.environ.copy();env['DATA_DIR']=str(data);env['HTTP_PROXY']=env['HTTPS_PROXY']='http://127.0.0.1:9'
p=subprocess.Popen([str(exe)],env=env)
user32=ctypes.windll.user32
user32.FindWindowW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR]
user32.FindWindowW.restype=wintypes.HWND
try:
    hwnd=0
    for _ in range(50):
        if p.poll() is not None: raise RuntimeError(f'EXE exit {p.returncode}')
        hwnd=user32.FindWindowW(None,'LeadEqualMoney')
        if hwnd: break
        time.sleep(.5)
    if not hwnd: raise RuntimeError('Window did not open')
    time.sleep(3)
    import clr
    clr.AddReference('System.Drawing')
    from System.Drawing import Bitmap,Graphics,Size
    from System.Drawing.Imaging import ImageFormat
    class RECT(ctypes.Structure):
        _fields_=[('left',ctypes.c_long),('top',ctypes.c_long),('right',ctypes.c_long),('bottom',ctypes.c_long)]
    rect=RECT();user32.GetWindowRect(hwnd,ctypes.byref(rect))
    w,h=rect.right-rect.left,rect.bottom-rect.top
    bitmap=Bitmap(w,h);g=Graphics.FromImage(bitmap)
    g.CopyFromScreen(rect.left,rect.top,0,0,Size(w,h))
    out=root/'verification/ui_tour/09_stage8_exe.png';bitmap.Save(str(out),ImageFormat.Png)
    g.Dispose();bitmap.Dispose()
    print(f'EXE_WINDOW {w}x{h} screenshot={out}')
    user32.PostMessageW(hwnd,0x0010,0,0)
    p.wait(timeout=20)
    print('EXIT',p.returncode)
finally:
    if p.poll() is None:
        user32.PostMessageW(hwnd,0x0010,0,0)
        try:p.wait(timeout=10)
        except subprocess.TimeoutExpired:p.terminate()

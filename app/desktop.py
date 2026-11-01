"""Windows desktop entry point. No HTTP server is started."""

from __future__ import annotations

import os
import msvcrt
import json
import webbrowser
import shutil
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

APP_NAME = "LeadEqualMoney"
APP_VERSION = "1.0.0"
DATA_MIGRATION_NOTICE = ""
TELEGRAM_MTPROTO_ENABLED = False


def resource_path(relative_path: str) -> Path:
    """Resolve bundled files independently of the process working directory."""
    relative = Path(relative_path)
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            roots.append(Path(bundle_root))
        executable_dir = Path(sys.executable).resolve().parent
        roots.extend((executable_dir / "_internal", executable_dir))
    else:
        roots.append(Path(__file__).resolve().parent.parent)

    candidates = [root / relative for root in roots]
    return next((path for path in candidates if path.is_file()), candidates[0])


def _monitor_work_areas() -> list[tuple[int, int, int, int, bool]]:
    """Return Windows monitor work areas in the logical pixels used by pywebview."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    class Rect(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", Rect),
                    ("rcWork", Rect), ("dwFlags", wintypes.DWORD)]

    areas: list[tuple[int, int, int, int, bool]] = []
    user32 = ctypes.windll.user32
    try:
        dpi = user32.GetDpiForSystem()
        scale = float(dpi) / 96 if dpi else 1.0
    except (AttributeError, OSError):
        scale = 1.0

    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                       ctypes.POINTER(Rect), wintypes.LPARAM)
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(Rect),
                                           callback_type, wintypes.LPARAM]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL

    def collect(monitor, _dc, _rect, _data):
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            work = info.rcWork
            areas.append((round(work.left / scale), round(work.top / scale),
                          round(work.right / scale), round(work.bottom / scale),
                          bool(info.dwFlags & 1)))
        return 1

    callback = callback_type(collect)
    user32.EnumDisplayMonitors(None, None, callback, 0)
    return sorted(areas, key=lambda area: not area[4])


def _initial_window_geometry(path: Path) -> dict[str, int | None]:
    """Restore a visible window size and position, or use the primary monitor."""
    monitors = _monitor_work_areas()
    primary = next((item for item in monitors if item[4]), monitors[0] if monitors else None)
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            saved = {}
    except (OSError, ValueError, TypeError):
        saved = {}

    def dimension(key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(saved.get(key, default))
        except (ValueError, TypeError, OverflowError):
            value = default
        return max(minimum, min(maximum, value))

    width = dimension("width", 1366, 1024, 4096)
    height = dimension("height", 850, 700, 2160)
    x = saved.get("x")
    y = saved.get("y")
    if isinstance(x, bool) or not isinstance(x, int):
        x = None
    if isinstance(y, bool) or not isinstance(y, int):
        y = None

    if primary:
        left, top, right, bottom, _ = primary
        width = min(width, max(1024, right - left))
        height = min(height, max(700, bottom - top))
    else:
        width, height = min(width, 1366), min(height, 850)

    if x is not None and y is not None:
        for left, top, right, bottom, _ in monitors:
            overlap_width = max(0, min(x + width, right) - max(x, left))
            overlap_height = max(0, min(y + height, bottom) - max(y, top))
            if overlap_width >= min(200, width) and overlap_height >= 80:
                width = min(width, max(1024, right - left))
                height = min(height, max(700, bottom - top))
                x = min(max(x, left), max(left, right - width))
                y = min(max(y, top), max(top, bottom - height))
                return {"x": x, "y": y, "width": width, "height": height}

    return {"x": None, "y": None, "width": width, "height": height}


class _WindowGeometryStore:
    """Debounced, atomic window geometry persistence in the user's data directory."""

    def __init__(self, path: Path, initial: dict[str, int | None]):
        self.path = path
        self._state = dict(initial)
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None

    def update(self, **values: int) -> None:
        with self._lock:
            self._state.update(values)
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(0.6, self.flush)
            self._timer.daemon = True
            self._timer.start()

    def flush(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            temporary = self.path.with_suffix(".tmp")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps(self._state, ensure_ascii=False), encoding="utf-8")
                temporary.replace(self.path)
            except OSError:
                logging.getLogger("LeadEqualMoney").exception("Не удалось сохранить размер и положение окна")


def _is_local_app_navigation(url: str, page_path: Path) -> bool:
    """Allow WebView2 to stay on the bundled desktop page only."""
    parsed = urlparse(str(url))
    if parsed.scheme.casefold() != "file" or parsed.netloc not in {"", "localhost"}:
        return False
    try:
        target = Path(url2pathname(parsed.path)).resolve(strict=True)
        return os.path.normcase(str(target)) == os.path.normcase(str(page_path.resolve(strict=True)))
    except (OSError, ValueError):
        return False


def _message_box(message: str, title: str = APP_NAME) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, str(message), title, 0x10 | 0x0)
    except Exception:
        pass


def _check_legacy_lock(folder: Path) -> None:
    lock_path = folder / "desktop.lock"
    if not lock_path.exists():
        return
    with lock_path.open("a+b") as handle:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("Сначала закройте уже запущенную версию приложения и повторите запуск") from exc
        else:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _configure_user_data_dir() -> None:
    global DATA_MIGRATION_NOTICE
    if "DATA_DIR" in os.environ:
        return
    roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    target = roaming / APP_NAME
    old_paths = [path for path in (local / APP_NAME, local / "LeadGenLocal")
                 if path.is_dir() and path.resolve() != target.resolve()]
    for old_path in old_paths:
        try:
            _check_legacy_lock(old_path)
        except RuntimeError as exc:
            _message_box(f"Закройте приложение в старом каталоге данных и запустите его снова.\n\n{old_path}")
            raise SystemExit(1) from exc
    if not target.exists() and old_paths:
        source = old_paths[0]
        staging = roaming / f"{APP_NAME}.migrating-{os.getpid()}"
        try:
            roaming.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, staging, symlinks=True,
                            ignore=shutil.ignore_patterns("desktop.lock", "webview"))
            staging.rename(target)
            DATA_MIGRATION_NOTICE = f"Данные перенесены в {target}. Старая копия сохранена в {source}."
        except Exception as exc:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            _message_box(f"Не удалось безопасно перенести данные из {source}. Старые файлы сохранены.\n\n{exc}")
            raise SystemExit(1) from exc
    elif target.exists() and old_paths:
        DATA_MIGRATION_NOTICE = f"Старая копия данных оставлена без изменений: {old_paths[0]}"
    os.environ["DATA_DIR"] = str(target)


class AlreadyRunningError(RuntimeError):
    pass


_configure_user_data_dir()

# Configure the user profile before importing modules whose paths are fixed at import time.
from . import db, media_service, services
from .config import DATA_DIR
from .desktop_services import (LocalWorker, backup_database, export_xlsx, import_xlsx, import_sqlite,
                               replace_database)
from .mailing.desktop_bridge import MailingBridgeMixin


def _configure_logging() -> None:
    (DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(DATA_DIR / "logs" / "app.log", maxBytes=3_000_000,
                                  backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[handler], force=True)
    logging.captureWarnings(True)
    logger = logging.getLogger("LeadEqualMoney")

    def on_error(exc_type, error, traceback):
        logger.critical("Необработанная ошибка приложения", exc_info=(exc_type, error, traceback))
        _message_box(f"Приложение завершилось с ошибкой. Подробности записаны в файл:\n{DATA_DIR / 'logs' / 'app.log'}")

    sys.excepthook = on_error
    threading.excepthook = lambda args: on_error(args.exc_type, args.exc_value, args.exc_traceback)


class Bridge(MailingBridgeMixin):
    """Explicit operations only; the browser has no filesystem or secret-reading API.

    Telegram mailing methods (mailing_*) come from MailingBridgeMixin; secrets never reach the page."""

    def __init__(self):
        self._window = None

    def _dialog(self, kind, **kwargs):
        import webview
        chosen = self._window.create_file_dialog(getattr(webview.FileDialog, kind), **kwargs)
        return chosen[0] if isinstance(chosen, (list, tuple)) and chosen else chosen

    def _result(self, func, *args, **kwargs):
        try:
            return {"ok": True, "data": func(*args, **kwargs)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def bootstrap(self):
        return self._result(lambda: {**services.bootstrap(), "data_dir": str(DATA_DIR),
                                     "telegram_mtproto_enabled": TELEGRAM_MTPROTO_ENABLED,
                                     "mailing_available": self._mailing is not None,
                                     "first_run": not (DATA_DIR / "first_run_complete").exists(),
                                     "version": APP_VERSION,
                                     "migration_notice": DATA_MIGRATION_NOTICE})

    def complete_setup(self):
        def run():
            (DATA_DIR / "first_run_complete").write_text(APP_VERSION, encoding="utf-8")
            return True
        return self._result(run)

    def open_logs(self):
        def run():
            folder = DATA_DIR / "logs"
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))
            return True
        return self._result(run)

    def open_data_dir(self):
        def run():
            os.startfile(str(DATA_DIR))
            return True
        return self._result(run)

    def media_open(self, job_id, name=""):
        return self._result(media_service.open_archive, job_id, name)

    def media_save(self, job_id, name=""):
        def run():
            target = (self._dialog("SAVE", save_filename=name,
                                   file_types=("ZIP (*.zip)",)) if name else self._dialog("FOLDER"))
            return media_service.copy_archive(job_id, name, target) if target else None
        return self._result(run)

    def import_xlsx(self):
        def run():
            path = self._dialog("OPEN", file_types=("Excel (*.xlsx)",))
            return import_xlsx(path) if path else None
        return self._result(run)

    def export_xlsx(self):
        def run():
            path = self._dialog("SAVE", save_filename="lem-export.xlsx",
                                file_types=("Excel (*.xlsx)",))
            return export_xlsx(path) if path else None
        return self._result(run)

    def export_scope(self, scope):
        def run():
            path = self._dialog("SAVE", save_filename="lem-export.xlsx",
                                file_types=("Excel (*.xlsx)",))
            return export_xlsx(path, dict(scope or {})) if path else None
        return self._result(run)

    def backup(self):
        return self._result(backup_database)

    def import_sqlite(self):
        def run():
            path = self._dialog("OPEN", file_types=("SQLite (*.sqlite3;*.sqlite;*.db)",))
            return import_sqlite(path) if path else None
        return self._result(run)

    def replace_database(self):
        """Make another app base the working one (backup first); the base is chosen in a dialog."""
        def run():
            path = self._dialog("OPEN", file_types=("SQLite (*.sqlite3;*.sqlite;*.db)",))
            return replace_database(path) if path else None
        return self._result(run)

    def open_external(self, url):
        def run():
            if not isinstance(url, str) or len(url) > 2048:
                raise ValueError("Ссылка пустая или слишком длинная")
            parsed = urlparse(url.strip())
            if (parsed.scheme.casefold() not in {"https", "http"} or not parsed.hostname
                    or parsed.username or parsed.password):
                raise ValueError("Можно открыть только обычную веб-ссылку")
            webbrowser.open(parsed.geturl(), new=2)
            return True
        return self._result(run)



def _shared_method(name):
    def method(self, *args):
        return self._result(services.SERVICES[name], *args)
    method.__name__ = name
    return method


for _name in services.SERVICES:
    if not hasattr(Bridge, _name):
        setattr(Bridge, _name, _shared_method(_name))


@contextmanager
def single_instance():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handle = (DATA_DIR / "desktop.lock").open("a+b")
    try:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise AlreadyRunningError("LeadEqualMoney уже запущен") from exc
        yield
    finally:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        handle.close()


def main():
    _configure_logging()
    logger = logging.getLogger("LeadEqualMoney")
    logger.info("Starting %s version %s", APP_NAME, APP_VERSION)
    try:
        # Load the CLR on the main thread before pywebview selects its backend.
        # This also avoids pywebview's retry path retaining a half-initialized runtime.
        import clr  # noqa: F401
    except Exception:
        logger.exception("Python.NET could not initialize the Windows UI runtime")
        _message_box(f"Не удалось запустить оконный компонент .NET. Подробности записаны в файл:\n{DATA_DIR / 'logs' / 'app.log'}")
        return
    try:
        import webview
    except ImportError as exc:
        logger.exception("pywebview is unavailable")
        _message_box(f"Не удалось запустить интерфейс.\n\nПодробности: {DATA_DIR / 'logs' / 'app.log'}")
        return
    geometry_store = None
    try:
        with single_instance():
            db.init_desktop_db()
            worker = LocalWorker()
            bridge = Bridge()
            try:
                from .mailing.desktop_bridge import create_service
                bridge._mailing = create_service(DATA_DIR)
                bridge._mailing.start()
            except Exception:
                bridge._mailing = None
                logger.exception("Модуль рассылки не запустился; остальное приложение работает")
            try:
                page_path = resource_path("app/desktop_ui/index.html")
                icon_path = resource_path("app/assets/lem-icon.ico")
                if not page_path.is_file():
                    raise FileNotFoundError(f"Не найден интерфейс приложения: {page_path}")
                if os.name == "nt":
                    try:
                        import ctypes
                        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                            "LeadEqualMoney.Desktop")
                    except (AttributeError, OSError):
                        logger.debug("Не удалось задать идентификатор приложения панели задач", exc_info=True)

                geometry = _initial_window_geometry(DATA_DIR / "window.json")
                geometry_store = _WindowGeometryStore(DATA_DIR / "window.json", geometry)
                window_options = {key: geometry[key] for key in ("x", "y")}
                # The saved theme goes into the page address so it applies before the first paint.
                theme = services.ui_settings()["theme"]
                window = webview.create_window(
                    APP_NAME,
                    page_path.resolve().as_uri() + "#theme=" + theme,
                    js_api=bridge,
                    width=int(geometry["width"] or 1366),
                    height=int(geometry["height"] or 850),
                    min_size=(1024, 700),
                    background_color="#0B1220" if theme == "dark" else "#F8FAFC",
                    **window_options,
                )
                bridge._window = window
                profile = DATA_DIR / "webview"
                profile.mkdir(parents=True, exist_ok=True)
                maximized = False
                icon_references = []

                def apply_window_icon(*_args):
                    if not icon_path.is_file():
                        logger.warning("Файл значка окна не найден: %s", icon_path)
                        return
                    # The "shown" handler runs in a worker thread. Setting Form.Icon from here is a
                    # synchronous cross-thread SendMessage made while holding the GIL, and the UI
                    # thread may be waiting for that GIL in on_shown -> deadlock ("не отвечает").
                    # BeginInvoke queues the work on the UI thread and returns at once.
                    def set_icon():
                        try:
                            from System.Drawing import Icon
                            icon = Icon(str(icon_path))
                            window.native.Icon = icon
                            icon_references.append(icon)
                        except Exception:
                            logger.exception("Не удалось установить значок заголовка окна")
                    try:
                        from System import Action
                        window.native.BeginInvoke(Action(set_icon))
                    except Exception:
                        logger.exception("Не удалось установить значок заголовка окна")

                def attach_navigation_guard(*_args):
                    browser = getattr(getattr(window.native, "browser", None), "webview", None)
                    if browser is None:
                        logger.error("Не удалось установить защиту переходов WebView2")
                        return

                    def block_external_navigation(_sender, args):
                        target = str(args.Uri)
                        if _is_local_app_navigation(target, page_path):
                            return
                        args.Cancel = True
                        parsed = urlparse(target)
                        logger.info("Blocked in-window navigation: scheme=%s host=%s",
                                    parsed.scheme, parsed.hostname or "")

                    browser.NavigationStarting += block_external_navigation

                def save_position(x, y):
                    if geometry_store and not maximized:
                        geometry_store.update(x=int(x), y=int(y))

                def save_size(width, height):
                    if geometry_store and not maximized:
                        geometry_store.update(width=max(1024, int(width)),
                                              height=max(700, int(height)))

                def on_maximized(*_args):
                    nonlocal maximized
                    maximized = True

                def on_restored(*_args):
                    nonlocal maximized
                    maximized = False

                window.events.shown += apply_window_icon
                window.events.before_show += attach_navigation_guard
                window.events.moved += save_position
                window.events.resized += save_size
                window.events.maximized += on_maximized
                window.events.restored += on_restored
                window.events.closed += lambda *_args: geometry_store.flush() if geometry_store else None

                def on_closing(*_args):
                    worker.stop.set()

                window.events.closing += on_closing
                # pywebview initializes the .NET/WebView2 backend before it starts
                # this callback. Start Selenium imports and the worker thread only
                # after that initialization to avoid loading Python.NET concurrently.
                webview.start(worker.start, gui="edgechromium", private_mode=False,
                              storage_path=str(profile))
            except Exception:
                logger.exception("Desktop window failed to start or stopped unexpectedly")
                _message_box("Не удалось открыть окно приложения. Проверьте системный Microsoft Edge WebView2 Runtime.\n\n"
                             "Если он отсутствует, установите его с https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
                             f"Подробности: {DATA_DIR / 'logs' / 'app.log'}")
            finally:
                if geometry_store:
                    geometry_store.flush()
                if bridge._mailing is not None:
                    logger.info("Mailing shutdown: %s", bridge._mailing.shutdown(15))
                worker_stopped = worker.shutdown(timeout=50)
                logger.info("Shutdown complete: worker=%s", worker_stopped)
                if not worker_stopped:
                    logger.error("A background thread did not stop before timeout")
    except AlreadyRunningError as exc:
        logger.info("Second launch rejected: %s", exc)
        _message_box(str(exc))
    except Exception:
        logger.exception("Application startup failed")
        _message_box(f"Не удалось запустить приложение.\n\nПодробности: {DATA_DIR / 'logs' / 'app.log'}")


if __name__ == "__main__":
    main()

"""Windows desktop entry point. No HTTP server is started."""

from __future__ import annotations

import os
import msvcrt
import webbrowser
import shutil
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

APP_NAME = "LeadEqualMoney"
APP_VERSION = "1.0.0"
DATA_MIGRATION_NOTICE = ""


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
from . import db, media_service, telegram_sender
from .config import DATA_DIR
from .desktop_services import LocalWorker, backup_database, export_xlsx, import_xlsx, import_sqlite, refresh_business, search_results, validate_search
from .domain import COUNTRIES, SITE_TYPES, STATUSES, render_template


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


class Bridge:
    """Explicit operations only; the browser has no filesystem or secret-reading API."""

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
        return self._result(lambda: {"countries": COUNTRIES, "statuses": STATUSES,
                                     "site_types": sorted(SITE_TYPES), "data_dir": str(DATA_DIR),
                                     "stats": db.stats(), "telegram": telegram_sender.state(),
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

    def stats(self):
        return self._result(db.stats)

    def search_start(self, payload):
        return self._result(lambda: db.create_job("search", validate_search(payload)))

    def search_jobs(self):
        return self._result(lambda: db.list_jobs("search"))

    def search_results(self, job_id):
        return self._result(search_results, job_id)

    def businesses(self, filters=None):
        def run():
            options = dict(filters or {})
            limit = min(200, max(1, int(options.pop("limit", 50))))
            offset = max(0, int(options.pop("offset", 0)))
            return db.list_businesses(options, limit=limit, offset=offset)
        return self._result(run)

    def business_filter_options(self):
        return self._result(db.filter_options)


    def business(self, identity):
        return self._result(db.business, identity)

    def business_save(self, identity, status=None, note=None):
        return self._result(lambda: db.business(identity) if db.set_business(identity, status, note) else None)

    def business_suppress(self, identity, blocked, reason=""):
        return self._result(lambda: db.business(identity) if db.set_suppression(identity, bool(blocked), reason) else None)

    def business_refresh(self, identity):
        return self._result(refresh_business, identity)

    def media_start(self, kind, payload):
        return self._result(media_service.start, kind, **payload)

    def media_jobs(self, business_id=None):
        return self._result(media_service.list_jobs, business_id)

    def activity_snapshot(self, known=None):
        return self._result(db.activity_snapshot, known)

    def media_open(self, job_id, name=""):
        return self._result(media_service.open_archive, job_id, name)

    def media_save(self, job_id, name=""):
        def run():
            target = (self._dialog("SAVE", save_filename=name,
                                   file_types=("ZIP (*.zip)",)) if name else self._dialog("FOLDER"))
            return media_service.copy_archive(job_id, name, target) if target else None
        return self._result(run)

    def media_delete(self, job_id):
        return self._result(media_service.delete, job_id)

    def templates(self):
        return self._result(db.templates)

    def template_save(self, slug, body):
        return self._result(db.save_template, slug, body)

    def template_render(self, slug, business_id):
        def render():
            item = db.business(business_id)
            template = next((row for row in db.templates() if row["slug"] == slug), None)
            if not item or not template:
                raise ValueError("Организация или скрипт не найдены")
            return render_template(template["body"], item)
        return self._result(render)

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

    def backup(self):
        return self._result(backup_database)

    def import_sqlite(self):
        def run():
            path = self._dialog("OPEN", file_types=("SQLite (*.sqlite3;*.sqlite;*.db)",))
            return import_sqlite(path) if path else None
        return self._result(run)

    def telegram_state(self):
        return self._result(telegram_sender.state)

    def telegram_policy_save(self, daily_limit, delay_seconds):
        return self._result(telegram_sender.configure_send_policy, daily_limit, delay_seconds)

    def contact_permissions(self, status="blocked"):
        return self._result(db.list_contact_permissions, status)

    def telegram_configure(self, api_id, api_hash, phone):
        return self._result(telegram_sender.configure, api_id, api_hash, phone)

    def telegram_request_code(self):
        return self._result(telegram_sender.request_code)

    def telegram_sign_in(self, code, password=""):
        return self._result(telegram_sender.sign_in, code, password)

    def telegram_check_phone(self, business_id, phone):
        return self._result(telegram_sender.check_phone, business_id, phone)

    def campaign_create(self, title, business_ids, template_slug, overrides=None):
        return self._result(telegram_sender.create_draft, title, business_ids, template_slug, overrides)

    def campaign_list(self):
        return self._result(telegram_sender.campaigns)

    def campaign_get(self, campaign_id):
        return self._result(telegram_sender.campaign, campaign_id)

    def campaign_update_recipient(self, campaign_id, business_id, message):
        return self._result(telegram_sender.update_recipient, campaign_id, business_id, message)

    def campaign_start(self, campaign_id):
        return self._result(telegram_sender.start, campaign_id)

    def campaign_check(self, campaign_id):
        return self._result(telegram_sender.check_draft, campaign_id)

    def campaign_stop(self, campaign_id):
        return self._result(telegram_sender.stop, campaign_id)

    def campaign_mark_manual_sent(self, campaign_id, business_id):
        return self._result(telegram_sender.mark_manual_sent, campaign_id, business_id)

    def campaign_permission(self, campaign_id, business_id, status, source="", note=""):
        return self._result(telegram_sender.set_contact_permission, campaign_id, business_id,
                            status, source, note)

    def campaign_manual_prepare(self, campaign_id, business_id, message):
        return self._result(telegram_sender.manual_prepare, campaign_id, business_id, message)

    def campaign_manual_not_sent(self, campaign_id, business_id):
        return self._result(telegram_sender.mark_manual_not_sent, campaign_id, business_id)

    def open_external(self, url):
        def run():
            parsed = urlparse(url)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                raise ValueError("Можно открыть только обычную веб-ссылку")
            webbrowser.open(url)
            return True
        return self._result(run)


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
    try:
        with single_instance():
            db.init_db()
            worker = LocalWorker()
            try:
                bridge = Bridge()
                page = (Path(__file__).parent / "desktop_ui" / "index.html").resolve().as_uri()
                window = webview.create_window(f"{APP_NAME} · LEM", page, js_api=bridge, width=1440, height=900,
                                               min_size=(1050, 700), background_color="#101c2a")
                bridge._window = window
                profile = DATA_DIR / "webview"
                profile.mkdir(parents=True, exist_ok=True)

                def on_closing(*_args):
                    worker.stop.set()
                    telegram_sender.request_shutdown()

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
                telegram_stopped = telegram_sender.shutdown(timeout=40)
                worker_stopped = worker.shutdown(timeout=50)
                logger.info("Shutdown complete: telegram=%s worker=%s", telegram_stopped, worker_stopped)
                if not (telegram_stopped and worker_stopped):
                    logger.error("A background thread did not stop before timeout")
    except AlreadyRunningError as exc:
        logger.info("Second launch rejected: %s", exc)
        _message_box(str(exc))
    except Exception:
        logger.exception("Application startup failed")
        _message_box(f"Не удалось запустить приложение.\n\nПодробности: {DATA_DIR / 'logs' / 'app.log'}")


if __name__ == "__main__":
    main()

"""Desktop bridge methods for the «Рассылка» section and the service factory.

The service is stored in `_mailing` (underscore): pywebview exposes public attributes
of the js_api object recursively, the service itself must stay invisible to the page.
"""

from __future__ import annotations

from pathlib import Path

from .logsafe import scrub


def create_service(data_dir: Path):
    from .. import db
    from ..domain import render_template
    from ..script_templates import FIRST_BY_SITE_TYPE
    from .service import MailingService
    from .telethon_gateway import TelethonGateway

    def blocked() -> set[str]:
        ids = set(db.suppressed_ids())
        ids.update(row["business_id"] for row in db.list_contact_permissions("blocked") if row.get("business_id"))
        return ids

    return MailingService(
        data_dir, TelethonGateway(),
        businesses=lambda query, limit: db.list_businesses(query, limit=limit),
        business=db.business, templates=db.templates, render=render_template,
        first_by_site_type=FIRST_BY_SITE_TYPE, external_blocked=blocked)


class MailingBridgeMixin:
    _mailing = None

    def _m(self, method: str, *args):
        try:
            if self._mailing is None:
                raise RuntimeError("Модуль рассылки не запустился. Подробности в журнале приложения")
            return {"ok": True, "data": getattr(self._mailing, method)(*args)}
        except Exception as exc:  # message shown in the UI; never echo secrets
            return {"ok": False, "error": scrub(str(exc))[:500]}

    def mailing_overview(self):
        return self._m("overview")

    def mailing_save_keys(self, api_id, api_hash):
        return self._m("save_keys", api_id, api_hash)

    def mailing_import_file(self):
        def run():
            path = self._dialog("OPEN", file_types=("JSON (*.json)",))
            return self._mailing.import_owner_file(path) if path else None
        try:
            return {"ok": True, "data": run()}
        except Exception as exc:
            return {"ok": False, "error": scrub(str(exc))[:500]}

    def mailing_account_add(self, label, phone, is_test=False):
        return self._m("add_account", str(label or ""), str(phone or ""), bool(is_test))

    def mailing_login_request(self, account_id):
        return self._m("login_start", int(account_id))

    def mailing_login_code(self, account_id, code):
        return self._m("login_code", int(account_id), str(code or ""))

    def mailing_login_password(self, account_id, password):
        return self._m("login_password", int(account_id), str(password or ""))

    def mailing_login_cancel(self, account_id):
        return self._m("login_cancel", int(account_id))

    def mailing_account_status(self, account_id, status):
        return self._m("set_account_status", int(account_id), str(status))

    def mailing_account_logout(self, account_id):
        return self._m("remove_account", int(account_id))

    def mailing_scripts(self, site_type=""):
        return self._m("script_suggestions", str(site_type or ""))

    def mailing_preview(self, payload):
        data = dict(payload or {})
        return self._m("preview", data.get("template", ""), dict(data.get("filters") or {}),
                       data.get("kind", "normal"))

    def mailing_new(self, payload):
        data = dict(payload or {})
        return self._m("create_campaign", data.get("title", ""), dict(data.get("filters") or {}),
                       data.get("template", ""), list(data.get("accounts") or []), data.get("kind", "normal"),
                       data.get("limit") or None)

    def mailing_list(self):
        return self._m("campaigns")

    def mailing_detail(self, campaign_id):
        return self._m("campaign", int(campaign_id))

    def mailing_run(self, campaign_id):
        return self._m("run", int(campaign_id))

    def mailing_go_live(self, campaign_id, confirm):
        return self._m("enable_live", int(campaign_id), str(confirm or ""))

    def mailing_pause(self, campaign_id):
        return self._m("pause", int(campaign_id))

    def mailing_remove(self, campaign_id):
        return self._m("delete_campaign", int(campaign_id))

    def mailing_emergency(self):
        return self._m("emergency_stop")

    def mailing_emergency_clear(self):
        return self._m("clear_emergency")

    def mailing_stoplist(self):
        return self._m("stoplist")

    def mailing_stop_add(self, kind, value):
        return self._m("stop_add", str(kind), str(value or ""))

    def mailing_stop_remove(self, entry_id):
        return self._m("stop_remove", int(entry_id))

    def mailing_policy_save(self, values):
        return self._m("save_policy", dict(values or {}))

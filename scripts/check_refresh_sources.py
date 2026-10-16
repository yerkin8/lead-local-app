"""Reject automatic navigation and ad-hoc polling in the desktop UI."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "app" / "desktop_ui"
CHECKS = {
    "location navigation": re.compile(r"\b(?:window\.)?location\s*(?:\.|\[)\s*(?:reload|assign|replace|href)\b|\b(?:window\.)?location\s*="),
    "periodic interval": re.compile(r"\bsetInterval\s*\("),
    "meta refresh": re.compile(r"<meta\b[^>]*http-equiv\s*=\s*['\"]?refresh\b", re.I),
    "service worker": re.compile(r"\bserviceWorker\s*\.\s*register\s*\("),
    "SSE or WebSocket": re.compile(r"\b(?:new\s+)?(?:EventSource|WebSocket)\s*\("),
}


def main() -> None:
    problems = []
    for path in sorted(UI.glob("*.js")) + sorted(UI.glob("*.html")):
        source = path.read_text(encoding="utf-8")
        for label, pattern in CHECKS.items():
            for match in pattern.finditer(source):
                line = source.count("\n", 0, match.start()) + 1
                problems.append(f"{path.relative_to(ROOT)}:{line}: {label}")
    poller = (UI / "activity-poller.js").read_text(encoding="utf-8")
    for label, pattern in {
        "poller must not trigger page rendering": re.compile(r"\brender\s*\("),
        "poller must not replace DOM content": re.compile(r"\binnerHTML\b|\breplaceChildren\s*\("),
    }.items():
        for match in pattern.finditer(poller):
            line = poller.count("\n", 0, match.start()) + 1
            problems.append(f"app/desktop_ui/activity-poller.js:{line}: {label}")
    backend = (ROOT / "app" / "desktop.py").read_text(encoding="utf-8")
    for label, pattern in {
        "WebView navigation": re.compile(r"\bwindow\.(?:load_url|load_html)\s*\("),
        "WebView JavaScript timer": re.compile(r"\bwindow\.evaluate_js\s*\("),
    }.items():
        for match in pattern.finditer(backend):
            line = backend.count("\n", 0, match.start()) + 1
            problems.append(f"app/desktop.py:{line}: {label}")
    if problems:
        raise SystemExit("\n".join(problems))
    print("Refresh source guard passed: no automatic navigation or independent interval")


if __name__ == "__main__":
    main()

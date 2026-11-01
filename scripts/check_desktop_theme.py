"""Static verification for the desktop theme tokens and bundled font assets."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOKENS = ROOT / "app" / "desktop_ui" / "lem.css"
STYLE = ROOT / "app" / "desktop_ui" / "style.css"
HTML = ROOT / "app" / "desktop_ui" / "index.html"
APP_JS = ROOT / "app" / "desktop_ui" / "app.js"
FLOWS_JS = ROOT / "app" / "desktop_ui" / "flows.js"
# Mini App modules brought into Desktop (docs/unify): their styles must use the same tokens.
UNIFY_FILES = tuple(ROOT / "app" / "desktop_ui" / name for name in
                    ("leads.css", "ai.css", "leads.js", "today.js", "ai.js", "stats.js", "stage9.js", "desk-shell.js", "settings.js", "mailing.css"))
FONT_DIR = ROOT / "app" / "desktop_ui" / "fonts"

EXPECTED = {
    "bg-primary": "#F8FAFC",
    "bg-secondary": "#FFFFFF",
    "bg-hover": "#F1F5F9",
    "border": "#E2E8F0",
    "text-primary": "#0F172A",
    "text-secondary": "#64748B",
    "text-disabled": "#94A3B8",
    "accent": "#3B82F6",
    "accent-hover": "#2563EB",
    "success": "#22C55E",
    "warning": "#F59E0B",
    "error": "#EF4444",
    "info": "#06B6D4",
    "log-bg": "#1E293B",
    "log-text": "#E2E8F0",
}

FONT_FILES = {
    "inter-cyrillic-ext.woff2",
    "inter-cyrillic.woff2",
    "inter-latin-ext.woff2",
    "inter-latin.woff2",
    "jetbrains-mono-cyrillic-ext.woff2",
    "jetbrains-mono-cyrillic.woff2",
    "jetbrains-mono-latin-ext.woff2",
    "jetbrains-mono-latin.woff2",
}


def parse_tokens(text: str) -> dict[str, str]:
    return {
        name: value.upper()
        for name, value in re.findall(
            r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;", text
        )
    }


def luminance(value: str) -> float:
    channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    errors: list[str] = []
    tokens_text = TOKENS.read_text(encoding="utf-8")
    style_text = STYLE.read_text(encoding="utf-8")
    html_text = HTML.read_text(encoding="utf-8")
    app_text = APP_JS.read_text(encoding="utf-8")
    flows_text = FLOWS_JS.read_text(encoding="utf-8")
    light_block = re.search(r":root\s*\{(.*?)\}", tokens_text, re.S)
    dark_block = re.search(r':root\[data-theme="dark"\]\s*\{(.*?)\}', tokens_text, re.S)
    tokens = parse_tokens(light_block.group(1) if light_block else "")
    dark = {**tokens, **parse_tokens(dark_block.group(1) if dark_block else "")}
    if not dark_block:
        errors.append("Dark theme block :root[data-theme=\"dark\"] is missing")
    else:
        missing = sorted(set(re.findall(r"--([a-z0-9-]+)\s*:\s*#", light_block.group(1)))
                         - set(re.findall(r"--([a-z0-9-]+)\s*:", dark_block.group(1))))
        if missing:
            errors.append(f"Dark theme does not override colour tokens: {missing}")

    for name, expected in EXPECTED.items():
        actual = tokens.get(name)
        if actual != expected:
            errors.append(f"Token --{name}: expected {expected}, found {actual!r}")

    outside_tokens = (STYLE, HTML, APP_JS, FLOWS_JS, *UNIFY_FILES)
    literal_color = re.compile(
        r"#[0-9a-fA-F]{3,8}(?![0-9a-fA-F])|\b(?:rgba?|hsla?)\s*\("
    )
    for path in outside_tokens:
        if literal_color.search(path.read_text(encoding="utf-8")):
            errors.append(f"Hardcoded color found outside tokens: {path.relative_to(ROOT)}")

    if "glass-folder" in html_text:
        errors.append("Glass folders («База») must stay removed from the desktop page")

    pairs = [
        ("Основной текст / фон приложения", "text-primary", "bg-primary", 4.5),
        ("Вторичный текст / фон приложения", "text-secondary", "bg-primary", 4.5),
        ("Вторичный текст / карточка", "text-secondary", "bg-secondary", 4.5),
        ("Текст кнопки / основное действие", "on-accent", "accent-action", 4.5),
        ("Текст кнопки / действие при наведении", "on-accent", "accent-action-hover", 4.5),
        ("Текст статуса / success", "success-text", "success-bg", 4.5),
        ("Текст статуса / warning", "warning-text", "warning-bg", 4.5),
        ("Текст статуса / error", "error-text", "error-bg", 4.5),
        ("Текст статуса / info", "info-text", "info-bg", 4.5),
        ("Текст метки / purple", "purple-text", "purple-bg", 4.5),
        ("Индикатор фокуса / белая поверхность", "accent", "bg-secondary", 3.0),
    ]
    ratios: list[tuple[str, float, float]] = []
    themed = [(label, fg, bg, m, tokens) for label, fg, bg, m in pairs]
    themed += [(f"[тёмная] {label}", fg, bg, m, dark) for label, fg, bg, m in pairs]
    for label, fg_name, bg_name, minimum, palette in themed:
        fg, bg = palette.get(fg_name), palette.get(bg_name)
        if not fg or not bg:
            errors.append(f"Missing contrast tokens for {label}: --{fg_name}, --{bg_name}")
            continue
        ratio = contrast(fg, bg)
        ratios.append((label, ratio, minimum))
        if ratio < minimum:
            errors.append(f"Contrast below {minimum:.1f}:1 for {label} ({ratio:.2f}:1)")

    referenced = {
        Path(name).name
        for name in re.findall(r"url\([\"']?fonts/([^\"')]+)", tokens_text)
    }
    if referenced != FONT_FILES:
        errors.append(
            "Font-face references differ from bundled WOFF2 files: "
            f"missing={sorted(FONT_FILES - referenced)}, extra={sorted(referenced - FONT_FILES)}"
        )
    font_records: list[tuple[str, int, str]] = []
    for name in sorted(FONT_FILES):
        path = FONT_DIR / name
        if not path.is_file():
            errors.append(f"Missing font file: {path.relative_to(ROOT)}")
            continue
        raw = path.read_bytes()
        if raw[:4] != b"wOF2":
            errors.append(f"Invalid WOFF2 signature: {path.relative_to(ROOT)}")
        font_records.append((name, len(raw), hashlib.sha256(raw).hexdigest()))

    for license_name in ("OFL-Inter.txt", "OFL-JetBrainsMono.txt"):
        path = FONT_DIR / license_name
        if not path.is_file() or "SIL OPEN FONT LICENSE Version 1.1" not in path.read_text(encoding="utf-8"):
            errors.append(f"Missing/invalid font license: {path.relative_to(ROOT)}")

    if not re.search(r"@media\s*\(\s*max-width:\s*1024px\s*\)", style_text):
        errors.append("Missing 1024px responsive breakpoint")
    if re.search(r"body\s*\{[^}]*min-width:\s*(?:[1-9]\d{3,}|[2-9]\d{2,})px", style_text, re.S):
        errors.append("Body has a fixed minimum width that can overflow desktop windows")
    if "min-height: 44px" not in style_text:
        errors.append("44px minimum button target is missing")
    if "prefers-reduced-motion: reduce" not in style_text:
        errors.append("Reduced-motion rules are missing")
    if ".skip-link" not in style_text or 'href="#main-content"' not in html_text:
        errors.append("Keyboard skip link is missing")
    if "focus-visible" not in style_text:
        errors.append("Visible keyboard focus styling is missing")

    accessible_fragments = [
        (flows_text, 'aria-labelledby="telegram-links-title"', "telegram phone links panel"),
        (flows_text, 'data-show-telegram-links', "telegram phone links action"),
    ]
    for text, fragment, label in accessible_fragments:
        if fragment not in text:
            errors.append(f"Accessible name missing for {label}")

    output = [
        "Desktop theme static check",
        "Scope: app/desktop_ui only; no Mini App/server code executed.",
        "Palette tokens: checked against the requested light palette.",
        "Raw color literals: none outside app/desktop_ui/lem.css.",
        "Responsive/layout checks: 1024px breakpoint, no fixed body minimum width, 44px buttons.",
        "Keyboard/motion checks: skip link, focus-visible, reduced motion.",
        "Local fonts: 8 WOFF2 files and 2 SIL OFL 1.1 license files.",
        "Contrast ratios:",
    ]
    output.extend(f"- {label}: {ratio:.2f}:1 (minimum {minimum:.1f}:1)" for label, ratio, minimum in ratios)
    output.append("Font asset hashes:")
    output.extend(f"- {name}: {size} bytes, SHA-256 {digest}" for name, size, digest in font_records)
    output.append("Result: FAIL" if errors else "Result: PASS")
    output.extend(f"- ERROR: {error}" for error in errors)
    report = "\n".join(output) + "\n"
    print(report, end="")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

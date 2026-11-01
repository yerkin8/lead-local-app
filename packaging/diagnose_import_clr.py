"""One-run frozen diagnostic for application imports that affect pythonnet."""

from __future__ import annotations

import importlib
import subprocess
import sys


MODULES = [
    "app.config",
    "app.domain",
    "app.script_templates",
    "app.db",
    "app.driver_registry",
    "app.lead_parser",
    "app.media",
    "app.media_service",
    "app.telegram_sender",
    "app.desktop_services",
    "app.desktop",
]


def child(count: int) -> int:
    for name in MODULES[:count]:
        try:
            importlib.import_module(name)
        except Exception as exc:
            print(f"IMPORT_FAIL {name}: {type(exc).__name__}: {exc}", flush=True)
            return 20
        print(f"IMPORT_OK {name}", flush=True)
    try:
        importlib.import_module("clr")
    except Exception as exc:
        print(f"CLR_FAIL after {count} app imports: {type(exc).__name__}: {exc}", flush=True)
        return 30
    print(f"CLR_OK after {count} app imports", flush=True)
    return 0


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        return child(max(0, min(len(MODULES), int(sys.argv[2]))))

    # Probe isolated processes because importing clr initializes process-global
    # runtime state and a single process cannot safely undo that between checks.
    print("Frozen import/CLR bisection; each probe starts a fresh process.", flush=True)
    low, high = 0, len(MODULES)
    first = subprocess.run([sys.executable, "--child", "0"], capture_output=True, text=True)
    print("--- probe prefix 0 ---\n" + first.stdout + first.stderr, flush=True)
    if first.returncode:
        print("RESULT: CLR fails without importing any app module.", flush=True)
        return 0

    full = subprocess.run([sys.executable, "--child", str(high)], capture_output=True, text=True)
    print(f"--- probe prefix {high} ---\n" + full.stdout + full.stderr, flush=True)
    if full.returncode == 0:
        print("RESULT: all listed app imports coexist with import clr.", flush=True)
        return 0

    while high - low > 1:
        middle = (low + high) // 2
        result = subprocess.run([sys.executable, "--child", str(middle)],
                                capture_output=True, text=True)
        print(f"--- probe prefix {middle} ---\n" + result.stdout + result.stderr, flush=True)
        if result.returncode == 0:
            low = middle
        else:
            high = middle

    suspect = MODULES[high - 1] if high else "none"
    print(f"RESULT: first failing prefix is {high}; suspected boundary module: {suspect}.", flush=True)
    print("The child output above distinguishes an import exception from CLR_FAIL.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""One-time: add Simplify Copilot to the pipeline's own Chrome window (the one APPLY.bat opens) and sign in.
Your normal Chrome already has it; this window is a separate profile, so it needs its own copy."""
from __future__ import annotations

import sys
import time

from common import CONFIG, ask, require_lock
from fill import open_browser, simplify_installed

STORE = "https://chromewebstore.google.com/detail/pbanhockgagggenencehbnadejlgchfc"

def wait_until_closed(ctx) -> None:
    """No keyboard (started from the Control Center): wait until you close the Chrome window."""
    print("\nWhen you are done, close the Chrome window. I'll check the result after that.", flush=True)
    started = time.time()
    why = "window closed"
    while True:
        try:
            if not ctx.pages:
                why = "no tabs left"
                break
            ctx.pages[0].wait_for_timeout(1000)
        except Exception as exc:  # noqa: BLE001  (window closed)
            why = f"{exc.__class__.__name__}: {str(exc).splitlines()[0][:120] if str(exc) else ''}"
            break
    if time.time() - started < 15:
        print(f"\nThe window closed after {time.time() - started:.0f} seconds ({why}). Usually that means another Chrome "
              "window is already using the apply profile (an old APPLY window). Close every Chrome window that the "
              "pipeline opened, then press Simplify setup again.", flush=True)


if __name__ == "__main__":
    require_lock("simplify setup")
    # Windows reports the NUL device as a terminal, so also require a real console on stdout (the Control Center
    # pipes stdout). Started from the Control Center = wait until the window is closed, no Enter prompts.
    interactive = bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = open_browser(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if simplify_installed():
            print("Simplify is already installed in the APPLY window. Opening simplify.jobs so you can check you're signed in.")
            page.goto("https://simplify.jobs/profile")
        else:
            page.goto(STORE)
            print("1) In the window that opened, click 'Add to Chrome', then 'Add extension'.")
            print("2) Sign in to Simplify with the same account you use in your normal Chrome.")
            print("3) Pin it: puzzle-piece icon > pin Simplify.")
        if interactive:
            ask("\nPress Enter here when you're done...")
        else:
            wait_until_closed(ctx)
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
    print("\n[ OK ] Simplify is installed in the APPLY window." if simplify_installed() else
          "\n[TODO] Simplify was not found in the APPLY window yet. Press Simplify setup again.")
    if interactive:
        ask("Press Enter to close.")

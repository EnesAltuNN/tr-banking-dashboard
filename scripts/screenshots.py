"""Screenshots of the running dashboard: the README image, and optionally every tab.

Playwright is not a project dependency; run it through uv with the installed Edge browser:

    uv run streamlit run src/tr_banking/app/dashboard.py        # in another terminal
    uv run --with playwright python scripts/screenshots.py      # docs/images/dashboard.png
    uv run --with playwright python scripts/screenshots.py --all data/screenshots

`--url https://tr-banking-dashboard.streamlit.app/` shoots the live page instead (the app runs
in an iframe there; a sleeping app is woken up). Without Edge, pass `--channel chromium` after
`uv run --with playwright playwright install chromium`.
"""

import argparse
import time
from pathlib import Path

from playwright.sync_api import Frame, Page, sync_playwright

TAB_LABELS = ["Krediler", "Faizler", "Kartlar", "Sektör"]
README_SIZE = {"width": 1600, "height": 1000}
MAX_README_BYTES = 500_000
# Streamlit's own chrome (Deploy button, menu, Cloud badge) does not belong in a screenshot.
HIDE_CHROME = """
[data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"],
[class*="viewerBadge"] { display: none !important; }
"""


def app_frame(page: Page) -> Frame:
    """Streamlit Community Cloud serves the app in an iframe; locally it is the page itself."""
    return next((frame for frame in page.frames if "/~/+/" in frame.url), page.main_frame)


def open_app(page: Page, url: str, timeout_s: int = 240) -> Frame:
    page.goto(url, timeout=120_000)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        frame = app_frame(page)
        if frame.locator('[data-testid="stMetric"]').count():
            frame.add_style_tag(content=HIDE_CHROME)
            page.wait_for_timeout(2500)  # let the charts finish drawing
            return frame
        wake = page.get_by_role("button", name="Yes, get this app back up!")
        if wake.count():
            wake.click()
        page.wait_for_timeout(2000)
    raise TimeoutError(f"the dashboard at {url} did not load within {timeout_s} s")


def switch_to_english(page: Page, frame: Frame) -> None:
    frame.get_by_text("EN", exact=True).click()
    frame.get_by_role("tab", name="Loans").wait_for(timeout=30_000)
    page.wait_for_timeout(2500)


def readme_image(page: Page, url: str, out: Path) -> None:
    """English, light theme, first tab, one screen (the README's "above the fold")."""
    frame = open_app(page, url)
    switch_to_english(page, frame)
    out.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=out)
    size = out.stat().st_size
    note = "" if size <= MAX_README_BYTES else f" (over {MAX_README_BYTES // 1000} KB: compress it)"
    print(f"{out}: {size // 1000} KB{note}")


def every_tab(page: Page, url: str, folder: Path, scheme: str) -> None:
    """Full-height screenshots of every tab, Turkish, in one color scheme."""
    frame = open_app(page, url)
    folder.mkdir(parents=True, exist_ok=True)
    for index, label in enumerate(TAB_LABELS, start=1):
        frame.get_by_role("tab", name=label).click()
        page.wait_for_timeout(4000)
        path = folder / f"{scheme}_{index}_{label}.png"
        page.screenshot(path=path, full_page=True)
        print(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8501/")
    parser.add_argument("--out", type=Path, default=Path("docs/images/dashboard.png"))
    parser.add_argument("--all", type=Path, metavar="FOLDER", help="also shoot every tab here")
    parser.add_argument("--channel", default="msedge", help="installed browser (msedge, chrome)")
    args = parser.parse_args()

    with sync_playwright() as playwright:
        channel = None if args.channel == "chromium" else args.channel
        browser = playwright.chromium.launch(channel=channel)
        page = browser.new_page(viewport=README_SIZE, color_scheme="light")
        readme_image(page, args.url, args.out)
        page.close()
        if args.all:
            for scheme in ("light", "dark"):
                # Tall viewport: the page scrolls inside Streamlit's own container.
                tall = browser.new_page(
                    viewport={"width": 1600, "height": 3000}, color_scheme=scheme
                )
                every_tab(tall, args.url, args.all, scheme)
                tall.close()
        browser.close()


if __name__ == "__main__":
    main()

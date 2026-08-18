import asyncio
import sys
from typing import Optional

from playwright.async_api import (
    Browser,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)
from playwright.sync_api import sync_playwright

from preview_screenshot.base import VIEWPORT_SIZES

PAGE_LOAD_TIMEOUT_MS = 15000
RENDER_SETTLE_MS = 250


class PlaywrightBackend:
    """Default backend: renders in local headless Chromium.

    Runs locally, so the page can load assets served from localhost
    (e.g. /local-assets/ URLs) that an external screenshot API cannot reach.
    Holds one shared browser, launched lazily and reused across captures.

    On Windows, asyncio subprocess creation is broken in both event loop
    policies. We work around this by probing availability synchronously
    (sync_playwright) and running captures in a dedicated thread with its
    own event loop.
    """

    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()
        self._is_windows = sys.platform == "win32"

    async def _get_browser(self) -> Browser:
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                # --no-sandbox: Chromium refuses to launch as root (the user in
                # most containers/hosted Linux) unless the sandbox is disabled.
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=["--no-sandbox"],
                )
            return self._browser

    async def available(self) -> bool:
        """Launch (and warm up) Chromium; report whether it works.

        Catches every failure mode — missing browser binary, missing Linux
        system libraries, sandbox errors — and logs why it's disabled.
        """
        print(f"[screenshot_preview] Platform: {sys.platform}, _is_windows: {self._is_windows}")
        if self._is_windows:
            # On Windows, async subprocess doesn't work. Use sync probe in a thread.
            return await asyncio.to_thread(self._sync_probe)

        try:
            await self._get_browser()
            print("[screenshot_preview] Chromium available — tool enabled.")
            return True
        except Exception as exc:
            print(
                "[screenshot_preview] Chromium unavailable — tool disabled. "
                f"Install it with `playwright install chromium`. Cause: {exc}"
            )
            return False

    def _sync_probe(self) -> bool:
        """Synchronous probe for Windows where async subprocess fails."""
        try:
            from playwright.sync_api import sync_playwright as sp
            pw = sp().start()
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            browser.close()
            pw.stop()
            print("[screenshot_preview] Chromium available — tool enabled (Windows sync probe).")
            return True
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(
                "[screenshot_preview] Chromium unavailable — tool disabled. "
                f"Install it with `playwright install chromium`. Cause: {exc}"
            )
            return False

    async def capture(
        self,
        html: str,
        device: str = "desktop",
        full_page: bool = True,
    ) -> bytes:
        if self._is_windows:
            return await asyncio.to_thread(
                self._sync_capture, html, device, full_page
            )

        browser = await self._get_browser()
        width, height = VIEWPORT_SIZES.get(device, VIEWPORT_SIZES["desktop"])
        page = await browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=1,
        )
        try:
            try:
                await page.set_content(
                    html,
                    wait_until="networkidle",
                    timeout=PAGE_LOAD_TIMEOUT_MS,
                )
            except PlaywrightTimeoutError:
                pass
            try:
                await page.evaluate("document.fonts.ready")
            except Exception:
                pass
            await page.wait_for_timeout(RENDER_SETTLE_MS)
            return await page.screenshot(full_page=full_page, type="png")
        finally:
            await page.close()

    def _sync_capture(
        self, html: str, device: str, full_page: bool
    ) -> bytes:
        """Synchronous capture for Windows (runs in a thread)."""
        from playwright.sync_api import sync_playwright as sp

        width, height = VIEWPORT_SIZES.get(device, VIEWPORT_SIZES["desktop"])
        pw = sp().start()
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=1,
        )
        try:
            try:
                page.set_content(
                    html,
                    wait_until="networkidle",
                    timeout=PAGE_LOAD_TIMEOUT_MS,
                )
            except Exception:
                pass
            try:
                page.evaluate("document.fonts.ready")
            except Exception:
                pass
            page.wait_for_timeout(RENDER_SETTLE_MS)
            return page.screenshot(full_page=full_page, type="png")
        finally:
            page.close()
            browser.close()
            pw.stop()

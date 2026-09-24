"""
Owns the Playwright lifecycle for a single execution: browser -> context ->
page. Uses the async Playwright API because the agent runs inside FastAPI's
asyncio event loop (the sync API cannot be used there).

Domain restriction (section 24 of the spec) is enforced here: navigation to
a domain outside ALLOWED_DOMAINS is refused before it happens.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from app.core.config import settings


class DomainNotAllowedError(Exception):
    pass


class BrowserSession:
    def __init__(self):
        self._playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    async def start(self):
        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(headless=settings.PLAYWRIGHT_HEADLESS)
        self.context = await self.browser.new_context(accept_downloads=True)
        self.page = await self.context.new_page()

    async def stop(self):
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
        finally:
            if self._playwright:
                await self._playwright.stop()

    @staticmethod
    def assert_domain_allowed(url: str):
        host = urlparse(url).hostname or ""
        allowed = settings.ALLOWED_DOMAINS
        if not any(host == d or host.endswith("." + d) for d in allowed):
            raise DomainNotAllowedError(
                f"Navigation to '{host}' is blocked. Allowed domains: {allowed}"
            )

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.stop()

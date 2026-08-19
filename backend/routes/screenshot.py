import base64
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx
from urllib.parse import urlparse

router = APIRouter()


def normalize_url(url: str) -> str:
    """
    Normalize URL to ensure it has a proper protocol.
    If no protocol is specified, default to https://
    """
    url = url.strip()
    
    # Parse the URL
    parsed = urlparse(url)
    
    # Check if we have a scheme
    if not parsed.scheme:
        # No scheme, add https://
        url = f"https://{url}"
    elif parsed.scheme in ['http', 'https']:
        # Valid scheme, keep as is
        pass
    else:
        # Check if this might be a domain with port (like example.com:8080)
        # urlparse treats this as scheme:netloc, but we want to handle it as domain:port
        if ':' in url and not url.startswith(('http://', 'https://', 'ftp://', 'file://')):
            # Likely a domain:port without protocol
            url = f"https://{url}"
        else:
            # Invalid protocol
            raise ValueError(f"Unsupported protocol: {parsed.scheme}")
    
    return url


def bytes_to_data_url(image_bytes: bytes, mime_type: str) -> str:
    base64_image = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{base64_image}"


async def capture_screenshot_with_playwright(
    target_url: str, device: str = "desktop"
) -> bytes:
    """Capture a screenshot using local Playwright (no external API needed)."""
    from playwright.async_api import async_playwright

    if device == "desktop":
        viewport = {"width": 1280, "height": 832}
    else:
        viewport = {"width": 342, "height": 684}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(
            viewport=viewport,
            device_scale_factor=1,
        )
        try:
            await page.goto(target_url, wait_until="networkidle", timeout=30000)
        except Exception:
            # If networkidle times out, capture whatever loaded
            pass
        # Wait a moment for any remaining renders
        await page.wait_for_timeout(500)
        screenshot_bytes = await page.screenshot(full_page=True, type="png")
        await page.close()
        await browser.close()
        return screenshot_bytes


async def capture_screenshot_with_api(
    target_url: str, api_key: str, device: str = "desktop"
) -> bytes:
    """Fallback: capture screenshot using ScreenshotOne API."""
    api_base_url = "https://api.screenshotone.com/take"

    params = {
        "access_key": api_key,
        "url": target_url,
        "full_page": "true",
        "device_scale_factor": "1",
        "format": "png",
        "block_ads": "true",
        "block_cookie_banners": "true",
        "block_trackers": "true",
        "cache": "false",
        "viewport_width": "342",
        "viewport_height": "684",
    }

    if device == "desktop":
        params["viewport_width"] = "1280"
        params["viewport_height"] = "832"

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(api_base_url, params=params)
        if response.status_code == 200 and response.content:
            return response.content
        else:
            raise Exception("Error taking screenshot")


class ScreenshotRequest(BaseModel):
    url: str
    apiKey: str = ""  # Now optional — Playwright is used by default


class ScreenshotResponse(BaseModel):
    url: str


@router.post("/api/screenshot")
async def app_screenshot(request: ScreenshotRequest):
    # Extract the URL from the request body
    url = request.url
    api_key = request.apiKey

    try:
        # Normalize the URL
        normalized_url = normalize_url(url)
        
        # Use Playwright locally (free, no API key needed)
        # Falls back to ScreenshotOne API if a key is provided and Playwright fails
        try:
            image_bytes = await capture_screenshot_with_playwright(normalized_url)
        except Exception as playwright_err:
            if api_key:
                # Fallback to ScreenshotOne if Playwright fails and key is available
                image_bytes = await capture_screenshot_with_api(normalized_url, api_key=api_key)
            else:
                raise playwright_err

        # Convert the image bytes to a data url
        data_url = bytes_to_data_url(image_bytes, "image/png")

        return ScreenshotResponse(url=data_url)
    except ValueError as e:
        # Handle URL normalization errors
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        # Handle other errors
        raise HTTPException(status_code=500, detail=f"Error capturing screenshot: {str(e)}")

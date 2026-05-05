import asyncio
import os
import zipfile
import xml.etree.ElementTree as ET
from playwright.async_api import async_playwright

async def run():
    url = "https://marketing.kaspi.kz/external/advertising/products"
    storage_state = "data/kaspi_auth_state.json"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=storage_state)
        page = await context.new_page()
        
        print(f"Navigating to {url}...")
        try:
            # Use domcontentloaded for faster interaction
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            
            # Wait for any button or link that might be the download button
            print("Searching for download target...")
            
            # Try to find the button by text, or just wait for the first button to appear
            try:
                button = page.get_by_text(re.compile("Скачать отчет", re.IGNORECASE))
                if await button.count() == 0:
                    button = page.locator("button, a").filter(has_text="Скачать отчет")
            except:
                import re
                button = page.locator("button, a").filter(has_text=re.compile("Скачать отчет", re.IGNORECASE))

            if await button.count() == 0:
                print("Button 'Скачать отчет' not found via standard locators. Checking all elements...")
                # Fallback: look for common icons or title attributes if text is missing
                button = page.locator("[title*='отчет'], [aria-label*='отчет'], button:has-text('отчет')")

            if await button.count() > 0:
                print(f"Found {await button.count()} potential targets. Clicking first.")
                async with page.expect_download(timeout=30000) as download_info:
                    await button.first.click()
                
                download = await download_info.value
                path = await download.path()
                
                print(f"Suggested filename: {download.suggested_filename}")
                print(f"File size: {os.path.getsize(path)} bytes")
                
                with open(path, "rb") as f:
                    header = f.read(200)
                
                is_zip = header.startswith(b"PK\x03\x04")
                print(f"Is ZIP/OOXML: {is_zip}")
                
                if is_zip:
                    with zipfile.ZipFile(path, 'r') as z:
                        worksheets = [f for f in z.namelist() if f.startswith("xl/worksheets/")]
                        for ws in worksheets:
                            tree = ET.fromstring(z.read(ws))
                            rows = tree.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row')
                            print(f"- {ws}: {len(rows)} rows")
            else:
                print("No download button found.")
                await page.screenshot(path="no_button.png")

        except Exception as e:
            print(f"Error: {e}")
            await page.screenshot(path="fast_error.png")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(run())

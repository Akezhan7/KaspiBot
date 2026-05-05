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
            await page.goto(url, wait_until="networkidle", timeout=60000)
            
            # Look for button with text "Скачать отчет" or "Скачать отчёт"
            button = page.get_by_role("button", name=lambda t: "Скачать отчет" in t or "Скачать отчёт" in t)
            
            if await button.count() == 0:
                print("Button 'Скачать отчет' not found. Trying link...")
                button = page.locator("a:has-text('Скачать отчет'), a:has-text('Скачать отчёт')")

            async with page.expect_download() as download_info:
                await button.first.click()
            
            download = await download_info.value
            path = await download.path()
            
            print(f"Suggested filename: {download.suggested_filename}")
            print(f"Download URL: {download.url}")
            
            error = await download.failure()
            if error:
                print(f"Download failure: {error}")
                return

            size = os.path.getsize(path)
            print(f"File size: {size} bytes")
            
            with open(path, "rb") as f:
                header = f.read(200)
            
            print(f"First 64 bytes (hex): {header[:64].hex()}")
            print(f"First 200 bytes (repr): {repr(header)}")
            
            is_zip = header.startswith(b"PK\x03\x04")
            print(f"Is ZIP/OOXML: {is_zip}")
            
            if is_zip:
                try:
                    with zipfile.ZipFile(path, 'r') as z:
                        print("Internal files:", z.namelist())
                        worksheets = [f for f in z.namelist() if f.startswith("xl/worksheets/")]
                        for ws in worksheets:
                            content = z.read(ws)
                            tree = ET.fromstring(content)
                            ns = {'main': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                            rows = tree.findall('.//main:row', ns)
                            print(f"- {ws}: {len(rows)} rows found")
                except Exception as e:
                    print(f"Error reading zip structure: {e}")
            else:
                text = header.decode('utf-8', errors='ignore')
                if "<html" in text.lower() or "<!doctype html" in text.lower():
                    print("Detected HTML (likely redirect or error page)")
                elif "," in text or ";" in text or "\t" in text:
                    print("Detected CSV-like text")
                else:
                    print("Unknown file format")

        except Exception as e:
            print(f"An error occurred: {e}")
            await page.screenshot(path="error_screenshot.png")
            print("Screenshot saved to error_screenshot.png")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(run())

import asyncio
import os
import zipfile
import xml.etree.ElementTree as ET
from playwright.async_api import async_playwright
import re

async def run():
    url = "https://marketing.kaspi.kz/external/advertising/products"
    storage_state = "data/kaspi_auth_state.json"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=storage_state)
        page = await context.new_page()
        
        print(f"Navigating to {url}...")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(5)
            
            print(f"Current URL: {page.url}")
            
            # Find any button or link containing 'отчет'
            targets = page.locator("button, a, span, div").filter(has_text=re.compile("отчет", re.IGNORECASE))
            count = await targets.count()
            print(f"Found {count} elements with 'отчет'")
            
            for i in range(count):
                target = targets.nth(i)
                text = await target.inner_text()
                tag = await target.evaluate("el => el.tagName")
                print(f"Target {i}: [{tag}] '{text[:30]}'")
                
            if count > 0:
                print("Attempting to click first target and expect download...")
                try:
                    async with page.expect_download(timeout=10000) as download_info:
                        await targets.first.click()
                    download = await download_info.value
                    path = await download.path()
                    print(f"Downloaded: {download.suggested_filename} ({os.path.getsize(path)} bytes)")
                    
                    with open(path, "rb") as f:
                        header = f.read(4)
                    if header == b"PK\x03\x04":
                        with zipfile.ZipFile(path, 'r') as z:
                            ws = [f for f in z.namelist() if "sheet1.xml" in f]
                            if ws:
                                tree = ET.fromstring(z.read(ws[0]))
                                rows = tree.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row')
                                print(f"Rows in {ws[0]}: {len(rows)}")
                    else:
                        print("Not a ZIP file.")
                except Exception as e:
                    print(f"Click/Download failed: {e}")
            else:
                # If no 'отчет', list all buttons
                btns = page.locator("button")
                b_count = await btns.count()
                print(f"Listing all {b_count} buttons:")
                for i in range(b_count):
                    print(f"Btn {i}: {await btns.nth(i).inner_text()}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(run())

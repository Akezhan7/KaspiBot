import asyncio
import os
import re
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        storage_state = "data/kaspi_auth_state.json"
        url = "https://marketing.kaspi.kz/external/advertising/products/campaigns?tab=overview&activeTab=Enabled"
        
        context_args = {}
        if os.path.exists(storage_state):
            context_args["storage_state"] = storage_state
        
        context = await browser.new_context(**context_args)
        page = await context.new_page()
        
        try:
            print(f"Navigating to {url}...")
            # Use domcontentloaded for faster return on timeout-prone pages
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print(f"Goto warning (continuing): {e}")

            await asyncio.sleep(10)
            
            body_text = await page.inner_text("body")
            lines = body_text.splitlines()
            
            regex = re.compile(r"SKU|Арт|артикул|\b\d{6,12}\b|%|₸", re.IGNORECASE)
            sku_regex = re.compile(r"\b\d{6,12}\b")
            
            matches = []
            skus = set()
            
            for i, line in enumerate(lines):
                if regex.search(line):
                    matches.append((i + 1, line.strip()))
                found_skus = sku_regex.findall(line)
                for s in found_skus:
                    skus.add(s)
            
            print(f"--- Top 120 matching lines ---")
            for i, (line_num, content) in enumerate(matches[:120]):
                if content:
                    print(f"{line_num}: {content}")
            
            print(f"\n--- Unique SKU-like numbers (up to 100) ---")
            print(list(skus)[:100])
            
            content = await page.content()
            with open("/tmp/kaspi_ads_debug.html", "w", encoding="utf-8") as f:
                f.write(content)
            
            if os.path.exists("/tmp/kaspi_ads_debug.html"):
                print(f"\nFile /tmp/kaspi_ads_debug.html created: {os.path.getsize('/tmp/kaspi_ads_debug.html')} bytes")
            
        except Exception as e:
            print(f"An error occurred: {e}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(run())

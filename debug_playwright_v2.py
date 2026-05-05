import asyncio
import re
from playwright.async_api import async_playwright

async def inspect_url(page, url):
    print(f"\n--- Testing URL: {url} ---")
    try:
        # Reduced wait_until or just rely on sleep if networkidle is too slow
        await page.goto(url, wait_until="load", timeout=60000)
        await asyncio.sleep(12)
        
        final_url = page.url
        title = await page.title()
        
        # Capture raw text carefully
        body_text = await page.evaluate("document.body.innerText")
        lines = [line.strip() for line in body_text.splitlines() if line.strip()]
        sku_tokens = re.findall(r'\b\d{6,12}\b', body_text)
        
        print(f"Final URL: {final_url}")
        print(f"Title: {title}")
        print(f"Body lines count: {len(body_text.splitlines())}")
        print(f"SKU-like tokens (6-12 digits) count: {len(sku_tokens)}")
        print(f"First 20 SKUs: {sku_tokens[:20]}")
        
        selectors = ["table tbody tr", "[role='row']", "[class*='row']", "[class*='list']", "[class*='item']", "[class*='product']"]
        for sel in selectors:
            try:
                count = await page.locator(sel).count()
                print(f"Selector '{sel}' count: {count}")
            except:
                print(f"Selector '{sel}' failed")
        
        print("First 30 non-empty lines:")
        for line in lines[:30]:
            print(line)
            
    except Exception as e:
        print(f"Error processing {url}: {e}")

async def run():
    urls = [
        "https://marketing.kaspi.kz/external/advertising/products",
        "https://marketing.kaspi.kz/promotions/shop",
        "https://marketing.kaspi.kz/promotions/shop/list"
    ]
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Use storage state if exists
        import os
        storage = "data/kaspi_auth_state.json"
        if os.path.exists(storage):
            context = await browser.new_context(storage_state=storage)
        else:
            print(f"Warning: {storage} not found.")
            context = await browser.new_context()
            
        page = await context.new_page()
        
        for url in urls:
            await inspect_url(page, url)
                
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())

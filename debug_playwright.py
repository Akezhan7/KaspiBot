import asyncio
import re
from playwright.async_api import async_playwright

async def run():
    urls = [
        "https://marketing.kaspi.kz/external/advertising/products",
        "https://marketing.kaspi.kz/promotions/shop",
        "https://marketing.kaspi.kz/promotions/shop/list"
    ]
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state="data/kaspi_auth_state.json")
        page = await context.new_page()
        
        for url in urls:
            print(f"\n--- Testing URL: {url} ---")
            try:
                await page.goto(url, wait_until="networkidle")
                await asyncio.sleep(12)
                
                final_url = page.url
                title = await page.title()
                content = await page.content()
                body_text = await page.inner_text("body")
                
                lines = [line.strip() for line in body_text.splitlines() if line.strip()]
                sku_tokens = re.findall(r'\b\d{6,12}\b', body_text)
                
                print(f"Final URL: {final_url}")
                print(f"Title: {title}")
                print(f"Body lines count: {len(body_text.splitlines())}")
                print(f"SKU-like tokens (6-12 digits) count: {len(sku_tokens)}")
                print(f"First 20 SKUs: {sku_tokens[:20]}")
                
                selectors = ["table tbody tr", "[role='row']", "[class*='row']", "[class*='list']", "[class*='item']", "[class*='product']"]
                for sel in selectors:
                    count = await page.locator(sel).count()
                    print(f"Selector '{sel}' count: {count}")
                
                print("First 30 non-empty lines:")
                for line in lines[:30]:
                    print(line)
                    
            except Exception as e:
                print(f"Error processing {url}: {e}")
                
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())

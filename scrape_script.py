import asyncio
import os
import json
from playwright.async_api import async_playwright

async def scrape():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        storage_state = "data/kaspi_auth_state.json"
        context_args = {}
        if os.path.exists(storage_state):
            context_args["storage_state"] = storage_state
            
        context = await browser.new_context(**context_args)
        page = await context.new_page()
        
        urls = [
            "https://marketing.kaspi.kz/external/advertising/products/campaigns?tab=overview&activeTab=Enabled",
            "https://marketing.kaspi.kz/promotions/shop/list"
        ]
        
        selectors = [
            "table tbody tr",
            "[role='row']",
            "[role='grid']",
            "[data-testid*='row']",
            "[class*='row']",
            "[class*='table']",
            "[class*='Table']",
            "[class*='list']",
            "a[href*='product']",
            "button"
        ]
        
        tokens = ['кампан', 'промо', 'бонус', 'войд', 'login', 'sign in', 'нет данных']
        
        for url in urls:
            print(f"--- URL: {url} ---")
            try:
                try:
                    await page.goto(url, wait_until="networkidle", timeout=45000)
                except:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                
                final_url = page.url
                title = await page.title()
                body_text = await page.inner_text("body")
                body_text_clean = " ".join(body_text.split())[:1200]
                
                print(f"Final URL: {final_url}")
                print(f"Title: {title}")
                print(f"Body (1200 chars): {body_text_clean}")
                
                counts = {}
                for selector in selectors:
                    count = await page.locator(selector).count()
                    counts[selector] = count
                print(f"Counts: {counts}")
                
                token_results = {t: t.lower() in body_text.lower() for t in tokens}
                print(f"Tokens: {token_results}")
                
            except Exception as e:
                print(f"Error scraping {url}: {e}")
            print("\n")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(scrape())

import asyncio
import os
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
            "[role='tab']", "[aria-selected]", "[data-state]", "[data-testid]", 
            "li", "article", "section", "div[class*='card']", 
            "div[class*='item']", "div[class*='list']"
        ]
        
        for url in urls:
            print(f"\n{'='*20} URL: {url} {'='*20}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(8)
                
                print(f"Title: {await page.title()}")
                print(f"Final URL: {page.url}")
                
                # Buttons
                buttons = await page.locator("button").all()
                btn_texts = []
                for b in buttons:
                    t = (await b.inner_text()).strip()
                    if t: btn_texts.append(t)
                    if len(btn_texts) >= 40: break
                print(f"--- Buttons (up to 40) ---\n{btn_texts}")
                
                # Links
                links = await page.locator("a").all()
                link_data = []
                for l in links:
                    t = (await l.inner_text()).strip()
                    h = await l.get_attribute("href")
                    if t or h:
                        link_data.append(f"{t} ({h})")
                    if len(link_data) >= 40: break
                print(f"--- Links (up to 40) ---\n{link_data}")
                
                # Body Text (80 lines)
                body_text = await page.inner_text("body")
                lines = body_text.splitlines()
                print(f"--- Body Text (up to 80 lines) ---")
                for i, line in enumerate(lines[:80]):
                    print(f"{i+1}: {line}")
                
                # Selector counts
                print(f"--- Selector Counts ---")
                for sel in selectors:
                    count = await page.locator(sel).count()
                    print(f"{sel}: {count}")
                    
            except Exception as e:
                print(f"Error: {e}")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(scrape())

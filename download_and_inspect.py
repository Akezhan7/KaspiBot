import asyncio
import os
import zipfile
import xml.etree.ElementTree as ET
from playwright.async_api import async_playwright

async def run():
    url = "https://marketing.kaspi.kz/external/advertising/products/campaigns?tab=overview&activeTab=Enabled"
    storage_state = "data/kaspi_auth_state.json"
    output_path = "/tmp/kaspi_marketing_report.xlsx"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=storage_state)
        # Set a default timeout for all actions
        context.set_default_timeout(60000)
        page = await context.new_page()

        print(f"Navigating to {url}...")
        try:
            # Use 'commit' to ensure we start looking as soon as the URL hits the server
            await page.goto(url, wait_until="domcontentloaded")
            print("URL matched. Waiting for link...")
            
            # The reports link often appears in a sidebar or top menu
            # Let's try to wait for it specifically
            link_selector = "a[href*='/reports/overview/xlsx']"
            await page.wait_for_selector(link_selector, state="attached", timeout=60000)
            
            href = await page.locator(link_selector).first.get_attribute("href")
            if not href:
                print("Link attribute empty.")
                await browser.close()
                return

            full_href = href if href.startswith("http") else f"https://marketing.kaspi.kz{href}"
            print(f"Downloading from: {full_href}")

            response = await context.request.get(full_href)
            if response.status == 200:
                with open(output_path, "wb") as f:
                    f.write(await response.body())
                print(f"File saved to {output_path}")
            else:
                print(f"Failed to download. Status: {response.status}")
                await browser.close()
                return

        except Exception as e:
            print(f"Execution failed: {e}")
            print(f"Current URL: {page.url}")
            await browser.close()
            return

        await browser.close()

    # Inspect XLSX
    try:
        with zipfile.ZipFile(output_path, 'r') as z:
            shared_strings = []
            if 'xl/sharedStrings.xml' in z.namelist():
                ss_tree = ET.fromstring(z.read('xl/sharedStrings.xml'))
                ns = {'main': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                for si in ss_tree.findall('main:si', ns):
                    texts = si.findall('.//main:t', ns)
                    shared_strings.append("".join([t.text for t in texts if t.text]))

            sheet_content = z.read('xl/worksheets/sheet1.xml')
            sheet_tree = ET.fromstring(sheet_content)
            ns = {'main': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            
            rows = []
            for row in sheet_tree.findall('.//main:row', ns):
                cells = []
                for c in row.findall('main:c', ns):
                    v = c.find('main:v', ns)
                    val = v.text if v is not None else ""
                    t = c.get('t')
                    if t == 's' and val:
                        try: val = shared_strings[int(val)]
                        except: pass
                    cells.append(val)
                rows.append(cells)
                if len(rows) >= 3: break
            
            print("\n--- Excel Findings ---")
            print(f"Total rows: {len(sheet_tree.findall('.//main:row', ns))}")
            for i, row in enumerate(rows):
                print(f"Row {i+1}: {row}")

    except Exception as e:
        print(f"Error inspecting XLSX: {e}")

if __name__ == "__main__":
    asyncio.run(run())

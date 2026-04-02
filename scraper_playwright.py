import os
import re
import json
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

SB_URL = "https://cltfjarsnvpxgzlirtiv.supabase.co"
SB_KEY = os.environ["SUPABASE_KEY"]

def sb_insert(table, row):
    import urllib.request
    url = f"{SB_URL}/rest/v1/{table}"
    data = json.dumps(row).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("apikey", SB_KEY)
    req.add_header("Authorization", f"Bearer {SB_KEY}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "return=representation")
    with urllib.request.urlopen(req) as res:
        return json.loads(res.read().decode())

def extract_miles_from_text(text):
    # Find all numbers > 1 billion
    nums = []
    # Comma-formatted: 9,002,319,780
    for m in re.findall(r'\b\d{1,3}(?:,\d{3}){3,}\b', text):
        nums.append(int(m.replace(",", "")))
    # Plain integers
    for m in re.findall(r'\b(\d{10,})\b', text):
        nums.append(int(m))
    nums = sorted(set(n for n in nums if n > 1_000_000_000), reverse=True)
    if not nums:
        return None, None
    total = nums[0]
    city = next((n for n in nums if n < total and n > 1_000_000_000), None)
    return total, city

def scrape_with_playwright():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        # Try multiple URLs
        for url in [
            "https://www.tesla.com/en_US/fsd/safety",
            "https://www.tesla.com/fsd/safety",
        ]:
            try:
                print(f"Loading {url}...")
                page.goto(url, wait_until="networkidle", timeout=30000)
                
                # Wait for the counter to appear (large number on page)
                page.wait_for_timeout(3000)  # Extra wait for JS rendering
                
                content = page.content()
                text = page.inner_text("body")
                
                print(f"  Page length: {len(content)}, text length: {len(text)}")
                
                # Try from visible text first (most reliable)
                total, city = extract_miles_from_text(text)
                if total:
                    browser.close()
                    return total, city
                
                # Try from full HTML
                total, city = extract_miles_from_text(content)
                if total:
                    browser.close()
                    return total, city
                    
            except Exception as e:
                print(f"  Error: {e}")
        
        browser.close()
        return None, None

def main():
    now = datetime.now(timezone.utc)
    print(f"[{now.isoformat()}] Scraping Tesla FSD miles with Playwright...")

    total_miles, city_miles = scrape_with_playwright()

    if total_miles is None:
        print("ERROR: Could not extract miles")
        exit(1)

    print(f"Total miles: {total_miles:,}")
    print(f"City miles:  {city_miles:,}" if city_miles else "City miles:  N/A")

    sb_insert("fsd_miles", {
        "recorded_at": now.isoformat(),
        "total_miles": total_miles,
        "city_miles": city_miles,
    })
    print("✓ Saved to Supabase")

if __name__ == "__main__":
    main()

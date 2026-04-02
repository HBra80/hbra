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

def parse_miles(text):
    """Parse a string like '9,013,765,338' or '9.013.765.338' into int."""
    cleaned = re.sub(r'[,.\s]', '', text.strip())
    if cleaned.isdigit():
        return int(cleaned)
    return None

def scrape_with_playwright():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            '--no-sandbox', '--disable-setuid-sandbox',
            '--disable-blink-features=AutomationControlled'
        ])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="de-AT",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        for url in [
            "https://www.tesla.com/de_AT/fsd/safety",
            "https://www.tesla.com/en_US/fsd/safety",
            "https://www.tesla.com/fsd/safety",
        ]:
            try:
                print(f"Loading {url} ...")
                page.goto(url, wait_until="domcontentloaded", timeout=45000)

                # Wait up to 15s for a number > 5 billion to appear anywhere on page
                print("  Waiting for miles counter to render...")
                try:
                    page.wait_for_function("""
                        () => {
                            const text = document.body.innerText;
                            // Look for numbers with commas that are > 5 billion
                            const matches = text.match(/\\d[\\d,\\.]{10,}/g) || [];
                            return matches.some(m => {
                                const n = parseInt(m.replace(/[,\\.]/g, ''));
                                return n > 5000000000;
                            });
                        }
                    """, timeout=15000)
                    print("  ✓ Large number detected on page")
                except Exception:
                    print("  No large number found after 15s, trying anyway...")

                # Get all visible text
                body_text = page.inner_text("body")
                print(f"  Body text length: {len(body_text)}")

                # Strategy 1: Find all numbers > 5B in visible text
                # Tesla shows: 9,013,765,338 (comma-separated)
                total_miles = None
                city_miles = None

                # Match numbers like 9,013,765,338 or 9.013.765.338
                raw_nums = re.findall(r'\b\d{1,3}(?:[,\.]\d{3}){3,}\b', body_text)
                print(f"  Raw number candidates: {raw_nums}")

                candidates = []
                for raw in raw_nums:
                    val = parse_miles(raw)
                    if val and val > 1_000_000_000:
                        candidates.append(val)
                        print(f"  Candidate: {val:,}")

                candidates = sorted(set(candidates), reverse=True)

                if candidates:
                    total_miles = candidates[0]
                    # City miles: second largest > 1B and < total
                    city_miles = next(
                        (n for n in candidates if n < total_miles and n > 500_000_000),
                        None
                    )

                # Strategy 2: Try to find specific elements Tesla uses for the counter
                if not total_miles:
                    selectors = [
                        '[class*="miles"]', '[class*="counter"]', '[class*="odometer"]',
                        '[data-testid*="miles"]', 'h1', 'h2', 'h3',
                        '[class*="stat"]', '[class*="number"]', '[class*="count"]'
                    ]
                    for sel in selectors:
                        try:
                            elements = page.query_selector_all(sel)
                            for el in elements:
                                text = el.inner_text().strip()
                                val = parse_miles(text)
                                if val and val > 5_000_000_000:
                                    total_miles = val
                                    print(f"  Found via selector '{sel}': {val:,}")
                                    break
                        except Exception:
                            pass
                        if total_miles:
                            break

                # Strategy 3: Screenshot for debugging + full HTML
                if not total_miles:
                    print("  Saving debug screenshot...")
                    page.screenshot(path="/tmp/tesla_debug.png")
                    html = page.content()
                    # Try plain large integers in HTML source
                    all_ints = [int(m) for m in re.findall(r'\b(\d{10,})\b', html)
                                if int(m) > 5_000_000_000]
                    print(f"  Large ints in HTML: {sorted(set(all_ints), reverse=True)[:5]}")
                    if all_ints:
                        all_ints.sort(reverse=True)
                        total_miles = all_ints[0]
                        city_miles = next((n for n in all_ints if n < total_miles and n > 500_000_000), None)

                if total_miles:
                    browser.close()
                    return total_miles, city_miles

            except Exception as e:
                print(f"  Error on {url}: {e}")

        browser.close()
        return None, None

def main():
    now = datetime.now(timezone.utc)
    print(f"[{now.isoformat()}] Scraping Tesla FSD miles...")

    total_miles, city_miles = scrape_with_playwright()

    if total_miles is None:
        print("ERROR: Could not extract miles from any URL")
        exit(1)

    # Sanity check: total FSD miles should be between 5B and 50B in 2026
    if total_miles < 5_000_000_000 or total_miles > 50_000_000_000:
        print(f"ERROR: Implausible value {total_miles:,} — aborting")
        exit(1)

    print(f"✓ Total miles: {total_miles:,}")
    print(f"✓ City miles:  {city_miles:,}" if city_miles else "  City miles:  N/A")

    target_table = os.environ.get("TARGET_TABLE", "fsd_miles")
    print(f"Writing to table: {target_table}")
    sb_insert(target_table, {
        "recorded_at": now.isoformat(),
        "total_miles": total_miles,
        "city_miles": city_miles,
    })
    print("✓ Saved to Supabase")

if __name__ == "__main__":
    main()

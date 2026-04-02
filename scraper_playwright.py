import os
import re
import json
import base64
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

SB_URL = "https://cltfjarsnvpxgzlirtiv.supabase.co"
SB_KEY = os.environ["SUPABASE_KEY"]
TARGET_TABLE = os.environ.get("TARGET_TABLE", "fsd_miles")

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

def sb_upload_screenshot(png_bytes, filename):
    """Upload screenshot to Supabase Storage for debugging."""
    import urllib.request
    url = f"{SB_URL}/storage/v1/object/fsd-screenshots/{filename}"
    req = urllib.request.Request(url, data=png_bytes, method="POST")
    req.add_header("apikey", SB_KEY)
    req.add_header("Authorization", f"Bearer {SB_KEY}")
    req.add_header("Content-Type", "image/png")
    try:
        with urllib.request.urlopen(req) as res:
            return True
    except Exception as e:
        print(f"  Screenshot upload failed (non-critical): {e}")
        return False

def parse_number(text):
    """Extract largest plausible FSD miles number from text."""
    # Match comma or period formatted large numbers: 9,013,765,338 or 9.013.765.338
    candidates = []
    
    # Comma-separated (US format): 9,013,765,338
    for m in re.findall(r'\b\d{1,3}(?:,\d{3}){3,}\b', text):
        val = int(m.replace(',', ''))
        candidates.append(val)
    
    # Period-separated (EU format): 9.013.765.338
    for m in re.findall(r'\b\d{1,3}(?:\.\d{3}){3,}\b', text):
        val = int(m.replace('.', ''))
        candidates.append(val)
    
    # Filter to plausible FSD range (5B–30B in 2026)
    valid = [v for v in candidates if 5_000_000_000 <= v <= 30_000_000_000]
    return sorted(set(valid), reverse=True)

def ocr_screenshot(page, selector=None):
    """Take screenshot and use Tesseract OCR to read numbers."""
    try:
        import subprocess
        import tempfile
        
        # Screenshot full page or specific element
        if selector:
            el = page.query_selector(selector)
            if el:
                png = el.screenshot()
            else:
                png = page.screenshot(full_page=False)
        else:
            png = page.screenshot(full_page=False)
        
        # Save to temp file
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            f.write(png)
            tmp_path = f.name
        
        # Run Tesseract OCR
        result = subprocess.run(
            ['tesseract', tmp_path, 'stdout', '--psm', '6', '-c', 'tessedit_char_whitelist=0123456789,. '],
            capture_output=True, text=True, timeout=30
        )
        ocr_text = result.stdout
        print(f"  OCR output: {repr(ocr_text[:200])}")
        
        nums = parse_number(ocr_text)
        print(f"  OCR numbers found: {nums[:5]}")
        return nums, png
        
    except Exception as e:
        print(f"  OCR error: {e}")
        return [], None

def scrape():
    with sync_playwright() as p:
        # Try with different browser configs to bypass detection
        for attempt, config in enumerate([
            # Attempt 1: Standard Chromium
            {"browser": "chromium", "extra_args": ["--no-sandbox", "--disable-setuid-sandbox"]},
            # Attempt 2: Firefox (different fingerprint)
            {"browser": "firefox", "extra_args": []},
        ]):
            try:
                print(f"\n--- Attempt {attempt+1}: {config['browser']} ---")
                
                if config["browser"] == "firefox":
                    browser = p.firefox.launch(headless=True)
                else:
                    browser = p.chromium.launch(
                        headless=True,
                        args=config["extra_args"] + [
                            '--disable-blink-features=AutomationControlled',
                            '--disable-web-security',
                        ]
                    )
                
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    locale="en-US",
                    viewport={"width": 1440, "height": 900},
                    # Mask automation
                    extra_http_headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Accept-Encoding": "gzip, deflate, br",
                        "DNT": "1",
                        "Upgrade-Insecure-Requests": "1",
                        "Sec-Fetch-Dest": "document",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Site": "none",
                        "Sec-Fetch-User": "?1",
                    }
                )
                
                # Remove navigator.webdriver flag
                context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3] });
                    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                    window.chrome = { runtime: {} };
                """)
                
                page = context.new_page()
                
                # First visit Tesla homepage to get cookies
                print("  Getting cookies from tesla.com...")
                page.goto("https://www.tesla.com/", wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2000)
                
                # Now visit FSD safety page
                print("  Loading FSD safety page...")
                page.goto("https://www.tesla.com/en_US/fsd/safety", 
                         wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(5000)  # Wait for JS counters to animate
                
                body_text = page.inner_text("body")
                page_len = len(body_text)
                print(f"  Page body length: {page_len}")
                
                if page_len < 500:
                    print("  Page too short — blocked")
                    browser.close()
                    continue
                
                print(f"  Body preview: {body_text[:300]}")
                
                # Strategy 1: Parse visible text directly
                nums = parse_number(body_text)
                print(f"  Numbers from text: {nums[:5]}")
                
                total_miles = nums[0] if nums else None
                city_miles = next((n for n in nums[1:] if n < total_miles * 0.9), None) if total_miles else None
                
                # Strategy 2: Screenshot + OCR if text parsing failed
                if not total_miles:
                    print("  Trying OCR on screenshot...")
                    # Install tesseract if needed
                    os.system("apt-get install -y tesseract-ocr -q 2>/dev/null")
                    
                    # Take full screenshot
                    png = page.screenshot(full_page=False, path="/tmp/tesla_fsd.png")
                    print("  Screenshot saved to /tmp/tesla_fsd.png")
                    
                    ocr_nums, _ = ocr_screenshot(page)
                    if ocr_nums:
                        total_miles = ocr_nums[0]
                        city_miles = next((n for n in ocr_nums[1:] if n < total_miles * 0.9), None)
                
                # Strategy 3: Look in page source for data attributes / JSON
                if not total_miles:
                    html = page.content()
                    # Look for numbers in script tags / JSON
                    script_nums = []
                    for m in re.findall(r'["\s:](\d{10,})[",\s]', html):
                        v = int(m)
                        if 5_000_000_000 <= v <= 30_000_000_000:
                            script_nums.append(v)
                    script_nums.sort(reverse=True)
                    print(f"  Script/HTML numbers: {script_nums[:5]}")
                    if script_nums:
                        total_miles = script_nums[0]
                        city_miles = next((n for n in script_nums[1:] if n < total_miles * 0.9), None)
                
                browser.close()
                
                if total_miles:
                    return total_miles, city_miles
                    
            except Exception as e:
                print(f"  Error: {e}")
                try:
                    browser.close()
                except:
                    pass
    
    return None, None

def main():
    now = datetime.now(timezone.utc)
    print(f"[{now.isoformat()}] Scraping Tesla FSD miles...")
    print(f"Target table: {TARGET_TABLE}")

    # Install firefox for playwright if needed
    os.system("playwright install firefox --with-deps 2>/dev/null || true")

    total_miles, city_miles = scrape()

    if total_miles is None:
        print("ERROR: Could not extract miles from any attempt")
        exit(1)

    print(f"\n✓ Total miles: {total_miles:,}")
    print(f"✓ City miles:  {city_miles:,}" if city_miles else "  City miles:  N/A")

    sb_insert(TARGET_TABLE, {
        "recorded_at": now.isoformat(),
        "total_miles": total_miles,
        "city_miles": city_miles,
    })
    print(f"✓ Saved to {TARGET_TABLE}")

if __name__ == "__main__":
    main()

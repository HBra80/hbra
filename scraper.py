import os
import re
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

SB_URL = "https://cltfjarsnvpxgzlirtiv.supabase.co"
SB_KEY = os.environ["SUPABASE_KEY"]

def sb_fetch(path, method="GET", body=None, headers=None):
    url = f"{SB_URL}/rest/v1/{path}"
    h = {
        "apikey": SB_KEY,
        "Authorization": f"Bearer {SB_KEY}",
        "Content-Type": "application/json",
    }
    if headers:
        h.update(headers)
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req) as res:
        text = res.read().decode()
        return json.loads(text) if text else []

def scrape_tesla_fsd():
    url = "https://www.tesla.com/de_at/fsd/safety"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept-Language": "de-AT,de;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=30) as res:
        html = res.read().decode("utf-8", errors="replace")

    # Look for the FSD miles counter - various patterns Tesla uses
    patterns = [
        r'"totalMiles"[:\s]*"?([\d,\.]+)"?',
        r'"fsdMiles"[:\s]*"?([\d,\.]+)"?',
        r'([\d,]{10,})\s*(?:Gefahrene\s*Meilen|Miles\s*Driven|miles driven)',
        r'data-miles["\s]*:["\s]*([\d,\.]+)',
        r'(\d{1,3}(?:,\d{3})+)\s*<[^>]*>\s*(?:Gefahrene Meilen|Miles)',
    ]
    
    total_miles = None
    city_miles = None
    
    for pattern in patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        if matches:
            # Take the largest number found (most likely total miles)
            for m in matches:
                val = int(m.replace(",", "").replace(".", ""))
                if val > 1_000_000_000:  # Must be > 1B to be total FSD miles
                    total_miles = val
                    break
            if total_miles:
                break

    # Try JSON embedded in page
    if not total_miles:
        json_matches = re.findall(r'\{[^{}]*miles[^{}]*\}', html, re.IGNORECASE)
        for jm in json_matches:
            try:
                obj = json.loads(jm)
                for k, v in obj.items():
                    if "mile" in k.lower() and isinstance(v, (int, float)) and v > 1_000_000_000:
                        total_miles = int(v)
                        break
            except:
                pass

    # Fallback: find all large numbers and pick the biggest
    if not total_miles:
        all_numbers = re.findall(r'[\d]{1,3}(?:,\d{3}){3,}', html)
        candidates = []
        for n in all_numbers:
            val = int(n.replace(",", ""))
            if val > 1_000_000_000:
                candidates.append(val)
        if candidates:
            total_miles = max(candidates)

    # City miles (usually smaller, ~3B range)
    if total_miles:
        city_patterns = [
            r'"cityMiles"[:\s]*"?([\d,\.]+)"?',
            r'([\d,]{10,})\s*(?:Gefahrene Meilen in einer Stadt|City Miles)',
            r'(\d{1,3}(?:,\d{3})+)\s*<[^>]*>\s*(?:Gefahrene Meilen in einer Stadt)',
        ]
        for pattern in city_patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            for m in matches:
                val = int(m.replace(",", "").replace(".", ""))
                if 1_000_000_000 < val < total_miles:
                    city_miles = val
                    break
            if city_miles:
                break

    return total_miles, city_miles, html[:500]  # Return snippet for debugging

def main():
    now = datetime.now(timezone.utc)
    print(f"[{now.isoformat()}] Scraping Tesla FSD miles...")

    total_miles, city_miles, debug_snippet = scrape_tesla_fsd()

    if total_miles is None:
        print("ERROR: Could not extract miles from page")
        print(f"Page snippet: {debug_snippet}")
        exit(1)

    print(f"Total miles: {total_miles:,}")
    print(f"City miles: {city_miles:,}" if city_miles else "City miles: N/A")

    row = {
        "recorded_at": now.isoformat(),
        "total_miles": total_miles,
        "city_miles": city_miles,
    }

    sb_fetch("fsd_miles", method="POST",
             body=row,
             headers={"Prefer": "return=representation"})

    print(f"✓ Saved to Supabase")

if __name__ == "__main__":
    main()

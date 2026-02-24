import requests
from bs4 import BeautifulSoup
import re
import random
import time
from datetime import datetime
import json
import os
import boto3
from services import db

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
]


def _parse_price(text):
    """Extract price from text, return as string or empty."""
    m = re.search(r'\$?([\d,]+\.?\d*)', text)
    return m.group(1).replace(",", "") if m else ""


def _scrape_rfd_hot_deals() -> list:
    """Scrape RedFlagDeals Hot Deals forum for Costco deals."""
    deals = []
    try:
        resp = requests.get(
            "https://forums.redflagdeals.com/hot-deals-f9/?c=5",
            headers={"User-Agent": random.choice(USER_AGENTS)},
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Noise filter - skip non-Costco items
        skip_keywords = ['nissan', 'toyota', 'honda', 'hyundai', 'kia', 'bmw', 'mercedes',
                         'scotiabank', 'amex', 'visa', 'mastercard', 'credit card',
                         'wine glass', 'ajax', 'rcss', 'walmart', 'amazon', 'ebay',
                         'little caesars', 'domino', 'skip the dishes', 'uber',
                         'shell go', 'gas station', 'car wash', 'mortgage',
                         'sponsored', 'topcashback', 'spc x skip']

        for el in soup.find_all(attrs={"data-thread-id": True}):
            for a in el.find_all("a"):
                title = a.get_text(strip=True)
                href = a.get("href", "")
                if len(title) > 30 and "[Sponsored]" not in title and "Last Page" not in title:
                    # Skip non-Costco noise
                    if any(skip in title.lower() for skip in skip_keywords):
                        break

                    prices = re.findall(r'\$([\d,]+\.?\d*)', title)
                    if prices:
                        name_part = title.split("$")[0].strip().rstrip(" -–|")
                        if len(name_part) > 5:
                            sale = prices[0].replace(",", "")
                            orig = ""
                            reg_match = re.search(r'(?:reg\.?|was|orig)\s*\$?([\d,]+\.?\d*)', title, re.IGNORECASE)
                            if reg_match:
                                orig = reg_match.group(1).replace(",", "")
                            elif len(prices) > 1:
                                orig = prices[1].replace(",", "")

                            # Build full URL
                            link = href
                            if link.startswith("/"):
                                link = "https://forums.redflagdeals.com" + link

                            deals.append({
                                "item_name": name_part[:100],
                                "sale_price": sale,
                                "original_price": orig,
                                "promo_start": "",
                                "promo_end": "",
                                "source": "redflagdeals.com",
                                "link": link,
                            })
                    break
    except Exception as e:
        print(f"RFD Hot Deals failed: {e}")
    return deals


def _scrape_rfd_clearance() -> list:
    """Scrape RedFlagDeals .97 clearance thread."""
    deals = []
    try:
        resp = requests.get(
            "https://forums.redflagdeals.com/east-gta-clearance-items-ending-97-general-thread-2146900/",
            headers={"User-Agent": random.choice(USER_AGENTS)},
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for post in soup.select(".post_content"):
            text = post.get_text()
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            for line in lines:
                if ".97" in line and "$" in line and len(line) < 200:
                    # Match "product name was $X.97" or "product name $X.97"
                    price_match = re.search(r'(.+?)\s*\$?([\d,]+\.97)', line)
                    if price_match:
                        name = price_match.group(1).strip()
                        name = re.sub(r'^[-•*\d\s]+', '', name).strip(' -:')
                        price = price_match.group(2).replace(",", "")
                        skip_words = ['thread', 'post', 'forum', 'missing', 'updated', 'weekly',
                                      'always', 'compiling', 'figured', 'instead', 'making']
                        if 5 < len(name) < 100 and not any(w in name.lower() for w in skip_words):
                            deals.append({
                                "item_name": name,
                                "sale_price": price,
                                "original_price": "",
                                "promo_start": "",
                                "promo_end": "",
                                "source": "redflagdeals.com/clearance",
                            })
    except Exception as e:
        print(f"RFD clearance failed: {e}")
    return deals


def _scrape_reddit(subreddit: str) -> list:
    """Scrape a Reddit subreddit for Costco deals with $ in title."""
    deals = []
    try:
        resp = requests.get(
            f"https://www.reddit.com/r/{subreddit}/search.json?q=%24&restrict_sr=on&sort=new&t=month&limit=25",
            headers={"User-Agent": "CostcoScanner/1.0"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        for post in data.get("data", {}).get("children", []):
            post_data = post["data"]
            title = post_data["title"]
            permalink = post_data.get("permalink", "")

            # Skip meta posts
            if any(skip in title.lower() for skip in ["megathread", "thread", "how costco gets you"]):
                continue

            if "$" in title:
                prices = re.findall(r'\$([\d,]+\.?\d*)', title)
                if prices:
                    name_part = title.split("$")[0].strip().rstrip(" -–|:")
                    name_part = re.sub(r'^(Found|Spotted|Deal|Sale|Price|Clearance):\s*', '', name_part, flags=re.IGNORECASE).strip()

                    if 5 < len(name_part) < 80:
                        deals.append({
                            "item_name": name_part,
                            "sale_price": prices[0].replace(",", ""),
                            "original_price": prices[1].replace(",", "") if len(prices) > 1 else "",
                            "promo_start": "",
                            "promo_end": "",
                            "source": f"reddit.com/r/{subreddit}",
                            "link": f"https://www.reddit.com{permalink}" if permalink else "",
                        })
    except Exception as e:
        print(f"Reddit r/{subreddit} failed: {e}")
    return deals


COUPON_PROMPT = """This is a Costco coupon book page. Extract every product deal.
Costco coupon books show: product name, item number (5-7 digit number), a SAVINGS amount (e.g. "$4 OFF" or "SAVE $5"), and sometimes the final price AFTER discount.

Return ONLY a valid JSON array:
[{"name": "PRODUCT NAME", "item_number": "1234567", "sale_price": "12.99", "savings": "4.00"}]

CRITICAL RULES:
- item_number = the Costco item/product number (5-7 digits, usually near the product name). Empty string if not visible.
- sale_price = the FINAL price the customer pays (the lower number). If only a savings amount is shown with no final price, leave sale_price empty.
- savings = the dollar amount saved (the OFF/SAVE amount)
- Do NOT put the savings amount in sale_price
- Skip headers, dates, fine print, non-product items"""

_bedrock = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-west-2"))


def _scrape_coupon_book() -> list:
    """Scrape Costco Canada coupon book from SmartCanucks and parse with Nova 2 Lite."""
    deals = []
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        # Find latest warehouse offers flyer
        r = requests.get("https://flyers.smartcanucks.ca/costco-canada", headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        flyer_url = None
        for a in soup.select("a[href*='costco']"):
            href = a.get("href", "")
            if "warehouse" in href.lower() and "business" not in href.lower() and "qc" not in href.lower():
                flyer_url = href if href.startswith("http") else "https://flyers.smartcanucks.ca" + href
                break
        # Fallback to any warehouse flyer if no non-QC found
        if not flyer_url:
            for a in soup.select("a[href*='costco']"):
                href = a.get("href", "")
                if "warehouse" in href.lower() and "business" not in href.lower():
                    flyer_url = href if href.startswith("http") else "https://flyers.smartcanucks.ca" + href
                    break

        if not flyer_url:
            print("  No Costco flyer found on SmartCanucks")
            return deals

        # Get flyer page to find image base URL
        r = requests.get(flyer_url, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        img = soup.select_one("img[src*='uploads/pages']")
        if not img:
            return deals

        base = re.sub(r"-\d+\.jpg$", "", img["src"])

        # Download and parse each page with Nova 2 Lite
        for i in range(1, 20):
            url = f"{base}-{i}.jpg"
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                break

            try:
                resp = _bedrock.converse(
                    modelId="us.amazon.nova-2-lite-v1:0",
                    messages=[{"role": "user", "content": [
                        {"image": {"format": "jpeg", "source": {"bytes": r.content}}},
                        {"text": COUPON_PROMPT},
                    ]}],
                    inferenceConfig={"maxTokens": 4096, "temperature": 0},
                )
                text = resp["output"]["message"]["content"][0]["text"]
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]

                items = json.loads(text.strip())
                for item in items:
                    sale = item.get("sale_price", "")
                    savings = item.get("savings", "")
                    name = item.get("name", "").strip()
                    item_num = item.get("item_number", "").strip()
                    if name and (sale or savings):
                        deals.append({
                            "item_name": name[:100],
                            "item_number": item_num,
                            "sale_price": sale.replace(",", "") if sale else "",
                            "original_price": "",
                            "promo_start": "",
                            "promo_end": "",
                            "source": "costco.ca/coupon-book",
                            "link": flyer_url,
                        })
                print(f"    Page {i}: {len(items)} items")
            except Exception as e:
                print(f"    Page {i} parse failed: {e}")

    except Exception as e:
        print(f"Coupon book scrape failed: {e}")
    return deals


def _scrape_coco_site(base_url: str, source_name: str, link_pattern: str) -> list:
    """Shared scraper for CocoWest/CocoEast (same format)."""
    deals = []
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        r = requests.get(base_url, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        post_url = None
        for a in soup.select(f'a[href*="{link_pattern}"]'):
            href = a.get("href", "")
            if len(a.get_text(strip=True)) > 20 and "/category/" not in href:
                post_url = href
                break
        if not post_url:
            return deals

        r = requests.get(post_url, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        content = soup.select_one(".entry-content")
        if not content:
            return deals

        for line in content.get_text().split("\n"):
            line = line.strip()
            m = re.match(r"^(\d{5,8})\s+(.+)", line)
            if not m:
                continue
            item_num = m.group(1)
            rest = m.group(2)
            prices = re.findall(r"\$([\d,]+\.?\d*)", rest)
            if not prices:
                continue

            sale_price = prices[-1].replace(",", "")
            expiry_m = re.search(r"EXPIRES ON (\d{4}-\d{2}-\d{2})", rest)
            name = re.sub(r"\(.*?\)", "", rest).strip()
            name = re.sub(r"\$[\d,.]+.*", "", name).strip()

            if name and len(name) > 3:
                deals.append({
                    "item_name": name[:100],
                    "item_number": item_num,
                    "sale_price": sale_price,
                    "original_price": "",
                    "promo_start": "",
                    "promo_end": expiry_m.group(1) if expiry_m else "",
                    "source": source_name,
                    "link": post_url,
                })
    except Exception as e:
        print(f"{source_name} scrape failed: {e}")
    return deals


def _scrape_cocowest() -> list:
    return _scrape_coco_site("https://cocowest.ca/", "cocowest", "weekend-update-costco")


def _scrape_cocoeast() -> list:
    return _scrape_coco_site("https://cocoeast.ca/", "cocoeast", "costco")


def scan_price_drops(force_refresh: bool = False) -> list:
    """Scan for Costco price drops from verified working sources."""

    if not force_refresh:
        cached_count = db.get_cached_deals_count()
        if cached_count > 0:
            print(f"Using {cached_count} cached deals from today")
            return db.get_all_price_drops()

    print("Fresh scan from working sources...")

    all_deals = []
    sources = [
        ("RFD Hot Deals", _scrape_rfd_hot_deals),
        ("RFD Clearance", _scrape_rfd_clearance),
        ("Reddit r/Costco", lambda: _scrape_reddit("Costco")),
        ("Reddit r/CostcoCanada", lambda: _scrape_reddit("CostcoCanada")),
        ("Costco Coupon Book", _scrape_coupon_book),
        ("CocoWest In-Store", _scrape_cocowest),
        ("CocoEast In-Store", _scrape_cocoeast),
    ]

    for name, scraper in sources:
        try:
            deals = scraper()
            all_deals.extend(deals)
            print(f"  {name}: {len(deals)} deals")
        except Exception as e:
            print(f"  {name}: FAILED - {e}")
        time.sleep(1)  # Rate limit

    # Deduplicate by normalized name
    today = datetime.now().strftime("%Y-%m-%d")
    seen = set()
    saved = []
    for deal in all_deals:
        promo_end = deal.get("promo_end", "")
        if promo_end and promo_end < today:
            continue  # Skip expired deals
        key = (deal["item_name"].lower().strip(), promo_end)
        if key not in seen and not db.item_exists(deal["item_name"], deal["source"], promo_end):
            seen.add(key)
            saved.append(db.put_price_drop(**deal))

    print(f"Saved {len(saved)} deals (skipped {len(all_deals) - len(saved)} duplicates)")
    return saved

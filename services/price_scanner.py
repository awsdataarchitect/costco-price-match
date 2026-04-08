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


def _scrape_costcoinsider() -> list:
    """Scrape CostcoInsider.com coupon book page for US deals.

    Uses the predictable monthly slug pattern /costco-{month}-{year}-coupon-book/
    which has a plain-text bullet list of deals at the bottom of the page.
    Falls back to the WordPress JSON API to find the most recent coupon book post.
    """
    deals = []
    headers = {"User-Agent": random.choice(USER_AGENTS)}

    def _try_url(url):
        try:
            r = requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            return r.text
        except Exception:
            return None

    # Build candidate URLs from current + previous month
    now = datetime.now()
    candidate_urls = []
    for delta_days in [0, -1]:
        if delta_days == 0:
            d = now
        else:
            from datetime import timedelta
            first = now.replace(day=1)
            d = first - timedelta(days=1)  # last day of previous month
        candidate_urls.append(
            f"https://www.costcoinsider.com/costco-{d.strftime('%B').lower()}-{d.year}-coupon-book/"
        )

    post_url = None
    html = None
    for url in candidate_urls:
        html = _try_url(url)
        if html:
            post_url = url
            break

    # Fallback: WordPress JSON API — find most recent post with "coupon book" in title
    if not html:
        try:
            r = requests.get(
                "https://www.costcoinsider.com/wp-json/wp/v2/posts?per_page=5&orderby=date&order=desc",
                headers=headers, timeout=15,
            )
            r.raise_for_status()
            for post in r.json():
                title = post.get("title", {}).get("rendered", "").lower()
                if "coupon book" in title or "insider deals" in title:
                    post_url = post.get("link", "")
                    content_html = post.get("content", {}).get("rendered", "")
                    soup = BeautifulSoup(content_html, "html.parser")
                    html = soup.get_text()
                    break
        except Exception as e:
            print(f"CostcoInsider API fallback failed: {e}")

    if not html:
        print("CostcoInsider: could not find a current coupon book post")
        return deals

    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one(".entry-content") or soup.select_one("article") or soup

    # Extract expiry date
    expiry = ""
    page_text = content.get_text() if hasattr(content, "get_text") else html
    date_m = re.search(r'(?:valid|expires?|through|until|runs? through)[^\d]*(\w+ \d{1,2},?\s*\d{4})',
                       page_text, re.IGNORECASE)
    if date_m:
        try:
            expiry = datetime.strptime(date_m.group(1).replace(",", ""), "%B %d %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Parse bullet list items (the text list at the bottom of the coupon book post)
    # Patterns: "NAME $PRICE", "NAME $PRICE – $SAVINGS off = $FINAL", "NAME $SAVINGS off"
    list_items = content.select("li") if hasattr(content, "select") else []
    lines_to_parse = [li.get_text(strip=True) for li in list_items] if list_items else []

    # Also try line-by-line from text (for API-returned content)
    if not lines_to_parse:
        lines_to_parse = page_text.split("\n")

    skip_words = ["http", "costco.com", "valid", "limit", "member", "while", "expires", "click", "follow", "sign up"]
    for line in lines_to_parse:
        line = line.strip().rstrip("…")
        if not line or len(line) > 200:
            continue
        # Pattern: NAME $FINAL_PRICE – $SAVINGS off = $... (take first price as orig, last as final)
        # Or: NAME $PRICE off / NAME $SAVINGS off
        # Or: NAME $PRICE
        prices = re.findall(r'\$([\d,.]+)', line)
        if not prices:
            continue
        # Name is everything before the first $
        name = line.split("$")[0].strip().rstrip(" –-=•")
        if any(s in name.lower() for s in skip_words) or len(name) < 4:
            continue
        # Determine sale_price: use the last price mentioned (after "= $") or the only price
        sale = prices[-1].replace(",", "")
        orig = prices[0].replace(",", "") if len(prices) > 1 else ""
        if 4 < len(name) < 100:
            deals.append({
                "item_name": name[:100],
                "item_number": "",
                "sale_price": sale,
                "original_price": orig,
                "promo_start": "",
                "promo_end": expiry,
                "source": "costcoinsider.com",
                "link": post_url or "",
            })

    return deals


def _scrape_hip2save() -> list:
    """Scrape Hip2Save for Costco US deals via WordPress JSON API."""
    deals = []
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        # Tag ID 291386168 = "Costco" tag on hip2save
        r = requests.get(
            "https://hip2save.com/wp-json/wp/v2/posts?per_page=20&tags=291386168&orderby=date&order=desc",
            headers=headers,
            timeout=15,
        )
        r.raise_for_status()
        posts = r.json()
        for post in posts:
            title = BeautifulSoup(post.get("title", {}).get("rendered", ""), "html.parser").get_text(strip=True)
            href = post.get("link", "")
            if not title or len(title) < 10:
                continue
            prices = re.findall(r'\$([\d,]+\.?\d*)', title)
            if not prices:
                continue
            name = re.split(r'\s+(?:for only|for|–|-|at|only|\$)', title, maxsplit=1)[0].strip()
            name = re.sub(r'^(Get|Score|Grab|Shop|Save on|Deal|Hot|Check out)\s+', '', name,
                          flags=re.IGNORECASE).strip()
            name = re.sub(r'\s+(?:Only|Just|Now|Sale|Deal|Shipped|at Costco[.\w]*)\s*$', '', name,
                          flags=re.IGNORECASE).strip()
            if 5 < len(name) < 80:
                sale = prices[0].replace(",", "")
                orig = prices[1].replace(",", "") if len(prices) > 1 else ""
                deals.append({
                    "item_name": name[:100],
                    "item_number": "",
                    "sale_price": sale,
                    "original_price": orig,
                    "promo_start": "",
                    "promo_end": "",
                    "source": "hip2save.com",
                    "link": href,
                })
    except Exception as e:
        print(f"Hip2Save scrape failed: {e}")
    return deals


def _scrape_slickdeals() -> list:
    """Scrape Slickdeals for Costco deals via RSS feed (more reliable than HTML scraping)."""
    import xml.etree.ElementTree as ET
    deals = []
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        r = requests.get(
            "https://slickdeals.net/newsearch.php?q=costco&searcharea=deals&searchin=first_word&sortby=newest&pp=20&rss=1",
            headers=headers,
            timeout=15,
        )
        r.raise_for_status()
        root = ET.fromstring(r.text)
        for item in root.findall(".//item")[:20]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not title:
                continue
            prices = re.findall(r'\$([\d,]+\.?\d*)', title)
            if not prices:
                continue
            # Strip trailing store/shipping info from name
            name = title.split("$")[0].strip()
            name = re.sub(
                r'\s+(?:Costco(?:\.com)?|Warehouse|Online|Shipping Included|In.Store|Members?)[^$]*$',
                '', name, flags=re.IGNORECASE,
            ).strip().rstrip(" -–|@")
            name = re.sub(r'^(Get|Score|Grab|Buy|Hot Deal:?)\s+', '', name, flags=re.IGNORECASE).strip()
            if 5 < len(name) < 100:
                deals.append({
                    "item_name": name[:100],
                    "item_number": "",
                    "sale_price": prices[0].replace(",", ""),
                    "original_price": prices[1].replace(",", "") if len(prices) > 1 else "",
                    "promo_start": "",
                    "promo_end": "",
                    "source": "slickdeals.net",
                    "link": link,
                })
    except Exception as e:
        print(f"Slickdeals scrape failed: {e}")
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

_bedrock = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-2"))


def _scrape_coupon_book_us() -> list:
    """Scrape Costco USA coupon book deals via Bedrock Nova Lite OCR.
    Fetches coupon book images from costco.com and runs them through OCR.
    This is Bedrock-dependent and will be skipped when the daily token limit is hit.
    """
    deals = []
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        r = requests.get("https://www.costco.com/current-coupon-book.html", headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        img_urls = []
        for img in soup.select("img[src*='mobilecontent.costco.com'], img[data-src*='mobilecontent.costco.com']"):
            src = img.get("src") or img.get("data-src", "")
            if src:
                img_urls.append(src)
                break
        if not img_urls:
            for script in soup.select("script"):
                text = script.string or ""
                m = re.search(r'(https://mobilecontent\.costco\.com[^\s"\']+?)-?\d*\.(?:jpg|png)', text)
                if m:
                    img_urls.append(m.group(0))
                    break

        if not img_urls:
            print("  Could not find coupon book images on costco.com")
            return deals

        base = re.sub(r'-?\d+\.(jpg|png)$', '', img_urls[0])
        ext = re.search(r'\.(jpg|png)$', img_urls[0])
        ext = ext.group(0) if ext else ".jpg"

        throttle_strikes = 0
        for i in range(1, 25):
            url = f"{base}-{i}{ext}"
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                break
            try:
                resp = _bedrock.converse(
                    modelId="us.amazon.nova-2-lite-v1:0",
                    messages=[{"role": "user", "content": [
                        {"image": {"format": ext.lstrip("."), "source": {"bytes": r.content}}},
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
                    name = item.get("name", "").strip()
                    item_num = item.get("item_number", "").strip()
                    if name and sale:
                        deals.append({
                            "item_name": name[:100],
                            "item_number": item_num,
                            "sale_price": sale.replace(",", ""),
                            "original_price": "",
                            "promo_start": "",
                            "promo_end": "",
                            "source": "costco.com/coupon-book",
                            "link": "https://www.costco.com/current-coupon-book.html",
                        })
                print(f"    Page {i}: {len(items)} items")
                throttle_strikes = 0
            except Exception as e:
                if "ThrottlingException" in str(e) or "Too many tokens" in str(e):
                    throttle_strikes += 1
                    if throttle_strikes >= 3:
                        print("    Bedrock daily token limit reached — stopping coupon book scan")
                        break
                else:
                    print(f"    Page {i} parse failed: {e}")
    except Exception as e:
        print(f"Coupon book scrape failed: {e}")
    return deals


# ---------------------------------------------------------------------------
# Canadian sources (original behaviour — default when COSTCO_COUNTRY=CA)
# ---------------------------------------------------------------------------

def _scrape_rfd_hot_deals() -> list:
    """Scrape RedFlagDeals Hot Deals forum for Costco Canada deals."""
    deals = []
    try:
        resp = requests.get(
            "https://forums.redflagdeals.com/hot-deals-f9/?c=5",
            headers={"User-Agent": random.choice(USER_AGENTS)},
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

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
                            link = href if href.startswith("http") else "https://forums.redflagdeals.com" + href
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
            for line in [l.strip() for l in text.split("\n") if l.strip()]:
                if ".97" in line and "$" in line and len(line) < 200:
                    price_match = re.search(r'(.+?)\s*\$?([\d,]+\.97)', line)
                    if price_match:
                        name = re.sub(r'^[-•*\d\s]+', '', price_match.group(1)).strip(' -:')
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


def _scrape_coupon_book_ca() -> list:
    """Scrape Costco Canada coupon book from SmartCanucks and parse with Nova 2 Lite."""
    deals = []
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        r = requests.get("https://flyers.smartcanucks.ca/costco-canada", headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        flyer_url = None
        for a in soup.select("a[href*='costco']"):
            href = a.get("href", "")
            if "warehouse" in href.lower() and "business" not in href.lower() and "qc" not in href.lower():
                flyer_url = href if href.startswith("http") else "https://flyers.smartcanucks.ca" + href
                break
        if not flyer_url:
            for a in soup.select("a[href*='costco']"):
                href = a.get("href", "")
                if "warehouse" in href.lower() and "business" not in href.lower():
                    flyer_url = href if href.startswith("http") else "https://flyers.smartcanucks.ca" + href
                    break
        if not flyer_url:
            print("  No Costco flyer found on SmartCanucks")
            return deals
        r = requests.get(flyer_url, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        img = soup.select_one("img[src*='uploads/pages']")
        if not img:
            return deals
        base = re.sub(r"-\d+\.jpg$", "", img["src"])
        throttle_strikes = 0
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
                throttle_strikes = 0
            except Exception as e:
                if "ThrottlingException" in str(e) or "Too many tokens" in str(e):
                    throttle_strikes += 1
                    print(f"    Page {i}: throttled (strike {throttle_strikes}/3) — skipping")
                    if throttle_strikes >= 3:
                        print("    Bedrock daily token limit reached — stopping coupon book scan")
                        break
                else:
                    print(f"    Page {i} parse failed: {e}")
    except Exception as e:
        print(f"Coupon book (CA) scrape failed: {e}")
    return deals


# ---------------------------------------------------------------------------
# Source registry — keyed by COSTCO_COUNTRY env var
# ---------------------------------------------------------------------------

_SOURCES_BY_COUNTRY = {
    "US": [
        ("CostcoInsider (Instant Savings)", _scrape_costcoinsider),
        ("Hip2Save",                        _scrape_hip2save),
        ("Slickdeals",                      _scrape_slickdeals),
        ("Reddit r/Costco",                 lambda: _scrape_reddit("Costco")),
        ("Costco USA Coupon Book",          _scrape_coupon_book_us),
    ],
    "CA": [
        ("RFD Hot Deals",          _scrape_rfd_hot_deals),
        ("RFD Clearance",          _scrape_rfd_clearance),
        ("Reddit r/Costco",        lambda: _scrape_reddit("Costco")),
        ("Reddit r/CostcoCanada",  lambda: _scrape_reddit("CostcoCanada")),
        ("Costco CA Coupon Book",  _scrape_coupon_book_ca),
        ("CocoWest In-Store",      _scrape_cocowest),
        ("CocoEast In-Store",      _scrape_cocoeast),
    ],
}


def scan_price_drops(force_refresh: bool = False) -> list:
    """Scan for Costco price drops. Set COSTCO_COUNTRY=US or CA (default CA)."""

    if not force_refresh:
        cached_count = db.get_cached_deals_count()
        if cached_count > 0:
            print(f"Using {cached_count} cached deals from today")
            return db.get_all_price_drops()

    country = os.environ.get("COSTCO_COUNTRY", "CA").upper()
    sources = _SOURCES_BY_COUNTRY.get(country, _SOURCES_BY_COUNTRY["CA"])
    print(f"Fresh scan from {country} sources...")

    all_deals = []
    for name, scraper in sources:
        try:
            deals = scraper()
            all_deals.extend(deals)
            print(f"  {name}: {len(deals)} deals")
        except Exception as e:
            print(f"  {name}: FAILED - {e}")
        time.sleep(1)

    today = datetime.now().strftime("%Y-%m-%d")
    seen = set()
    saved = []
    for deal in all_deals:
        promo_end = deal.get("promo_end", "")
        if promo_end and promo_end < today:
            continue
        key = (deal["item_name"].lower().strip(), promo_end)
        if key not in seen and not db.item_exists(deal["item_name"], deal["source"], promo_end):
            seen.add(key)
            saved.append(db.put_price_drop(**deal))

    print(f"Saved {len(saved)} deals (skipped {len(all_deals) - len(saved)} duplicates)")
    return saved

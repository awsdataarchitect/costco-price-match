import requests
from bs4 import BeautifulSoup
import re
import random
import time
from datetime import datetime
import json
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


def _scrape_reddit_deals() -> list:
    """Scrape r/Costco for deal/clearance/markdown posts (broader than $ search)."""
    deals = []
    try:
        for query in ["clearance", "markdown", "price drop", "instant savings"]:
            resp = requests.get(
                f"https://www.reddit.com/r/Costco/search.json?q={query}&restrict_sr=on&sort=new&t=month&limit=10",
                headers={"User-Agent": "CostcoScanner/1.0"},
                timeout=15,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            for post in data.get("data", {}).get("children", []):
                post_data = post["data"]
                title = post_data["title"]
                permalink = post_data.get("permalink", "")
                if any(skip in title.lower() for skip in ["megathread", "how costco gets you"]):
                    continue
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
                            "source": "reddit.com/r/Costco",
                            "link": f"https://www.reddit.com{permalink}" if permalink else "",
                        })
            time.sleep(0.5)
    except Exception as e:
        print(f"Reddit deals search failed: {e}")
    return deals


def _scrape_kcl_deals() -> list:
    """Scrape The Krazy Coupon Lady Costco deals listing page."""
    deals = []
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        resp = requests.get(
            "https://thekrazycouponlady.com/coupons-for/costco",
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Find deal links - they contain dates and prices in URL slugs
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            # Match deal article URLs (e.g. /2026/03/26/product-name-usd37-99-at-costco)
            if not re.search(r'/\d{4}/\d{2}/\d{2}/', href):
                continue

            title = ""
            # Get title from heading inside the link, or from the link text
            h = a.find(["h2", "h3", "h4"])
            if h:
                title = h.get_text(strip=True)
            elif len(a.get_text(strip=True)) > 20:
                title = a.get_text(strip=True)

            if not title or "$" not in title:
                continue

            # Skip non-deal articles
            if any(skip in title.lower() for skip in ["coupon book", "membership", "return policy", "best time"]):
                continue

            prices = re.findall(r'\$([\d,]+\.?\d*)', title)
            if not prices:
                continue

            # Extract product name (before the first price mention)
            name_part = re.split(r'(?:,\s*(?:Only|Now|Just))?\s*\$', title)[0].strip()
            name_part = re.sub(r'^New\s+(?:at\s+)?Costco[:\s]*', '', name_part, flags=re.IGNORECASE).strip()
            name_part = name_part.rstrip(" -–|:,")

            if 5 < len(name_part) < 100:
                # Look for original/regular price pattern
                orig = ""
                reg_match = re.search(r'(?:reg\.?|was|orig\.?)\s*\$?([\d,]+\.?\d*)', title, re.IGNORECASE)
                if reg_match:
                    orig = reg_match.group(1).replace(",", "")
                elif len(prices) > 1:
                    orig = prices[1].replace(",", "")

                link = href
                if link.startswith("/"):
                    link = "https://thekrazycouponlady.com" + link

                deals.append({
                    "item_name": name_part[:100],
                    "sale_price": prices[0].replace(",", ""),
                    "original_price": orig,
                    "promo_start": "",
                    "promo_end": "",
                    "source": "thekrazycouponlady.com",
                    "link": link,
                })
    except Exception as e:
        print(f"KCL deals failed: {e}")
    return deals


def _scrape_kcl_coupon_book() -> list:
    """Scrape The Krazy Coupon Lady Costco coupon book (structured deal data)."""
    deals = []
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        resp = requests.get(
            "https://thekrazycouponlady.com/tips/couponing/costco-coupon-book",
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Try JSON-LD structured data first (most reliable)
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                ld = json.loads(script.string)
                offers = []
                if isinstance(ld, dict):
                    # Handle Product type with nested offers
                    if ld.get("@type") == "Product" and "offers" in ld:
                        off = ld["offers"]
                        if isinstance(off, dict):
                            offers = off.get("offers", [])
                            if not offers and off.get("price"):
                                offers = [off]
                        elif isinstance(off, list):
                            offers = off
                    elif ld.get("@type") == "AggregateOffer":
                        offers = ld.get("offers", [])
                    elif "offers" in ld:
                        off = ld["offers"]
                        if isinstance(off, dict) and off.get("@type") == "AggregateOffer":
                            offers = off.get("offers", [])
                        elif isinstance(off, list):
                            offers = off

                for offer in offers:
                    price = offer.get("price", "")
                    valid_until = offer.get("priceValidUntil", "")
                    name = offer.get("name", "")
                    url = offer.get("url", "")

                    # Extract name from URL slug if not on the offer directly
                    # e.g. ".../hellmanns-big-squeeze-real-mayonnaise-25-fl-oz-2-count/100634569"
                    if not name and url:
                        slug_match = re.search(r'/([a-z0-9-]+)/\d+$', url)
                        if slug_match:
                            name = slug_match.group(1).replace("-", " ").title()

                    if price and name:
                        promo_end = ""
                        if valid_until:
                            promo_end = valid_until[:10]  # "2026-05-04T..." -> "2026-05-04"

                        deals.append({
                            "item_name": name[:100],
                            "sale_price": str(price).replace(",", ""),
                            "original_price": "",
                            "promo_start": "",
                            "promo_end": promo_end,
                            "source": "costco-coupon-book",
                            "link": url or "https://thekrazycouponlady.com/tips/couponing/costco-coupon-book",
                        })
            except (json.JSONDecodeError, TypeError):
                continue

        # Fallback: parse page text for deal patterns if JSON-LD didn't yield results
        if not deals:
            text = soup.get_text()
            # Match patterns like "Product Name ... $12.99 ... reg $16.99 ... Exp 05/03/26"
            for line in text.split("\n"):
                line = line.strip()
                if "$" not in line or len(line) < 10 or len(line) > 300:
                    continue

                prices = re.findall(r'\$([\d,]+\.?\d*)', line)
                if not prices:
                    continue

                # Try to extract name before the first price
                name = line.split("$")[0].strip()
                name = re.sub(r'^[\d\.\s\-•*]+', '', name).strip()

                skip_words = ["buy", "save", "limit", "exp", "valid", "through", "offer", "see"]
                if not name or len(name) < 5 or any(name.lower().startswith(w) for w in skip_words):
                    continue

                # Look for expiry
                exp_match = re.search(r'(?:Exp|expires?)\s*(\d{1,2}/\d{1,2}/\d{2,4})', line, re.IGNORECASE)
                promo_end = ""
                if exp_match:
                    try:
                        for fmt in ("%m/%d/%y", "%m/%d/%Y"):
                            try:
                                dt = datetime.strptime(exp_match.group(1), fmt)
                                promo_end = dt.strftime("%Y-%m-%d")
                                break
                            except ValueError:
                                continue
                    except Exception:
                        pass

                orig = ""
                reg_match = re.search(r'(?:reg\.?|was)\s*\$?([\d,]+\.?\d*)', line, re.IGNORECASE)
                if reg_match:
                    orig = reg_match.group(1).replace(",", "")

                deals.append({
                    "item_name": name[:100],
                    "sale_price": prices[0].replace(",", ""),
                    "original_price": orig,
                    "promo_start": "",
                    "promo_end": promo_end,
                    "source": "costco-coupon-book",
                    "link": "https://thekrazycouponlady.com/tips/couponing/costco-coupon-book",
                })

    except Exception as e:
        print(f"KCL coupon book failed: {e}")
    return deals


def _scrape_costcofan() -> list:
    """Scrape CostcoFan.com — fetch recent article pages and extract prices from body text."""
    deals = []
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        resp = requests.get("https://costcofan.com/", headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Collect article URLs and titles from homepage
        # Structure: <h2><a href="...">Title</a></h2>
        articles = []
        seen_urls = set()
        for h in soup.find_all(["h2", "h3"]):
            a = h.find("a", href=True)
            if not a:
                continue
            href = a.get("href", "")
            if any(skip in href for skip in ["/category/", "/tag/", "/page/", "/author/"]):
                continue
            title = a.get_text(strip=True)
            link = href if href.startswith("http") else "https://costcofan.com" + href
            if title and len(title) > 10 and link not in seen_urls:
                seen_urls.add(link)
                articles.append((title, link))
            if len(articles) >= 8:
                break

        # Fetch each article and extract prices from body
        for title, link in articles:
            try:
                r = requests.get(link, headers=headers, timeout=10)
                r.raise_for_status()
                page = BeautifulSoup(r.text, "html.parser")
                content = page.select_one(".entry-content")
                if not content:
                    continue
                text = content.get_text()
                # Look for price patterns like "$12.99" or "costs $24.99"
                price_match = re.search(r'(?:costs?|priced?\s+at|for|only|sale)\s+\$(\d+\.?\d*)', text, re.IGNORECASE)
                if not price_match:
                    price_match = re.search(r'\$(\d+\.\d{2})', text)
                if price_match:
                    price = price_match.group(1)
                    name = re.sub(r'^(?:Costco|New\s+at\s+Costco)[:\s]*', '', title, flags=re.IGNORECASE).strip()
                    name = name.rstrip(" -–|:,")
                    if 5 < len(name) < 100:
                        deals.append({
                            "item_name": name[:100],
                            "sale_price": price,
                            "original_price": "",
                            "promo_start": "",
                            "promo_end": "",
                            "source": "costcofan.com",
                            "link": link,
                        })
            except Exception:
                continue
            time.sleep(0.5)
    except Exception as e:
        print(f"CostcoFan failed: {e}")
    return deals


def scan_price_drops(force_refresh: bool = False) -> tuple:
    """Scan for Costco price drops from US deal sources.

    Returns:
        tuple: (saved_deals, source_results) where source_results is a list of
        dicts with keys: name, count, status, duration_s, error
    """

    if not force_refresh:
        cached_count = db.get_cached_deals_count()
        if cached_count > 0:
            print(f"Using {cached_count} cached deals from today")
            return db.get_all_price_drops(), [{
                "name": "cache",
                "count": cached_count,
                "status": "cached",
                "duration_s": 0,
                "error": None,
            }]

    print("Fresh scan from US sources...")

    all_deals = []
    source_results = []
    sources = [
        ("Reddit r/Costco", lambda: _scrape_reddit("Costco")),
        ("Reddit r/CostcoDeals", lambda: _scrape_reddit("CostcoDeals")),
        ("Reddit r/Costco (deals)", lambda: _scrape_reddit_deals()),
        ("KCL Costco Deals", _scrape_kcl_deals),
        ("KCL Coupon Book", _scrape_kcl_coupon_book),
        ("CostcoFan", _scrape_costcofan),
    ]

    for name, scraper in sources:
        t0 = time.time()
        try:
            deals = scraper()
            elapsed = round(time.time() - t0, 1)
            all_deals.extend(deals)
            source_results.append({
                "name": name,
                "count": len(deals),
                "status": "ok" if deals else "empty",
                "duration_s": elapsed,
                "error": None,
            })
            print(f"  {name}: {len(deals)} deals ({elapsed}s)")
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            source_results.append({
                "name": name,
                "count": 0,
                "status": "error",
                "duration_s": elapsed,
                "error": str(e),
            })
            print(f"  {name}: FAILED ({elapsed}s) - {e}")
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
    return saved, source_results

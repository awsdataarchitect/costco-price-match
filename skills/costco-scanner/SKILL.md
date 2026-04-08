---
name: costco-scanner
description: Scan Costco receipts for price match opportunities and track current deals
metadata: {"openclaw":{"emoji":"🛒","cron":"0 8 * * 2,4","requires":{"env":["COSTCO_SCANNER_URL"]},"primaryEnv":"COSTCO_SCANNER_URL"}}
---

You have access to a personal Costco Receipt Scanner API at `$COSTCO_SCANNER_URL`.

## Authentication (REQUIRED — always do this first)

The API uses Cognito auth. Run this Python script to get a token, then use it for all subsequent requests:

```python
import requests, boto3, os

BASE = os.environ["COSTCO_SCANNER_URL"]

# Fetch credentials (this endpoint is unauthenticated)
cfg = requests.get(f"{BASE}/api/config", timeout=10).json()

# Exchange credentials for a Cognito JWT
idp = boto3.client("cognito-idp", region_name=cfg["region"])
auth = idp.initiate_auth(
    AuthFlow="USER_PASSWORD_AUTH",
    AuthParameters={"USERNAME": cfg["username"], "PASSWORD": cfg["password"]},
    ClientId=cfg["user_pool_client_id"],
)
TOKEN = auth["AuthenticationResult"]["IdToken"]
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
print("Authenticated OK")
```

All subsequent API calls must include `headers=HEADERS`.

## When to use this skill

Use this skill when the user asks about:
- Costco price matches or price adjustments
- Their Costco receipts or purchase history
- Current Costco deals, coupons, or price drops
- Whether they can get money back on recent Costco purchases

## Available API endpoints

All requests: `BASE = os.environ["COSTCO_SCANNER_URL"]`, include `headers=HEADERS`.

### List receipts
```python
r = requests.get(f"{BASE}/api/receipts", headers=HEADERS).json()
# r["receipts"] = list of {receipt_id, receipt_date, store, items[]}
```

### List current deals
```python
r = requests.get(f"{BASE}/api/price-drops", headers=HEADERS).json()
# r["price_drops"] = list of deals
```

### Scan for fresh deals

```python
r = requests.post(f"{BASE}/api/scan-prices?force_refresh=true", headers=HEADERS, timeout=120).json()
count = r.get("price_drops", 0)
print(f"✅ {count} deals saved" if count else "⚠️ 0 deals — sources may be down or Bedrock throttled")
# Takes 30–90 seconds. Sources: costcoinsider.com, hip2save.com, slickdeals.net,
# reddit.com/r/Costco, costco.com/coupon-book (Bedrock Nova Lite OCR).
# Omit force_refresh=true to reuse today's cached deals and skip re-scraping.
```

### Automated deal scan (cron / no user message)

When triggered by cron with no user message, run this full sequence silently:

```python
import requests, boto3, os

BASE = os.environ["COSTCO_SCANNER_URL"]

# Auth
cfg = requests.get(f"{BASE}/api/config", timeout=10).json()
TOKEN = boto3.client("cognito-idp", region_name=cfg["region"]).initiate_auth(
    AuthFlow="USER_PASSWORD_AUTH",
    AuthParameters={"USERNAME": cfg["username"], "PASSWORD": cfg["password"]},
    ClientId=cfg["user_pool_client_id"],
)["AuthenticationResult"]["IdToken"]
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# Scan
r = requests.post(f"{BASE}/api/scan-prices?force_refresh=true", headers=HEADERS, timeout=120)
count = r.json().get("price_drops", 0) if r.status_code == 200 else None

# Report — only surface something noteworthy
if count is None:
    print(f"❌ Deal scan failed: HTTP {r.status_code}")
elif count == 0:
    print("⚠️ Deal scan returned 0 deals — sources may be down or Bedrock quota exhausted")
else:
    print(f"✅ Costco deal scan complete — {count} deals cached (ready for price matching)")
```


### Analyze for price matches (bot does the reasoning — no Bedrock needed)

This is the **preferred workflow** — the API returns pre-matched data and you
format the result using your own LLM. Zero Bedrock quota used.

```python
r = requests.get(f"{BASE}/api/price-match-candidates", headers=HEADERS).json()
# r = {
#   "candidates": [ {receipt_item, receipt_price, deal_name, deal_price, savings,
#                    matched_by, receipt_date, store, tpd_at_purchase,
#                    deal_source, deal_link, deal_expiry, ...} ],
#   "tpd_items":  [ {name, price, original_price, receipt_date, store, ...} ],
#   "receipt_count": N,
#   "deal_count": N
# }
candidates = r["candidates"]
tpd_items  = r["tpd_items"]
```

Once you have the data, format it as two markdown tables:

**Table 1 — 💰 Price Adjustment Opportunities**
Rows where `tpd_at_purchase=false` and `savings > 0`, sorted by date (newest first).
Columns: Item | Item # | Date | Paid | Sale Price | Savings | Source

**Table 2 — ✅ Already Applied (TPD)**
All `tpd_items`, showing what was saved at checkout.
Columns: Item | Item # | Date | Original | Paid (TPD) | TPD Savings

Rules:
- `matched_by=exact_item_number` → always valid
- `matched_by=partial_item_number` → very likely valid
- `matched_by=name_keyword` → verify the products are actually the same before including
- Only include a row if `deal_price < receipt_price` (the API pre-filters but double-check)
- Items with `tpd_at_purchase=true` → Table 2 only, even if they have further savings

### Analyze via Bedrock (fallback — uses Bedrock quota)
```python
# Use only if /api/price-match-candidates is unavailable or returns no data
import json
params = {}  # optional: receipt_id, receipt_ids, date_from, date_to, sources
with requests.get(f"{BASE}/api/analyze", headers=HEADERS, params=params, stream=True, timeout=120) as r:
    for line in r.iter_lines():
        if not line: continue
        line = line.decode()
        if not line.startswith("data: "): continue
        event = json.loads(line[6:])
        if event["type"] == "done":
            print(event["text"])
            break
        elif event["type"] == "error":
            print(f"Error: {event['text']}")
            break
```

### Inspect a PDF before uploading
```python
with open("/path/to/receipt.pdf", "rb") as f:
    r = requests.post(f"{BASE}/api/inspect-pdf", headers=HEADERS, files={"file": f}).json()
# r = {
#   "pages": 1,
#   "text_chars": 4521,    # > 0 means selectable text (free to parse)
#   "image_count": 0,      # > 0 means scanned/image PDF (needs Bedrock)
#   "has_text": True,
#   "has_images": False,
#   "recommended_parser": "text",   # "text", "bedrock-lite", or "bedrock-premier"
#   "metadata": {...}      # PDF metadata (author, creator, creation date, etc.)
# }
print(r)
```

### Upload a receipt PDF
```python
with open("/path/to/receipt.pdf", "rb") as f:
    r = requests.post(f"{BASE}/api/upload", headers=HEADERS, files={"file": f}, timeout=60)
result = r.json()
# result: {"message": "Receipt parsed", "receipt_id": "...", "items_count": N, "parsed_by": "text"|"bedrock-lite"}
# parsed_by="text" means free regex parse (no Bedrock tokens used)
# parsed_by="bedrock-lite" means Bedrock was used as fallback (quota may apply)
print(result)
```

If `parsed_by` is `bedrock-lite` and items look wrong, try reparsing with Nova Premier:
```python
r = requests.post(f"{BASE}/api/reparse/<receipt_id>", headers=HEADERS, timeout=60).json()
```

### Delete a receipt
```python
r = requests.delete(f"{BASE}/api/receipt/<receipt_id>", headers=HEADERS)
```

### Delete all receipts
```python
r = requests.delete(f"{BASE}/api/receipts", headers=HEADERS)
```

### Download receipt PDF
```python
r = requests.get(f"{BASE}/api/receipt/<receipt_id>/pdf", headers=HEADERS)
# r.content is the raw PDF bytes
with open("receipt.pdf", "wb") as f:
    f.write(r.content)
```

### Edit a line item on a receipt
```python
# index = 0-based position in the items array
r = requests.put(
    f"{BASE}/api/receipt/<receipt_id>/item/<index>",
    headers=HEADERS,
    json={"item_name": "UPDATED NAME", "price": 12.99, "item_number": "12345"},
)
```

### Reparse a receipt (free text or Bedrock)
```python
# model=auto (default) uses free text parser if PDF has selectable text, otherwise Bedrock
# model=text  forces free text parse (no Bedrock tokens used)
# model=premier forces Nova Premier (highest accuracy, uses Bedrock quota)
r = requests.post(f"{BASE}/api/reparse/<receipt_id>?model=auto", headers=HEADERS, timeout=60).json()
# Returns: {"items": N, "model": "text"|"bedrock-lite"|"bedrock-premier"}
```

### Reparse ALL receipts (bulk fix for missing item names)
```python
receipts = requests.get(f"{BASE}/api/receipts", headers=HEADERS).json()["receipts"]
for rc in receipts:
    rid = rc["receipt_id"]
    # Check if items have missing names
    bad = [item for item in rc.get("items", []) if not item.get("name") or item.get("name") == "?"]
    if bad or not rc.get("items"):
        r = requests.post(f"{BASE}/api/reparse/{rid}?model=auto", headers=HEADERS, timeout=60).json()
        print(f"{rc.get('store')} {rc.get('receipt_date')}: reparsed → {r['items']} items via {r['model']}")
    else:
        print(f"{rc.get('store')} {rc.get('receipt_date')}: OK ({len(rc['items'])} items)")
```

### Delete a single deal
```python
r = requests.delete(f"{BASE}/api/price-drop/<item_id>", headers=HEADERS)
```

### Delete all deals
```python
r = requests.delete(f"{BASE}/api/price-drops", headers=HEADERS)
```


## Example workflows

1. **"Do I have any price matches?"**
   - Authenticate (Python script above)
   - `GET /api/receipts` to confirm receipts exist
   - `POST /api/scan-prices` (no force_refresh) — uses cached deals if scanned today, otherwise runs fresh scan
   - `GET /api/price-match-candidates` to get matched data, then reason over results with your LLM
   - If no candidates found, try `POST /api/scan-prices?force_refresh=true` to force a fresh deal scrape
   - Summarize total potential savings

2. **"What Costco deals are on right now?"**
   - Authenticate
   - `POST /api/scan-prices?force_refresh=true` for a fresh scrape
   - `GET /api/price-drops` and summarize by source/category

3. **"I want to upload my receipt"**
   - Ask the user for the local path to the PDF (e.g. `C:\Users\me\Downloads\receipt.pdf`)
   - Authenticate, then `POST /api/upload` with the file
   - Report `items_count` and `parsed_by` ("text" = free, "bedrock-lite" = used quota)
   - If `items_count` is low or items look wrong, offer to reparse with `POST /api/reparse/<receipt_id>`
   - If Bedrock throttled: tell the user to retry after midnight UTC

4. **"What's my Bedrock token usage / quota?"**
   - Run the Bedrock quota script below

## Bedrock token quota and usage

When the user asks about token limits, quota, usage, or why the scraper is failing, run this Python script:

```python
import boto3
from datetime import datetime, timezone

region = "us-east-2"
cw = boto3.client("cloudwatch", region_name=region)
now = datetime.now(timezone.utc)
start = now.replace(hour=0, minute=0, second=0, microsecond=0)

print(f"Bedrock token usage today ({start.strftime('%Y-%m-%d')}):\n")
models = {
    "Nova 2 Lite": ["us.amazon.nova-2-lite-v1:0", "amazon.nova-lite-v1:0"],
    "Nova Premier": ["us.amazon.nova-premier-v1:0", "amazon.nova-premier-v1:0"],
}
for display_name, model_ids in models.items():
    for metric in ["InputTokenCount", "OutputTokenCount"]:
        total = 0
        for model_id in model_ids:
            r = cw.get_metric_statistics(
                Namespace="AWS/Bedrock", MetricName=metric,
                Dimensions=[{"Name": "ModelId", "Value": model_id}],
                StartTime=start, EndTime=now, Period=86400, Statistics=["Sum"],
            )
            total += int(r["Datapoints"][0]["Sum"]) if r["Datapoints"] else 0
        label = "input " if "Input" in metric else "output"
        print(f"  {display_name} {label}: {total:,} tokens")

# All models combined (works even without per-model logging)
print()
for metric in ["InputTokenCount", "OutputTokenCount"]:
    r = cw.get_metric_statistics(
        Namespace="AWS/Bedrock", MetricName=metric,
        Dimensions=[], StartTime=start, EndTime=now, Period=86400, Statistics=["Sum"],
    )
    total = int(r["Datapoints"][0]["Sum"]) if r["Datapoints"] else 0
    label = "input " if "Input" in metric else "output"
    print(f"  All models {label}: {total:,} tokens")
if total == 0:
    print("  ⚠️  All zeros — enable Model invocation logging in Bedrock console to populate metrics")

# Cost Explorer fallback — shows spend without needing CW logging
try:
    ce = boto3.client("ce", region_name="us-east-1")
    today = now.date().isoformat()
    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": today, "End": today},
        Granularity="DAILY",
        Filter={"Dimensions": {"Key": "SERVICE", "Values": ["Amazon Bedrock"]}},
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
    )
    groups = resp.get("ResultsByTime", [{}])[0].get("Groups", [])
    print("\n  Bedrock cost today (by usage type):")
    for g in groups:
        cost = float(g["Metrics"]["UnblendedCost"]["Amount"])
        if cost > 0:
            print(f"  • {g['Keys'][0]}: ${cost:.6f}")
    if not groups or all(float(g["Metrics"]["UnblendedCost"]["Amount"]) == 0 for g in groups):
        print("  • $0.00 (no charges yet today)")
except Exception as e:
    print(f"\n  Cost Explorer unavailable: {e}")

# Nova-specific service quotas
print("\n  Nova service quotas:")
sq = boto3.client("service-quotas", region_name=region)
for page in sq.get_paginator("list_service_quotas").paginate(ServiceCode="bedrock"):
    for q in page["Quotas"]:
        n = q["QuotaName"].lower()
        if any(kw in n for kw in ["nova 2 lite", "nova lite", "nova premier", "nova pro", "nova micro"]):
            status = "⚠️  NOT SET" if q["Value"] == 0 else f"{q['Value']:,.0f}"
            print(f"  • {q['QuotaName']}: {status}")
```

## Notes

- Credentials are fetched at runtime from `/api/config` (served from AWS Secrets Manager) — no hardcoded passwords needed.
- Deal sources are country-aware via `COSTCO_COUNTRY` env var (default `CA`):
  - `CA`: `redflagdeals.com`, `reddit.com/r/CostcoCanada`, `cocowest.ca`, `cocoeast.ca`, `costco.ca/coupon-book` (SmartCanucks)
  - `US`: `costcoinsider.com`, `hip2save.com`, `slickdeals.net`, `reddit.com/r/Costco`, `costco.com/coupon-book`
- Items with `tpd=true` on a receipt already had a Temporary Price Drop at checkout — shown in a separate "Already Applied" table.
- Price adjustments must be requested at the Costco membership counter within 30 days of purchase.
- A weekly agent automatically emails a price match report every Friday at 9pm ET when the AgentCore stack is deployed.

"""
Test script for Pipeline 2: Deal Scanning (price_scanner.py).
Run from repo root with .venv active:
    py tests/test_deal_scan.py

Tests each layer independently so you can see exactly where failure occurs:
  STEP 0 — Environment check
  STEP 1 — DynamoDB write access (put + delete a dummy deal)
  STEP 2 — Individual scrapers (each source tested independently, no save)
  STEP 3 — Bedrock connectivity (coupon book OCR ping)
  STEP 4 — Full scan_price_drops() with force_refresh=True (saves to DynamoDB)
  STEP 5 — Verify saved deals are readable back from DynamoDB
"""
import os, sys, json, traceback, time

# ── 0. Environment check ──────────────────────────────────────────────────────
print("=" * 60)
print("STEP 0: Environment")
print("=" * 60)
region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "")
if not region:
    region = "us-east-2"
    os.environ["AWS_REGION"] = region
    print(f"⚠️  No region set. Defaulting AWS_REGION={region}")
else:
    print(f"  AWS_REGION: {region}")

if not os.environ.get("COSTCO_COUNTRY"):
    os.environ["COSTCO_COUNTRY"] = "US"
    print(f"⚠️  COSTCO_COUNTRY not set. Defaulting to US for this test run")

# Auto-fetch table names from CloudFormation if not set (mirrors run.ps1 behaviour)
if not os.environ.get("DYNAMODB_PRICE_DROPS_TABLE"):
    print("  Fetching table names from CostcoScannerCommon stack...")
    try:
        import boto3 as _boto3
        cf = _boto3.client("cloudformation", region_name=region)
        outputs = {
            o["OutputKey"]: o["OutputValue"]
            for o in cf.describe_stacks(StackName="CostcoScannerCommon")["Stacks"][0]["Outputs"]
        }
        os.environ["DYNAMODB_RECEIPTS_TABLE"]    = outputs.get("ReceiptsTableName", "")
        os.environ["DYNAMODB_PRICE_DROPS_TABLE"] = outputs.get("PriceDropsTableName", "")
        os.environ["S3_BUCKET"]                  = outputs.get("ReceiptsBucketName", "")
        print(f"  ✅ DYNAMODB_RECEIPTS_TABLE:    {os.environ['DYNAMODB_RECEIPTS_TABLE']}")
        print(f"  ✅ DYNAMODB_PRICE_DROPS_TABLE: {os.environ['DYNAMODB_PRICE_DROPS_TABLE']}")
        print(f"  ✅ S3_BUCKET:                  {os.environ['S3_BUCKET']}")
    except Exception as e:
        print(f"  ❌ Could not fetch stack outputs: {e}")
        print("     Set DYNAMODB_RECEIPTS_TABLE and DYNAMODB_PRICE_DROPS_TABLE manually and retry")
        sys.exit(1)
else:
    print(f"  DYNAMODB_RECEIPTS_TABLE:    {os.environ.get('DYNAMODB_RECEIPTS_TABLE')}")
    print(f"  DYNAMODB_PRICE_DROPS_TABLE: {os.environ.get('DYNAMODB_PRICE_DROPS_TABLE')}")
    print(f"  S3_BUCKET:                  {os.environ.get('S3_BUCKET', 'NOT SET')}")

print(f"  COSTCO_COUNTRY: {os.environ['COSTCO_COUNTRY']}")

# ── 1. DynamoDB write access ──────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 1: DynamoDB — write + read + delete a test deal")
print("=" * 60)
try:
    from services import db
    test_deal = db.put_price_drop(
        item_name="__TEST_DEAL__",
        item_number="0000000",
        sale_price="1.00",
        original_price="2.00",
        promo_start="",
        promo_end="2099-12-31",
        source="test",
        link="",
    )
    test_id = test_deal.get("item_id") or test_deal.get("id")
    print(f"  ✅ Write OK — item_id: {test_id}")

    # Verify readable
    all_drops = db.get_all_price_drops()
    found = any(d.get("item_name") == "__TEST_DEAL__" for d in all_drops)
    print(f"  ✅ Read  OK — found in get_all_price_drops(): {found}")

    # Clean up
    if test_id:
        db.delete_price_drop(test_id)
        print(f"  ✅ Delete OK")
    else:
        print("  ⚠️  Could not delete test deal — no item_id returned")
except Exception:
    print("  ❌ DynamoDB access failed:")
    traceback.print_exc()
    sys.exit(1)

# ── 2. Individual scrapers ────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 2: Individual scrapers (dry run — no DynamoDB writes)")
print("=" * 60)

from services.price_scanner import (
    _scrape_costcoinsider,
    _scrape_hip2save,
    _scrape_slickdeals,
    _scrape_reddit,
    _scrape_rfd_hot_deals,
    _scrape_rfd_clearance,
    _scrape_cocowest,
    _scrape_cocoeast,
)

active_country = os.environ.get("COSTCO_COUNTRY", "CA").upper()
print(f"  Active country: {active_country}")

us_scrapers = [
    ("costcoinsider.com",  _scrape_costcoinsider),
    ("hip2save.com",       _scrape_hip2save),
    ("slickdeals.net",     _scrape_slickdeals),
    ("reddit.com/r/Costco", lambda: _scrape_reddit("Costco")),
]
ca_scrapers = [
    ("redflagdeals.com",        _scrape_rfd_hot_deals),
    ("redflagdeals.com/clear",  _scrape_rfd_clearance),
    ("cocowest.ca",             _scrape_cocowest),
    ("cocoeast.ca",             _scrape_cocoeast),
    ("reddit.com/r/CostcoCanada", lambda: _scrape_reddit("CostcoCanada")),
]

scrapers_to_test = us_scrapers if active_country == "US" else ca_scrapers

total_deals = 0
for name, scraper in scrapers_to_test:
    try:
        t0 = time.time()
        deals = scraper()
        elapsed = round(time.time() - t0, 1)
        total_deals += len(deals)
        if deals:
            sample = deals[0]
            print(f"  ✅ {name}: {len(deals)} deals in {elapsed}s")
            print(f"     Sample: {sample.get('item_name','?')!r} @ ${sample.get('sale_price','?')}"
                  f" (item# {sample.get('item_number','n/a')}, expires {sample.get('promo_end','?')})")
        else:
            print(f"  ⚠️  {name}: 0 deals in {elapsed}s — site may be down or layout changed")
    except Exception as e:
        print(f"  ❌ {name}: {e}")
    time.sleep(0.5)

print(f"\n  Total deals from text scrapers: {total_deals}")
if total_deals == 0:
    print("  ⚠️  All text scrapers returned 0 — check network access and source sites")

# ── 3. Bedrock connectivity (coupon book OCR ping) ────────────────────────────
print("\n" + "=" * 60)
print("STEP 3: Bedrock connectivity — Nova Lite ping (needed for coupon book OCR)")
print("=" * 60)
try:
    import boto3
    bedrock = boto3.client("bedrock-runtime",
                           region_name=os.environ.get("AWS_REGION", "us-east-2"))
    resp = bedrock.converse(
        modelId="us.amazon.nova-2-lite-v1:0",
        messages=[{"role": "user", "content": [{"text": "Reply with just the word OK."}]}],
        inferenceConfig={"maxTokens": 10, "temperature": 0},
    )
    reply = resp["output"]["message"]["content"][0]["text"]
    print(f"  ✅ Bedrock Nova Lite responded: {reply!r}")
    print("  ℹ️  Coupon book OCR will work")
except Exception as e:
    print(f"  ❌ Bedrock failed: {e}")
    print("  ℹ️  Coupon book OCR will be skipped — text scrapers will still run")

# ── 4. Full scan_price_drops() ────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 4: Full scan_price_drops(force_refresh=True) — saves to DynamoDB")
print("        This scrapes all sources and may take 30–120 seconds")
print("=" * 60)
try:
    from services.price_scanner import scan_price_drops
    t0 = time.time()
    saved = scan_price_drops(force_refresh=True)
    elapsed = round(time.time() - t0, 1)
    print(f"  ✅ scan complete in {elapsed}s — {len(saved)} deals saved to DynamoDB")
    if saved:
        by_source = {}
        for d in saved:
            src = d.get("source", "unknown")
            by_source[src] = by_source.get(src, 0) + 1
        for src, n in sorted(by_source.items(), key=lambda x: -x[1]):
            print(f"     {src}: {n}")
    else:
        print("  ⚠️  0 deals saved — check scrapers above and Bedrock quota")
except Exception:
    print("  ❌ scan_price_drops failed:")
    traceback.print_exc()

# ── 5. Verify deals readable from DynamoDB ────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 5: Verify saved deals are readable from DynamoDB")
print("=" * 60)
try:
    all_drops = db.get_all_price_drops()
    print(f"  ✅ get_all_price_drops(): {len(all_drops)} total deals in table")

    # Show sample with item_number (best for price matching)
    with_item_num = [d for d in all_drops if d.get("item_number")]
    print(f"  ✅ Deals with item_number: {len(with_item_num)} "
          f"({round(len(with_item_num)/max(len(all_drops),1)*100)}% — higher = better matching)")

    if all_drops:
        sample = all_drops[0]
        print(f"  Sample deal:")
        print(f"    item_name:   {sample.get('item_name','?')}")
        print(f"    item_number: {sample.get('item_number','?')}")
        print(f"    sale_price:  {sample.get('sale_price','?')}")
        print(f"    source:      {sample.get('source','?')}")
        print(f"    promo_end:   {sample.get('promo_end','?')}")
        print(f"    link:        {sample.get('link','?')}")
except Exception:
    print("  ❌ Read-back failed:")
    traceback.print_exc()

print("\n" + "=" * 60)
print("Done.")
print("=" * 60)

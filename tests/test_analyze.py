"""
Debug script for the /api/analyze endpoint.
Run from repo root with .venv active:
    py tests/test_analyze.py

Tests each layer independently so you can see exactly where failure occurs.
"""
import os, sys, json, traceback

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

# ── 1. DB access ──────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 1: DynamoDB — fetching receipts and price drops")
print("=" * 60)
try:
    from services import db
    receipts = db.get_all_receipts()
    drops = db.get_all_price_drops()
    print(f"  ✅ Receipts:    {len(receipts)}")
    for r in receipts:
        items = r.get("items", [])
        bad = sum(1 for i in items if not i.get("name") or i.get("name") == "?")
        print(f"     {r.get('store','?')} {r.get('receipt_date','?')}: "
              f"{len(items)} items, {bad} missing names")
    print(f"  ✅ Price drops: {len(drops)}")
    if drops:
        sample = drops[0]
        print(f"     Sample: {sample.get('item_name','?')} @ ${sample.get('sale_price','?')} "
              f"from {sample.get('source','?')}")
except Exception:
    print("  ❌ DB access failed:")
    traceback.print_exc()
    sys.exit(1)

# ── 2. Pre-filter matching (no Bedrock) ───────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 2: find_potential_matches — pure Python, no Bedrock")
print("=" * 60)
try:
    recent = db.get_recent_receipts(30)
    if not recent:
        print("  ⚠️  No receipts in last 30 days — using ALL receipts instead")
        recent = receipts

    skip_words = {"the", "and", "for", "with", "pack", "size", "sizes", "plus", "mens", "womens"}
    candidates = []
    for r in recent:
        for item in r.get("items", []):
            name = item.get("name", "")
            price_str = item.get("price", "0")
            ri_num = item.get("item_number", "")
            ri_words = [w for w in name.lower().replace("/", " ").split()
                        if len(w) >= 4 and w not in skip_words]
            for d in drops:
                d_num = d.get("item_number", "")
                d_name = d["item_name"].lower()
                matched_by = None
                if ri_num and d_num and ri_num == d_num:
                    matched_by = "exact_item_number"
                elif ri_num and d_num and len(ri_num) >= 5 and len(d_num) >= 5 and ri_num[:5] == d_num[:5]:
                    matched_by = "partial_item_number"
                elif len(ri_words) >= 2 and sum(1 for w in ri_words if w in d_name) >= 2:
                    matched_by = "name_keyword"
                if not matched_by:
                    continue
                try:
                    paid = float(price_str)
                    deal = float(d["sale_price"])
                    if deal < paid:
                        candidates.append({
                            "receipt_item": name,
                            "paid": price_str,
                            "deal_name": d["item_name"],
                            "deal_price": d["sale_price"],
                            "savings": round(paid - deal, 2),
                            "matched_by": matched_by,
                        })
                except (ValueError, TypeError):
                    pass

    print(f"  ✅ Candidates found: {len(candidates)}")
    for c in candidates[:5]:
        print(f"     {c['receipt_item']} paid=${c['paid']} → {c['deal_name']} "
              f"@ ${c['deal_price']} saves ${c['savings']} [{c['matched_by']}]")
    if len(candidates) > 5:
        print(f"     ... and {len(candidates)-5} more")
    if not candidates:
        print("  ℹ️  No matches — analysis will say 'No matches found'")
except Exception:
    print("  ❌ Matching failed:")
    traceback.print_exc()

# ── 3. Bedrock connectivity (no actual analysis, just a ping) ─────────────────
print("\n" + "=" * 60)
print("STEP 3: Bedrock connectivity — tiny test call")
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
    print(f"  ✅ Bedrock responded: {reply!r}")
except Exception as e:
    print(f"  ❌ Bedrock failed: {e}")
    print("  ℹ️  Analysis will fail if Bedrock is throttled or quota is exhausted")

# ── 4. strands import ─────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 4: strands library import")
print("=" * 60)
try:
    from strands import Agent, tool
    from strands.models import BedrockModel
    print("  ✅ strands imported OK")
except ImportError as e:
    print(f"  ❌ strands import failed: {e}")
    print("  Fix: pip install strands-agents")
    sys.exit(1)

# ── 5. Full streaming analysis ────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 5: Full run_analysis_stream")
print("=" * 60)
try:
    from services.analyzer import run_analysis_stream
    print("  Running... (this calls Bedrock, may take 30-60s)")
    for event_str in run_analysis_stream():
        if not event_str.startswith("data: "):
            continue
        ev = json.loads(event_str[6:])
        if ev["type"] == "tool":
            print(f"  🔧 tool: {ev['name']}")
        elif ev["type"] == "chunk":
            print(".", end="", flush=True)
        elif ev["type"] == "error":
            print(f"\n  ❌ Error from stream: {ev['text']}")
            break
        elif ev["type"] == "done":
            print(f"\n  ✅ Analysis complete ({len(ev['text'])} chars)")
            print("\n--- RESULT PREVIEW (first 1000 chars) ---")
            print(ev["text"][:1000])
            break
except Exception:
    print("  ❌ Stream failed:")
    traceback.print_exc()

print("\n" + "=" * 60)
print("Done.")
print("=" * 60)

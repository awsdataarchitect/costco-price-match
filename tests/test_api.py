"""
Costco Scanner API test script.
Usage: python test_api.py
"""

import json
import os
import sys
import boto3
import requests

# Note: set $COSTCO_SCANNER_URL environment variable to override the default API URL
BASE = os.environ.get("COSTCO_SCANNER_URL", "https://xxx.execute-api.us-east-2.amazonaws.com")


def get_token() -> str:
    print("🔑 Fetching credentials from /api/config...")
    cfg = requests.get(f"{BASE}/api/config", timeout=10).json()
    print(f"   Region: {cfg['region']}, User Pool: {cfg['user_pool_id']}")

    client = boto3.client("cognito-idp", region_name=cfg["region"])
    resp = client.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": cfg["username"], "PASSWORD": cfg["password"]},
        ClientId=cfg["user_pool_client_id"],
    )
    print("   ✓ Authenticated")
    return resp["AuthenticationResult"]["IdToken"]


def test_receipts(headers):
    print("\n📄 GET /api/receipts")
    r = requests.get(f"{BASE}/api/receipts", headers=headers, timeout=10)
    data = r.json()
    receipts = data.get("receipts", [])
    print(f"   Status: {r.status_code} — {len(receipts)} receipt(s)")
    for rc in receipts[:3]:
        print(f"   • {rc.get('receipt_date', 'unknown date')} — {rc.get('store', '')} — {len(rc.get('items', []))} items  [id: {rc['receipt_id']}]")
    return receipts


def test_price_drops(headers):
    print("\n💰 GET /api/price-drops")
    r = requests.get(f"{BASE}/api/price-drops", headers=headers, timeout=10)
    drops = r.json().get("price_drops", [])
    print(f"   Status: {r.status_code} — {len(drops)} deal(s)")
    sources = {}
    for d in drops:
        src = d.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
    for src, count in sorted(sources.items(), key=lambda x: -x[1]):
        print(f"   • {src}: {count}")
    return drops


def test_scan(headers, force: bool = False):
    print(f"\n🔍 POST /api/scan-prices?force_refresh={str(force).lower()}")
    r = requests.post(f"{BASE}/api/scan-prices?force_refresh={str(force).lower()}", headers=headers, timeout=120)
    data = r.json()
    print(f"   Status: {r.status_code} — {data.get('price_drops', 0)} deals saved")


def test_analyze(headers, receipt_id: str = None):
    url = f"{BASE}/api/analyze"
    if receipt_id:
        url += f"?receipt_id={receipt_id}"
    print(f"\n🤖 GET /api/analyze{f'?receipt_id={receipt_id}' if receipt_id else ''}")
    print("   Streaming... (this may take 20-60s)")

    with requests.get(url, headers=headers, stream=True, timeout=120) as r:
        print(f"   Status: {r.status_code}")
        for line in r.iter_lines():
            if not line:
                continue
            line = line.decode("utf-8")
            if not line.startswith("data: "):
                continue
            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            if event["type"] == "tool":
                print(f"   ⚙️  tool: {event['name']}")
            elif event["type"] == "error":
                print(f"   ❌ error: {event['text']}")
                break
            elif event["type"] == "done":
                print("\n--- Analysis Report ---")
                print(event["text"])
                break


def test_bedrock_quota():
    from datetime import datetime, timezone

    print("\n📊 Bedrock token usage today")
    region = "us-east-2"
    cw = boto3.client("cloudwatch", region_name=region)
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Try both model ID formats Bedrock uses in CloudWatch
    models = {
        "Nova 2 Lite": ["us.amazon.nova-2-lite-v1:0", "amazon.nova-lite-v1:0"],
        "Nova Premier": ["us.amazon.nova-premier-v1:0", "amazon.nova-premier-v1:0"],
    }

    for display_name, model_ids in models.items():
        for metric in ["InputTokenCount", "OutputTokenCount"]:
            total = 0
            for model_id in model_ids:
                r = cw.get_metric_statistics(
                    Namespace="AWS/Bedrock",
                    MetricName=metric,
                    Dimensions=[{"Name": "ModelId", "Value": model_id}],
                    StartTime=start,
                    EndTime=now,
                    Period=86400,
                    Statistics=["Sum"],
                )
                total += int(r["Datapoints"][0]["Sum"]) if r["Datapoints"] else 0
            label = "input " if "Input" in metric else "output"
            print(f"   {display_name} {label}: {total:,} tokens")

    # Also check without dimension filter (all models combined)
    print()
    all_zero = True
    for metric in ["InputTokenCount", "OutputTokenCount"]:
        r = cw.get_metric_statistics(
            Namespace="AWS/Bedrock",
            MetricName=metric,
            Dimensions=[],
            StartTime=start,
            EndTime=now,
            Period=86400,
            Statistics=["Sum"],
        )
        total = int(r["Datapoints"][0]["Sum"]) if r["Datapoints"] else 0
        if total > 0:
            all_zero = False
        label = "input " if "Input" in metric else "output"
        print(f"   All models {label}: {total:,} tokens")
    if all_zero:
        print("   ⚠️  All zeros — enable Model invocation logging in Bedrock console to populate these metrics")

    # Cost Explorer fallback — shows actual spend even without CW logging enabled
    try:
        ce = boto3.client("ce", region_name="us-east-1")  # Cost Explorer is global, us-east-1 only
        today = now.date().isoformat()
        resp = ce.get_cost_and_usage(
            TimePeriod={"Start": today, "End": today},
            Granularity="DAILY",
            Filter={"Dimensions": {"Key": "SERVICE", "Values": ["Amazon Bedrock"]}},
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
        )
        results = resp.get("ResultsByTime", [{}])[0].get("Groups", [])
        if results:
            print("\n   Bedrock cost today (by usage type):")
            for g in results:
                cost = float(g["Metrics"]["UnblendedCost"]["Amount"])
                if cost > 0:
                    print(f"   • {g['Keys'][0]}: ${cost:.6f}")
        else:
            print("\n   Bedrock cost today: $0.00 (no charges yet today)")
    except Exception as e:
        print(f"\n   Cost Explorer unavailable: {e}")

    # Nova-specific quotas only
    print("\n   Nova service quotas:")
    sq = boto3.client("service-quotas", region_name=region)
    paginator = sq.get_paginator("list_service_quotas")
    nova_keywords = ["nova 2 lite", "nova lite", "nova premier", "nova pro", "nova micro"]
    for page in paginator.paginate(ServiceCode="bedrock"):
        for q in page["Quotas"]:
            name_lower = q["QuotaName"].lower()
            if any(kw in name_lower for kw in nova_keywords):
                status = "⚠️  NOT SET" if q["Value"] == 0 else f"{q['Value']:,.0f}"
                print(f"   • {q['QuotaName']}: {status}")


def main():
    try:
        token = get_token()
    except Exception as e:
        print(f"❌ Auth failed: {e}")
        sys.exit(1)

    headers = {"Authorization": f"Bearer {token}"}

    receipts = test_receipts(headers)
    test_price_drops(headers)
    test_scan(headers, force=False)

    # Run analysis on first receipt if available
    if receipts:
        first_id = receipts[0]["receipt_id"]
        test_analyze(headers, receipt_id=first_id)
    else:
        print("\n⚠️  No receipts found — skipping analysis. Upload a receipt via the web UI first.")

    test_bedrock_quota()
    print("\n✅ All tests complete")


if __name__ == "__main__":
    main()

"""
Test receipt upload against the deployed API with both PDF formats.
Usage:
    py tests\test_upload.py
    $env:COSTCO_SCANNER_URL="https://your-api.execute-api.us-east-2.amazonaws.com"; py tests\test_upload.py
"""
import os, sys, json, boto3, requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.environ.get("COSTCO_SCANNER_URL", "").rstrip("/")

AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", ""))
if not AWS_REGION:
    print("⚠️  No region set. Defaulting AWS_REGION=us-east-2")
    AWS_REGION = "us-east-2"
    os.environ["AWS_REGION"] = AWS_REGION

PDFS = [
    (
        r"C:\Users\sanat\.openclaw\workspace\costco_receipt_2026-03-28_text.pdf",
        "dot-leader format (new)",
        28,
    ),
    (
        r"D:\OnlineDrives\OneDrive\Costco_reciepts\Invoice_20260321_145543 1.pdf",
        "multi-line physical receipt (old)",
        11,
    ),
]


def get_api_url() -> str:
    if BASE:
        return BASE
    # Auto-discover ApiUrl from CloudFormation stack outputs
    print("  Fetching API URL from CloudFormation...")
    try:
        cf = boto3.client("cloudformation", region_name=AWS_REGION)
        for stack in ["CostcoScannerApi", "CostcoScanner", "CostcoScannerCommon"]:
            try:
                outputs = {
                    o["OutputKey"]: o["OutputValue"]
                    for o in cf.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
                }
                url = outputs.get("ApiUrl") or next(
                    (v for k, v in outputs.items() if "url" in k.lower()), None
                )
                if url:
                    print(f"  ✅ API URL from stack '{stack}': {url}")
                    return url.rstrip("/")
            except Exception:
                continue
    except Exception as e:
        print(f"  ❌ CloudFormation lookup failed: {e}")
    return None


def get_auth_headers(base: str) -> dict:
    try:
        cfg = requests.get(f"{base}/api/config", timeout=10).json()
        if not cfg.get("user_pool_id"):
            return {}
        client = boto3.client("cognito-idp", region_name=cfg["region"])
        resp = client.initiate_auth(
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": cfg["username"], "PASSWORD": cfg["password"]},
            ClientId=cfg["user_pool_client_id"],
        )
        token = resp["AuthenticationResult"]["IdToken"]
        print(f"   ✅ Authenticated")
        return {"Authorization": f"Bearer {token}"}
    except Exception as e:
        print(f"   ⚠️  Auth skipped ({e}) — trying unauthenticated")
        return {}


def upload_pdf(base: str, headers: dict, pdf_path: str, label: str, expected_items: int):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  File: {os.path.basename(pdf_path)}")
    print(f"{'='*60}")

    if not os.path.exists(pdf_path):
        print(f"  ⚠️  File not found — skipping")
        return

    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    print(f"  Size: {len(pdf_bytes):,} bytes")

    # First inspect (no auth needed usually)
    try:
        r = requests.post(
            f"{base}/api/inspect-pdf",
            files={"file": (os.path.basename(pdf_path), pdf_bytes, "application/pdf")},
            headers=headers,
            timeout=30,
        )
        if r.status_code == 200:
            info = r.json()
            print(f"  inspect: text_chars={info.get('text_chars')}, recommended={info.get('recommended_parser')}")
    except Exception as e:
        print(f"  inspect failed: {e}")

    # Upload
    try:
        r = requests.post(
            f"{base}/api/upload",
            files={"file": (os.path.basename(pdf_path), pdf_bytes, "application/pdf")},
            headers=headers,
            timeout=60,
        )
        print(f"  HTTP {r.status_code}")

        if r.status_code == 200:
            data = r.json()
            receipt = data.get("receipt", {})
            items_count = data.get("items_count", 0)
            parsed_by = data.get("parsed_by", "?")
            receipt_date = receipt.get("receipt_date", "?")
            store = receipt.get("store", "?")

            print(f"  parsed_by:    {parsed_by}")
            print(f"  store:        {store}")
            print(f"  receipt_date: {receipt_date}")
            print(f"  items_count:  {items_count}  (expected ~{expected_items})")

            receipt_id = receipt.get("receipt_id", "")
            if items_count == 0:
                print(f"  ❌ FAIL — 0 items parsed!")
                if receipt_id:
                    # Delete the bad receipt then re-upload fresh
                    print(f"  🗑  Deleting bad receipt {receipt_id}...")
                    dr = requests.delete(f"{base}/api/receipt/{receipt_id}", headers=headers, timeout=10)
                    print(f"     delete: HTTP {dr.status_code}")
                    print(f"  🔄 Re-uploading...")
                    rr = requests.post(
                        f"{base}/api/upload",
                        files={"file": (os.path.basename(pdf_path), pdf_bytes, "application/pdf")},
                        headers=headers,
                        timeout=60,
                    )
                    if rr.status_code == 200:
                        rd = rr.json()
                        new_count = rd.get("items_count", 0)
                        new_id = rd.get("receipt", {}).get("receipt_id", "?")
                        print(f"  Re-upload items_count: {new_count}")
                        if new_count > 0:
                            print(f"  ✅ PASS after re-upload  [id: {new_id}]")
                            items = rd.get("receipt", {}).get("items", [])
                            for item in items[:5]:
                                print(f"    • [{item.get('item_number','?'):>8}] {item.get('name','?'):<30} ${item.get('price','?')}")
                        else:
                            print(f"  ❌ Still 0 items — deploy the fix first: .\\scripts\\deploy.ps1")
                    else:
                        print(f"  ❌ Re-upload failed: {rr.text[:200]}")
            elif items_count < expected_items * 0.7:
                print(f"  ⚠️  LOW — fewer items than expected ({items_count} vs {expected_items})")
            else:
                print(f"  ✅ PASS")

            print(f"  receipt_id: {receipt.get('receipt_id', '?')}")

            items = receipt.get("items", [])
            print(f"  Sample items:")
            for item in items[:5]:
                print(f"    • [{item.get('item_number','?'):>8}] {item.get('name','?'):<30} ${item.get('price','?')}")
        else:
            print(f"  ❌ FAIL — {r.text[:300]}")

    except Exception as e:
        print(f"  ❌ Exception: {e}")


def main():
    print("\n🔍 Costco Receipt Upload Test")
    print("="*60)

    base = get_api_url()
    if not base:
        print("❌ No API URL found.")
        print("   Set: $env:COSTCO_SCANNER_URL='https://your-api.execute-api.us-east-2.amazonaws.com'")
        sys.exit(1)

    print(f"  API: {base}")

    print("\n🔑 Getting auth token...")
    headers = get_auth_headers(base)

    for pdf_path, label, expected in PDFS:
        upload_pdf(base, headers, pdf_path, label, expected)

    print(f"\n{'='*60}")
    print("Done")
    print("="*60)


if __name__ == "__main__":
    main()

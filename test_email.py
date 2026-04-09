"""Test script: run the weekly report locally and optionally send via SES."""
from services.price_scanner import scan_price_drops
from services.analyzer import run_analysis
from agent import _md_to_html, _presign_links
import boto3

SEND_EMAIL = True
SENDER = "amfritz@gmail.com"
RECIPIENTS = ["amfritz@gmail.com"]

deals, sr = scan_price_drops(force_refresh=False)
print(f"{len(deals)} deals from {len(sr)} sources")
for s in sr:
    print(f"  {s['name']}: {s['count']} ({s['status']})")

print("\nRunning analysis...")
report = run_analysis()
print("--- REPORT ---")
print(report[:3000])

if SEND_EMAIL:
    email_report = _presign_links(report)
    html = _md_to_html(email_report)
    ses = boto3.client("ses", region_name="us-east-1")
    response  = ses.send_email(
        Source=SENDER,
        Destination={"ToAddresses": RECIPIENTS},
        Message={
            "Subject": {"Data": "Costco Weekly Price Match Report (TEST)"},
            "Body": {"Html": {"Data": html}},
        },
    )
    print("\nEmail sent!")
    print(response)
else:
    print("\nSet SEND_EMAIL = True to send the email")

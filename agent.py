"""AgentCore Runtime entry point — weekly Costco price match scan + SES HTML report."""

import logging
import os
import re
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
import boto3
from services.price_scanner import scan_price_drops
from services.analyzer import run_analysis

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")

app = BedrockAgentCoreApp()

REGION = os.environ.get("AWS_REGION", "us-west-2")
S3_BUCKET = os.environ.get("S3_BUCKET", "")
SENDER = os.environ.get("NOTIFY_EMAIL", "")
RECIPIENT = os.environ.get("NOTIFY_EMAIL", "")

s3 = boto3.client("s3", region_name=REGION)
ses = boto3.client("ses", region_name=REGION)


def _presign_links(text: str) -> str:
    def _replace(m):
        rid = m.group(1)
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": f"receipts/{rid}.pdf"},
            ExpiresIn=604800,
        )
        return f"]({url})"
    return re.sub(r"\]\(/api/receipt/([^/]+)/pdf\)", _replace, text)


def _md_to_html(md: str) -> str:
    lines = md.split("\n")
    html_lines = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if re.match(r"^\|[-| ]+\|$", stripped):
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_table:
                in_table = True
                html_lines.append('<table style="border-collapse:collapse;width:100%;font-family:Arial,sans-serif;font-size:14px">')
                html_lines.append("<tr>" + "".join(f'<th style="border:1px solid #ddd;padding:8px;background:#f4f4f4;text-align:left">{c}</th>' for c in cells) + "</tr>")
            else:
                row_cells = []
                for c in cells:
                    c = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', c)
                    row_cells.append(f'<td style="border:1px solid #ddd;padding:8px">{c}</td>')
                html_lines.append("<tr>" + "".join(row_cells) + "</tr>")
        else:
            if in_table:
                html_lines.append("</table><br>")
                in_table = False
            if stripped.startswith(">"):
                stripped = stripped.lstrip("> ")
            # Convert markdown headers
            if stripped.startswith("### "):
                html_lines.append(f"<h3 style='font-family:Arial,sans-serif;margin:16px 0 8px'>{stripped[4:]}</h3>")
            elif stripped.startswith("## "):
                html_lines.append(f"<h2 style='font-family:Arial,sans-serif;margin:20px 0 8px'>{stripped[3:]}</h2>")
            else:
                converted = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", stripped)
                if converted:
                    html_lines.append(f"<p style='font-family:Arial,sans-serif;font-size:14px;margin:4px 0'>{converted}</p>")
    if in_table:
        html_lines.append("</table>")
    return "\n".join(html_lines)


@app.entrypoint
async def invoke(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        logging.info(f"Payload received: {payload}")
        deals = scan_price_drops(force_refresh=True)
        logging.info(f"Scanned {len(deals)} deals")

        report = run_analysis()
        logging.info(f"Analysis complete ({len(report)} chars)")

        email_report = _presign_links(report)
        html_body = _md_to_html(email_report)

        ses.send_email(
            Source=SENDER,
            Destination={"ToAddresses": [RECIPIENT]},
            Message={
                "Subject": {"Data": "Costco Weekly Price Match Report"},
                "Body": {"Html": {"Data": html_body}},
            },
        )
        logging.info("SES HTML report sent")
        return {"status": "success", "deals_scanned": len(deals), "report": report}
    except Exception as e:
        logging.error(f"Error: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


app.run()

import boto3
import json
import re
import os

_bedrock = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-west-2"))
MODEL_LITE = "us.amazon.nova-2-lite-v1:0"
MODEL_PREMIER = "us.amazon.nova-premier-v1:0"

EXTRACTION_PROMPT = """Extract all lines from this Costco receipt as items.
Return ONLY valid JSON with this exact structure, no other text:
{
  "store": "store location or number",
  "receipt_date": "YYYY-MM-DD",
  "items": [
    {"name": "ITEM NAME", "price": "12.99", "qty": "1", "item_number": "1234567"}
  ]
}
Rules:
- Include EVERY line as a separate item, including TPD lines
- TPD lines should have name like "TPD/SHOES" or "TPD/3333332" exactly as shown
- Price should be a string with 2 decimals. If price ends with "-" on receipt, include the minus sign (e.g. "10.00-")
- qty defaults to "1" if not shown
- item_number = the number shown before the item name on that line. Empty string if not visible.
- Do NOT merge or combine any lines
- Do NOT skip any lines
- Ignore tax lines, subtotals, totals, payment lines
- receipt_date should be extracted from the receipt date field"""

_ITEMS_PROMPT = (
    "List ONLY the item numbers and names from the LEFT side of this Costco receipt, top to bottom.\n"
    "Format: ITEM_NUMBER | NAME\n"
    "Include TPD/ lines. Skip membership, tax, subtotal, total. One per line. No prices."
)

_PRICES_PROMPT = (
    "Count and list EVERY dollar amount on the RIGHT side of this Costco receipt, "
    "from the FIRST item to the LAST item BEFORE subtotal.\n"
    "One price per line. Include minus signs for discounts.\n"
    "Do NOT skip any price. Do NOT include subtotal, tax, or total.\n"
    "There should be exactly one price for each item line on the receipt.\n"
    "List ONLY the number (e.g. 39.99 or 10.00-), nothing else."
)

_META_PROMPT = (
    "What is the store name/number and receipt date on this Costco receipt? "
    "Return ONLY JSON: {\"store\":\"\",\"receipt_date\":\"YYYY-MM-DD\"}"
)

_NOISE_PATTERNS = re.compile(
    r"^(AGE\s*VERIFIED|DEPOSIT|L\d+\s*MEMBER|N\d+\s*MEMBER|\d+\s*@\s*[\d.]+)",
    re.IGNORECASE,
)


def _call_model(content, prompt, model_id):
    resp = _bedrock.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": content + [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 4096, "temperature": 0},
    )
    return resp["output"]["message"]["content"][0]["text"]


def _parse_premier(pdf_bytes: bytes) -> dict:
    """Two-call image approach: extract items and prices separately, then zip."""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pix = doc[0].get_pixmap(dpi=300)
    img_bytes = pix.tobytes("png")
    doc.close()

    img_content = [{"image": {"format": "png", "source": {"bytes": img_bytes}}}]

    # Three parallel-safe calls
    items_raw = _call_model(img_content, _ITEMS_PROMPT, MODEL_PREMIER)
    prices_raw = _call_model(img_content, _PRICES_PROMPT, MODEL_PREMIER)
    meta_raw = _call_model(img_content, _META_PROMPT, MODEL_PREMIER)

    # Parse items
    items = []
    for line in items_raw.strip().split("\n"):
        line = line.strip().strip("|").strip()
        if not line or line.startswith("ITEM") or line.startswith("---"):
            continue
        parts = line.split("|")
        if len(parts) >= 2:
            items.append({"item_number": parts[0].strip(), "name": parts[1].strip()})
        else:
            m = re.match(r"^(\d{4,8})?\s*(.*)", line)
            if m:
                items.append({"item_number": m.group(1) or "", "name": m.group(2).strip()})

    # Parse prices
    prices = []
    for line in prices_raw.strip().split("\n"):
        m = re.match(r"^[\d.]+[-]?", line.strip())
        if m:
            prices.append(m.group(0))

    # Zip items with prices
    result_items = []
    for i, item in enumerate(items):
        price = prices[i] if i < len(prices) else "0"
        result_items.append({
            "name": item["name"],
            "price": price,
            "qty": "1",
            "item_number": item["item_number"],
        })

    # Parse metadata
    meta = {"store": "", "receipt_date": ""}
    try:
        mt = meta_raw
        if "```" in mt:
            mt = mt.split("```")[1]
            if mt.startswith("json"):
                mt = mt[4:]
        meta = json.loads(mt.strip())
    except Exception:
        pass

    return {
        "store": meta.get("store", ""),
        "receipt_date": meta.get("receipt_date", ""),
        "items": result_items,
    }


def _post_process(items: list) -> list:
    """Filter noise, then merge TPD discount lines into their preceding item."""
    cleaned = []
    pending_qty = None
    for item in items:
        name = item.get("name", "").strip()
        price_str = item.get("price", "0").strip()

        qty_match = re.match(r"^(\d+)\s*@\s*[\d.]+", name)
        if qty_match:
            pending_qty = qty_match.group(1)
            continue

        if _NOISE_PATTERNS.match(name):
            continue
        if "TPD/" not in name.upper() and (not price_str or price_str in ("0", "0.00", "")):
            continue

        if pending_qty and "TPD/" not in name.upper():
            item["qty"] = pending_qty
            pending_qty = None

        cleaned.append(item)

    merged = []
    for item in cleaned:
        name = item.get("name", "")
        price_str = item.get("price", "0").strip()
        clean_price = price_str.rstrip("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ @#*")
        is_tpd = "TPD/" in name.upper()
        is_negative = clean_price.endswith("-")

        if (is_tpd or is_negative) and merged:
            prev = merged[-1]
            try:
                discount = float(clean_price.replace("-", ""))
                orig = float(prev["price"])
                if discount < orig:
                    prev["original_price"] = prev["price"]
                    prev["price"] = f"{orig - discount:.2f}"
                    prev["tpd"] = True
            except ValueError:
                pass
            continue

        item["price"] = clean_price.replace("-", "")
        item.setdefault("tpd", False)
        item.setdefault("original_price", "")

        try:
            q = int(item.get("qty", "1"))
            p = float(item["price"])
            if q > 1 and abs(p / q - round(p / q, 2)) > 0.001:
                item["qty"] = "1"
        except (ValueError, ZeroDivisionError):
            pass

        n = item.get("name", "")
        num = item.get("item_number", "")
        if not num:
            m = re.match(r"^([\dOoBbIlSsGg]{4,8})\s+", n)
            if m:
                raw = m.group(1)
                fixed = raw.translate(str.maketrans("OoBbIlSsGg", "0088115599"))
                if fixed.isdigit():
                    num = fixed
                    item["item_number"] = num
                    item["name"] = n[len(raw):].strip()
                    n = item["name"]
        if num and len(num) > 8:
            item["item_number"] = ""
            num = ""
        if num and n.startswith(num):
            item["name"] = n[len(num):].strip()
        merged.append(item)
    return merged


def parse_receipt_pdf(pdf_bytes: bytes, model: str = "lite") -> dict:
    """Parse receipt PDF. Lite=fast single-call, Premier=two-call image approach."""
    if model == "premier":
        result = _parse_premier(pdf_bytes)
    else:
        response = _bedrock.converse(
            modelId=MODEL_LITE,
            messages=[{
                "role": "user",
                "content": [
                    {"document": {"format": "pdf", "name": "receipt", "source": {"bytes": pdf_bytes}}},
                    {"text": EXTRACTION_PROMPT},
                ],
            }],
            inferenceConfig={"maxTokens": 4096, "temperature": 0},
        )
        text = response["output"]["message"]["content"][0]["text"]
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())

    result["items"] = _post_process(result.get("items", []))
    return result

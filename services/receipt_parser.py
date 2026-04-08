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

# Matches a typical Costco receipt item line:
# optional item number (6-8 digits), item name, price (with optional trailing letter or -)
_ITEM_LINE_RE = re.compile(
    r"^(?P<item_num>\d{6,8})?\s*(?P<name>[A-Z][A-Z0-9/& ,'.()-]{2,}?)\s{2,}(?P<price>\d{1,4}\.\d{2}-?)(?:[A-Z])?$"
)
_TPD_LINE_RE = re.compile(r"^\s*(?:TPD/[\w/ ]+)\s+(?P<price>\d{1,4}\.\d{2})-?", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b")
_STORE_RE = re.compile(r"(COSTCO\s+\w[\w\s]*?)(?:\s{3,}|\n)", re.IGNORECASE)
_QTY_RE = re.compile(r"^(\d+)\s+@\s+[\d.]+")


def inspect_pdf(pdf_bytes: bytes) -> dict:
    """
    Inspect PDF content without parsing it.
    Returns metadata useful for deciding which parser to use.
    """
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    info = {
        "pages": len(doc),
        "text_chars": 0,
        "image_count": 0,
        "has_text": False,
        "has_images": False,
        "recommended_parser": "text",  # "text", "bedrock-lite", or "bedrock-premier"
        "metadata": doc.metadata,
    }

    for page in doc:
        text = page.get_text()
        info["text_chars"] += len(text.strip())
        info["image_count"] += len(page.get_images(full=False))

    doc.close()

    info["has_text"] = info["text_chars"] > 100
    info["has_images"] = info["image_count"] > 0

    if info["has_text"]:
        info["recommended_parser"] = "text"           # selectable PDF — free
    elif info["has_images"]:
        info["recommended_parser"] = "bedrock-premier"  # scanned image — use best model
    else:
        info["recommended_parser"] = "bedrock-lite"   # unknown — try lite first

    return info



    """
    Parse a Costco receipt PDF using pure text extraction (PyMuPDF).
    Returns None if the text doesn't look like a valid Costco receipt,
    so the caller can fall back to Bedrock.
    """
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    doc.close()

    lines = [l.rstrip() for l in text.splitlines()]

    # Quick sanity check — Costco receipts always contain these
    combined = text.upper()
    if "COSTCO" not in combined and "SUBTOTAL" not in combined:
        return None

    # Extract date
    receipt_date = ""
    for line in lines:
        m = _DATE_RE.search(line)
        if m:
            try:
                from datetime import datetime
                for fmt in ("%m/%d/%Y", "%m/%d/%y"):
                    try:
                        receipt_date = datetime.strptime(m.group(1), fmt).strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        continue
            except Exception:
                pass
            if receipt_date:
                break

    # Extract store
    store = ""
    m = _STORE_RE.search(text)
    if m:
        store = m.group(1).strip()

    # Extract items — stop at subtotal/total
    items = []
    pending_qty = None
    for line in lines:
        upper = line.upper().strip()
        if upper.startswith(("SUBTOTAL", "TAX", "TOTAL", "PAYMENT", "CASH", "VISA", "MASTERCARD", "DEBIT", "CREDIT")):
            break

        # qty lines: "2 @ 7.99"
        qm = _QTY_RE.match(line.strip())
        if qm:
            pending_qty = qm.group(1)
            continue

        # TPD discount line
        tpd_m = _TPD_LINE_RE.match(line.strip())
        if tpd_m:
            items.append({
                "name": line.strip().split()[0],  # e.g. "TPD/NUTS"
                "price": tpd_m.group("price") + "-",
                "qty": "1",
                "item_number": "",
            })
            pending_qty = None
            continue

        # Regular item line
        m = _ITEM_LINE_RE.match(line.strip())
        if m:
            items.append({
                "name": m.group("name").strip(),
                "price": m.group("price"),
                "qty": pending_qty or "1",
                "item_number": m.group("item_num") or "",
            })
            pending_qty = None

    return {"store": store, "receipt_date": receipt_date, "items": items}

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


def inspect_pdf(pdf_bytes: bytes) -> dict:
    """Inspect a PDF to determine if it has selectable text or only images."""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_text_chars = 0
    total_images = 0
    for page in doc:
        total_text_chars += len(page.get_text().strip())
        total_images += len(page.get_images(full=False))
    doc.close()

    has_text = total_text_chars > 100
    has_images = total_images > 0

    if has_text:
        recommended = "text"
    elif has_images:
        recommended = "bedrock-premier"
    else:
        recommended = "bedrock-lite"

    return {
        "has_text": has_text,
        "has_images": has_images,
        "text_chars": total_text_chars,
        "image_count": total_images,
        "recommended_parser": recommended,
    }


def _parse_text(pdf_bytes: bytes) -> dict:
    """
    Parse a text-based Costco receipt PDF using a line-by-line state machine.

    Costco text PDFs use a multi-line format per item:
        E                      ← tax-exempt flag (optional)
        <item_number>          ← digits only, OR combined with name below
        <item name>            ← may span two lines; may be combined with price
        <price> N              ← e.g. "16.78 N"

    Discount/TPD lines appear without an E:
        <coupon_number>
        / <item_number>
        <amount>-
    """
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = "\n".join(page.get_text() for page in doc)
    doc.close()

    lines = [l.strip() for l in full_text.split("\n")]
    # Normalize dot-leader format used by computer-generated receipts:
    # "KS Organic 2% Milk .................. 14.99" → "KS Organic 2% Milk 14.99"
    lines = [re.sub(r'\s*\.{3,}\s*', ' ', l) for l in lines]


    # Store: first non-empty line (e.g. "LYNNWOOD #1190")
    store = next((l for l in lines if l), "")

    # Date: first MM/DD/YYYY occurrence
    receipt_date = ""
    date_m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", full_text)
    if date_m:
        mo, dy, yr = date_m.group(1), date_m.group(2), date_m.group(3)
        receipt_date = f"{yr}-{mo.zfill(2)}-{dy.zfill(2)}"

    # Patterns
    _PRICE_RE = re.compile(r"^(\d+\.\d{2}-?)\s*[A-Z]?$")
    _NUM_RE = re.compile(r"^\d{1,8}$")          # item number alone on its line
    _INLINE_FULL_RE = re.compile(               # "1068080 PASTURE EGGS 8.49 N"
        r"^(\d{1,8})\s+(.+?)\s+(-?\d+\.\d{2}-?)\s*[A-Z]?$"
    )
    _INLINE_PARTIAL_RE = re.compile(r"^(\d{1,8})\s+(.+)$")   # "1729565 LAUGHING"
    _NAME_PRICE_RE = re.compile(r"^(.+?)\s+(\d+\.\d{2}-?)\s*[A-Z]?$")
    _STOP = {"SUBTOTAL", "TAX", "TOTAL", "CHANGE", "VISA", "MASTERCARD", "AMEX", "DISCOVER"}

    items = []
    n = len(lines)

    # Skip header lines until we reach the "Items" section marker or the first item line.
    # This prevents address lines like "8629 120th Avenue NE" from being misread as item numbers.
    i = 0
    _ITEM_START_RE = re.compile(r"^\d{1,8}\s+\S")
    while i < n:
        l = lines[i]
        if l.upper() == "ITEMS":
            i += 1  # skip the "Items" header itself, then start parsing
            break
        i += 1
    # If no "Items" header found, fall back to scanning from the start
    if i >= n:
        i = 0

    while i < n:
        line = lines[i]

        # Stop at footer
        if any(line.upper().startswith(w) for w in _STOP):
            break

        # Skip "E" (tax indicator) and blank lines
        if line == "E" or not line:
            i += 1
            continue

        # ── Full inline: "1068080 PASTURE EGGS 8.49 N" ──────────────────────
        m = _INLINE_FULL_RE.match(line)
        if m:
            price = m.group(3)
            if price.startswith("-"):  # "-3.00" → "3.00-" for uniform discount handling
                price = price[1:] + "-"
            items.append({
                "item_number": m.group(1),
                "name": m.group(2).strip(),
                "price": price,
                "qty": "1",
            })
            i += 1
            continue

        # ── Partial inline: "1729565 LAUGHING" (name/price continue below) ──
        m = _INLINE_PARTIAL_RE.match(line)
        if m:
            item_num = m.group(1)
            name_parts = [m.group(2).strip()]
            i += 1
            while i < n:
                l = lines[i]
                pm = _PRICE_RE.match(l)
                if pm:
                    items.append({
                        "item_number": item_num,
                        "name": " ".join(name_parts).strip(),
                        "price": pm.group(1),
                        "qty": "1",
                    })
                    i += 1
                    break
                if l == "E" or any(l.upper().startswith(w) for w in _STOP):
                    break
                if l:
                    name_parts.append(l)
                i += 1
            continue

        # ── Item number on its own line ───────────────────────────────────────
        if _NUM_RE.match(line):
            item_num = line
            i += 1
            if i >= n:
                break
            next_line = lines[i]

            # Discount/TPD: "/ 5331" follows the coupon number
            if next_line.startswith("/"):
                i += 1  # skip "/ XXXX"
                if i < n:
                    disc_m = re.match(r"^(\d+\.\d{2}-)$", lines[i])
                    if disc_m:
                        items.append({
                            "item_number": item_num,
                            "name": f"TPD/{item_num}",
                            "price": disc_m.group(1),
                            "qty": "1",
                        })
                    i += 1
                continue

            # Name + price on the same line: "ORG STRAWBRY 8.79 N"
            np_m = _NAME_PRICE_RE.match(next_line)
            if np_m:
                items.append({
                    "item_number": item_num,
                    "name": np_m.group(1).strip(),
                    "price": np_m.group(2),
                    "qty": "1",
                })
                i += 1
                continue

            # Multi-line name then price on its own line
            name_parts = []
            while i < n:
                l = lines[i]
                pm = _PRICE_RE.match(l)
                if pm:
                    items.append({
                        "item_number": item_num,
                        "name": " ".join(name_parts).strip(),
                        "price": pm.group(1),
                        "qty": "1",
                    })
                    i += 1
                    break
                if l == "E" or any(l.upper().startswith(w) for w in _STOP):
                    break
                if l:
                    name_parts.append(l)
                i += 1
            continue

        i += 1

    return {
        "store": store,
        "receipt_date": receipt_date,
        "items": items,
        "_raw_text": full_text,
    }


def parse_receipt_pdf(pdf_bytes: bytes, model: str = "auto") -> dict:
    """
    Parse receipt PDF. model can be:
      "auto"    — inspect PDF first, choose best parser automatically (default)
      "text"    — force PyMuPDF text extraction (free)
      "lite"    — force Bedrock Nova 2 Lite
      "premier" — force Bedrock Nova Premier (highest accuracy, image-based)

    Auto-routing logic (inspect_pdf):
      text PDF  → text parser (free, no Bedrock)
      image PDF → bedrock-premier
      unknown   → bedrock-lite
    """
    if model == "auto":
        info = inspect_pdf(pdf_bytes)
        if info["has_text"]:
            # PDF has selectable text — parse directly, no Bedrock needed
            result = _parse_text(pdf_bytes)
            result.pop("_raw_text", None)
            result["items"] = _post_process(result.get("items", []))
            result["parsed_by"] = "text"
            return result
        # No text — route to Bedrock based on content type
        model = info["recommended_parser"].replace("bedrock-", "")  # "lite" or "premier"

    if model == "text":
        text_result = _parse_text(pdf_bytes)
        if text_result:
            text_result.pop("_raw_text", None)
            text_result["items"] = _post_process(text_result.get("items", []))
            text_result["parsed_by"] = "text"
            return text_result
        # Forced text mode but no text found — fall back to lite
        model = "lite"

    if model == "premier":
        result = _parse_premier(pdf_bytes)
        result["parsed_by"] = "bedrock-premier"
    else:
        # Bedrock Nova 2 Lite
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
        result["parsed_by"] = "bedrock-lite"

    result["items"] = _post_process(result.get("items", []))
    return result

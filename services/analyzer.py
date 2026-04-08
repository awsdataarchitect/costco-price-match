import json
import os
from botocore.config import Config
from strands import Agent, tool
from strands.models import BedrockModel
from services import db

model = BedrockModel(
    model_id="us.amazon.nova-2-lite-v1:0",
    region_name=os.environ.get("AWS_REGION", "us-west-2"),
    max_tokens=32000,
    boto_client_config=Config(read_timeout=300),
)

SYSTEM_PROMPT = """You are a Costco price match analyst.

1. Use find_potential_matches to get pre-filtered candidates.
2. Verify which are real matches. Discard false positives (e.g. "ORGANIC DALA" ≠ "Organika").
3. ALWAYS call get_receipt_items to find items with tpd=true for the TPD table.

Match types:
- exact_item_number: Always valid
- partial_item_number: Very likely valid (size/region variants)
- name_keyword: Verify products are the same. Receipt names are abbreviated ("ALDO SHOE" = "ALDO COURT SHOE")

CRITICAL RULES:
- Only report a savings opportunity if the deal's sale_price is STRICTLY LESS than the receipt item's price (what was paid).
- If the deal price >= the price paid, there is NO savings. Do NOT include it.
- Items with tpd=true already received a Temporary Price Drop at purchase. Their "price" is the discounted amount they paid. Only report further savings if a deal is even cheaper than what they paid after TPD.
- "original_price" on receipt items is what it cost BEFORE the TPD discount. Do NOT compare deals against original_price — compare against "price" (what was actually paid).

Present as TWO MARKDOWN TABLES, sorted by Date (newest first). Use EXACTLY this format:

## 💰 Price Adjustment Opportunities

| Item | Item # | Date | Paid | Sale Price | Savings | Source |
(rows where savings > $0 AND tpd_at_purchase is false. Do NOT include items where tpd_at_purchase is true here — those go in the TPD table below even if they have further savings.)

**💰 Potential Savings: $X.XX**

💡 Request price adjustment at the membership counter within 30 days of purchase.

## ✅ Already Applied (TPD on Receipt)

| Item | Item # | Date | Original | Paid (TPD) | TPD Savings | Source |
(List ALL receipt items where tpd=true from get_receipt_items. These are NOT from find_potential_matches. Original = original_price, Paid = price, TPD Savings = original_price - price. Source = leave empty or use deal source if matched.)

**🎉 Already Saved: $X.XX**

ℹ️ These items already had a Temporary Price Drop (TPD) applied at checkout.

Source column: format as markdown link using deal_link, e.g. [cocowest](https://cocowest.ca/...)
Potential Savings = sum of Savings column in Table 1. Already Saved = sum of TPD Savings column in Table 2 (original_price - price for each row).
All dollar amounts MUST include the $ sign (e.g. $24.99, NOT 24.99).
Do NOT include an Action column. Do NOT deviate from the format above.
If no matches found, say so clearly."""


def _build_receipt_lookup() -> dict:
    """Map item_number and item_name to receipt_id for post-processing."""
    lookup = {}
    for r in db.get_all_receipts():
        rid = r["receipt_id"]
        for item in r.get("items", []):
            num = item.get("item_number", "")
            if num:
                lookup[num] = rid
            lookup[item["name"].strip().upper()] = rid
    return lookup


_SOURCE_URLS = {
    "cocowest": "https://cocowest.ca", "cocoeast": "https://cocoeast.ca",
    "coupon_book": "https://www.costco.ca/coupons.html",
    "rfd_hot_deals": "https://forums.redflagdeals.com/hot-deals-f9/",
    "rfd_clearance": "https://forums.redflagdeals.com/costco-clearance-f729/",
    "reddit_costco": "https://reddit.com/r/Costco",
    "reddit_costcocanada": "https://reddit.com/r/CostcoCanada",
}


def _inject_receipt_links(text: str, lookup: dict) -> str:
    """Post-process: make item name in first column a link to its receipt PDF."""
    lines = text.split("\n")
    out = []
    for line in lines:
        if line.strip().startswith("|") and not line.strip().startswith("| Item") and not all(c in "|- :" for c in line.replace("|", "").strip()):
            parts = line.split("|")
            if len(parts) >= 3:
                name = parts[1].strip()
                num = parts[2].strip()
                rid = lookup.get(num) or lookup.get(name.upper())
                if rid and name:
                    parts[1] = f" [{name}](/api/receipt/{rid}/pdf) "
            line = "|".join(parts)
        out.append(line)
    return "\n".join(out)


def run_analysis_stream(receipt_ids=None, date_from=None, date_to=None, sources=None):
    """Streaming generator: yields SSE events as agent produces output."""
    import queue, threading

    # Capture state in closure (not module-level globals) for thread safety
    target_receipt_ids = receipt_ids
    filter_date_from = date_from
    filter_date_to = date_to
    filter_sources = sources

    def _get_filtered_deals():
        drops = db.get_all_price_drops()
        if filter_sources:
            src_set = set(filter_sources)
            drops = [d for d in drops if d.get("source", "") in src_set]
        if filter_date_from:
            drops = [d for d in drops if (d.get("promo_end") or d.get("scanned_date", "")[:10]) >= filter_date_from]
        if filter_date_to:
            drops = [d for d in drops if (d.get("scanned_date", "")[:10] or d.get("promo_end", "9999")) <= filter_date_to]
        return drops

    q = queue.Queue()

    class StreamHandler:
        def __call__(self, **kwargs):
            data = kwargs.get("data", "")
            if data:
                q.put(("chunk", data))
            if kwargs.get("event", {}).get("contentBlockStart", {}).get("start", {}).get("toolUse"):
                tool_name = kwargs["event"]["contentBlockStart"]["start"]["toolUse"]["name"]
                q.put(("tool", tool_name))

    def run():
        try:
            # Define tools as closures so they use captured state, not globals
            @tool
            def get_receipt_items_local() -> str:
                """Fetch items from the selected receipt (or last 30 days of receipts if none selected)."""
                if target_receipt_ids:
                    receipts = [r for r in [db.get_receipt(rid) for rid in target_receipt_ids] if r]
                else:
                    receipts = db.get_recent_receipts(30)
                if not receipts:
                    return "No receipts found. Please upload a receipt first."
                items = []
                for r in receipts:
                    for item in r.get("items", []):
                        items.append({
                            "name": item["name"],
                            "price": item["price"],
                            "qty": item.get("qty", "1"),
                            "item_number": item.get("item_number", ""),
                            "receipt_date": r.get("receipt_date", ""),
                            "tpd": item.get("tpd", False),
                            "original_price": item.get("original_price", ""),
                        })
                return json.dumps(items)

            @tool
            def get_current_price_drops_local() -> str:
                """Fetch all current Costco price drops / deals from DynamoDB."""
                drops = _get_filtered_deals()
                if not drops:
                    return "No price drops found. Please run the price scanner first."
                return json.dumps([{
                    "item_name": d["item_name"],
                    "item_number": d.get("item_number", ""),
                    "sale_price": d["sale_price"],
                    "original_price": d["original_price"],
                    "promo_end": d.get("promo_end", ""),
                    "source": d.get("source", ""),
                } for d in drops])

            @tool
            def find_potential_matches_local() -> str:
                """Pre-filter: find deals matching receipt items by item number or name keywords."""
                if target_receipt_ids:
                    receipts = [r for r in [db.get_receipt(rid) for rid in target_receipt_ids] if r]
                else:
                    receipts = db.get_recent_receipts(30)
                drops = _get_filtered_deals()
                if not receipts or not drops:
                    return "Need both receipts and price drops."

                receipt_items = []
                for r in receipts:
                    for item in r.get("items", []):
                        receipt_items.append({
                            "name": item["name"],
                            "price": item["price"],
                            "original_price": item.get("original_price", ""),
                            "item_number": item.get("item_number", ""),
                            "receipt_date": r.get("receipt_date", ""),
                            "tpd": item.get("tpd", False),
                        })

                skip_words = {"the", "and", "for", "with", "pack", "size", "sizes", "plus", "mens", "womens"}
                candidates = []
                for ri in receipt_items:
                    ri_num = ri["item_number"]
                    ri_words = [w for w in ri["name"].lower().replace("/", " ").split()
                                if len(w) >= 4 and w not in skip_words]
                    ri_matches = []
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
                        elif len(ri_words) == 1 and len(ri_words[0]) >= 5 and ri_words[0] in d_name:
                            matched_by = "name_keyword"
                        if not matched_by:
                            continue
                        try:
                            paid = float(ri["price"])
                            deal = float(d["sale_price"])
                            if deal >= paid:
                                continue
                            savings = round(paid - deal, 2)
                        except (ValueError, TypeError):
                            continue
                        match_rank = {"exact_item_number": 3, "partial_item_number": 2, "name_keyword": 1}
                        ri_matches.append({
                            "receipt_item": ri["name"],
                            "receipt_price": ri["price"],
                            "original_price": ri.get("original_price", ""),
                            "receipt_item_number": ri_num,
                            "receipt_date": ri["receipt_date"],
                            "tpd_at_purchase": ri["tpd"],
                            "deal_name": d["item_name"],
                            "deal_price": d["sale_price"],
                            "deal_item_number": d_num,
                            "deal_source": d.get("source", ""),
                            "deal_link": d.get("link", ""),
                            "deal_expiry": d.get("promo_end", ""),
                            "matched_by": matched_by,
                            "savings": savings,
                            "_rank": match_rank.get(matched_by, 0),
                        })
                    if ri_matches:
                        best = max(ri_matches, key=lambda m: (m["savings"], m["_rank"]))
                        del best["_rank"]
                        candidates.append(best)

                candidates.sort(key=lambda m: m.get("receipt_date", ""))
                return json.dumps(candidates) if candidates else "No potential matches found."

            prompt = "Analyze my Costco receipt against current price drops and show all price match opportunities."
            if target_receipt_ids:
                prompt = f"Analyze receipts {', '.join(target_receipt_ids)} against current price drops and show all price match opportunities."
            a = Agent(
                model=model,
                system_prompt=SYSTEM_PROMPT,
                tools=[get_receipt_items_local, get_current_price_drops_local, find_potential_matches_local],
                callback_handler=StreamHandler(),
            )
            a(prompt)
            q.put(("done", ""))
        except BaseException as e:
            q.put(("error", str(e)))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    full_text = []
    TIMEOUT = 270  # 4.5 min — Lambda max is 5 min, give 30s headroom
    while True:
        try:
            msg_type, data = q.get(timeout=TIMEOUT)
        except queue.Empty:
            yield f"data: {json.dumps({'type':'error','text':'Analysis timed out after 4.5 minutes'})}\n\n"
            break
        if msg_type == "chunk":
            full_text.append(data)
            yield f"data: {json.dumps({'type':'chunk','text':data})}\n\n"
        elif msg_type == "tool":
            full_text.clear()  # reset — only keep final response chunks
            yield f"data: {json.dumps({'type':'tool','name':data})}\n\n"
        elif msg_type == "error":
            yield f"data: {json.dumps({'type':'error','text':data})}\n\n"
            break
        elif msg_type == "done":
            # Post-process: inject receipt links
            text = "".join(full_text)
            lookup = _build_receipt_lookup()
            text = _inject_receipt_links(text, lookup)
            yield f"data: {json.dumps({'type':'done','text':text})}\n\n"
            break


def run_analysis(receipt_ids: list = None) -> str:
    """Run non-streaming analysis by collecting the streaming generator output."""
    error = None
    for event_str in run_analysis_stream(receipt_ids=receipt_ids):
        if not event_str.startswith("data: "):
            continue
        ev = json.loads(event_str[6:])
        if ev["type"] == "done":
            return ev["text"]
        elif ev["type"] == "error":
            error = ev["text"]
            break
    return f"Analysis failed: {error}" if error else ""


def get_price_match_data(receipt_ids=None, date_from=None, date_to=None, sources=None) -> dict:
    """
    Pure-Python pre-filter with NO Bedrock calls.
    Returns candidates + full receipt items so ClawdBot's own LLM can do the
    analysis reasoning instead of invoking Bedrock from the Lambda.
    """
    # Filter deals
    drops = db.get_all_price_drops()
    if sources:
        src_set = set(sources)
        drops = [d for d in drops if d.get("source", "") in src_set]
    if date_from:
        drops = [d for d in drops if (d.get("promo_end") or d.get("scanned_date", "")[:10]) >= date_from]
    if date_to:
        drops = [d for d in drops if (d.get("scanned_date", "")[:10] or d.get("promo_end", "9999")) <= date_to]

    # Get receipts
    if receipt_ids:
        receipts = [r for r in [db.get_receipt(rid) for rid in receipt_ids] if r]
    else:
        receipts = db.get_recent_receipts(30)

    # Build flat receipt items list
    receipt_items = []
    for r in receipts:
        for item in r.get("items", []):
            receipt_items.append({
                "name": item.get("name", ""),
                "price": item.get("price", "0"),
                "qty": item.get("qty", "1"),
                "item_number": item.get("item_number", ""),
                "receipt_date": r.get("receipt_date", ""),
                "store": r.get("store", ""),
                "receipt_id": r.get("receipt_id", ""),
                "tpd": item.get("tpd", False),
                "original_price": item.get("original_price", ""),
            })

    # Pre-filter candidates (pure Python)
    skip_words = {"the", "and", "for", "with", "pack", "size", "sizes", "plus", "mens", "womens"}
    candidates = []
    for ri in receipt_items:
        ri_num = ri["item_number"]
        ri_words = [w for w in ri["name"].lower().replace("/", " ").split()
                    if len(w) >= 4 and w not in skip_words]
        best = None
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
            elif len(ri_words) == 1 and len(ri_words[0]) >= 5 and ri_words[0] in d_name:
                matched_by = "name_keyword"
            if not matched_by:
                continue
            try:
                paid = float(ri["price"])
                deal = float(d["sale_price"])
                if deal >= paid:
                    continue
                savings = round(paid - deal, 2)
            except (ValueError, TypeError):
                continue
            rank = {"exact_item_number": 3, "partial_item_number": 2, "name_keyword": 1}
            candidate = {
                "receipt_item": ri["name"],
                "receipt_price": ri["price"],
                "receipt_item_number": ri_num,
                "receipt_date": ri["receipt_date"],
                "store": ri["store"],
                "tpd_at_purchase": ri["tpd"],
                "original_price": ri.get("original_price", ""),
                "deal_name": d["item_name"],
                "deal_price": d["sale_price"],
                "deal_item_number": d_num,
                "deal_source": d.get("source", ""),
                "deal_link": d.get("link", ""),
                "deal_expiry": d.get("promo_end", ""),
                "matched_by": matched_by,
                "savings": savings,
                "_rank": rank.get(matched_by, 0),
            }
            if best is None or (savings, candidate["_rank"]) > (best["savings"], best["_rank"]):
                best = candidate
        if best:
            del best["_rank"]
            candidates.append(best)

    candidates.sort(key=lambda m: m.get("receipt_date", ""))

    # TPD items (already discounted at checkout)
    tpd_items = [ri for ri in receipt_items if ri.get("tpd")]

    return {
        "candidates": candidates,
        "tpd_items": tpd_items,
        "receipt_count": len(receipts),
        "deal_count": len(drops),
    }

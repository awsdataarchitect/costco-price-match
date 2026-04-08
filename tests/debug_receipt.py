"""
Debug script for receipt parsing issues.
Usage:
    py tests\debug_receipt.py "C:\path\to\receipt.pdf"
"""
import sys, os, re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.receipt_parser import inspect_pdf, _parse_text, _post_process


def debug(pdf_path: str):
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    print(f"\n{'='*60}")
    print(f"FILE: {os.path.basename(pdf_path)}  ({len(pdf_bytes):,} bytes)")
    print("="*60)

    # ── Step 1: inspect ──────────────────────────────────────────
    print("\n[1] inspect_pdf()")
    info = inspect_pdf(pdf_bytes)
    for k, v in info.items():
        print(f"    {k}: {v}")

    # ── Step 2: raw text ─────────────────────────────────────────
    print("\n[2] Raw text from PyMuPDF:")
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for i, page in enumerate(doc):
        text = page.get_text()
        print(f"    --- Page {i} ({len(text.strip())} chars) ---")
        for line in text.splitlines():
            print(f"    | {repr(line)}")
    doc.close()

    # ── Step 3: regex trace (inline test of normalization + matching) ──────────
    print("\n[3a] Dot-normalization + regex trace (independent of _parse_text import):")
    import re as _re
    doc2 = fitz.open(stream=pdf_bytes, filetype="pdf")
    raw_lines = [l.strip() for l in "\n".join(p.get_text() for p in doc2).split("\n")]
    doc2.close()
    norm_lines = [_re.sub(r'\s*\.{3,}\s*', ' ', l) for l in raw_lines]
    INLINE_FULL = _re.compile(r"^(\d{1,8})\s+(.+?)\s+(-?\d+\.\d{2}-?)\s*[A-Z]?$")
    STOP = {"SUBTOTAL", "TAX", "TOTAL", "CHANGE", "VISA", "MASTERCARD", "AMEX", "DISCOVER"}
    for orig, norm in zip(raw_lines, norm_lines):
        if not norm:
            continue
        if any(norm.upper().startswith(w) for w in STOP):
            print(f"    STOP:  {norm!r}")
            break
        m = INLINE_FULL.match(norm)
        if m:
            print(f"    MATCH: item={m.group(1)!r}  name={m.group(2)!r}  price={m.group(3)!r}")
        else:
            print(f"    MISS:  {norm!r}")

    # ── Step 4: _parse_text via imported module ──────────────────────────────
    print("\n[3b] _parse_text() via imported services.receipt_parser:")
    import services.receipt_parser as _rp
    print(f"    module file: {_rp.__file__}")
    if not info["has_text"]:
        print("    ⚠ No selectable text — text parser would be skipped")
    else:
        raw = _rp._parse_text(pdf_bytes)
        print(f"    store:        {raw.get('store', '')!r}")
        print(f"    receipt_date: {raw.get('receipt_date', '')!r}")
        raw_items = raw.get("items", [])
        print(f"    raw items:    {len(raw_items)}")
        for item in raw_items:
            print(f"      {item}")

        if raw_items:
            processed = _rp._post_process(raw_items)
            print(f"\n[4] _post_process() → {len(processed)} items kept:")
            for item in processed:
                print(f"      {item}")
        else:
            print("\n[4] No items to post-process (0 raw items from _parse_text)")
            print("     ↳ The regex trace above (Step 3a) shows the ground truth.")
            print("       If 3a shows MATCHes but 3b shows 0 items, it's a stale .pyc cache.")
            print("       Fix: delete services/__pycache__/ and re-run")

    print("\n" + "="*60)
    print("Done")
    print("="*60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: py tests\\debug_receipt.py <path_to_receipt.pdf>")
        sys.exit(1)
    debug(sys.argv[1])

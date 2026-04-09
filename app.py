from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from services import db, receipt_parser, price_scanner, analyzer
import hashlib

app = FastAPI(title="Costco Receipt Scanner")
app.add_middleware(CORSMiddleware, allow_origins=["https://costco.dunkinspeeps.com", "http://localhost:8000"], allow_methods=["*"], allow_headers=["*"], expose_headers=["*"])
db.ensure_tables()
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def root():
    return FileResponse("static/index.html")


@app.get("/api/config")
def get_config():
    """Unauthenticated endpoint returning pool config + credentials for iOS BYOI flow."""
    import os, json, boto3
    region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    result = {
        "user_pool_id": os.environ.get("USER_POOL_ID", ""),
        "user_pool_client_id": os.environ.get("USER_POOL_CLIENT_ID", ""),
        "region": region,
        "username": "",
        "password": "",
    }
    secret_arn = os.environ.get("APP_SECRET_ARN", "")
    if secret_arn:
        try:
            sm = boto3.client("secretsmanager", region_name=region)
            secret = json.loads(sm.get_secret_value(SecretId=secret_arn)["SecretString"])
            result["username"] = secret.get("username", "")
            result["password"] = secret.get("password", "")
        except Exception as e:
            print(f"Failed to read secret: {e}")
    return result


IMAGE_EXTENSIONS = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".gif": "gif", ".webp": "webp"}

@app.post("/api/upload")
async def upload_receipt(file: UploadFile = File(...)):
    ext = "." + file.filename.lower().rsplit(".", 1)[-1] if "." in file.filename else ""
    is_pdf = ext == ".pdf"
    is_image = ext in IMAGE_EXTENSIONS
    if not is_pdf and not is_image:
        raise HTTPException(400, "Supported formats: PDF, JPG, PNG, GIF, WebP")
    file_bytes = await file.read()
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 10MB)")
    # Validate file magic bytes match claimed type
    magic_map = {b'%PDF': '.pdf', b'\xff\xd8\xff': '.jpg', b'\x89PNG': '.png',
                 b'RIFF': '.webp', b'GIF8': '.gif'}
    detected = next((e for sig, e in magic_map.items() if file_bytes[:len(sig)] == sig), None)
    if is_pdf and detected != '.pdf':
        raise HTTPException(400, "File content is not a valid PDF")
    if is_image and detected not in IMAGE_EXTENSIONS:
        raise HTTPException(400, "File content does not match a supported image format")
    try:
        if is_image:
            parsed = receipt_parser.parse_receipt_image(file_bytes, IMAGE_EXTENSIONS[ext])
        else:
            parsed = receipt_parser.parse_receipt_pdf(file_bytes)
    except Exception as e:
        import logging; logging.getLogger(__name__).error(f"Receipt parse error: {e}", exc_info=True)
        raise HTTPException(500, "Failed to parse receipt. Please try a different file.")
    receipt = db.put_receipt(
        items=parsed.get("items", []),
        receipt_date=parsed.get("receipt_date", ""),
        store=parsed.get("store", ""),
        pdf_hash=hashlib.md5(file_bytes).hexdigest(),
    )
    # Store original file in S3 for potential reparse
    db.upload_pdf(receipt["receipt_id"], file_bytes)
    return {"receipt": receipt, "parsed_items": len(receipt["items"])}


@app.get("/api/receipts")
def list_receipts():
    return {"receipts": db.get_all_receipts()}


@app.delete("/api/receipts")
def clear_all_receipts():
    db.clear_receipts()
    return {"message": "All receipts deleted"}


@app.delete("/api/receipt/{receipt_id}")
def delete_single_receipt(receipt_id: str):
    db.delete_receipt(receipt_id)
    return {"message": "Receipt deleted"}


@app.post("/api/scan-prices")
def scan_prices(force_refresh: bool = False):
    drops, source_results = price_scanner.scan_price_drops(force_refresh)
    return {"price_drops": len(drops), "items": drops, "sources": source_results}


@app.get("/api/price-drops")
def list_price_drops():
    return {"price_drops": db.get_all_price_drops()}


@app.delete("/api/price-drops")
def clear_all_price_drops():
    db.clear_price_drops()
    return {"message": "All price drops deleted"}


@app.delete("/api/price-drop/{item_id}")
def delete_single_deal(item_id: str):
    db.delete_price_drop(item_id)
    return {"message": "Deal deleted"}


@app.get("/api/analyze")
def analyze_receipts(
    receipt_id: str = Query(default=None),
    receipt_ids: str = Query(default=None),
    date_from: str = Query(default=None),
    date_to: str = Query(default=None),
    sources: str = Query(default=None),
):
    src_list = [s.strip() for s in sources.split(",")] if sources else None
    # Support both single receipt_id and comma-separated receipt_ids
    rid_list = None
    if receipt_ids:
        rid_list = [r.strip() for r in receipt_ids.split(",") if r.strip()]
    elif receipt_id:
        rid_list = [receipt_id]
    
    # Check if this is a streaming request (from Amplify with auth)
    # For now, keep existing StreamingResponse for compatibility
    return StreamingResponse(
        analyzer.run_analysis_stream(rid_list, date_from, date_to, src_list),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/receipt/{receipt_id}/pdf")
def get_receipt_pdf(receipt_id: str):
    pdf_bytes = db.download_pdf(receipt_id)
    if not pdf_bytes:
        raise HTTPException(404, "PDF not found")
    return Response(content=pdf_bytes, media_type="application/pdf")


@app.put("/api/receipt/{receipt_id}/item/{index}")
def update_item(receipt_id: str, index: int, item: dict = Body(...)):
    rc = db.get_receipt(receipt_id)
    if not rc or index < 0 or index >= len(rc.get("items", [])):
        raise HTTPException(404, "Item not found")
    db.update_receipt_item(receipt_id, index, item)
    return {"ok": True}


@app.put("/api/receipt/{receipt_id}")
def update_receipt_meta(receipt_id: str, body: dict = Body(...)):
    rc = db.get_receipt(receipt_id)
    if not rc:
        raise HTTPException(404, "Receipt not found")
    updates = "SET "
    values = {}
    names = {}
    parts = []
    if "store" in body:
        parts.append("#store = :s")
        values[":s"] = body["store"]
        names["#store"] = "store"
    if "receipt_date" in body:
        parts.append("receipt_date = :d")
        values[":d"] = body["receipt_date"]
    if not parts:
        raise HTTPException(400, "Nothing to update")
    import boto3, os
    ddb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    table_name = os.environ.get("DYNAMODB_RECEIPTS_TABLE", "CostcoReceipts")
    ddb.Table(table_name).update_item(
        Key={"receipt_id": receipt_id},
        UpdateExpression="SET " + ", ".join(parts),
        ExpressionAttributeValues=values,
        **({"ExpressionAttributeNames": names} if names else {}),
    )
    return {"ok": True}


@app.delete("/api/receipt/{receipt_id}/item/{index}")
def delete_item(receipt_id: str, index: int):
    rc = db.get_receipt(receipt_id)
    if not rc or index < 0 or index >= len(rc.get("items", [])):
        raise HTTPException(404, "Item not found")
    items = rc["items"]
    items.pop(index)
    db.update_receipt_items(receipt_id, items)
    return {"ok": True}


@app.post("/api/reparse/{receipt_id}")
def reparse_receipt(receipt_id: str):
    pdf_bytes = db.download_pdf(receipt_id)
    if not pdf_bytes:
        raise HTTPException(404, "PDF not found in S3 for this receipt")
    try:
        parsed = receipt_parser.parse_receipt_pdf(pdf_bytes, model="premier")
    except Exception as e:
        import logging; logging.getLogger(__name__).error(f"Premier reparse error: {e}", exc_info=True)
        raise HTTPException(500, "Premier reparse failed. Please try again.")
    db.update_receipt_items(
        receipt_id,
        items=parsed.get("items", []),
        store=parsed.get("store", ""),
        receipt_date=parsed.get("receipt_date", ""),
    )
    return {"items": len(parsed.get("items", [])), "model": "premier"}


try:
    from mangum import Mangum
    handler = Mangum(app, lifespan="off")
except ImportError:
    handler = None

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

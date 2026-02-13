import boto3
import uuid
import os
from datetime import datetime

REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-west-2"))
RECEIPTS_TABLE = os.environ.get("DYNAMODB_RECEIPTS_TABLE", "CostcoReceipts")
PRICE_DROPS_TABLE = os.environ.get("DYNAMODB_PRICE_DROPS_TABLE", "CostcoPriceDrops")
PDF_BUCKET = os.environ.get("S3_BUCKET", "costco-receipt-pdfs-scanner")

_ddb = boto3.resource("dynamodb", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)


def ensure_tables():
    existing = boto3.client("dynamodb", region_name=REGION).list_tables()["TableNames"]
    for table_name, pk in [(RECEIPTS_TABLE, "receipt_id"), (PRICE_DROPS_TABLE, "item_id")]:
        if table_name not in existing:
            _ddb.create_table(
                TableName=table_name,
                KeySchema=[{"AttributeName": pk, "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": pk, "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )
            _ddb.Table(table_name).wait_until_exists()


# --- Receipts ---

def put_receipt(items: list, receipt_date: str = "", store: str = "", pdf_hash: str = "") -> dict:
    # Deduplicate by PDF hash
    if pdf_hash:
        existing = _ddb.Table(RECEIPTS_TABLE).scan(
            FilterExpression="pdf_hash = :h",
            ExpressionAttributeValues={":h": pdf_hash},
        )
        if existing["Items"]:
            return existing["Items"][0]

    receipt = {
        "receipt_id": str(uuid.uuid4()),
        "items": items,
        "receipt_date": receipt_date or datetime.now().strftime("%Y-%m-%d"),
        "store": store,
        "upload_date": datetime.now().isoformat(),
        "pdf_hash": pdf_hash,
        "s3_key": "",
    }
    _ddb.Table(RECEIPTS_TABLE).put_item(Item=receipt)
    return receipt


def get_all_receipts() -> list:
    return _ddb.Table(RECEIPTS_TABLE).scan()["Items"]


def get_receipt(receipt_id: str) -> dict | None:
    resp = _ddb.Table(RECEIPTS_TABLE).get_item(Key={"receipt_id": receipt_id})
    return resp.get("Item")


def clear_receipts():
    _batch_delete(RECEIPTS_TABLE, "receipt_id")


def delete_receipt(receipt_id: str):
    _ddb.Table(RECEIPTS_TABLE).delete_item(Key={"receipt_id": receipt_id})
    try:
        _s3.delete_object(Bucket=PDF_BUCKET, Key=f"receipts/{receipt_id}.pdf")
    except Exception:
        pass


def upload_pdf(receipt_id: str, pdf_bytes: bytes) -> str:
    key = f"receipts/{receipt_id}.pdf"
    _s3.put_object(Bucket=PDF_BUCKET, Key=key, Body=pdf_bytes)
    _ddb.Table(RECEIPTS_TABLE).update_item(
        Key={"receipt_id": receipt_id},
        UpdateExpression="SET s3_key = :k",
        ExpressionAttributeValues={":k": key},
    )
    return key


def download_pdf(receipt_id: str) -> bytes | None:
    receipt = get_receipt(receipt_id)
    if not receipt or not receipt.get("s3_key"):
        return None
    return _s3.get_object(Bucket=PDF_BUCKET, Key=receipt["s3_key"])["Body"].read()


def update_receipt_item(receipt_id: str, index: int, item: dict):
    _ddb.Table(RECEIPTS_TABLE).update_item(
        Key={"receipt_id": receipt_id},
        UpdateExpression="SET #items[%d] = :v" % index,
        ExpressionAttributeValues={":v": item},
        ExpressionAttributeNames={"#items": "items"},
    )


def update_receipt_items(receipt_id: str, items: list, store: str = "", receipt_date: str = ""):
    updates = "SET #items = :i"
    values = {":i": items}
    names = {"#items": "items"}
    if store:
        updates += ", #store = :s"
        values[":s"] = store
        names["#store"] = "store"
    if receipt_date:
        updates += ", receipt_date = :d"
        values[":d"] = receipt_date
    _ddb.Table(RECEIPTS_TABLE).update_item(
        Key={"receipt_id": receipt_id},
        UpdateExpression=updates,
        ExpressionAttributeValues=values,
        ExpressionAttributeNames=names,
    )


# --- Price Drops ---

def put_price_drop(item_name: str, sale_price: str, original_price: str = "",
                   promo_start: str = "", promo_end: str = "", source: str = "manual",
                   link: str = "", item_number: str = "") -> dict:
    drop = {
        "item_id": str(uuid.uuid4()),
        "item_name": item_name,
        "item_number": item_number,
        "original_price": original_price,
        "sale_price": sale_price,
        "promo_start": promo_start,
        "promo_end": promo_end,
        "source": source,
        "link": link,
        "scanned_date": datetime.now().isoformat(),
    }
    _ddb.Table(PRICE_DROPS_TABLE).put_item(Item=drop)
    return drop


def get_all_price_drops() -> list:
    return _ddb.Table(PRICE_DROPS_TABLE).scan()["Items"]


def clear_price_drops():
    _batch_delete(PRICE_DROPS_TABLE, "item_id")


def delete_price_drop(item_id: str):
    _ddb.Table(PRICE_DROPS_TABLE).delete_item(Key={"item_id": item_id})


def item_exists(item_name: str, source: str, promo_end: str = "") -> bool:
    try:
        expr = "item_name = :name AND #src = :source"
        vals = {":name": item_name, ":source": source}
        if promo_end:
            expr += " AND promo_end = :pe"
            vals[":pe"] = promo_end
        resp = _ddb.Table(PRICE_DROPS_TABLE).scan(
            FilterExpression=expr,
            ExpressionAttributeNames={"#src": "source"},
            ExpressionAttributeValues=vals,
        )
        return len(resp["Items"]) > 0
    except Exception:
        return False


def get_cached_deals_count() -> int:
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        resp = _ddb.Table(PRICE_DROPS_TABLE).scan(
            FilterExpression="begins_with(scanned_date, :today)",
            ExpressionAttributeValues={":today": today},
        )
        return len(resp["Items"])
    except Exception:
        return 0


def _batch_delete(table_name: str, key_name: str):
    table = _ddb.Table(table_name)
    items = table.scan(ProjectionExpression=key_name)["Items"]
    with table.batch_writer() as batch:
        for item in items:
            batch.delete_item(Key={key_name: item[key_name]})

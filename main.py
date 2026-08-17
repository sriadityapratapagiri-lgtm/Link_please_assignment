import os
import hmac
import hashlib
import uuid
from fastapi import FastAPI, Request, HTTPException, Header
import redis
import psycopg2
from psycopg2.extras import DictCursor
from dotenv import load_dotenv

# This is the line your code was missing! 
load_dotenv()
app = FastAPI()

# Initialize Redis connection
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
API_KEY = os.getenv("API_KEY", "").strip()
def get_db_connection():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

@app.post("/webhook")
async def handle_webhook(
    request: Request,
    x_pseudogram_signature: str = Header(None)
):
    if not x_pseudogram_signature:
        print("❌ Blocked: Missing signature header")
        raise HTTPException(status_code=401, detail="Missing signature header")
        
    raw_body = await request.body()
    
    if not x_pseudogram_signature.startswith("sha256="):
        print("❌ Blocked: Invalid signature format")
        raise HTTPException(status_code=400, detail="Invalid format")
        
    provided_hash = x_pseudogram_signature.split("sha256=")[1]
    
    # ADD THESE TWO LINES:
    print(f"DEBUG: API Key length is {len(API_KEY)}")
    print(f"DEBUG: API Key starts with {API_KEY[:10]}")

    expected_mac = hmac.new(
        API_KEY.encode('utf-8'),
        raw_body,
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(expected_mac, provided_hash):
        print(f"❌ Blocked: Signature mismatch.")
        print(f"   Expected: {expected_mac}")
        print(f"   Received: {provided_hash}")
        raise HTTPException(status_code=401, detail="Signature mismatch")
        
    redis_client.rpush("webhook_queue", raw_body)
    return {"status": "received"}

@app.post("/rules", status_code=201)
async def create_rule(request: Request):
    data = await request.json()
    rule_id = str(uuid.uuid4())
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO rules (rule_id, keyword, dm_message) VALUES (%s, %s, %s)",
            (rule_id, data["keyword"], data["dm_message"])
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()
        
    return {
        "rule_id": rule_id,
        "keyword": data["keyword"],
        "dm_message": data["dm_message"]
    }

@app.get("/stats")
def get_stats():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    try:
        cursor.execute("SELECT duplicates_blocked FROM system_stats WHERE id = 1")
        duplicates = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT status, COUNT(*) 
            FROM outbound_dms 
            GROUP BY status
        """)
        counts = {row["status"]: row["count"] for row in cursor.fetchall()}
    finally:
        cursor.close()
        conn.close()
        
    return {
        "sent": counts.get("sent", 0),
        "failed": counts.get("failed", 0),
        "queued": counts.get("queued", 0),
        "duplicates_blocked": duplicates
    }
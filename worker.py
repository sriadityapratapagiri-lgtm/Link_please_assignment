import os
import json
import time
import hashlib
import psycopg2
from psycopg2.errors import UniqueViolation
from psycopg2.extras import DictCursor
import redis
import requests
from dotenv import load_dotenv

# Load environment variables so it can find your API key and Database
load_dotenv()

# Initialize Redis and variables
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://red-da1gm815efls73egkm30:6379"))
API_KEY = os.getenv("API_KEY")
API_BASE = "https://pseudogram-api.onrender.com"
DB_URL = os.getenv("postgresql://linkplease_342l_user:zrTzW13d1gvwj44tm8WMw4JiDaVwrqgU@dpg-da1gktpt0dsc73br7hug-a/linkplease_342l")

def get_db_connection():
    conn = psycopg2.connect(DB_URL)
    # Autocommit allows catching individual UniqueViolations without transaction aborts
    conn.autocommit = True 
    return conn

def process_event(raw_body):
    data = json.loads(raw_body)
    event_id = data["event_id"]
    event_type = data["event_type"]
    payload = data.get("data", {})
    comment_id = payload.get("comment_id")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    
    try:
        # --- 1. Webhook Deduplication ---
        try:
            cursor.execute("INSERT INTO processed_events (event_id) VALUES (%s)", (event_id,))
        except UniqueViolation:
            # We already saw this webhook. Increment the global stat and drop it.
            cursor.execute("UPDATE system_stats SET duplicates_blocked = duplicates_blocked + 1 WHERE id = 1")
            return  
            
        # --- 2. Tombstone Check ---
        if event_type == "comment.deleted":
            cursor.execute(
                "INSERT INTO deleted_comments (comment_id) VALUES (%s) ON CONFLICT (comment_id) DO NOTHING", 
                (comment_id,)
            )
            return
            
        cursor.execute("SELECT 1 FROM deleted_comments WHERE comment_id = %s", (comment_id,))
        if cursor.fetchone():
            return  # The comment was deleted before this 'created' event arrived. Drop it.
            
        # --- 3. Rule Evaluation ---
        text = payload.get("text", "")
        cursor.execute("SELECT rule_id, keyword, dm_message FROM rules")
        rules = cursor.fetchall()
        
        # Guard against malformed payloads missing 'from'
        if "from" not in payload or "user_id" not in payload["from"]:
            return
            
        user_id = payload["from"]["user_id"]
        
        for rule in rules:
            # Case-insensitive substring match
            if rule["keyword"].lower() in text.lower():
                
                # --- 4. User-Rule Deduplication ---
                try:
                    cursor.execute(
                        "INSERT INTO outbound_dms (user_id, rule_id, status) VALUES (%s, %s, 'queued') RETURNING id",
                        (user_id, rule["rule_id"])
                    )
                    outbound_id = cursor.fetchone()[0]
                except UniqueViolation:
                    # User already triggered this specific rule. Block it.
                    cursor.execute("UPDATE system_stats SET duplicates_blocked = duplicates_blocked + 1 WHERE id = 1")
                    continue 
                    
                # --- 5. Execute DM Request ---
                idempotency_key = hashlib.sha256(f"{user_id}:{rule['rule_id']}".encode()).hexdigest()
                
                headers = {
                    "X-API-Key": API_KEY,
                    "Idempotency-Key": idempotency_key
                }
                req_body = {
                    "recipient_user_id": user_id,
                    "message": rule["dm_message"],
                    "comment_id": comment_id
                }
                
                # In-worker retry loop
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        resp = requests.post(f"{API_BASE}/v1/dm/send", headers=headers, json=req_body)
                        
                        if resp.status_code == 202:
                            dm_id = resp.json()["dm_id"]
                            cursor.execute("UPDATE outbound_dms SET dm_id = %s WHERE id = %s", (dm_id, outbound_id))
                            break
                        elif resp.status_code == 429:
                            retry_after = int(resp.headers.get("Retry-After", 60))
                            time.sleep(retry_after) 
                        elif resp.status_code == 500:
                            time.sleep(2 ** attempt) 
                        elif resp.status_code == 400:
                            cursor.execute("UPDATE outbound_dms SET status = 'failed' WHERE id = %s", (outbound_id,))
                            break
                    except requests.exceptions.RequestException:
                        time.sleep(2 ** attempt)
                else:
                    # Triggered if retries are exhausted
                    cursor.execute("UPDATE outbound_dms SET status = 'failed' WHERE id = %s", (outbound_id,))
                    
    finally:
        cursor.close()
        conn.close()

def run_worker():
    print("Worker listening on webhook_queue...")
    while True:
        # blpop blocks indefinitely until an item is available
        _, raw_body = redis_client.blpop("webhook_queue")
        try:
            process_event(raw_body)
        except Exception as e:
            print(f"Failed to process event: {e}")

if __name__ == "__main__":
    run_worker()
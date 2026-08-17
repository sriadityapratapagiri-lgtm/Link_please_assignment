-- 1. Rules: Stores the keyword triggers and messages
CREATE TABLE IF NOT EXISTS rules (
    rule_id VARCHAR(255) PRIMARY KEY,
    keyword VARCHAR(255) NOT NULL,
    dm_message TEXT NOT NULL
);

-- 2. Processed Events: The shield against redelivered webhooks (event_id repeats)
CREATE TABLE IF NOT EXISTS processed_events (
    event_id VARCHAR(255) PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. Deleted Comments: The tombstone table for out-of-order events
CREATE TABLE IF NOT EXISTS deleted_comments (
    comment_id VARCHAR(255) PRIMARY KEY,
    deleted_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 4. Outbound DMs: The core state machine and user deduplication
CREATE TABLE IF NOT EXISTS outbound_dms (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    rule_id VARCHAR(255) NOT NULL REFERENCES rules(rule_id),
    dm_id VARCHAR(255),
    status VARCHAR(50) NOT NULL CHECK (status IN ('queued', 'sent', 'failed')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    -- CRITICAL: This constraint enforces the "never DM twice for the same rule" requirement
    UNIQUE(user_id, rule_id)
);

-- 5. System Stats: The atomic counter for /stats
CREATE TABLE IF NOT EXISTS system_stats (
    id INT PRIMARY KEY,
    duplicates_blocked INT NOT NULL DEFAULT 0
);

-- Seed the initial row for system_stats so the worker can safely UPDATE it
INSERT INTO system_stats (id, duplicates_blocked) 
VALUES (1, 0)
ON CONFLICT (id) DO NOTHING;
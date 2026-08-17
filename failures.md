System Failures & Edge Cases
This document outlines the known edge cases and potential failure points within the current architecture (FastAPI ingress → Redis Queue → Background Worker → PostgreSQL) where data loss, duplication, or metric inaccuracies could occur under heavy load or hardware failure.

1. Losing a Direct Message (In-Memory Queue Crash)
If the Redis instance crashes or restarts before the background worker processes the queued webhooks, any pending HTTP requests currently stored in memory will be lost. Because the FastAPI server immediately returns a 200 OK after pushing to Redis, the mock API believes the payload is safe, but nothing on disk actually knows it was pending.

2. Losing a Direct Message (Worker Death Mid-Execution)
If the worker.py process is killed (e.g., due to an out-of-memory error or manual restart) exactly after pulling a request from the Redis queue using blpop, but before it successfully dispatches the POST request to the external /dm/send API, the event is permanently dropped. The message is no longer in the queue and hasn't reached the external provider.

3. Sending a Duplicate DM (The Two-Generals Problem)
If the worker successfully sends the DM via the external API and receives a 202 Accepted, but the connection to the PostgreSQL database drops immediately before the worker can commit the unique idempotency key to the outbound_dms table, the database has no record of the action. If the mock API redelivers that same webhook later, the system will process it as a brand-new request, bypass the deduplication check, and send a duplicate DM.

4. Reporting Inaccurate Numbers (Non-Atomic Updates)
If updating the outbound_dms status and incrementing the counters in system_stats are not wrapped in a strict, single database transaction (BEGIN / COMMIT), a sudden database or worker crash between the two operations will leave the /stats endpoint permanently out of sync. For example, a DM could be marked as "failed" in the tables, but the failed counter in the stats response might not reflect it.
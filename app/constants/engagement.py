# Batch engagement ingestion (POST /batch/) — see app/rest/batch.py.
# Sanity caps, not real limits: the FE already pre-aggregates client-side
# before flushing, so a well-behaved client never gets close to either.
BATCH_MAX_EVENTS = 200
BATCH_MAX_EVENT_COUNT = 1000

# config.py — shared configuration for FIFA buyer/farmer bots

# ─── Queue / Shop target ─────────────────────────────────────────
# The FIFA queue entry URL (e.g. https://access.tickets.fifa.com/...)
TARGET_URL = "https://access.tickets.fifa.com"

# ─── Solve-server default port ───────────────────────────────────
# Used by the main bot; queue_farmer overrides this with FARM_SERVER_PORT (9099)
SERVER_PORT = 9098

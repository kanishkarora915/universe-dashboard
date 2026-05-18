"""
Trinity Engine — Real vs Trap vs Fake move detection.

Triangulates 3 streams:
  1. Nifty Spot (truth)
  2. Nifty Future (institutional intent via premium/discount)
  3. Synthetic Nifty (CE/PE put-call parity stress)

7-regime classifier: REAL_RALLY, REAL_CRASH, BULL_TRAP, BEAR_TRAP,
DISTRIBUTION, ACCUMULATION, CHURN.

Author: Kanishk Arora
"""

from .storage import init_db, DB_PATH

__all__ = ["init_db", "DB_PATH"]

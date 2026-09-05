import sys
sys.path.insert(0, '/opt/leads')
from ig_client import dm

msg = ("Hey Saan, just saw your SPX bull-mode call on ES - solid breakdown of the shift in momentum, "
       "especially with that volume confirmation. Curious if you think this rally has legs into next week's CPI, "
       "or if we're just filling a gap before another leg down. What's your key level to watch if we pull back?")

ok = dm("swing_trader_saan", msg, min_delay=90, max_delay=180)
print("RESULT:", "SUCCESS" if ok else "FAILED")
sys.exit(0 if ok else 1)

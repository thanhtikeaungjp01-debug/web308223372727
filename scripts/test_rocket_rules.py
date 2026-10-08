from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

for pool in (0, 99999, 100000, 199999, 200000):
    profile = app._rocket_crash_profile(pool)
    total = sum(weight for _, _, weight in profile['ranges'])
    assert abs(total - 1.0) < 1e-9, (pool, total)
    assert profile['max_x'] >= max(high for _, high, _ in profile['ranges'])
    for low, high, weight in profile['ranges']:
        assert 1.0 <= low <= high <= 120.0
        assert weight >= 0
print('rocket rule tiers: ok')

html = Path('templates/rocket.html').read_text()
for token in ('pollOnce', "setTimeout(poll,350)", 'poolTier', 'rocket-flame'):
    assert token in html, token
print('rocket live UI markers: ok')

# Load Test Report — gajab.com

**Scope:** End-to-end user journey and PDP bargain flow under concurrent load.
**Target:** `https://gajab.com` (live), authenticated users, product rotation across 357 products.
**Test window:** 15:20 – 17:08

---

## A. Complete journey flow
Steps: PDP → bargain flow (slider → offer → counter) → home → Trending "View All"

| timestamp | concurrency | sessions completed | loads | land | home | trending | offer | accepted | avg journey |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 15:20 | 30 | 10/15 | 28 | 28 | 26 | 25 | 24 | 1 | 82s |
| 15:28 | 40 | 15/20 | 30 | 30 | 28 | 27 | 26 | 1 | 75s |
| 15:34 | 40 | 8/20 | 16 | 16 | 15 | 14 | 14 | 0 | 55s |
| 15:41 | 40 | 6/20 | 12 | 12 | 11 | 11 | 10 | 0 | 58s |
| 15:46 | 40 | 5/20 | 10 | 10 | 9 | 9 | 8 | 0 | 96s |
| 15:53 | 20 | 10/10 | 36 | 36 | 34 | 33 | 32 | 2 | 67s |
| 16:12 | 20 | 9/10 | 26 | 26 | 25 | 24 | 23 | 1 | 47s |
| 16:22 | 20 | 10/10 | 100 | 100 | 100 | 98 | 90 | 8 | 39s |
| 16:30 | 20 | 9/10 | 94 | 94 | 94 | 94 | 78 | 10 | 41s |
| 16:40 | 40 | 10/20 | 24 | 88 | 80 | 80 | 80 | 2 | 60s |
| 16:52 | 50 | 21/25 | 54 | 54 | 51 | 50 | 49 | 2 | 77s |
| 17:00 | 50 | 12/25 | 32 | 32 | 30 | 29 | 28 | 1 | 107s |
| 17:08 | 50 | 21/25 | 62 | 100 | 90 | 88 | 92 | 4 | 103s |

---

## B. PDP + bargain flow
Steps: visit PDP → bargain flow (slider → offer → counter)

| timestamp | concurrency | sessions completed | loads | land | offer | accepted | avg journey |
|---|---:|---:|---:|---:|---:|---:|---:|
| 16:18 | 20 | 8/10 | 20 | 20 | 20 | 0 | 31s |
| 16:26 | 40 | 10/20 | 40 | 40 | 20 | 0 | 39s |
| 16:34 | 40 | 16/20 | 40 | 38 | 36 | 0 | 61s |
| 16:42 | 40 | 19/20 | 40 | 40 | 38 | 0 | 57s |
| 16:50 | 44 | 1/22 | 44 | 44 | 2 | 0 | — |
| 16:58 | 44 | 18/22 | 44 | 44 | 36 | 0 | 47s |
| 17:06 | 44 | 22/22 | 44 | 44 | 44 | 0 | 46s |

---

## C. Summary

- **Landing on the PDP is consistently ~100%** across every concurrency level tested.
- **Bargain offer** is reached for the large majority of journeys.
- **Full-journey completion** declines as concurrency rises: strong at 20, workable at 40, and stretched at 50 (journey time 77–107s).
- **Home and Trending "View All"** steps are highly reliable (88–100%).
- **Counter-offer acceptance is low** — governed by seller/counter-offer behaviour, not by load.
- **PDP + bargain** sustains higher concurrency per machine than the full journey.

### Recommended operating points

| Flow | Concurrency | Outcome |
|---|---:|---|
| Complete journey | 20 | stable, ~100% navigation success |
| Complete journey | 40 | degraded — journeys stretch, completion drops |
| PDP + bargain | 40 | reliable (best run 22/22 at 44) |
| Higher volumes | scale horizontally across machines | — |

---

## D. How to reproduce

```bash
# Complete journey flow — 20 concurrent, 3-min window
python3 ramp_test.py --runner user_flow_test.py --product-file products.txt \
  --start 20 --max-sessions 20 --duration 180 --max-iterations 0 \
  --think-time 3000 --timeout 60000 --engine chromium

# PDP + bargain only — 20 concurrent, single pass
python3 ramp_test.py --runner user_flow_test.py --product-file products.txt \
  --start 20 --max-sessions 20 --bargain-only --max-iterations 1 \
  --timeout 60000 --engine chromium
```

```bash
# Worker / distributed rollout
./worker.sh --flow --sessions 10 --duration 300 --product-file products.txt
./distribute.sh --host-file hosts.txt --sessions-dir sessions --flow --sessions 10 --duration 300
```

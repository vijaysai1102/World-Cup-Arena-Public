"""
Halftime betting script — run at kickoff + 45 minutes (within the 15-min halftime window).
This complements _run.py (pre-match). Each match has TWO independent windows:
  - Pre-match : run _run.py before kickoff
  - Halftime  : run THIS script ~45 min after kickoff (window closes at kickoff+60)
Requires the same ARENA_KEY and ANTHROPIC_KEY as _run.py.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import os, json, time, uuid, requests, re, anthropic

# ── Config (same as _run.py) ───────────────────────────────────────────────────
ARENA            = "https://staging.stair-ai.com"
SPORTMONKS_PROXY = f"{ARENA}/api/v1/data/proxy/sportmonks/v3/football"
POLYMARKET_CLOB  = f"{ARENA}/api/v1/data/proxy/polymarket-clob"
POLYMARKET_GAMMA = f"{ARENA}/api/v1/data/proxy/polymarket-gamma"
ARENA_KEY        = "YOUR_ARENA_KEY_HERE"
ANTHROPIC_KEY    = "YOUR_ANTHROPIC_KEY_HERE"
H_ARENA          = {"x-api-key": ARENA_KEY}

SPORTMONKS_SEASON_ID  = 26618
SPORTMONKS_FIXTURE_ID = 19609127   # MEX vs ZAF — update each match day

LLM_MODEL             = "claude-haiku-4-5-20251001"
LLM_MAX_TOKENS        = 2400
LLM_THINKING_BUDGET   = 1024
LLM_THINKING          = {"type": "enabled", "budget_tokens": LLM_THINKING_BUDGET}
LEDGER_SCHEMA_VERSION = "0.3"

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


def _extract(resp):
    if hasattr(resp, "content") and isinstance(resp.content, list):
        text_parts, thinking_parts = [], []
        for block in resp.content:
            if block.type == "thinking":
                thinking_parts.append(block.thinking)
            elif block.type == "text":
                text_parts.append(block.text)
        return "".join(text_parts), "\n\n".join(thinking_parts)
    if hasattr(resp, "choices"):
        msg = resp.choices[0].message
        return (msg.content or ""), (getattr(msg, "reasoning_content", "") or "")
    if hasattr(resp, "text"):
        return (resp.text or ""), ""
    raise TypeError(f"Unrecognized LLM response type: {type(resp)!r}")


# ── Step 1: Fetch live fixture data with halftime score ────────────────────────
print("Fetching live fixture data...")
r = requests.get(
    f"{SPORTMONKS_PROXY}/fixtures/{SPORTMONKS_FIXTURE_ID}",
    params={"include": "participants;predictions;odds;scores;xGFixture"},
    headers=H_ARENA, timeout=60,
)
r.raise_for_status()
fixture = r.json()["body"]["data"]

home = next(p for p in fixture["participants"] if p["meta"]["location"] == "home")
away = next(p for p in fixture["participants"] if p["meta"]["location"] == "away")
print(f"Fixture : {fixture['name']}")

# Extract halftime score
ht_home_goals = ht_away_goals = None
for s in (fixture.get("scores") or []):
    desc = (s.get("description") or "").upper()
    if desc in ("HT", "1ST_HALF", "HALF_TIME", "45'"):
        sc = s.get("score") or {}
        if sc.get("participant") == "home":
            ht_home_goals = sc.get("goals")
        elif sc.get("participant") == "away":
            ht_away_goals = sc.get("goals")

if ht_home_goals is None or ht_away_goals is None:
    # Try CURRENT score as fallback (if HT description not yet set)
    for s in (fixture.get("scores") or []):
        desc = (s.get("description") or "").upper()
        if desc in ("CURRENT", "FT", "90'", "2ND_HALF"):
            continue  # skip full-time scores
        sc = s.get("score") or {}
        if sc.get("participant") == "home" and ht_home_goals is None:
            ht_home_goals = sc.get("goals")
        elif sc.get("participant") == "away" and ht_away_goals is None:
            ht_away_goals = sc.get("goals")

ht_score = (
    f"{home['short_code']} {ht_home_goals}-{ht_away_goals} {away['short_code']}"
    if ht_home_goals is not None and ht_away_goals is not None
    else "score unavailable"
)
print(f"HT score: {ht_score}")


# ── Step 2: Get Polymarket prices (updated for in-play) ───────────────────────
print("Fetching Polymarket halftime prices...")
# Find the Polymarket market for this fixture
slug_r = requests.get(
    f"{ARENA}/api/v1/web/mapping",
    params={"fixture_id": SPORTMONKS_FIXTURE_ID},
    headers=H_ARENA, timeout=30,
)
polymarket_mids = {}
POLYMARKET_MARKET_SLUG = None
if slug_r.ok:
    mapping = slug_r.json().get("body") or {}
    POLYMARKET_MARKET_SLUG = mapping.get("polymarket_market_slug") or mapping.get("slug")

if POLYMARKET_MARKET_SLUG:
    mkt_r = requests.get(
        f"{ARENA}/api/v1/data/polymarket/markets/{POLYMARKET_MARKET_SLUG}",
        headers=H_ARENA, timeout=30,
    )
    if mkt_r.ok:
        for mkt in (mkt_r.json().get("body") or {}).get("data") or []:
            slug = (mkt.get("market_slug") or "").lower()
            mid  = mkt.get("current_mid_yes")
            if mid is None:
                continue
            if any(x in slug for x in [home["short_code"].lower(), "home"]):
                polymarket_mids["home"] = mid
            elif "draw" in slug:
                polymarket_mids["draw"] = mid
            elif any(x in slug for x in [away["short_code"].lower(), "away"]):
                polymarket_mids["away"] = mid

print(f"Polymarket mids: {polymarket_mids}")


# ── Step 3: Aggregate bookmaker odds (same as pre-match) ───────────────────────
from collections import defaultdict as _dd
_bk_rows = [o for o in (fixture.get("odds") or []) if o.get("market_id") == 1]
_bk_label = _dd(list)
for _o in _bk_rows:
    _lbl = (_o.get("label") or "").strip().lower()
    _p = str(_o.get("probability") or "").replace("%", "").strip()
    try:
        _bk_label[_lbl].append(float(_p))
    except ValueError:
        pass
_bk_n = max((len(_bk_label.get(l, [])) for l in ("home", "draw", "away")), default=0)
_bk_avg = lambda lbl: round(sum(_bk_label[lbl]) / len(_bk_label[lbl]) / 100, 4) if _bk_label.get(lbl) else None
_bk_summary = {
    "home_avg_implied_prob": _bk_avg("home"),
    "draw_avg_implied_prob": _bk_avg("draw"),
    "away_avg_implied_prob": _bk_avg("away"),
    "bookmaker_count": _bk_n,
} if _bk_n > 0 else None


# ── Step 4: Halftime prediction via LLM ───────────────────────────────────────
HT_PREDICT_SYS = (
    "You are a soccer match analyst producing HALFTIME win-probability estimates "
    "for the second half and final result of a match.\n\n"

    "## Context\n"
    "The first half has ended. You have the halftime score and updated market data.\n"
    "The halftime score is the most important signal — use it as your primary anchor.\n\n"

    "## Signal weighting (most to least reliable)\n"
    "  1. halftime_score — most powerful signal. A goal up at HT wins ~75% of the time.\n"
    "  2. polymarket_mids — live in-play prices reflect sharp money + first-half performance.\n"
    "  3. bookmaker_ht_odds — live bookmaker consensus at halftime.\n"
    "  4. pre_match_priors — useful baseline but less relevant once the score is known.\n\n"

    "## Score interpretation guidelines\n"
    "  - 1-0 lead at HT: leading team wins ~72%, draw ~17%, trailing team wins ~11%.\n"
    "  - 2-0 lead at HT: leading team wins ~87%, draw ~9%, trailing team wins ~4%.\n"
    "  - 0-0 at HT: roughly equal odds unless one team dominated possession/xG.\n"
    "  - Adjust for team quality (FIFA ranking gap) and xG dominance.\n\n"

    "## Output schema (return ONLY this JSON — no prose, no code fences)\n"
    "{\n"
    "  'fixture'    : str,\n"
    "  'ht_score'   : str,                          // e.g. 'MEX 1-0 ZAF'\n"
    "  'win_prob'   : {home_code: float, 'draw': float, away_code: float},\n"
    "  'outcome'    : str,                          // highest win_prob outcome\n"
    "  'probability': float,                        // win_prob[outcome]\n"
    "  'rationale'  : str,                          // 2-3 sentences. Name teams, score, key signals.\n"
    "  'used_signals': {'ht_score': 'primary', 'polymarket': 'leaned_on' | 'unavailable'},\n"
    "  'confidence_level': 'high' | 'medium' | 'low'\n"
    "}\n\n"

    "Be decisive. The halftime score is strong evidence — do not revert to pre-match priors "
    "if there is a clear lead. Output probabilities that sum to approximately 1.0."
)

ht_input = json.dumps({
    "fixture":        fixture["name"],
    "home_code":      home["short_code"],
    "away_code":      away["short_code"],
    "halftime_score": ht_score,
    "ht_home_goals":  ht_home_goals,
    "ht_away_goals":  ht_away_goals,
    "polymarket_mids": polymarket_mids,
    "bookmaker_ht_summary": _bk_summary,
    "pre_match_priors": {
        "predictions": fixture.get("predictions"),
        "xGFixture":   fixture.get("xgfixture"),
    },
})

print("\nRunning halftime LLM prediction...")
llm_ht = client.messages.create(
    model=LLM_MODEL,
    max_tokens=LLM_MAX_TOKENS,
    thinking=LLM_THINKING,
    system=HT_PREDICT_SYS,
    messages=[{"role": "user", "content": ht_input}],
)
raw_ht, thinking_ht = _extract(llm_ht)
m = re.search(r"\{.*\}", raw_ht, re.DOTALL)
ht_prediction = json.loads(m.group(0)) if m else None

print(f"HT prediction ({len(thinking_ht)} chars thinking):")
print(json.dumps(ht_prediction, indent=2))


# ── Step 5: Strategy ───────────────────────────────────────────────────────────
HT_STRATEGY_SYS = (
    "You are a bankroll manager for a $100 demo account making HALFTIME bets.\n\n"

    "## Rules\n"
    "  - Polymarket is BUY-YES only. direction must ALWAYS be 'long'.\n"
    "  - Always pick the outcome with the highest positive edge.\n"
    "  - Edge = agent_win_prob[outcome] - polymarket_mid[outcome].\n"
    "  - Only bet if edge > 0.03 (3 percentage points). Skip if all edges are small.\n"
    "  - At halftime, be more decisive — the score provides strong evidence.\n"
    "  - Bet 8-15% of remaining wallet per match (halftime bets are higher-conviction).\n\n"

    "## Output schema (return ONLY this JSON)\n"
    "{\n"
    "  'outcome'    : str | null,         // home_code | 'draw' | away_code; null if no edge\n"
    "  'direction'  : 'long',             // ALWAYS 'long'\n"
    "  'size_usdc'  : float,              // bet size in USD (min $1)\n"
    "  'limit_price': float,              // Polymarket YES price to pay (0.01-0.99)\n"
    "  'edge'       : float,              // agent_prob - market_mid for chosen outcome\n"
    "  'should_trade': bool,              // true if edge > 0.03\n"
    "  'rationale'  : str                 // 1-2 sentences\n"
    "}"
)

strat_input = json.dumps({
    "ht_prediction":  ht_prediction,
    "agent_win_prob": (ht_prediction or {}).get("win_prob"),
    "polymarket_mids": polymarket_mids,
    "wallet_balance":  100,   # approximate; update if you know exact balance
    "window":          "halftime",
    "ht_score":        ht_score,
})

print("\nRunning halftime strategy...")
llm_strat = client.messages.create(
    model=LLM_MODEL,
    max_tokens=LLM_MAX_TOKENS,
    thinking=LLM_THINKING,
    system=HT_STRATEGY_SYS,
    messages=[{"role": "user", "content": strat_input}],
)
raw_strat, thinking_strat = _extract(llm_strat)
m2 = re.search(r"\{.*\}", raw_strat, re.DOTALL)
ht_strategy = json.loads(m2.group(0)) if m2 else None

print("Strategy:")
print(json.dumps(ht_strategy, indent=2))


# ── Step 6: Place halftime order ───────────────────────────────────────────────
order_payload  = None
order_response = None

if ht_strategy and ht_strategy.get("should_trade"):
    team_code = ht_strategy.get("outcome")
    if team_code is not None:
        order_payload = {
            "fixture_id":             str(SPORTMONKS_FIXTURE_ID),
            "team_code":              team_code,
            "usd_size":               str(ht_strategy["size_usdc"]),
            "limit_price":            ht_strategy["limit_price"],
            "time_in_force_seconds":  30,
            "idempotency_key":        str(uuid.uuid4()),
        }
        print(f"\nPlacing halftime order: {team_code} ${ht_strategy['size_usdc']} @ {ht_strategy['limit_price']}")
        order_r = requests.post(
            f"{ARENA}/api/v1/arena/orders",
            json=order_payload,
            headers=H_ARENA, timeout=30,
        )
        order_response = order_r.json()
        print(f"Order response: {json.dumps(order_response, indent=2)}")
    else:
        print("Strategy: no trade (outcome is None)")
else:
    reason = (ht_strategy or {}).get("rationale", "no strategy returned")
    print(f"No trade: {reason}")


# ── Step 7: Reasoning ledger ────────────────────────────────────────────────────
LEDGER_SESSION_ID = f"halftime:{SPORTMONKS_FIXTURE_ID}:{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"

def _new_record(behavior, **fields):
    rec = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "session_id":     LEDGER_SESSION_ID,
        "record_id":      str(uuid.uuid4()),
        "behavior":       behavior,
        "client_ts_utc":  int(time.time() * 1000),
    }
    rec.update({k: v for k, v in fields.items() if v is not None})
    return rec

def _mi(resp):
    if resp is None:
        return {"provider": "none", "model_name": "none", "tokens_in": 0, "tokens_out": 0}
    _, thinking = _extract(resp)
    if hasattr(resp, "usage") and hasattr(resp.usage, "input_tokens"):
        provider, model = "anthropic", LLM_MODEL
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
    else:
        provider, model, tokens_in, tokens_out = "unknown", LLM_MODEL, None, None
    mi = {"provider": provider, "model_name": model, "tokens_in": tokens_in, "tokens_out": tokens_out}
    if thinking:
        mi["internal_reasoning"] = thinking
    return mi

def _trunc(obj, limit=30000):
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return s if len(s) <= limit else s[:limit] + f"...[truncated]"

# Ledger records
records = []

# (1) Observing — halftime trigger
records.append(_new_record(
    "Observing",
    trigger_source="halftime-window",
    trigger_type="cron_trigger",
    trigger_description=f"Halftime prediction run for fixture {SPORTMONKS_FIXTURE_ID} ({fixture['name']}). HT score: {ht_score}",
    trigger_payload_summary=f"fixture_id={SPORTMONKS_FIXTURE_ID}; window=HALFTIME; ht_score={ht_score}",
    observation_type="halftime_score",
    raw_observation=_trunc({"ht_score": ht_score, "fixture": fixture["name"]}),
))

# (2) Thinking — halftime prediction
records.append(_new_record(
    "Thinking",
    thought_type="ht_prediction",
    input_payload=_trunc(ht_input),
    output_payload=_trunc(ht_prediction),
    model_invocation=_mi(llm_ht),
    system_prompt_summary="Halftime win-probability estimation anchored to HT score + live market prices.",
))

# (3) Thinking — strategy
records.append(_new_record(
    "Thinking",
    thought_type="ht_strategy",
    input_payload=_trunc(strat_input),
    output_payload=_trunc(ht_strategy),
    model_invocation=_mi(llm_strat),
    system_prompt_summary="Halftime bankroll strategy — edge calculation vs live Polymarket prices.",
))

# (4) Acting — order (if placed)
if order_payload and order_response:
    records.append(_new_record(
        "Acting",
        action_type="order",
        target_system="arena",
        action_summary=f"Halftime LONG {ht_strategy['outcome']} @ p={ht_strategy['limit_price']} for {fixture['name']}",
        parameters={
            "fixture_id": str(SPORTMONKS_FIXTURE_ID),
            "outcome":    ht_strategy["outcome"],
            "size_usdc":  ht_strategy["size_usdc"],
            "limit_price": ht_strategy["limit_price"],
            "window":     "halftime",
            "ht_score":   ht_score,
        },
        dry_run=False,
        execution_status="confirmed" if order_response.get("status") == "ok" else "attempted",
        result_summary=_trunc(order_response),
        upstream_record_id=[r["record_id"] for r in records],
    ))

# Submit all records to ledger
print(f"\nSubmitting {len(records)} ledger records...")
for rec in records:
    lr = requests.post(
        f"{ARENA}/api/v1/arena/ledger",
        json=rec,
        headers=H_ARENA, timeout=30,
    )
    status = lr.json().get("status") or lr.status_code
    print(f"  {rec['behavior']:12} ({rec.get('thought_type') or rec.get('action_type') or rec.get('trigger_type') or ''}): {status}")

print("\n=== HALFTIME RUN COMPLETE ===")
print(f"  HT score  : {ht_score}")
print(f"  Prediction: {(ht_prediction or {}).get('outcome')} @ {(ht_prediction or {}).get('probability')}")
print(f"  Order     : {'PLACED' if order_payload else 'SKIPPED'}")
print(f"  Session   : {LEDGER_SESSION_ID}")

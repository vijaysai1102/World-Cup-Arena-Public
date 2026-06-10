import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import os, json, time, uuid, requests

ARENA            = "https://staging.stair-ai.com"
SPORTMONKS_PROXY = f"{ARENA}/api/v1/data/proxy/sportmonks/v3/football"
POLYMARKET_CLOB  = f"{ARENA}/api/v1/data/proxy/polymarket-clob"
POLYMARKET_GAMMA = f"{ARENA}/api/v1/data/proxy/polymarket-gamma"
ARENA_KEY        = "YOUR_ARENA_KEY_HERE"
# Staging shares a single publishable Supabase key for every builder — no
# per-account JWT, no extra setup. The arena will publish these two values
# alongside the API key minted in the portal.
SUPABASE         = "https://ezvbmtvrvzageqixvdak.supabase.co"
SUPABASE_KEY     = "sb_publishable__m8bOkD05ToFwATpaWST5w_2-3fGS7V"
ANTHROPIC_KEY    = "YOUR_ANTHROPIC_KEY_HERE"

# --- Other LLM providers (OPTIONAL) ------------------------------------------
# This notebook calls Anthropic by default. To use a DIFFERENT provider instead:
#   1. paste its key below,
#   2. pip install its SDK (see the optional section in requirements.txt),
#   3. uncomment its client in the next code cell, and
#   4. in each LLM cell, comment out the Anthropic block and UNCOMMENT the block
#      for your provider.
# The _extract() / _mi() helpers already understand all four response shapes, so
# nothing else has to change.
GEMINI_API_KEY   = "FILL IN YOUR GOOGLE GEMINI KEY HERE"    # Google AI Studio: https://aistudio.google.com/apikey
OPENAI_API_KEY   = "FILL IN YOUR OPENAI KEY HERE"           # OpenAI:           https://platform.openai.com/api-keys
DEEPSEEK_API_KEY = "FILL IN YOUR DEEPSEEK KEY HERE"         # DeepSeek:         https://platform.deepseek.com/api_keys
H_ARENA          = {"x-api-key": ARENA_KEY}
H_WCA            = {"apikey": SUPABASE_KEY, "Accept-Profile": "world_cup_arena"}

# Tournament constant — WC2026 is the only season this guide targets.
SPORTMONKS_SEASON_ID = 26618

# Reasoning-Ledger schema constants (per schema/records.schema.json v0.3 in
# StairAI/Reasoning-Ledger). agent_id is NOT set client-side: the arena
# resolves it server-side from the x-api-key on POST, so the wire records
# omit it. The local dump produced by this script also omits it for fidelity
# with what the agent actually transmits.
LEDGER_SCHEMA_VERSION = "0.3"
# The model each provider should use. LLM_MODEL stays the Anthropic model so the
# default path is unchanged; the others are only used if you switch providers.
LLM_MODEL             = "claude-haiku-4-5-20251001"   # Anthropic (default)
GEMINI_MODEL          = "gemini-2.0-flash"            # Google Gemini
OPENAI_MODEL          = "gpt-4o-mini"                 # OpenAI
DEEPSEEK_MODEL        = "deepseek-chat"               # DeepSeek (use "deepseek-reasoner" for a thinking trace)

# Anthropic extended-thinking knobs. budget_tokens must be < max_tokens; when
# enabled, response.content contains both `thinking` and `text` blocks — see
# scripts/model_reasoning_blocks.ipynb (Pattern A) for the canonical reference.
LLM_MAX_TOKENS      = 2400
LLM_THINKING_BUDGET = 1024
LLM_THINKING        = {"type": "enabled", "budget_tokens": LLM_THINKING_BUDGET}


def _extract(resp):
    """Return (final_text, thinking_text) from ANY of the four providers, so the
    rest of the notebook stays provider-agnostic:
      - Anthropic        : resp.content is a list of typed blocks (text/thinking)
      - OpenAI & DeepSeek: resp.choices[0].message.content (+ reasoning_content,
                           which DeepSeek's 'deepseek-reasoner' model returns)
      - Gemini           : resp.text (Gemini hides its thinking by default)"""
    # Anthropic
    if hasattr(resp, "content") and isinstance(resp.content, list):
        text_parts, thinking_parts = [], []
        for block in resp.content:
            if block.type == "thinking":
                thinking_parts.append(block.thinking)
            elif block.type == "text":
                text_parts.append(block.text)
        return "".join(text_parts), "\n\n".join(thinking_parts)
    # OpenAI / DeepSeek (OpenAI-compatible)
    if hasattr(resp, "choices"):
        msg = resp.choices[0].message
        return (msg.content or ""), (getattr(msg, "reasoning_content", "") or "")
    # Gemini
    if hasattr(resp, "text"):
        return (resp.text or ""), ""
    raise TypeError(f"Unrecognized LLM response type: {type(resp)!r}")


# --- Sanity check: catch a placeholder key NOW, not 6 cells from now. ---
_missing = [n for n, v in [("ARENA_KEY", ARENA_KEY), ("ANTHROPIC_KEY", ANTHROPIC_KEY)]
            if "FILL IN" in v]
if _missing:
    print(f"WARNING: still need to set {', '.join(_missing)} (edit this cell first).")
else:
    print("Both API keys are set.")
print(f"Arena  : {ARENA}")
print(f"Model  : {LLM_MODEL}")
print(f"Season : World Cup 2026 (id {SPORTMONKS_SEASON_ID})")
print("Setup complete -- run the cells below in order.")

print("=" * 40 + " Cell 2 done")

r = requests.get(
    f"{SPORTMONKS_PROXY}/schedules/seasons/{SPORTMONKS_SEASON_ID}",
    headers=H_ARENA, timeout=10,
)
r.raise_for_status()

# Every arena proxy call wraps the upstream reply in an "envelope":
#   {body, duration, statusCode, requestId, _proxy, headers}
# The real Sportmonks payload lives under envelope["body"]["data"].
envelope = r.json()
schedule = envelope["body"]["data"]

print(f"HTTP {r.status_code} (OK) -- the arena answered.")
print(f"Envelope keys from the proxy: {list(envelope.keys())}")
print(f"Found {len(schedule)} schedule entries (stages / rounds / fixtures) for WC2026.\n")

# A real agent would scan `schedule` and pick fixtures itself. For this guide we
# hard-code the tournament opener so everyone analyzes the same match:
#   Mexico (MEX) vs South Africa (ZAF) -- 2026-06-11 -- fixture_id 19609127
SPORTMONKS_FIXTURE_ID = 19609127
print(f"Chosen fixture: Mexico vs South Africa (fixture_id {SPORTMONKS_FIXTURE_ID})")

print("=" * 40 + " Cell 5 done")

r = requests.get(
    f"{ARENA}/api/v1/web/mapping",
    params={"fixture_id": SPORTMONKS_FIXTURE_ID},
    headers=H_ARENA, timeout=10,
)
r.raise_for_status()
mappings = r.json().get("mappings") or []
polymarket_event_slug = mappings[0]["polymarket_event_slug"] if mappings else None
print(f"HTTP {r.status_code} (OK)")
if polymarket_event_slug:
    print(f"This fixture maps to Polymarket event slug: {polymarket_event_slug!r}")
    print("We'll use this slug in Step 3 to pull the live market.")
else:
    print("No Polymarket market is mapped to this fixture.")
    print("That's fine -- the agent will run in predict-only mode (no betting).")

print("=" * 40 + " Cell 7 done")

r = requests.get(
    f"{SPORTMONKS_PROXY}/fixtures/{SPORTMONKS_FIXTURE_ID}",
    params={"include": "participants;predictions;odds;xGFixture"},
    headers=H_ARENA, timeout=60,
)
r.raise_for_status()
fixture = r.json()["body"]["data"]    # same envelope as Step 1: peel body -> data

home = next(p for p in fixture["participants"] if p["meta"]["location"] == "home")
away = next(p for p in fixture["participants"] if p["meta"]["location"] == "away")

print(f"HTTP {r.status_code} (OK)")
print(f"Fixture : {fixture['name']}")
print(f"Kickoff : {fixture.get('starting_at')}")
print(f"Home    : {home['name']} ({home['short_code']})")
print(f"Away    : {away['name']} ({away['short_code']})")
print("\nHow much pre-game data came back? (empty rows are possible on staging)")
print(f"  - Sportmonks model predictions : {len(fixture.get('predictions') or [])} rows")
print(f"  - bookmaker odds               : {len(fixture.get('odds') or [])} rows")
print(f"  - expected-goals (xG)          : {len(fixture.get('xgfixture') or [])} rows")

print("=" * 40 + " Cell 10 done")

import anthropic
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# To use a different provider, uncomment its client here (after pip-installing the
# SDK), then uncomment that provider's call block in each LLM cell below.
# from google import genai                                    # pip install google-genai
# gemini_client   = genai.Client(api_key=GEMINI_API_KEY)
# from openai import OpenAI                                   # pip install openai
# openai_client   = OpenAI(api_key=OPENAI_API_KEY)
# deepseek_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

DIGEST_SYS = (
    "You are a soccer analyst. You receive a raw Sportmonks pre-match payload for "
    "one fixture and must distil it into a self-contained JSON digest that a "
    "downstream LLM (with no other context about Sportmonks) will read.\n\n"

    "## Input shape\n"
    "  - fixture       : match name (e.g. 'Mexico vs South Africa')\n"
    "  - home_code     : home team short code (use as a JSON key for the home outcome)\n"
    "  - away_code     : away team short code (use as a JSON key for the away outcome)\n"
    "  - predictions[] : Sportmonks ML model rows. Each row has `type_id` (numeric — "
    "                    the Full-Time-Result / 1X2 winner type carries win/draw/loss "
    "                    probabilities) and a `predictions` object with the numeric "
    "                    probability values. May be empty if Sportmonks has no model "
    "                    output for this fixture.\n"
    "  - bookmaker_summary : pre-aggregated 1X2 consensus across all bookmakers."
    "                    Fields: home_avg_implied_prob, draw_avg_implied_prob,"
    "                    away_avg_implied_prob (each 0..1), bookmaker_count."
    "                    This is the MOST RELIABLE signal — bookmakers are highly"
    "                    calibrated. Null if no bookmaker data available.\n"
    "  - xGFixture[]   : expected-goals entries per team. Each row has "
    "                    participant_id and value (xG number). May be empty.\n"
    "  - team_form    : {home_code: {form, fifa_ranking}, away_code: ...}\n"
    "                    form: {matches, record (W3 D1 L1), goals_for_avg,\n"
    "                    goals_against_avg, last_5 (W/D/L list)}. null if unavailable.\n"
    "                    fifa_ranking: int position (lower = better). null if unavailable.\n\n"

    "## Output schema (return ONLY this JSON — no prose, no code fences)\n"
    "{\n"
    "  'fixture'                       : str,                                                          // echo input\n"
    "  'home_team'                     : str,                                                          // home_code\n"
    "  'away_team'                     : str,                                                          // away_code\n"
    "  'sportmonks_ml_win_prob'        : {home_code: float, 'draw': float, away_code: float} | null,   // probabilities in 0..1; sum ≈ 1\n"
    "  'bookmaker_consensus_win_prob'  : {home_code: float, 'draw': float, away_code: float} | null,   // from bookmaker_summary, keys = home_code/away_code/draw\n"
    "  'bookmaker_count'               : int | null,                                                   // from bookmaker_summary.bookmaker_count\n"
    "  'expected_goals'                : {home_code: float, away_code: float} | null,                  // xG per side\n"
    "  'data_availability': {                                                                          // honest reporting so downstream knows what's missing\n"
    "    'sportmonks_ml'        : 'available' | 'missing',\n"
    "    'bookmaker_consensus'  : 'available' | 'missing',\n"
    "    'expected_goals'       : 'available' | 'missing'\n"
    "  },\n"
    "  'recent_form'                   : {home_code: {record, goals_for_avg, goals_against_avg, last_5} | null,\n"
    "                               away_code: {record, goals_for_avg, goals_against_avg, last_5} | null} | null,\n"
    "  'fifa_rankings'              : {home_code: int | null, away_code: int | null} | null,\n"
    "  'summary': str   // 1-3 sentences. MUST be readable in isolation. Name available signals; mention FIFA ranking gap and form if present.\n"
    "}\n\n"

    "Use null (not 0) when source data is missing. Do NOT fabricate values."
)

# Pre-aggregate 1X2 bookmaker odds in Python before sending to the LLM.
# Labels are "Home"/"Draw"/"Away" strings; probability is a "12.05%" string.
# Sending 2000+ raw rows overflows the context window.
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


# ── Team form + FIFA rankings (fail silently if endpoint unavailable) ──────────
def _fetch_team_form(team_id, n=5):
    """Last N completed fixtures for a team -> W/D/L record + goals."""
    try:
        r = requests.get(
            f"{SPORTMONKS_PROXY}/fixtures",
            params={"filter[team_id]": team_id, "sort": "-starting_at",
                    "per_page": n, "include": "participants;scores"},
            headers=H_ARENA, timeout=20,
        )
        if not r.ok:
            return None
        fixtures = (r.json().get("body") or {}).get("data") or []
        results = []
        for fix in fixtures:
            parts  = fix.get("participants") or []
            scores = fix.get("scores") or []
            side   = next((p["meta"]["location"] for p in parts if p["id"] == team_id), None)
            if not side:
                continue
            home_g = away_g = None
            for s in scores:
                desc = (s.get("description") or "").upper()
                if desc not in ("CURRENT", "FT", "90'", "2ND_HALF"):
                    continue
                sc = s.get("score") or {}
                if sc.get("participant") == "home":
                    home_g = sc.get("goals")
                elif sc.get("participant") == "away":
                    away_g = sc.get("goals")
            if home_g is None or away_g is None:
                continue
            gf = home_g if side == "home" else away_g
            ga = away_g if side == "home" else home_g
            results.append({"result": "W" if gf > ga else ("D" if gf == ga else "L"),
                             "gf": gf, "ga": ga})
        if not results:
            return None
        w = sum(1 for r in results if r["result"] == "W")
        d = sum(1 for r in results if r["result"] == "D")
        l = sum(1 for r in results if r["result"] == "L")
        return {
            "matches": len(results),
            "record": f"W{w} D{d} L{l}",
            "goals_for_avg":     round(sum(r["gf"] for r in results) / len(results), 2),
            "goals_against_avg": round(sum(r["ga"] for r in results) / len(results), 2),
            "last_5": [r["result"] for r in results],
        }
    except Exception:
        return None

def _fetch_fifa_ranking(team_id):
    """FIFA world ranking position for a national team (lower = better)."""
    try:
        r = requests.get(
            f"{SPORTMONKS_PROXY}/teams/{team_id}",
            params={"include": "rankings"},
            headers=H_ARENA, timeout=20,
        )
        if not r.ok:
            return None
        team_data = (r.json().get("body") or {}).get("data") or {}
        for ranking in (team_data.get("rankings") or []):
            rtype = str(ranking.get("type") or ranking.get("type_id") or "")
            if "fifa" in rtype.lower() or "world" in rtype.lower():
                return ranking.get("position")
        return None
    except Exception:
        return None

_home_form    = _fetch_team_form(home["id"])
_away_form    = _fetch_team_form(away["id"])
_home_ranking = _fetch_fifa_ranking(home["id"])
_away_ranking = _fetch_fifa_ranking(away["id"])

_form_summary = {
    home["short_code"]: {"form": _home_form, "fifa_ranking": _home_ranking},
    away["short_code"]: {"form": _away_form, "fifa_ranking": _away_ranking},
}
_form_available = any(v["form"] is not None or v["fifa_ranking"] is not None
                      for v in _form_summary.values())
print(f"Team form/rankings: {json.dumps(_form_summary, default=str)}")

digest_input = json.dumps({
    "fixture":           fixture["name"],
    "home_code":         home["short_code"],
    "away_code":         away["short_code"],
    "predictions":       fixture.get("predictions"),
    "bookmaker_summary": _bk_summary,
    "team_form":         _form_summary if _form_available else None,
    "xGFixture":         fixture.get("xgfixture"),
})

# === Anthropic (default) =====================================================
llm_digest = client.messages.create(
    model=LLM_MODEL,
    max_tokens=LLM_MAX_TOKENS,
    thinking=LLM_THINKING,
    system=DIGEST_SYS,
    messages=[{"role": "user", "content": digest_input}],
)

# === Gemini -- uncomment to use (and comment out the Anthropic block above) ===
# llm_digest = gemini_client.models.generate_content(
#     model=GEMINI_MODEL,
#     contents=digest_input,
#     config={"system_instruction": DIGEST_SYS, "max_output_tokens": LLM_MAX_TOKENS},
# )

# === OpenAI -- uncomment to use ==============================================
# llm_digest = openai_client.chat.completions.create(
#     model=OPENAI_MODEL,
#     max_tokens=LLM_MAX_TOKENS,
#     messages=[{"role": "system", "content": DIGEST_SYS},
#               {"role": "user",   "content": digest_input}],
# )

# === DeepSeek -- uncomment to use ============================================
# llm_digest = deepseek_client.chat.completions.create(
#     model=DEEPSEEK_MODEL,
#     max_tokens=LLM_MAX_TOKENS,
#     messages=[{"role": "system", "content": DIGEST_SYS},
#               {"role": "user",   "content": digest_input}],
# )

raw, thinking_digest = _extract(llm_digest)

# Claude returns the digest as text; pull the {...} object out of it
# (re.DOTALL lets the regex span newlines; also strips any prose/code fences).
import re
match = re.search(r"\{.*\}", raw, re.DOTALL)
if match:
    _raw_json = match.group(0)
    try:
        sportmonks_digest = json.loads(_raw_json)
    except json.JSONDecodeError:
        # LLM sometimes uses single quotes or has trailing commas — repair and retry
        import ast, re as _re
        _fixed = _re.sub(r"(?<!\)'([^']*)'\s*:", lambda m: '"' + m.group(1) + '":', _raw_json)
        _fixed = _re.sub(r",\s*([\}\]])", r"", _fixed)
        try:
            sportmonks_digest = json.loads(_fixed)
        except Exception:
            try:
                sportmonks_digest = ast.literal_eval(_raw_json)
            except Exception:
                sportmonks_digest = None
                print("WARNING: could not parse digest JSON — using null")
else:
    sportmonks_digest = None

print(f"Claude reasoned for {len(thinking_digest)} chars before answering.")
print("Clean digest the rest of the notebook will use:\n")
print(json.dumps(sportmonks_digest, indent=2))

print("=" * 40 + " Cell 12 done (digest)")

import re
TICKER_RE = re.compile(r"^fifwc-([a-z]{2,4})-([a-z]{2,4})-(\d{4}-\d{2}-\d{2})$")


def _clob_mid(token_id_str: str) -> float:
    """Single CLOB midpoint call. Polymarket's CLOB takes the token id as a
    decimal string (the raw value is a 78-digit integer)."""
    if not token_id_str:
        return None
    try:
        resp = requests.get(
            f"{POLYMARKET_CLOB}/midpoint",
            params={"token_id": token_id_str},
            headers=H_ARENA, timeout=10,
        )
        if not resp.ok:
            return None
        body = resp.json().get("body")
        if isinstance(body, dict) and "mid" in body:
            return float(body["mid"])
    except Exception:
        pass
    return None


def _outcome_from_market_slug(market_slug: str, ticker: str,
                              home_code: str, away_code: str) -> str:
    """Map a child-market slug ('fifwc-mex-rsa-2026-06-11-mex') to an
    outcome key ('home' | 'draw' | 'away')."""
    if not market_slug.startswith(ticker + "-"):
        return None
    suffix = market_slug[len(ticker) + 1:]
    if suffix == home_code: return "home"
    if suffix == "draw":    return "draw"
    if suffix == away_code: return "away"
    return None


if not polymarket_event_slug:
    moneyline = None
else:
    # 3a · Gamma: one call returns the event + its 3 child markets.
    r = requests.get(
        f"{POLYMARKET_GAMMA}/events",
        params={"slug": polymarket_event_slug},
        headers=H_ARENA, timeout=15,
    )
    r.raise_for_status()
    events = r.json().get("body") or []
    event  = events[0] if events else None

    if event is None:
        moneyline = None
    else:
        ticker = (event.get("ticker") or "").lower()
        m = TICKER_RE.match(ticker)
        if not m:
            moneyline = None
        else:
            pm_home_code, pm_away_code, _ = m.groups()
            outcomes = {}
            for mkt in (event.get("markets") or []):
                key = _outcome_from_market_slug((mkt.get("slug") or "").lower(),
                                                ticker, pm_home_code, pm_away_code)
                if key is None:
                    continue
                # clobTokenIds is a JSON-encoded string: [YES_token, NO_token].
                try:
                    token_ids = json.loads(mkt.get("clobTokenIds") or "[]")
                except json.JSONDecodeError:
                    token_ids = []
                token_yes = token_ids[0] if token_ids else None
                outcomes[key] = {
                    "team_code":       key if key == "draw" else (
                                            pm_home_code.upper() if key == "home"
                                            else pm_away_code.upper()),
                    "condition_id":    mkt.get("conditionId"),
                    "token_yes":       token_yes,
                    "current_mid_yes": _clob_mid(token_yes),  # 3b · one CLOB call per YES token
                }

            moneyline = {
                "sportmonks_match_id":   SPORTMONKS_FIXTURE_ID,
                "fixture":               event.get("title"),
                "kickoff_utc":           event.get("startDate"),
                "polymarket_event_slug": polymarket_event_slug,
                "outcomes":              outcomes,
            }

if moneyline is None:
    print("No tradable Polymarket market for this fixture -- predict-only mode.")
else:
    n_mids = sum(1 for o in moneyline["outcomes"].values()
                 if o["current_mid_yes"] is not None)
    print(f"Built the 3-way moneyline for: {moneyline['fixture']}")
    print(f"Live mid prices retrieved for {n_mids}/3 outcomes (home / draw / away).")
    print("Full market (prices + the token ids needed to place an order):\n")
print(json.dumps(moneyline, indent=2, default=str))

print("=" * 40 + " Cell 15 done")

POLYMARKET_DIGEST_SYS = (
    "You are an analyst digesting a Polymarket moneyline (3-way match-winner) "
    "market response into a self-contained JSON for a downstream LLM that has "
    "no other Polymarket context.\n\n"

    "## Input shape\n"
    "  - sportmonks_match_id   : numeric fixture id (echo)\n"
    "  - fixture               : match name (e.g. 'Mexico vs South Africa')\n"
    "  - kickoff_utc           : ISO kickoff timestamp\n"
    "  - polymarket_event_slug : Polymarket event slug grouping the 3 binary markets\n"
    "  - outcomes.{home,draw,away}\n"
    "      team_code           : team short code (or 'draw' for the draw outcome)\n"
    "      condition_id        : Polymarket condition id (needed for trade execution)\n"
    "      token_yes           : ERC1155 YES-side token id (buy YES to back the outcome)\n"
    "      current_mid_yes     : midpoint price of the YES token in 0..1 == implied probability\n"
    "                            of that outcome winning. null if CLOB lookup failed.\n\n"

    "## Output schema (return ONLY this JSON — no prose, no code fences)\n"
    "{\n"
    "  'fixture'              : str,\n"
    "  'market_handle'        : str,                                                          // polymarket_event_slug\n"
    "  'implied_win_prob'     : {home_code: float, 'draw': float, away_code: float} | null,   // from current_mid_yes; null if unavailable\n"
    "  'sum_implied_prob'     : float | null,                                                 // should be ≈1.0; outside [0.95, 1.10] = stale prices or arb gap\n"
    "  'execution_handles'    : {home_code: {condition_id, token_yes},                        // for the downstream trade-execution step\n"
    "                            'draw'   : {condition_id, token_yes},\n"
    "                            away_code: {condition_id, token_yes}},\n"
    "  'data_availability'    : 'mids_available' | 'mids_partial' | 'mids_missing' | 'no_market',\n"
    "  'summary'              : str   // 1-3 sentences self-contained. Name the favorite (highest implied prob), the spread, and any anomaly. If mids are missing, say so plainly and identify what's still available (execution handles can still be used to place orders blind).\n"
    "}\n\n"

    "Use null when input shows null. Do NOT fabricate prices."
)

if moneyline is None:
    polymarket_digest = {
        "fixture":              None,
        "market_handle":        None,
        "implied_win_prob":     None,
        "sum_implied_prob":     None,
        "execution_handles":    None,
        "data_availability":    "no_market",
        "summary":              f"No Polymarket moneyline mapping for Sportmonks fixture "
                                f"{SPORTMONKS_FIXTURE_ID}. The fixture either isn't listed on "
                                f"Polymarket yet or its curated mapping is marked no_match.",
    }
else:
    pm_input = json.dumps(moneyline)

    # === Anthropic (default) =================================================
    llm_pm = client.messages.create(
        model=LLM_MODEL,
        max_tokens=LLM_MAX_TOKENS,
        thinking=LLM_THINKING,
        system=POLYMARKET_DIGEST_SYS,
        messages=[{"role": "user", "content": pm_input}],
    )

    # === Gemini -- uncomment to use (comment out the Anthropic block above) ==
    # llm_pm = gemini_client.models.generate_content(
    #     model=GEMINI_MODEL,
    #     contents=pm_input,
    #     config={"system_instruction": POLYMARKET_DIGEST_SYS, "max_output_tokens": LLM_MAX_TOKENS},
    # )

    # === OpenAI -- uncomment to use =========================================
    # llm_pm = openai_client.chat.completions.create(
    #     model=OPENAI_MODEL,
    #     max_tokens=LLM_MAX_TOKENS,
    #     messages=[{"role": "system", "content": POLYMARKET_DIGEST_SYS},
    #               {"role": "user",   "content": pm_input}],
    # )

    # === DeepSeek -- uncomment to use =======================================
    # llm_pm = deepseek_client.chat.completions.create(
    #     model=DEEPSEEK_MODEL,
    #     max_tokens=LLM_MAX_TOKENS,
    #     messages=[{"role": "system", "content": POLYMARKET_DIGEST_SYS},
    #               {"role": "user",   "content": pm_input}],
    # )

    raw_pm, thinking_pm = _extract(llm_pm)
    m = re.search(r"\{.*\}", raw_pm, re.DOTALL)
    polymarket_digest = json.loads(m.group(0)) if m else None
    print(f"Claude digested the market ({len(thinking_pm)} chars of thinking).")

print("\nMarket digest (implied probabilities + execution handles):\n")
print(json.dumps(polymarket_digest, indent=2))

print("=" * 40 + " Cell 17 done")


# ── Supabase stub ── DNS for Supabase is unavailable; use empty priors ────────
print("Supabase skipped (DNS unavailable) -- using empty digest.")
catalog = []
priors_rows = []
WANTED_TABLE = "ads_a_h2h_country"
TEAM_A_ID = None
TEAM_B_ID = None
SUPABASE_DIGEST_SYS = (
    "You are an analyst aggregating Supabase priors data into a self-contained JSON. "
    "Return ONLY the JSON with no prose."
)
supabase_digest = {
    "fixture": fixture["name"] if "fixture" in dir() else "unknown",
    "source_table": "ads_a_h2h_country",
    "teams": {
        home["short_code"]: {
            "set_piece_efficiency": None, "set_piece_sample": None,
            "group_goals_per_game": None, "ko_goals_per_game": None,
        },
        away["short_code"]: {
            "set_piece_efficiency": None, "set_piece_sample": None,
            "group_goals_per_game": None, "ko_goals_per_game": None,
        },
    },
    "data_availability": "sparse",
    "summary": "No country-style data available. Both teams lack historical priors from this source.",
}
llm_sb = None


PREDICT_SYS = (
    "You are a soccer match analyst producing calibrated win-probability estimates "
    "for all three match outcomes (home win / draw / away win).\n\n"

    "## Input shape\n"
    "  - fixture           : match name\n"
    "  - home_code         : home team short code\n"
    "  - away_code         : away team short code\n"
    "  - sportmonks_digest : Sportmonks pre-match data. Contains:\n"
    "      * bookmaker_consensus_win_prob \u2014 MOST RELIABLE signal when available.\n"
    "        Consensus of professional bookmakers; use as primary anchor.\n"
    "      * sportmonks_ml_win_prob \u2014 ML model output; useful corroboration but\n"
    "        often less calibrated than bookmaker consensus.\n"
    "      * expected_goals \u2014 xG per side; good tiebreaker when consensus is close.\n"
    "      * recent_form \u2014 last 5 matches W/D/L + goals avg per team. Use to detect\n"
    "        momentum shifts (e.g. 5-game winning streak vs winless run).\n"
    "      * fifa_rankings \u2014 world ranking per team (lower = better). Large gaps\n"
    "        (>30 positions) are meaningful; small gaps (<10) are noise.\n"
    "    Check data_availability flags to see what is actually present.\n"
    "  - supabase_digest   : long-horizon priors (set-piece efficiency, goals-per-game).\n"
    "                        Use as secondary context; note its sample-size caveats.\n\n"

    "## Signal weighting (most to least reliable)\n"
    "  1. bookmaker_consensus_win_prob \u2014 use as primary anchor when available\n"
    "  2. sportmonks_ml_win_prob \u2014 corroborate or adjust the bookmaker line\n"
    "  3. expected_goals \u2014 tiebreaker for close calls\n"
    "  4. recent_form / fifa_rankings \u2014 context for momentum and quality gap\n"
    "  5. supabase priors \u2014 long-run context; discount heavily if small sample\n"
    "  When signals conflict, prefer bookmaker consensus. When all are null, output\n"
    "  near-equal probs (0.38/0.26/0.36) and set confidence_level to 'low'.\n\n"

    "## Output schema (return ONLY this JSON \u2014 no prose, no code fences)\n"
    "{\n"
    "  'fixture'    : str,\n"
    "  'win_prob'   : {home_code: float, 'draw': float, away_code: float},  // ALL 3 outcomes, sum ~= 1\n"
    "  'outcome'    : str,                          // home_code | 'draw' | away_code \u2014 highest win_prob\n"
    "  'probability': float,                        // win_prob[outcome] \u2014 for backward compatibility\n"
    "  'rationale'  : str,                          // 2-3 sentences. Name teams, primary signal used,\n"
    "                                               // any divergence between signals, key caveats.\n"
    "  'used_signals': {                            // for traceability\n"
    "    'sportmonks' : 'leaned_on' | 'unavailable',\n"
    "    'supabase'   : 'leaned_on' | 'unavailable'\n"
    "  },\n"
    "  'confidence_level': 'high' | 'medium' | 'low'   // high = strong bookmaker consensus; low = data sparse\n"
    "}\n\n"

    "Be honest about uncertainty. Do NOT anchor to Polymarket prices \u2014 you don't have them. "
    "Output probabilities that reflect your best estimate from the available priors alone."
)

predict_input = json.dumps({
    "fixture":           fixture["name"],
    "home_code":         home["short_code"],
    "away_code":         away["short_code"],
    "sportmonks_digest": sportmonks_digest,
    "supabase_digest":   supabase_digest,
})

# === Anthropic (default) =====================================================
llm_predict = client.messages.create(
    model=LLM_MODEL,
    max_tokens=LLM_MAX_TOKENS,
    thinking=LLM_THINKING,
    system=PREDICT_SYS,
    messages=[{"role": "user", "content": predict_input}],
)

# === Gemini -- uncomment to use (and comment out the Anthropic block above) ===
# llm_predict = gemini_client.models.generate_content(
#     model=GEMINI_MODEL,
#     contents=predict_input,
#     config={"system_instruction": PREDICT_SYS, "max_output_tokens": LLM_MAX_TOKENS},
# )

# === OpenAI -- uncomment to use ==============================================
# llm_predict = openai_client.chat.completions.create(
#     model=OPENAI_MODEL,
#     max_tokens=LLM_MAX_TOKENS,
#     messages=[{"role": "system", "content": PREDICT_SYS},
#               {"role": "user",   "content": predict_input}],
# )

# === DeepSeek -- uncomment to use ============================================
# llm_predict = deepseek_client.chat.completions.create(
#     model=DEEPSEEK_MODEL,
#     max_tokens=LLM_MAX_TOKENS,
#     messages=[{"role": "system", "content": PREDICT_SYS},
#               {"role": "user",   "content": predict_input}],
# )

raw_pred, thinking_pred = _extract(llm_predict)
m = re.search(r"\{.*\}", raw_pred, re.DOTALL)
prediction = json.loads(m.group(0)) if m else None

print(f"The agent formed its own prediction ({len(thinking_pred)} chars of thinking):\n")
print(json.dumps(prediction, indent=2))
if prediction:
    print(f"\n-> In plain words: most likely '{prediction['outcome']}' at "
          f"{prediction['probability']:.0%} confidence ({prediction['confidence_level']}).")

print("=" * 40 + " Cell 28 done")

STRATEGY_SYS = (
    "You are a bankroll manager for a $100 demo account. You receive the agent's "
    "own prediction and the current Polymarket market view, and decide whether "
    "to trade and on what terms.\n\n"

    "## Input shape\n"
    "  - prediction        : {outcome, probability, confidence_level, rationale, ...}\n"
    "                        The agent's primary pick, formed without seeing the market.\n"
    "  - agent_win_prob    : {team_code: float, 'draw': float, team_code: float}\n"
    "                        Sportmonks ML probability for ALL 3 outcomes. Team code keys\n"
    "                        may differ from polymarket_digest (e.g. 'ZAF' vs 'RSA') --\n"
    "                        match by role (home / draw / away) using fixture context.\n"
    "  - polymarket_digest : {implied_win_prob, sum_implied_prob, execution_handles,\n"
    "                        market_handle, data_availability, summary}.\n"
    "                        The market's view (implied_win_prob keys match team codes).\n\n"

    "## How to decide\n"
    "  1. Compute edge for EACH of the 3 outcomes:\n"
    "       edge[X] = agent_win_prob[X] - polymarket_digest.implied_win_prob[X]\n"
    "     (match keys by team role if codes differ). Positive edge = market under-prices\n"
    "     that outcome -- it is a LONG opportunity.\n"
    "  2. Pick the outcome with the HIGHEST positive edge. If no outcome has edge > 0.05\n"
    "     (5 percentage points), set should_trade: false.\n"
    "  3. Size discipline (max $5 per trade, $100 wallet):\n"
    "       edge < 5pp                    -> don't trade (noise)\n"
    "       edge 5-15pp                   -> $1-2  (modest position)\n"
    "       edge > 15pp                   -> $3-5  (high-conviction position)\n"
    "     Then HALVE the size if confidence_level is 'low'.\n"
    "       confidence 'medium'           -> use the size above\n"
    "       confidence 'high'             -> use up to 1.5x (capped at $5)\n"
    "     If the Polymarket digest's data_availability is not 'mids_available', skip --\n"
    "     you can't price an edge without mids.\n"
    "  4. limit_price: set a bit ABOVE the current mid for the chosen outcome's YES token\n"
    "     (e.g. mid 0.205 -> limit 0.22). The API only supports buy-YES (long); always\n"
    "     output direction: 'long'.\n\n"

    "## Output schema (return ONLY this JSON — no prose, no code fences)\n"
    "{\n"
    "  'should_trade'   : bool,\n"
    "  'outcome'        : str,                    // team code to LONG (use polymarket_digest keys)\n"
    "  'direction'      : 'long',                 // always 'long' -- API is buy-YES only\n"
    "  'size_usdc'      : float,                  // 0 when not trading; <=5 for this demo\n"
    "  'limit_price'    : float,                  // 0..1; slightly above the YES mid for chosen outcome\n"
    "  'edge_pp'        : float,                  // (agent_prob - market_prob) x 100 for chosen outcome\n"
    "  'market_handle'  : str,                    // echo polymarket_digest.market_handle for traceability\n"
    "  'rationale'      : str                     // 1-3 sentences: which outcome has highest edge,\n"
    "                                             // the size logic, and the limit_price logic.\n"
    "}\n\n"

    "Be conservative: small wallet, weak conviction → skipping is a valid answer."
)

# Use prediction.win_prob (all 3 calibrated outcomes) if available;
# fall back to raw Sportmonks ML probs if the prediction cell omitted win_prob.
strategy_input = json.dumps({
    "prediction":        prediction,
    "agent_win_prob":    (prediction or {}).get("win_prob") or sportmonks_digest.get("sportmonks_ml_win_prob"),
    "polymarket_digest": polymarket_digest,
})

# === Anthropic (default) =====================================================
llm_strategy = client.messages.create(
    model=LLM_MODEL,
    max_tokens=LLM_MAX_TOKENS,
    thinking=LLM_THINKING,
    system=STRATEGY_SYS,
    messages=[{"role": "user", "content": strategy_input}],
)

# === Gemini -- uncomment to use (and comment out the Anthropic block above) ===
# llm_strategy = gemini_client.models.generate_content(
#     model=GEMINI_MODEL,
#     contents=strategy_input,
#     config={"system_instruction": STRATEGY_SYS, "max_output_tokens": LLM_MAX_TOKENS},
# )

# === OpenAI -- uncomment to use ==============================================
# llm_strategy = openai_client.chat.completions.create(
#     model=OPENAI_MODEL,
#     max_tokens=LLM_MAX_TOKENS,
#     messages=[{"role": "system", "content": STRATEGY_SYS},
#               {"role": "user",   "content": strategy_input}],
# )

# === DeepSeek -- uncomment to use ============================================
# llm_strategy = deepseek_client.chat.completions.create(
#     model=DEEPSEEK_MODEL,
#     max_tokens=LLM_MAX_TOKENS,
#     messages=[{"role": "system", "content": STRATEGY_SYS},
#               {"role": "user",   "content": strategy_input}],
# )

raw_strat, thinking_strat = _extract(llm_strategy)
m = re.search(r"\{.*\}", raw_strat, re.DOTALL)
strategy = json.loads(m.group(0)) if m else None

print(f"The agent decided on a strategy ({len(thinking_strat)} chars of thinking):\n")
print(json.dumps(strategy, indent=2))
if strategy and strategy.get("should_trade"):
    print(f"\n-> In plain words: {strategy['direction'].upper()} ${strategy['size_usdc']:.2f} on "
          f"'{strategy['outcome']}' (edge {strategy['edge_pp']:+.1f} points).")
elif strategy:
    print(f"\n-> In plain words: no trade -- edge {strategy.get('edge_pp', 0):+.1f} points "
          f"isn't worth it for this wallet.")

print("=" * 40 + " Cell 30 done")

order_payload  = None
order_response = None

if strategy and strategy.get("should_trade"):
    team_code = strategy["outcome"]

    if team_code is not None:
        order_payload = {
            "fixture_id":             str(SPORTMONKS_FIXTURE_ID),
            "team_code":              team_code,
            "usd_size":               str(strategy["size_usdc"]),
            "limit_price":            strategy["limit_price"],
            "time_in_force_seconds":  30,
            "idempotency_key":        str(uuid.uuid4()),
        }
        print("\nStrategy says TRADE. Here's the exact order we'd submit:\n")
        print(json.dumps(order_payload, indent=2))
        try:
            r = requests.post(
                f"{ARENA}/api/v1/arena/orders",
                headers=H_ARENA, timeout=60,
                json=order_payload,
            )
            if r.status_code == 404:
                print("\nHTTP 404 -- /arena/orders not live on this deploy yet. "
                      "Expected on staging-in-progress; payload above is what a real run would send.")
            elif r.ok:
                order_response = r.json()
                order_id = order_response.get("order_id")
                print(f"\nHTTP {r.status_code} (OK) -- order accepted "
                      f"(order_id={order_id}, status={order_response.get('status')}, "
                      f"locked=${order_response.get('size_usdc_locked')}).")

                # Poll the order to a terminal state. The execution worker
                # round-trips to the live Polymarket CLOB; on a freshly funded
                # wallet a fill typically lands in 5-15s, but allow up to ~30s.
                final_status   = order_response.get("status")
                tx_hash        = None
                clob_order_id  = None
                reject_reason  = None
                for i in range(6):                # 6 × 5s = 30s
                    time.sleep(5)
                    got = requests.get(
                        f"{ARENA}/api/v1/arena/orders/{order_id}",
                        headers=H_ARENA, timeout=10,
                    )
                    if not got.ok:
                        continue
                    d = got.json()
                    final_status  = d.get("status")
                    reject_reason = d.get("rejection_reason") or reject_reason
                    fills         = d.get("open_fills") or []
                    if fills:
                        tx_hash       = fills[0].get("tx_hash")       or tx_hash
                        clob_order_id = fills[0].get("clob_order_id") or clob_order_id
                    print(f"  poll {i+1}: status={final_status}  filled=${d.get('size_usdc_filled')}")
                    if final_status in ("closed", "filled", "rejected"):
                        break

                if final_status in ("filled", "closed"):
                    if tx_hash:
                        print(f"\nFilled. On-chain settlement tx:\n  https://polygonscan.com/tx/{tx_hash}")
                    if clob_order_id:
                        print(f"CLOB order id: {clob_order_id}")
                elif final_status == "rejected":
                    print(f"\nOrder rejected. reason: {reject_reason or '(none reported)'}")
                else:
                    print(f"\nOrder still '{final_status}' after 30s -- check the dashboard for the final state.")
            else:
                print(f"\nHTTP {r.status_code} -- order rejected. Body: {r.text[:300]}")
        except Exception as e:
            print(f"\nOrder POST failed: {type(e).__name__}: {e}")
else:
    print("Strategy says DON'T trade, so we skip placing an order.")
    print("Predict-only runs are fully supported -- Step 8 still records everything.")

print("=" * 40 + " Cell 32 done")

LEDGER_SESSION_ID = f"prematch:{SPORTMONKS_FIXTURE_ID}:{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"

def _new_record(behavior, **fields):
    """Compose the BaseRecord envelope + behavior-specific fields.

    Note: agent_id is intentionally omitted. The arena resolves it server-side
    from the x-api-key on POST, so wire records do not carry it. The local
    dump produced by this script mirrors that — schema-wise, agent_id is
    required, but it only becomes present after the server enriches the
    record."""
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
    """Build a ModelInvocation dict from ANY provider's response. Returns a
    minimal dict when resp is None (e.g. skipped Supabase step)."""
    if resp is None:
        return {"provider": "none", "model_name": "none",
                "tokens_in": 0, "tokens_out": 0}
    _, thinking = _extract(resp)
    if hasattr(resp, "usage") and hasattr(resp.usage, "input_tokens"):          # Anthropic
        provider, model = "anthropic", LLM_MODEL
        tokens_in, tokens_out = resp.usage.input_tokens, resp.usage.output_tokens
    elif hasattr(resp, "usage") and hasattr(resp.usage, "prompt_tokens"):       # OpenAI / DeepSeek
        model = getattr(resp, "model", "") or ""
        provider = "deepseek" if "deepseek" in model else "openai"
        tokens_in, tokens_out = resp.usage.prompt_tokens, resp.usage.completion_tokens
    elif hasattr(resp, "usage_metadata"):                                       # Gemini
        provider, model = "gemini", GEMINI_MODEL
        um = resp.usage_metadata
        tokens_in  = getattr(um, "prompt_token_count", None)
        tokens_out = getattr(um, "candidates_token_count", None)
    else:
        provider, model, tokens_in, tokens_out = "unknown", "", None, None
    mi = {"provider": provider, "model_name": model,
          "tokens_in": tokens_in, "tokens_out": tokens_out}
    if thinking:
        mi["internal_reasoning"] = thinking
    return mi

def _trunc(obj, limit=30000):
    """JSON-stringify + truncate to keep individual fields under SDK size limits
    (Thinking.output_payload ≤ 32 KB; per-record JSON ≤ 64 KB)."""
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return s if len(s) <= limit else s[:limit] + f"…[truncated, was {len(s)} chars]"


# (1) Observing — synthetic cron trigger that woke the agent.
rec_trigger = _new_record(
    "Observing",
    trigger_source="dev-guide-workflow-test",
    trigger_type="cron_trigger",
    trigger_description=f"Pre-match prediction run for fixture {SPORTMONKS_FIXTURE_ID} ({fixture['name']})",
    trigger_payload_summary=(
        f"fixture_id={SPORTMONKS_FIXTURE_ID}; window=PRE_MATCH; "
        f"kickoff_utc={fixture['starting_at']}; home={home['short_code']}; away={away['short_code']}"
    ),
)

# (2) ToolCalling — Sportmonks schedule
rec_sm_schedule = _new_record(
    "ToolCalling",
    upstream_record_id=[rec_trigger["record_id"]],
    tool_meta={"name": "sportmonks", "endpoint": "/v3/football/schedules/seasons/{season_id}",
               "via": "arena.sportmonks_proxy"},
    description="List WC2026 season schedule to discover fixtures",
    input_payload={"season_id": 26618},
    output_payload={"stage_count": len(schedule), "picked_fixture_id": SPORTMONKS_FIXTURE_ID},
    success=True,
)

# (3) ToolCalling — Sportmonks fixture detail
rec_sm_fixture = _new_record(
    "ToolCalling",
    upstream_record_id=[rec_sm_schedule["record_id"]],
    tool_meta={"name": "sportmonks", "endpoint": "/v3/football/fixtures/{fixture_id}",
               "via": "arena.sportmonks_proxy"},
    description="Fetch fixture detail with pre-match prediction includes",
    input_payload={"fixture_id": SPORTMONKS_FIXTURE_ID,
                   "include":    "participants;predictions;odds;xGFixture"},
    output_payload={
        "fixture_name":      fixture["name"],
        "kickoff_utc":       fixture["starting_at"],
        "participants":      [{"id": p["id"], "name": p["name"],
                               "short_code": p["short_code"],
                               "country_id": p["country_id"],
                               "location": p["meta"]["location"]} for p in fixture["participants"]],
        "predictions_count": len(fixture.get("predictions") or []),
        "odds_count":        len(fixture.get("odds") or []),
        "xgfixture_count":   len(fixture.get("xgfixture") or []),
    },
    success=True,
)

# (4) Thinking — Sportmonks digest
rec_th_sportmonks = _new_record(
    "Thinking",
    upstream_record_id=[rec_sm_fixture["record_id"]],
    model_invocation=_mi(llm_digest),
    prompt=_trunc(DIGEST_SYS, limit=16000),
    inputs=[{
        "input_record_id": rec_sm_fixture["record_id"],
        "input_payload":   _trunc({
            "fixture":     fixture["name"],
            "home_code":   home["short_code"],
            "away_code":   away["short_code"],
            "predictions": fixture.get("predictions"),
            "odds":        fixture.get("odds"),
            "xGFixture":   fixture.get("xgfixture"),
        }),
    }],
    output_payload=_trunc(sportmonks_digest),
)

# (5a) ToolCalling — arena: look up the polymarket event slug for the fixture.
rec_pm_slug = _new_record(
    "ToolCalling",
    upstream_record_id=[rec_sm_schedule["record_id"]],
    tool_meta={"name": "arena-mapping",
               "endpoint": "/api/v1/web/mapping"},
    description="Look up curated Polymarket event_slug for this Sportmonks fixture",
    input_payload={"fixture_id": SPORTMONKS_FIXTURE_ID},
    output_payload={"polymarket_event_slug": polymarket_event_slug},
    success=polymarket_event_slug is not None,
)

# (5b) ToolCalling — Polymarket Gamma: fetch the event + nested markets
# (condition_ids + clobTokenIds for home / draw / away).
rec_pm_event = _new_record(
    "ToolCalling",
    upstream_record_id=[rec_pm_slug["record_id"]],
    tool_meta={"name": "polymarket-gamma",
               "endpoint": "/api/v1/data/proxy/polymarket-gamma/events",
               "via": "arena.proxy"},
    description="Fetch Polymarket event + 3 child winner markets by slug",
    input_payload={"slug": polymarket_event_slug},
    output_payload={
        "outcomes": {k: {"team_code":     moneyline["outcomes"][k]["team_code"],
                         "condition_id":  moneyline["outcomes"][k]["condition_id"],
                         "token_yes":     moneyline["outcomes"][k]["token_yes"]}
                     for k in moneyline["outcomes"]}
    } if moneyline else None,
    success=moneyline is not None,
)

# (5c) ToolCalling — Polymarket CLOB: live midpoint per YES token (3 calls
# summarized into one record).
rec_pm_mids = _new_record(
    "ToolCalling",
    upstream_record_id=[rec_pm_event["record_id"]],
    tool_meta={"name": "polymarket-clob",
               "endpoint": "/api/v1/data/proxy/polymarket-clob/midpoint",
               "via": "arena.proxy"},
    description="Fetch CLOB midpoint per outcome YES token (home / draw / away)",
    input_payload={"token_ids": [
        moneyline["outcomes"][k]["token_yes"] for k in moneyline["outcomes"]
    ] if moneyline else None},
    output_payload={
        k: moneyline["outcomes"][k]["current_mid_yes"] for k in moneyline["outcomes"]
    } if moneyline else None,
    success=moneyline is not None,
)

# (6) Thinking — Polymarket digest
rec_th_polymarket = _new_record(
    "Thinking",
    upstream_record_id=[rec_pm_slug["record_id"],
                        rec_pm_event["record_id"],
                        rec_pm_mids["record_id"]],
    model_invocation=_mi(llm_pm),
    prompt=_trunc(POLYMARKET_DIGEST_SYS, limit=16000),
    inputs=[{
        "input_record_id": rec_pm_mids["record_id"],
        "input_payload":   _trunc(moneyline),
    }],
    output_payload=_trunc(polymarket_digest),
)

# (7) ToolCalling — Supabase catalog discovery
rec_sb_catalog = _new_record(
    "ToolCalling",
    upstream_record_id=[rec_trigger["record_id"]],
    tool_meta={"name": "supabase", "endpoint": "/rest/v1/catalog_full"},
    description="Discover available Supabase tables via the public catalog",
    input_payload={"params": {"select": "table_name,category,row_count,table_description",
                              "order":  "category,table_name"}},
    output_payload={"available_tables": [t["table_name"] for t in catalog],
                    "count": len(catalog)},
    success=True,
)

# (8) ToolCalling — Supabase priors fetch
rec_sb_priors = _new_record(
    "ToolCalling",
    upstream_record_id=[rec_sb_catalog["record_id"], rec_sm_fixture["record_id"]],
    tool_meta={"name": "supabase", "endpoint": f"/rest/v1/{WANTED_TABLE}",
               "schema": "world_cup_arena"},
    description=f"Fetch {WANTED_TABLE} priors for both teams",
    input_payload={"country_id": f"in.({TEAM_A_ID},{TEAM_B_ID})", "select": "*"},
    output_payload=priors_rows,
    success=True,
)

# (9) Thinking — Supabase digest
rec_th_supabase = _new_record(
    "Thinking",
    upstream_record_id=[rec_sb_priors["record_id"]],
    model_invocation=_mi(llm_sb),
    prompt=_trunc(SUPABASE_DIGEST_SYS, limit=16000),
    inputs=[{
        "input_record_id": rec_sb_priors["record_id"],
        "input_payload":   _trunc({
            "fixture":      fixture["name"],
            "source_table": WANTED_TABLE,
            "home_code":    home["short_code"],
            "away_code":    away["short_code"],
            "rows":         priors_rows,
        }),
    }],
    output_payload=_trunc(supabase_digest),
)

# (10) Thinking — Predict (priors only, blind to market).
# The reasoning lives here; the structured prediction is committed via the
# Acting record below, which is the form the arena validates + scores.
rec_th_predict = _new_record(
    "Thinking",
    upstream_record_id=[rec_th_sportmonks["record_id"], rec_th_supabase["record_id"]],
    model_invocation=_mi(llm_predict),
    prompt=_trunc(PREDICT_SYS, limit=16000),
    inputs=[
        {"input_record_id": rec_th_sportmonks["record_id"],
         "input_payload":   _trunc(sportmonks_digest)},
        {"input_record_id": rec_th_supabase["record_id"],
         "input_payload":   _trunc(supabase_digest)},
    ],
    output_payload=_trunc(prediction),
)

# (11) Acting — Prediction (validated + scored by the arena).
# Per the new ledger contract, predictions are emitted as Acting records with
# action_type="prediction" and structured `parameters` the arena snapshots
# for scoring at settlement. probability is clamped to the schema range
# [0.001, 0.999].
_pred_prob = max(0.001, min(0.999, float(prediction["probability"])))
rec_act_predict = _new_record(
    "Acting",
    upstream_record_id=[rec_th_predict["record_id"]],
    action_type=     "prediction",
    target_system=   "arena",
    action_summary=  f"Predict {prediction['outcome']} @ p={_pred_prob:.2f} for fixture {SPORTMONKS_FIXTURE_ID}",
    parameters=      {
        "fixture_code": str(SPORTMONKS_FIXTURE_ID),
        "outcome":      prediction["outcome"],
        "probability":  _pred_prob,
    },
    dry_run=         False,
    execution_status="confirmed",
)

# (12) Thinking — Strategy (prediction + market → trade decision)
rec_th_strategy = _new_record(
    "Thinking",
    upstream_record_id=[rec_th_predict["record_id"], rec_th_polymarket["record_id"]],
    model_invocation=_mi(llm_strategy),
    prompt=_trunc(STRATEGY_SYS, limit=16000),
    inputs=[
        {"input_record_id": rec_th_predict["record_id"],
         "input_payload":   _trunc(prediction)},
        {"input_record_id": rec_th_polymarket["record_id"],
         "input_payload":   _trunc(polymarket_digest)},
    ],
    output_payload=_trunc(strategy),
)

records = [
    rec_trigger, rec_sm_schedule,
    rec_pm_slug, rec_pm_event, rec_pm_mids,
    rec_sm_fixture, rec_th_sportmonks,
    rec_th_polymarket,
    rec_sb_catalog, rec_sb_priors, rec_th_supabase,
    rec_th_predict, rec_act_predict, rec_th_strategy,
]

# (13) Acting — emit only when the agent actually submitted an order.
# This is the AGENT-side Acting (intent / submission). The arena will
# additionally write its own Acting record(s) server-side at fill / close
# time with target_system="public-chain" + execution_id=<tx_hash>. The two
# are independent evidence of the same logical action.
if strategy and strategy.get("should_trade"):
    # Did the order POST land cleanly? If yes, status=pending (waiting on fill);
    # if not, status=failed. order_response is None on 404 / exception.
    submitted_ok = isinstance(order_response, dict) and bool(order_response)
    rec_act = _new_record(
        "Acting",
        upstream_record_id=[rec_th_strategy["record_id"]],
        action_type=     "open_order",
        target_system=   "arena",     # we submit to arena; arena routes to polymarket-clob
        action_summary=  (f"Open {strategy['direction']} ${strategy['size_usdc']:.2f} on "
                          f"{strategy['outcome']} @ ≤{strategy['limit_price']}"),
        parameters=      order_payload,
        dry_run=         False,
        execution_status="pending" if submitted_ok else "failed",
        execution_id=    (order_response.get("order_id") if submitted_ok else None),
    )
    records.append(rec_act)

print(f"Built {len(records)} ledger records -- one per step the agent took:\n")
for rec in records:
    label = (rec.get("description")
             or rec.get("action_summary")
             or rec.get("trigger_description")
             or rec.get("prompt", "")[:50])
    print(f"  {rec['behavior']:12s} {rec['record_id'][:8]}...  {label}")

# Submit the trace as a single batch. Per the new ledger contract:
#   - No session-create endpoint; session_id is purely a client-side string.
#   - Bare record dicts (no {"body": {...}} envelope).
#   - agent_id is derived server-side from x-api-key.
#   - One round-trip per cycle via /records/batch (≤50 records). Response:
#       {"records": [<enriched echoes>], "errors": [{index, code, message}, ...]}
# Endpoint isn't live on staging yet — expect 404. Script reports rather than raises.
try:
    r = requests.post(
        f"{ARENA}/api/v1/arena/ledger/records/batch",
        headers=H_ARENA, timeout=60,
        json={"records": records},
    )
    if r.status_code == 404:
        print(f"\nHTTP 404 -- the ledger endpoint isn't live on staging yet (expected). "
              f"The {len(records)} records above are exactly what a real run would submit.")
    elif r.ok:
        resp = r.json()
        print(f"\nHTTP {r.status_code} (OK) -- ledger accepted: "
              f"{len(resp.get('records', []))} stored, {len(resp.get('errors', []))} error(s).")
        for e in resp.get("errors", []):
            print(f"    [#{e.get('index')}] {e.get('code')}: {e.get('message')}")
    else:
        print(f"\nHTTP {r.status_code} -- ledger rejected. Body: {r.text[:300]}")
except Exception as e:
    print(f"\nLedger POST failed: {type(e).__name__}: {e}")

print("=" * 40 + " Cell 36 done")

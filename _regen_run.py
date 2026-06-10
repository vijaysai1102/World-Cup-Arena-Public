"""Regenerate _run.py from the current notebook cells."""
import sys, json, re
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

nb = json.load(open('worldcup-arena-sample-agent.ipynb', encoding='utf-8'))
code_cells = {i: ''.join(c['source']) for i, c in enumerate(nb['cells']) if c['cell_type'] == 'code'}

# Supabase stub (replaces cells 19-26 which require a live DB)
SUPABASE_STUB = '''
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
'''

parts = [
    'import sys',
    'sys.stdout.reconfigure(encoding="utf-8", errors="replace")',
    '',
]

# Cell order: setup, clients+digest_sys, schedule, polymarket_slug,
#             fixture_detail, polymarket_event, clob_mids, polymarket_digest,
#             supabase_stub, predict, strategy, order, ledger
cell_order = [2, 5, 7, 10, 12, 15, 17, 28, 30, 32, 36]

sep_labels = {
    2:  'Cell 2 done',
    5:  'Cell 5 done',
    7:  'Cell 7 done',
    10: 'Cell 10 done',
    12: 'Cell 12 done (digest)',
    15: 'Cell 15 done',
    17: 'Cell 17 done',
    28: 'Cell 28 done',
    30: 'Cell 30 done',
    32: 'Cell 32 done',
    36: 'Cell 36 done',
}

for idx in cell_order:
    src = code_cells.get(idx, '')
    if not src.strip():
        print(f'WARNING: cell {idx} is empty')
        continue
    parts.append(src)
    label = sep_labels.get(idx, f'Cell {idx} done')
    parts.append(f'print("=" * 40 + " {label}")')
    parts.append('')

    # After cell 17 (polymarket digest), insert supabase stub
    if idx == 17:
        parts.append(SUPABASE_STUB)
        parts.append('')

final = '\n'.join(parts)
open('_run.py', 'w', encoding='utf-8').write(final)
print('_run.py regenerated from notebook cells.')

# Quick syntax check
import py_compile, tempfile, os
tmp = tempfile.mktemp(suffix='.py')
open(tmp, 'w', encoding='utf-8').write(final)
try:
    py_compile.compile(tmp, doraise=True)
    print('Syntax check: OK')
except py_compile.PyCompileError as e:
    print(f'Syntax error: {e}')
finally:
    os.unlink(tmp)

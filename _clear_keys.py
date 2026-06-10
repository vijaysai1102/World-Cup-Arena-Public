import sys, json, re
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

nb = json.load(open('worldcup-arena-sample-agent.ipynb', encoding='utf-8'))

# Find cell 2 (has ARENA_KEY and ANTHROPIC_KEY)
for i, cell in enumerate(nb['cells']):
    if cell['cell_type'] != 'code':
        continue
    src = ''.join(cell['source'])
    if 'ARENA_KEY' in src and 'ANTHROPIC_KEY' in src:
        # Replace actual key values with placeholders
        src = re.sub(
            r'(ARENA_KEY\s*=\s*)["\']([^"\']{10,})["\']',
            r'\1"YOUR_ARENA_KEY_HERE"',
            src
        )
        src = re.sub(
            r'(ANTHROPIC_KEY\s*=\s*)["\']([^"\']{10,})["\']',
            r'\1"YOUR_ANTHROPIC_KEY_HERE"',
            src
        )
        cell['source'] = [src]
        print(f'Cell {i}: API keys cleared.')
        break

json.dump(nb, open('worldcup-arena-sample-agent.ipynb', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
print('Notebook saved with keys redacted.')

import sys

lines = open('backend/routes/market.py', 'r', encoding='utf-8').readlines()

start_idx = -1
end_idx = -1

for i, line in enumerate(lines):
    if '── FALLING KNIFE GUARD / IMPULSE METRICS ──' in line:
        start_idx = i
    if 'except Exception as e:' in line and 'Error in _build_manual_trading_payload' in lines[i+1]:
        end_idx = i
        break

if start_idx != -1 and end_idx != -1:
    for i in range(start_idx, end_idx):
        if lines[i].strip() == '':
            continue
        lines[i] = '    ' + lines[i]
    with open('backend/routes/market.py', 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print(f'Fixed indentation from {start_idx} to {end_idx}', file=sys.stderr)
else:
    print(f'Could not find bounds. start={start_idx}, end={end_idx}', file=sys.stderr)

from pathlib import Path
from collections import defaultdict
from decimal import Decimal
import csv
import shlex
import hashlib
import tempfile
import math

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def weights(label):
    result = defaultdict(list)
    seen = set()
    with (HERE / f'{label}_weights.tsv').open() as f:
        for row in csv.DictReader(f, delimiter='\t'):
            key = (row['ORIGINAL_NET'], row['SPLIT_NODE'])
            if key in seen:
                raise RuntimeError('가중치 장부 중복')
            seen.add(key)
            value = float(row['WEIGHT'])
            if not math.isfinite(value) or value < 0:
                raise RuntimeError('가중치 값 오류')
            result[key[0]].append((key[1], value))
    for net, entries in result.items():
        if abs(sum((v for _, v in entries)) - 1) > 1e-12:
            raise RuntimeError(f'가중치 합 오류: {net}')
    return result

def source_caps(folder):
    result = []
    scale_seen = False
    for line in (folder / 'comet_ram_2x1.ext').read_text().splitlines():
        f = shlex.split(line)
        if not f:
            continue
        if f[0] == 'scale':
            if f[2] != '1':
                raise RuntimeError('예상과 다른 EXT C 스케일')
            scale_seen = True
        elif f[0] in ('use', 'equiv', 'subcap'):
            raise RuntimeError(f'추가 해석이 필요한 EXT 레코드: {f[0]}')
        elif f[0] == 'node':
            result.append((f[1], 'vss', Decimal(f[3]) / 1000))
        elif f[0] == 'cap':
            result.append((f[1], f[2], Decimal(f[3]) / 1000))
        elif f[0] == 'substrate' and Decimal(f[3]) != 0:
            raise RuntimeError('0이 아닌 기판 C의 별도 해석 필요')
    if not scale_seen:
        raise RuntimeError('EXT C 스케일 기록 없음')
    return result

def is_cap(line):
    f = line.split()
    return bool(f) and f[0][0].upper() == 'C'

def non_caps(text):
    return [line for line in text.splitlines() if not is_cap(line)]

def cap_ledger(text):
    ledger = defaultdict(Decimal)
    for line in text.splitlines():
        if not is_cap(line):
            continue
        f = line.split()
        if len(f) < 4 or not f[3].lower().endswith('f'):
            raise RuntimeError('예상과 다른 C 레코드 형식')
        key = tuple(sorted((f[1], f[2])))
        ledger[key] += Decimal(f[3][:-1])
    return ledger

def build(label, folder):
    w = weights(label)
    caps = source_caps(folder)
    raw = (folder / 'comet_ram_2x1.spice').read_text()
    kept = non_caps(raw)
    endings = [i for i, line in enumerate(kept) if line.strip().lower().startswith('.ends')]
    if len(endings) != 1:
        raise RuntimeError('단일 서브회로 종료 위치 확인 실패')
    generated = []
    balance = defaultdict(Decimal)
    expected = defaultdict(Decimal)
    for a, b, value in caps:
        if value < 0:
            raise RuntimeError('음수 C')
        if not value:
            continue
        if a == b:
            raise RuntimeError('동일 노드 양단의 C')
        if a not in w or b not in w:
            raise RuntimeError(f'C 배분 가중치 누락: {a}, {b}')
        pair = tuple(sorted((a, b)))
        expected[pair] += value
        for na, wa in w[a]:
            for nb, wb in w[b]:
                number = float(value) * wa * wb
                if number == 0:
                    continue
                printed = f'{number:.15g}'
                generated.append(f'CMODEL{len(generated)} {na} {nb} {printed}f')
                balance[pair] += Decimal(printed)
    for pair, value in expected.items():
        if abs(balance[pair] - value) > Decimal('1e-12'):
            raise RuntimeError(f'노드 쌍별 C 보존 실패: {pair}')
    i = endings[0]
    text = '\n'.join(kept[:i] + generated + kept[i:]) + '\n'
    if non_caps(text) != non_caps(raw):
        raise RuntimeError('비C 레코드 보존 실패')
    if label == 'original':
        old = (BASE / 'layout_rc_candidate.spice').read_text()
        if non_caps(text) != non_caps(old):
            raise RuntimeError('기존 모델 비C 레코드 재현 실패')
        a, b = (cap_ledger(text), cap_ledger(old))
        if a.keys() != b.keys():
            raise RuntimeError('기존 모델 C 연결 쌍 재현 실패')
        error = max((abs(a[key] - b[key]) for key in a))
        if error > Decimal('1e-12'):
            raise RuntimeError('기존 모델 C 값 재현 실패')
        print('기존 모델 C 연결·값 재현: 통과')
        print('최대 연결 쌍별 차이_fF:', error)
    target = WORK / label
    target.mkdir()
    (target / 'layout_rc_candidate.spice').write_text(text)
    with (target / 'pair_balance.tsv').open('w') as f:
        f.write('NODE_A\tNODE_B\tEXPECTED_FF\tACTUAL_FF\tERROR_FF\n')
        for pair in sorted(expected):
            f.write(f'{pair[0]}\t{pair[1]}\t{expected[pair]}\t{balance[pair]}\t{balance[pair] - expected[pair]}\n')
    verdict = f'모델=용량 보존 가중 배분 근사\n접지 기준=vss\n비C 레코드 보존=통과\n원래 노드 쌍별 용량 보존=통과\n생성 C 개수={len(generated)}\n기대 총 C_fF={sum(expected.values())}\n기록 총 C_fF={sum(balance.values())}\n공간 배분·물리 정확도=미검증\n기능 시험=미실행\n'
    (target / 'verdict.txt').write_text(verdict)
    print(f'\n=== {label} ===\n{verdict}')
import re

def recover_weights(folder):
    groups = defaultdict(list)
    for line in (folder / 'comet_ram_2x1.res.ext').read_text().splitlines():
        f = shlex.split(line)
        if f and f[0] == 'rnode':
            stem = re.sub('\\.(?:n|t)\\d+$', '', f[1])
            value = float(f[3])
            if not math.isfinite(value) or value < 0:
                raise RuntimeError('유효하지 않은 분할 C')
            groups[stem].append((f[1], value))
    result = {}
    for line in (folder / 'comet_ram_2x1.ext').read_text().splitlines():
        f = shlex.split(line)
        if not f or f[0] not in ('node', 'substrate'):
            continue
        name = f[1]
        if name in result:
            raise RuntimeError('원래 신호 이름 중복')
        if name == 'vss':
            result[name] = [('vss', 1.0)]
            continue
        stem = name[:-1] if name.endswith('#') else name
        entries = groups.get(stem)
        if not entries:
            raise RuntimeError(f'가중치 대응 없음: {name}')
        total = sum((value for _, value in entries))
        if total <= 0:
            raise RuntimeError(f'분할 C 합 오류: {name}')
        result[name] = [(node, value / total) for node, value in entries]
    return result

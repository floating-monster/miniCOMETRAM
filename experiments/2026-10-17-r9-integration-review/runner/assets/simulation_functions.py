from pathlib import Path
from collections import defaultdict
from decimal import Decimal
import hashlib
import os
import re
import shutil
import subprocess
import tempfile

def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def net(name):
    return re.sub('\\.(?:n|t)\\d+$', '', name)

def devices(text):
    result = {}
    for line in text.splitlines():
        f = line.split()
        if len(f) >= 6 and f[0].upper().startswith('X'):
            require(f[0] not in result, 'MOS 번호 중복')
            result[f[0]] = f
    require(len(result) == 53, '53 MOS 조건 미충족')
    return result

def circuit_nodes(text):
    nodes = set()
    ports = None
    for line in text.splitlines():
        f = line.split()
        if not f or f[0].startswith('*'):
            continue
        if f[0].lower() == '.subckt':
            require(ports is None, '단일 서브회로 조건 미충족')
            ports = f[2:]
            nodes.update(ports)
        elif f[0][0].upper() in ('R', 'C'):
            nodes.update(f[1:3])
        elif f[0][0].upper() == 'X':
            nodes.update(f[1:5])
    require(ports is not None, '모델 포트 없음')
    return (nodes, ports)

def check_parameters(label, name, old, new, folder):
    require(old[5] == new[5], f'{label}: MOS 모델 차이: {name}')

    def parameters(fields):
        result = {}
        for item in fields[6:]:
            require('=' in item, f'해석할 수 없는 MOS 파라미터: {item}')
            key, value = item.split('=', 1)
            require(key not in result, f'중복 MOS 파라미터: {key}')
            result[key] = value
        return result
    a, b = (parameters(old), parameters(new))
    require(a.keys() == b.keys(), f'{label}: 파라미터 종류 차이: {name}')
    differences = {k: (a[k], b[k]) for k in a if a[k] != b[k]}
    if not differences:
        return
    allowed = {'X20': {'ad': ('0.1113', '0.111'), 'as': ('0.1113', '0.111')}}
    for number in ('X32', 'X36', 'X44', 'X45'):
        allowed[number] = {'ad': ('1.272', '1.27'), 'as': ('1.272', '1.27'), 'pd': ('10.13', '10.1'), 'ps': ('10.13', '10.1')}
    require(label == 'candidate' and name in allowed and (differences == allowed[name]), f'{label}: 확인되지 않은 MOS 파라미터 차이: {name}: {differences}')
    with (folder / 'mos_output_precision_differences.tsv').open('a') as stream:
        for key, (before, after) in sorted(differences.items()):
            stream.write(f'{name}\t{key}\t{before}\t{after}\n')
    print(f'{label}: 확인된 출력 자릿수 차이 기록: {name}', flush=True)

#!/usr/bin/env python3
"""읽기 전용으로 분할 노드를 대응하고 두 RC 모델의 장부를 비교합니다."""
from pathlib import Path
from collections import Counter, defaultdict
from decimal import Decimal
import csv
import hashlib
import json
import re
import shlex


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stem(name):
    return re.sub(r'\.(?:n|t)\d+$', '', name).rstrip('#')


def load(raw, model):
    coords, node_c = {}, {}
    graph = defaultdict(list)
    resistors = []
    for line in (raw / 'comet_ram_2x1.res.ext').read_text().splitlines():
        f = shlex.split(line)
        if not f:
            continue
        if f[0] == 'rnode':
            require(f[1] not in coords, '분할 노드 중복')
            coords[f[1]] = tuple(f[4:6])
            node_c[f[1]] = Decimal(f[3])
        elif f[0] == 'resist':
            a, b, value = f[1], f[2], Decimal(f[3])
            graph[a].append(b)
            graph[b].append(a)
            resistors.append((a, b, value))
    mos, caps, spice_r, ports = {}, defaultdict(Decimal), [], []
    roles = defaultdict(list)
    nodes = set(coords)
    for line in model.read_text().splitlines():
        f = line.split()
        if not f or f[0].startswith('*'):
            continue
        if f[0].lower() == '.subckt':
            require(not ports, '단일 서브회로 조건 미충족')
            ports = f[2:]
            nodes.update(ports)
        elif f[0][0].upper() == 'X':
            require(f[0] not in mos, 'MOS 번호 중복')
            mos[f[0]] = f
            nodes.update(f[1:5])
            for role, node in zip('DGSB', f[1:5]):
                roles[node].append((f[0], role))
        elif f[0][0].upper() == 'R':
            spice_r.append((f[1], f[2], Decimal(f[3])))
            nodes.update(f[1:3])
        elif f[0][0].upper() == 'C':
            require(f[3].lower().endswith('f'), '예상 밖의 C 단위')
            caps[tuple(sorted(f[1:3]))] += Decimal(f[3][:-1])
            nodes.update(f[1:3])
    pair = lambda a, b: tuple(sorted((a, b)))
    raw_r_same = (Counter((pair(a,b),v) for a,b,v in resistors) ==
                  Counter((pair(a,b),v) for a,b,v in spice_r))
    graph = defaultdict(list)
    for a,b,_ in spice_r:
        graph[a].append(b)
        graph[b].append(a)
    raw_resistors, resistors = resistors, spice_r
    initial = {
        n: (stem(n), coords.get(n), tuple(sorted(roles[n])), n if n in ports else '')
        for n in nodes
    }
    return dict(coords=coords, node_c=node_c, graph=graph, resistors=resistors,
                mos=mos, caps=caps, ports=ports, nodes=nodes, initial=initial,
                raw_resistors=raw_resistors, raw_r_same=raw_r_same)


def node_mapping(old, new):
    require(old['ports'] == new['ports'], '상위 포트 순서 차이')
    require(len(old['nodes']) == len(new['nodes']), '전체 노드 개수 차이: 자동 대응 보류')
    def color(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    colors = [{n: color(v) for n,v in data['initial'].items()} for data in (old,new)]
    for iteration in range(16):
        groups = []
        for data, labels in zip((old,new), colors):
            g = defaultdict(list)
            for n in data['nodes']:
                g[labels[n]].append(n)
            groups.append(g)
        if groups[0].keys() != groups[1].keys() or any(
                len(groups[0][k]) != len(groups[1][k]) for k in groups[0]):
            raise RuntimeError('좌표·MOS 역할·저항 연결 구조의 대응 차이: 자동 대응 보류')
        unresolved = [k for k in groups[0] if len(groups[0][k]) != 1]
        if not unresolved:
            print('노드 대응 반복 단계:', iteration)
            return {values[0]: groups[1][key][0] for key,values in groups[0].items()}
        colors = [
            {n: color((labels[n], sorted(labels[x] for x in data['graph'][n])))
             for n in data['nodes']}
            for data, labels in zip((old,new),colors)
        ]
    raise RuntimeError('구분할 수 없는 노드가 남아 자동 대응 보류')


def source_caps(path):
    book = defaultdict(Decimal)
    for line in path.read_text().splitlines():
        f = shlex.split(line)
        if not f:
            continue
        if f[0] == 'scale':
            require(f[2] == '1', '원래 C 스케일 차이')
        elif f[0] == 'node':
            book[tuple(sorted((f[1], 'vss')))] += Decimal(f[3])/1000
        elif f[0] == 'cap':
            book[tuple(sorted(f[1:3]))] += Decimal(f[3])/1000
        elif f[0] in ('use','equiv','subcap'):
            raise RuntimeError('추가 해석이 필요한 원시 C 레코드')
    return {k:v for k,v in book.items() if v}


def read_weights(path):
    with path.open() as stream:
        return {(r['ORIGINAL_NET'],r['SPLIT_NODE']): Decimal(r['WEIGHT'])
                for r in csv.DictReader(stream,delimiter='\t')}


def compare(oldraw, newraw, oldmodel, newmodel, oldweights, newweights):
    old, new = load(oldraw,oldmodel), load(newraw,newmodel)
    mapping = node_mapping(old,new)
    print('기존 원시 R와 SPICE R의 문자 수치 동일:',old['raw_r_same'])
    print('이번 원시 R와 SPICE R의 문자 수치 동일:',new['raw_r_same'])
    print('원시 R와 SPICE R는 출력 정밀도 등의 차이가 있을 수 있어 별도로 기록합니다.')
    print('노드 일대일 대응 완료:',len(mapping))
    print('대응 근거=원래 신호·좌표·MOS 번호와 단자 역할·저항 이웃 구조')
    print('저항 값과 C 값은 노드 대응의 근거로 사용하지 않습니다.')
    pair = lambda a,b: tuple(sorted((a,b)))
    old_r = Counter((pair(mapping[a],mapping[b]),v) for a,b,v in old['resistors'])
    new_r = Counter((pair(a,b),v) for a,b,v in new['resistors'])
    print('\n=== R 비교 ===')
    print('저항 가지 수:',len(old['resistors']),'→',len(new['resistors']))
    print('대응 후 모델 R 연결·값·중복 개수 동일:',old_r == new_r)
    raw_old_r = Counter((pair(mapping[a],mapping[b]),v) for a,b,v in old['raw_resistors'])
    raw_new_r = Counter((pair(a,b),v) for a,b,v in new['raw_resistors'])
    print('대응 후 원시 R 연결·값·중복 개수 동일:',raw_old_r == raw_new_r)
    for title, changes in (('기존에만',old_r-new_r),('이번에만',new_r-old_r)):
        print(title,'저항 레코드 수:',sum(changes.values()))
        for item,count in list(changes.items())[:12]:print(item,'중복',count)

    print('\n=== MOS 비교 ===')
    require(old['mos'].keys() == new['mos'].keys(),'MOS 번호 집합 차이')
    mos_changes=[]
    for name,a in old['mos'].items():
        b=new['mos'][name]
        if [mapping[n] for n in a[1:5]] != b[1:5] or a[5] != b[5] or sorted(a[6:]) != sorted(b[6:]):
            mos_changes.append(name)
            print(name,'기존:', ' '.join(a),'\n이번:', ' '.join(b))
    print('MOS 수:',len(old['mos']),'→',len(new['mos']))
    print('대응 후 전체 단자·모델·파라미터 동일:',not mos_changes)

    def delta_report(title,a,b,unit):
        print('\n=== '+title+' ===')
        print('연결·항목 집합 동일:',a.keys()==b.keys())
        changes=[(key,a.get(key,Decimal(0)),b.get(key,Decimal(0)))
                 for key in a.keys() | b.keys()
                 if a.get(key,Decimal(0)) != b.get(key,Decimal(0))]
        print('차이 항목 수:',len(changes))
        print('최대 절대 차이_'+unit+':',max((abs(y-x) for _,x,y in changes),default=Decimal(0)))
        for key,x,y in sorted(changes,key=lambda z:abs(z[2]-z[1]),reverse=True)[:12]:
            print(key,'기존',x,'이번',y,'차이',y-x)
        return changes

    old_c = {pair(mapping[a],mapping[b]):v for (a,b),v in old['caps'].items()}
    require(len(old_c)==len(old['caps']),'C 대응 후 연결 쌍 중복')
    c_changes=delta_report('모델 C 노드 쌍별 값',old_c,new['caps'],'fF')
    print('기존 총 C_fF:',sum(old_c.values()))
    print('이번 총 C_fF:',sum(new['caps'].values()))
    node_changes=delta_report('원시 분할 rnode C',
        {mapping[n]:v for n,v in old['node_c'].items()},new['node_c'],'원본단위')
    old_w=read_weights(oldweights)
    weight_changes=delta_report('모델 배분 가중치',
        {(net,mapping[n]):v for (net,n),v in old_w.items()},read_weights(newweights),'비율')
    cap_changes=delta_report('원래 EXT 노드 쌍별 C',
        source_caps(oldraw/'comet_ram_2x1.ext'),source_caps(newraw/'comet_ram_2x1.ext'),'fF')
    print('\n읽기 전용 비교 완료 / 파일·허용 오차·판정 기준 변경 없음')
    print('모델 차이가 발견돼도 측정 차이의 단독 원인으로 자동 확정하지 않습니다.')
    return {'R동일':old_r==new_r,'MOS동일':not mos_changes,
            '모델C차이':len(c_changes),'원시분할C차이':len(node_changes),
            '가중치차이':len(weight_changes),'원래C차이':len(cap_changes)}


def verify_files(folder,names):
    book={}
    for line in (folder/'results.sha256').read_text().splitlines():
        h,name=line.split(None,1)
        book[name.strip()]=h
    for name in names:
        require(name in book and digest(folder/name)==book[name],f'해시 불일치: {folder/name}')


def main():
    root=Path('/home/gwen/workspace/COMETRAM_R9')
    task=root/'decoder_vss_m1_candidate_01_4or4fbvy'
    oldraw=task/'export_413_path_01_8spxtmkc'
    oldrecord=task/'skylark_413_models_01__e6d78vg'
    work=root/'integrated_skylark_01_q3wpzp03'
    verify_files(oldraw,['comet_ram_2x1.ext','comet_ram_2x1.res.ext'])
    verify_files(oldrecord,['models/candidate/layout_rc_candidate.spice','candidate_weights.tsv'])
    verify_files(work,['extract/comet_ram_2x1.ext','extract/comet_ram_2x1.res.ext',
                       'model/models/candidate/layout_rc_candidate.spice','model/candidate_weights.tsv'])
    result=compare(oldraw,work/'extract',
        oldrecord/'models/candidate/layout_rc_candidate.spice',
        work/'model/models/candidate/layout_rc_candidate.spice',
        oldrecord/'candidate_weights.tsv',work/'model/candidate_weights.tsv')
    print('\n집계:',result)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit(f'장부 비교 중단: {error}')

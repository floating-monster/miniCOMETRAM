#!/usr/bin/env python3
"""COMETRAM 후보의 생성·물리 검증·RC 근사·기존 시험 재현을 연결합니다."""
from pathlib import Path
from collections import defaultdict, Counter
from decimal import Decimal
import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import traceback
sys.dont_write_bytecode = True

PACKAGE = Path(__file__).resolve().parent
ASSETS = PACKAGE / "assets"
IMAGE = "sha256:b12e325417c221e4db0d1ad09e1282edc81bf5e566c004cfef0c9415e4d75371"
BIN = "/opt/miniconda3/envs/skyram-193r2/bin"
GDS = "82c0b12c0c82c8e22ae2eadf299c305cd03a6af69ae7391910d2ca7b08609c55"
SCHEMATIC = "8197748b08f8f68b905a59e132c8d66f65b18b3a1976dbd8643b3f5e128aef63"
SOURCE = "76cba3e5e0bd388100182159aa4b83db57a7ae37502006718e327f48feefc6c9"
INTERNAL_PDK = {
    "magic/sky130A.magicrc": "f717a54ec62b7df3e77426c38c6fbe925b4872b57a193b66d4d336e56e1fb88b",
    "magic/sky130A.tech": "8f4debca9f51ced32dd24666168ea039363a8e35afda951e39646ffa4bd33aab",
    "netgen/setup.tcl": "b0b1013a1911d1fb41dc1a727c5a82330add975ada62919e0a8214814d9282a6",
    "ngspice/sky130.lib.spice": "5efa041a988893c1a3580d0ecd57870ea3146b27741c7d42b56baaa336b9549e",
}


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def write_manifest(folder):
    (folder / "results.sha256").write_text("".join(
        f"{sha(p)}  {p.relative_to(folder)}\n"
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p != folder / "results.sha256"
    ))


def verify_manifest(folder, names):
    book = {}
    for line in (folder / "results.sha256").read_text().splitlines():
        digest, name = line.split(None, 1)
        require(name.strip() not in book, "해시 장부 중복")
        book[name.strip()] = digest
    for name in names:
        require(name in book and (folder / name).is_file() and
                sha(folder / name) == book[name], f"보존 결과 해시 불일치: {folder}/{name}")


def load_functions(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command_run(folder, label, command, cwd=None, env=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{label}_command.json").write_text(json.dumps(command, ensure_ascii=False, indent=2))
    print(f"{label}: 시작 — {folder / (label + '.log')}", flush=True)
    with (folder / f"{label}.log").open("w") as log:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
    (folder / f"{label}_exit.txt").write_text(f"종료코드={result.returncode}\n")
    if result.returncode:
        print((folder / f"{label}.log").read_text(errors="replace")[-5000:], flush=True)
    require(result.returncode == 0, f"{label}: 실행 실패, 종료코드 {result.returncode}")
    print(f"{label}: 종료", flush=True)
    return (folder / f"{label}.log").read_text(errors="replace")


def docker_command(folder, mount, shell, readonly=None):
    command = ["sudo", "docker", "run", "--rm", "--network", "none", "--read-only",
               "--tmpfs", "/tmp:rw,nosuid,nodev", "--user", f"{os.getuid()}:{os.getgid()}",
               "-e", "HOME=/tmp", "-e", "PDK_ROOT=/opt/pdk", "-e", "PDK=sky130A",
               "-v", f"{folder}:{mount}:rw"]
    if readonly:
        command.extend(["-v", f"{readonly}:/input:ro"])
    command.extend(["-w", mount, "--entrypoint", "/bin/sh", IMAGE, "-c",
                    f"set -eu\nexport PATH={BIN}:$PATH\n" + shell])
    return command


def verify_generated_gds(reference, generated, folder):
    import struct
    from collections import Counter

    def parse(path):
        data = path.read_bytes()
        position = 0
        library, cells = [], {}
        current, element = None, None
        starts = {0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x15, 0x2d}
        while position < len(data):
            require(position + 4 <= len(data), 'GDS 헤더 잘림')
            length, kind, datatype = struct.unpack('>HBB', data[position:position+4])
            require(length >= 4 and length % 2 == 0 and position + length <= len(data), 'GDS 레코드 길이 오류')
            payload = data[position+4:position+length]
            record = (kind, datatype, payload)
            position += length
            if kind == 0x01:
                require(current is None and len(payload) == 24, '라이브러리 시각 오류')
                library.append((kind, datatype, bytes(24)))
            elif kind == 0x05:
                require(current is None and len(payload) == 24, '셀 시작 오류')
                current = {'name': None, 'metadata': [(kind, datatype, bytes(24))], 'elements': []}
            elif kind == 0x06:
                require(current is not None and current['name'] is None, '셀 이름 오류')
                current['name'] = payload
                current['metadata'].append(record)
            elif kind in starts:
                require(current is not None and element is None, '요소 시작 오류')
                element = [record]
            elif kind == 0x11:
                require(element is not None, '요소 종료 오류')
                element.append(record)
                current['elements'].append(tuple(element))
                element = None
            elif kind == 0x07:
                require(current is not None and element is None, '셀 종료 오류')
                name = current['name']
                require(name is not None and name not in cells, '셀 이름 누락·중복')
                current['metadata'].append(record)
                cells[name] = (tuple(current['metadata']), Counter(current['elements']))
                current = None
            elif element is not None:
                element.append(record)
            elif current is not None:
                current['metadata'].append(record)
            else:
                library.append(record)
        require(current is None and element is None, 'GDS 종료 불완전')
        return tuple(library), cells

    require(sha(reference) == GDS, '비교 기준 후보 GDS 해시 불일치')
    old_library, old = parse(reference)
    new_library, new = parse(generated)
    same = old_library == new_library and old == new
    report = {
        '기준_원본_SHA256': sha(reference),
        '생성_원본_SHA256': sha(generated),
        '바이트동일': sha(reference) == sha(generated),
        '시각·셀요소순서제외_전체내용동일': same,
        '비교방법': '좌표·레이어·문자열·요소내부순서·중복개수·기타메타데이터 보존',
        '기준셀별요소개수': {n.rstrip(bytes(1)).decode(errors='replace'): sum(v[1].values()) for n, v in old.items()},
        '생성셀별요소개수': {n.rstrip(bytes(1)).decode(errors='replace'): sum(v[1].values()) for n, v in new.items()},
    }
    (folder / 'gds_identity.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    require(same, '재생성 GDS 요소 내용·개수 또는 메타데이터 차이')


def prepare_test(s, model_path, mode, folder):
    folder.mkdir()
    base = ASSETS / mode
    original = (base / "test.spice").read_text()
    old_devices = s.devices((base / "cell.spice").read_text())
    model = model_path.read_text()
    current = s.devices(model)
    require(current.keys() == old_devices.keys(), "MOS 번호 집합 차이")
    (folder / "mos_output_precision_differences.tsv").write_text("소자\t파라미터\t기존값\t시험값\n")
    options = defaultdict(set)
    roles = ["소자\t단자\t기존노드\t시험노드"]
    for name, old in old_devices.items():
        new = current[name]
        s.check_parameters("candidate", name, old, new, folder)
        require([s.net(x) for x in old[1:5]] == [s.net(x) for x in new[1:5]],
                f"MOS의 원래 신호 연결 차이: {name}")
        for role, a, b in zip("DGSB", old[1:5], new[1:5]):
            options[a].add(b)
            roles.append(f"{name}\t{role}\t{a}\t{b}")
    used = set(re.findall(r"(?i)\bxtop\.([A-Za-z0-9_.$#]+)", original))
    mapping = {}
    for node in used:
        require(len(options[node]) == 1, f"시험 노드 유일 대응 실패: {node}")
        mapping[node] = next(iter(options[node]))
    test = re.sub(r"(?i)\bxtop\.([A-Za-z0-9_.$#]+)", lambda m: "xtop." + mapping[m[1]], original)
    old_pdk = "/pdk/sky130A/libs.tech/ngspice/sky130.lib.spice"
    require(test.count(old_pdk) == 1, "PDK 경로 대응 실패")
    test = test.replace(old_pdk, "/opt/pdk/sky130A/libs.tech/ngspice/sky130.lib.spice")
    stop = "50n" if mode == "read" else "40n"
    require(test.count(f".tran 10p {stop}") == 1, "과도해석 설정 대응 실패")
    test = test.replace(f".tran 10p {stop}", f".tran 10p {stop} 0 2.5p")
    nodes, ports = s.circuit_nodes(model)
    top = next(line.split() for line in test.splitlines() if line.strip().lower().startswith("xtop "))
    require(len(ports) == len(top[1:-1]), "시험 포트 개수 차이")
    bindings = dict(zip(ports, top[1:-1]))
    groups = defaultdict(list)
    for node in sorted(nodes):
        if node != "0" and bindings.get(node) != "0":
            groups[s.net(node)].append(node)
    exports, columns = [], ["파일\t신호\t열번호\t노드"]
    for index, (signal, members) in enumerate(sorted(groups.items())):
        filename = f"net_{index:02d}.tsv"
        expressions = [f"v({bindings[n] if n in bindings else 'xtop.' + n})" for n in members]
        exports.append(f"wrdata {filename} " + " ".join(expressions))
        columns.extend(f"{filename}\t{signal}\t{i}\t{n}" for i, n in enumerate(members, 1))
    require(len(re.findall(r"(?m)^\s*quit\s*$", test)) == 1, "시험 종료 위치 차이")
    test = re.sub(r"(?m)^\s*quit\s*$", "\n".join(exports) + "\nquit", test)
    shutil.copy2(model_path, folder / "cell.spice")
    shutil.copy2(base / ".spiceinit", folder / ".spiceinit")
    (folder / "test.spice").write_text(test)
    (folder / "wave_columns.tsv").write_text("\n".join(columns) + "\n")
    (folder / "mos_terminal_mapping.tsv").write_text("\n".join(roles) + "\n")
    (folder / "test_node_mapping.tsv").write_text("기존노드\t시험노드\n" + "".join(f"{n}\t{mapping[n]}\n" for n in sorted(mapping)))
    (folder / "node_count.txt").write_text(f"전체 회로 노드={len(nodes)}\n")
    return [x.split()[1] for x in exports]


def measurements(folder, mode):
    names = re.findall(r"(?im)^\s*\.meas(?:ure)?\s+tran\s+(\w+)", (folder / "test.spice").read_text())
    require(len(names) == (36 if mode == "read" else 20) and len(set(names)) == len(names), "측정 개수·이름 오류")
    log = (folder / "ngspice.log").read_text(errors="replace")
    found = {m[1].lower(): Decimal(m[2]) for m in re.finditer(
        r"(?m)^\s*(\w+)\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?=\s|$)", log)}
    require(all(n.lower() in found and found[n.lower()].is_finite() for n in names), "측정 누락·비유한 값")
    result = {n: found[n.lower()] for n in names}
    (folder / "measurements.tsv").write_text("측정\t값\n" + "".join(f"{n}\t{v}\n" for n, v in result.items()))
    return result


def write_observations(folder, s):
    signals = {"r0_q": "a_20000_6483", "r0_qb": "a_19896_8041", "r1_q": "a_20000_14483", "r1_qb": "a_19896_16041"}
    times = [19, 26, 29, 36, 39]
    expected_q = {"r0": [False, True, True, True, True], "r1": [True, True, True, False, False]}
    files, members = defaultdict(list), defaultdict(set)
    with (folder / "wave_columns.tsv").open() as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            for name, signal in signals.items():
                if row["신호"].rstrip("#") == signal:
                    files[row["파일"]].append((int(row["열번호"]), row["노드"], name))
                    members[name].add(row["노드"])
    nodes, _ = s.circuit_nodes((folder / "cell.spice").read_text())
    for name, signal in signals.items():
        expected = {n for n in nodes if n.rstrip("#") == signal or n.startswith(signal + ".")}
        require(expected and expected == members[name], "전체 저장 노드 관측 범위 차이")
    samples = defaultdict(dict)
    for filename, columns in files.items():
        previous, pending = None, set(times)
        with (folder / filename).open() as stream:
            for line in stream:
                try:
                    values = [float(x) for x in line.split()]
                except ValueError:
                    continue
                if not values:
                    continue
                require(all(math.isfinite(x) for x in values), "비유한 저장 노드 파형")
                if previous is not None:
                    t0, t1 = previous[0], values[0]
                    require(t1 >= t0, "파형 시간 역전")
                    if t1 > t0:
                        for ns in sorted(pending):
                            if t0 <= ns * 1e-9 <= t1:
                                ratio = (ns * 1e-9 - t0) / (t1 - t0)
                                for column, node, name in columns:
                                    require(column < len(values) and column < len(previous), "파형 열 부족")
                                    samples[(ns, name)][node] = previous[column] + ratio * (values[column] - previous[column])
                                pending.remove(ns)
                previous = values
                if not pending:
                    break
        require(not pending, "저장 노드 측정 시점 누락")
    rows = ["시점_ns\t신호\t노드\t전압_V\t기대높음\t일치"]
    passed = True
    for index, ns in enumerate(times):
        for name in signals:
            points = samples[(ns, name)]
            require(points.keys() == members[name], "저장 노드 표본 누락")
            high = expected_q[name[:2]][index]
            if name.endswith("_qb"):
                high = not high
            for node, voltage in sorted(points.items()):
                match = voltage > 0.9 if high else voltage < 0.9
                passed = passed and match
                rows.append(f"{ns}\t{name}\t{node}\t{voltage:.15g}\t{high}\t{match}")
    (folder / "all_storage_observations.tsv").write_text("\n".join(rows) + "\n")
    require(passed, "비교용 0.9V 기준의 저장 노드 기대 상태 불일치")
    return len(rows) - 1


def run_pipeline(args):
    root, pdk = args.root.resolve(), args.pdk_root.resolve()
    protected = {
        root / "src/main.rs": "a5294b9c07372d8e784347ef59495fcfd5a230ba4bc21085d88d5c425b98dea5",
        root / "output/comet_ram_2x1.gds": "dc81603a01f7eca84fdeded61979503e1324b70d307f3c63e5b86476d3bd535c",
    }
    for path, expected in protected.items():
        require(path.is_file() and sha(path) == expected, f"기준 원형 해시 불일치: {path}")
    for line in (PACKAGE / "package.sha256").read_text().splitlines():
        digest, name = line.split(None, 1)
        require(sha(PACKAGE / name.strip()) == digest, f"통합 패키지 해시 불일치: {name}")
    require(pdk.is_dir(), "생성용 호스트 PDK 경로 없음")
    references = {"read": args.read_reference.resolve(), "write": args.write_reference.resolve()}
    for mode, folder in references.items():
        verify_manifest(folder.parent, [f"candidate/{n}" for n in ("test.spice", "cell.spice", "measurements.tsv", "ngspice.log")])
        require(sha(folder / "cell.spice") == sha(root / "decoder_vss_m1_candidate_01_4or4fbvy/skylark_413_models_01__e6d78vg/models/candidate/layout_rc_candidate.spice"), "시험 대조본의 413 모델 차이")
    work = Path(tempfile.mkdtemp(prefix="integrated_skylark_01_", dir=root))
    print("통합 실행 폴더:", work, flush=True)
    print("현재 상태 확인: 이 폴더의 progress.txt와 단계별 로그", flush=True)
    stages = []
    def stage(name):
        stages.append(name)
        (work / "progress.txt").write_text("\n".join(stages) + "\n")
        print(f"\n=== {name} ===", flush=True)
    ok, error = False, ""
    try:
        shutil.copytree(PACKAGE, work / "runner", ignore=shutil.ignore_patterns("__pycache__"))
        baseline = work / "replay_reference"
        for mode, folder in references.items():
            target = baseline / mode
            target.mkdir(parents=True)
            for name in ("measurements.tsv", "test.spice", "cell.spice"):
                shutil.copy2(folder / name, target / name)
        stage("환경 확인")
        metadata = work / "environment"
        hostenv = os.environ.copy()
        hostenv["COMETRAM_PDK_ROOT"] = str(pdk)
        hostenv["CARGO_TARGET_DIR"] = str(root / "integration_build_cache_01")
        command_run(metadata, "rustc", ["rustc", "--version"])
        command_run(metadata, "cargo", ["cargo", "--version"])
        sysroot = Path(command_run(metadata, "rust_sysroot", ["rustc", "--print", "sysroot"]).strip())
        (metadata / "host_tools.sha256").write_text("".join(
            f"{sha(sysroot / 'bin' / name)}  {sysroot / 'bin' / name}\n"
            for name in ("rustc", "cargo")
        ))
        (metadata / "host_generation.json").write_text(json.dumps({
            "생성용_PDK": str(pdk), "검증용_PDK": "/opt/pdk/sky130A", "이미지": IMAGE,
            "호스트생성_PDK_기술파일_SHA256": sha(pdk / "sky130A/libs.tech/magic/sky130A.tech"),
            "외부의존": "호스트 Rust/Cargo·캐시된 Substrate 의존성·생성용 호스트 PDK",
        }, ensure_ascii=False, indent=2))
        (metadata / "netgen_version.tcl").write_text("puts {NETGEN_BATCH_CHECK_COMPLETED}\nquit\n")
        checks = "\n".join(f"{v}  /opt/pdk/sky130A/libs.tech/{k}" for k, v in INTERNAL_PDK.items())
        shell = "magic --version\nnetgen -batch source /environment/netgen_version.tcl\nngspice --version\npython3 --version\nreadlink -f /opt/pdk/sky130A\n" + "sha256sum -c <<'HASHES'\n" + checks + "\nHASHES\n"
        log = command_run(metadata, "container", docker_command(metadata, "/environment", shell))
        require("8.3.413" in log and "1.5.286" in log and "ngspice-41" in log, "도구 버전 확인 실패")
        stage("후보 생성")
        project = work / "project"
        shutil.copytree(ASSETS / "project", project)
        (project / "output").mkdir()
        command_run(work / "generation", "cargo_run", ["cargo", "run", "--locked", "--offline"], cwd=project, env=hostenv)
        require(sha(project / "src/main.rs") == SOURCE, "생성 소스 해시 차이")
        verify_generated_gds(root / "decoder_vss_m1_candidate_01_4or4fbvy/project/output/comet_ram_2x1.gds", project / "output/comet_ram_2x1.gds", work / "generation")
        require(sha(project / "output/comet_ram_2x1.spice") == SCHEMATIC, "재생성 회로도 차이")
        stage("DRC·LVS")
        verify = work / "verify"
        verify.mkdir()
        shutil.copy2(ASSETS / "check.tcl", verify / "check.tcl")
        log = command_run(verify, "magic", docker_command(verify, "/verify", "exec magic -dnull -noconsole -rcfile /opt/pdk/sky130A/libs.tech/magic/sky130A.magicrc /verify/check.tcl", project / "output"))
        require("FULL_CHECK_SCRIPT_COMPLETED" in log, "물리 검증 완료 표식 없음")
        require(re.search(r"^DRC_COUNT=0\s*$", (verify / "drc_report.txt").read_text(), re.M), "DRC 오류 0 조건 미충족")
        command_run(verify, "netgen", docker_command(verify, "/verify", 'exec netgen -batch lvs "/input/comet_ram_2x1.spice comet_ram_2x1" "/verify/layout_raw.spice comet_ram_2x1" /opt/pdk/sky130A/libs.tech/netgen/setup.tcl /verify/lvs_report.txt', project / "output"))
        report = (verify / "lvs_report.txt").read_text()
        require("Final result: Circuits match uniquely." in report, "LVS 고유 일치 미확인")
        require(re.search(r"Number of devices:\s*53\s*\|Number of devices:\s*53\b", report), "양쪽 53소자 조건 미확인")
        stage("413 RC 추출")
        extract = work / "extract"
        extract.mkdir()
        shutil.copy2(project / "output/comet_ram_2x1.gds", extract / "input.gds")
        for name in ("reference.spice", "fix_separator.py"):
            shutil.copy2(ASSETS / name, extract / name)
        tcl = (ASSETS / "openram_flow.tcl").read_text()
        require(tcl.count("ext2spice comet_ram_2x1") == 1, "추출 진입점 변경 대상 차이")
        tcl = tcl.replace("ext2spice comet_ram_2x1", "ext2spice -p $work comet_ram_2x1")
        (extract / "openram_flow.tcl").write_text(tcl)
        log = command_run(extract, "magic", docker_command(extract, "/extract", "exec magic -dnull -noconsole -rcfile /opt/pdk/sky130A/libs.tech/magic/sky130A.magicrc /extract/openram_flow.tcl"))
        require("OPENRAM_FLOW_COMPLETED" in log and "OPENRAM_FLOW_ERROR=" not in log, "RC 추출 완료 조건 미충족")
        import shlex
        counts = Counter()
        for line in (extract / "comet_ram_2x1.res.ext").read_text().splitlines():
            fields = shlex.split(line)
            if fields:
                counts[fields[0]] += 1
        require(counts["device"] == 53 and counts["resist"] == 486 and counts["rnode"] == 447, "413 저항망 개수 기준 미충족")
        raw_counts = Counter()
        for line in (extract / "comet_ram_2x1.spice").read_text().splitlines():
            f = line.split()
            if f and f[0][0].upper() in ("X", "R", "C"):
                raw_counts[f[0][0].upper()] += 1
        require(raw_counts["X"] == 53 and raw_counts["R"] == 486, "최종 SPICE에 MOS·R 미반영")
        stage("용량 보존 모델 재구성")
        m = load_functions("model_functions", ASSETS / "model_functions.py")
        m.HERE = work / "model"
        m.WORK = m.HERE / "models"
        m.WORK.mkdir(parents=True)
        weights = m.recover_weights(extract)
        with (m.HERE / "candidate_weights.tsv").open("w") as stream:
            stream.write("ORIGINAL_NET\tSPLIT_NODE\tWEIGHT\n")
            for net in sorted(weights):
                for node, value in weights[net]:
                    stream.write(f"{net}\t{node}\t{value:.17g}\n")
        m.build("candidate", extract)
        model = m.WORK / "candidate/layout_rc_candidate.spice"
        require(sum((v for _, _, v in m.source_caps(extract)), Decimal(0)) == Decimal("500.794664373"), "413 원래 총 C 차이")
        s = load_functions("simulation_functions", ASSETS / "simulation_functions.py")
        model_nodes, _ = s.circuit_nodes(model.read_text())
        require(len(model_nodes) == 447, "모델 전체 노드 개수 차이")
        require(len(m.cap_ledger(model.read_text())) == 21731, "재구성 C 연결 쌍 수 차이")
        replay_all = True
        simroot = work / "simulation"
        simroot.mkdir()
        for mode in ("read", "write"):
            stage("읽기 시험" if mode == "read" else "쓰기 시험")
            folder = simroot / mode
            wavefiles = prepare_test(s, model, mode, folder)
            command_run(folder, "ngspice", docker_command(folder, "/sim", "exec ngspice -b test.spice"))
            values = measurements(folder, mode)
            for name in ["waveform.tsv"] + wavefiles:
                require((folder / name).is_file() and (folder / name).stat().st_size > 0, f"파형 누락: {name}")
            with (baseline / mode / "measurements.tsv").open() as stream:
                expected = {r["측정"]: Decimal(r["값"]) for r in csv.DictReader(stream, delimiter="\t")}
            require(values.keys() == expected.keys(), "대조 측정 이름 집합 차이")
            rows = ["측정\t기존413\t이번413\t차이\t로그정밀도동일"]
            for name, value in values.items():
                same = value == expected[name]
                replay_all = replay_all and same
                rows.append(f"{name}\t{expected[name]}\t{value}\t{value-expected[name]}\t{same}")
            (folder / "replay_comparison.tsv").write_text("\n".join(rows) + "\n")
            if mode == "write":
                require(write_observations(folder, s) == 140, "140개 저장 노드·시점 관측 수 차이")
            else:
                require(values["dout_read0"] < Decimal("0.9") and values["dout_read1"] > Decimal("0.9"), "비교용 중간값 기준 DOUT 상태 차이")
        stage("재현 판정")
        require(replay_all, "기존 413 측정과 로그 정밀도 차이: 비교 장부 검토 필요")
        ok = True
    except BaseException as exc:
        error = str(exc)
        (work / "failure.txt").write_text(traceback.format_exc())
    finally:
        for path, expected in protected.items():
            if not path.is_file() or sha(path) != expected:
                ok = False
                error += f"\n기준 원형 변경 감지: {path}"
        verdict = (
            f"통합 재실행={'통과' if ok else '미완료·검토 필요'}\n"
            f"실행 단계={' → '.join(stages)}\n"
            "판정 범위=고정된 53T 후보·도구·PDK·기존 시험의 재현\n"
            "모델=용량 보존 가중 배분 근사\n"
            "시험 재현 기준=기존 413 측정 36개·20개의 로그 정밀도 수치 동일\n"
            "저장 상태 분류=비교용 0.9V 중간값·기존 5개 시점\n"
            "기존 기능 임계값 기반의 전체 재판정=별도 미실행\n"
            "결합 위치·물리 정확도·연속 시간 여유=미검증\n"
            "호스트 외부 의존=Rust/Cargo·Substrate 캐시·생성용 PDK\n"
            "검증·RC 추출·해석 환경=고정 스카이락램 이미지·내부 PDK\n"
            + (f"사유={error}\n" if error else "")
        )
        (work / "verdict.txt").write_text(verdict)
        write_manifest(work)
        print("\n=== 최종 기록 ===\n" + verdict, flush=True)
        print("결과 폴더:", work, flush=True)
        if ok:
            archive = root / (work.name + ".tar.gz")
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(work, arcname=work.name)
            with tarfile.open(archive, "r:gz") as tar:
                for member in tar:
                    if member.isfile():
                        relative = Path(member.name).relative_to(work.name)
                        h = hashlib.sha256()
                        with tar.extractfile(member) as stream:
                            for block in iter(lambda: stream.read(1048576), b""):
                                h.update(block)
                        require(h.hexdigest() == sha(work / relative), "통합 압축본 내부 해시 차이")
            Path(str(archive) + ".sha256").write_text(f"{sha(archive)}  {archive.name}\n")
            print("통합 보존 압축본:", archive, flush=True)
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path("/home/gwen/workspace/COMETRAM_R9")
    task = root / "decoder_vss_m1_candidate_01_4or4fbvy"
    parser.add_argument("--root", type=Path, default=root, help="R9 작업 경로")
    parser.add_argument("--pdk-root", type=Path, default=Path("/home/gwen/workspace/skylarkRAM_R0/pdk"), help="호스트 생성용 PDK 경로")
    parser.add_argument("--read-reference", type=Path, default=task / "read_skylark_413_pair_02_jpf648ae/candidate", help="완료된 413 읽기 대조본")
    parser.add_argument("--write-reference", type=Path, default=task / "write_skylark_413_pair_01_wmtyaw8z/candidate", help="완료된 413 쓰기 대조본")
    args = parser.parse_args()
    try:
        return run_pipeline(args)
    except Exception as exc:
        print(f"통합 시작 실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

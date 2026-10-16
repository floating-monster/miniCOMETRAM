from pathlib import Path
from collections import defaultdict
from decimal import Decimal
import csv
import hashlib
import math
import os
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "rc2_candidate_hv7qNI"
MODEL = ROOT / "rc1_cap_model_candidate_Cywisq/layout_rc_candidate.spice"
IMAGE = "sha256:b12e325417c221e4db0d1ad09e1282edc81bf5e566c004cfef0c9415e4d75371"

def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

require(sha(MODEL) ==
        "ff2bf2feb604a07189dfd6acf93620078e17ba53cd184a621c39aa16eb8b90bf",
        "RC 모델 해시 불일치")

groups = defaultdict(set)
owner = {}
with (MODEL.parent / "weights.tsv").open() as f:
    for row in csv.DictReader(f, delimiter="\t"):
        node, net = row["SPLIT_NODE"], row["ORIGINAL_NET"]
        require(node not in owner, f"장부 중복 노드: {node}")
        owner[node] = net
        groups[net].add(node)

used, graph, ports = set(), defaultdict(set), []
for line in MODEL.read_text().splitlines():
    f = line.split()
    if not f or f[0].startswith("*"):
        continue
    if f[0].lower() == ".subckt":
        ports = f[2:]
    elif f[0][0].lower() == "x":
        used.update(f[1:5])
    elif f[0][0].lower() in ("r", "c"):
        used.update(f[1:3])
        if f[0][0].lower() == "r":
            graph[f[1]].add(f[2])
            graph[f[2]].add(f[1])

vss, pending = {"vss"}, ["vss"]
while pending:
    node = pending.pop()
    for other in graph[node] - vss:
        vss.add(other)
        pending.append(other)

missing = used - set(owner)
require(missing == vss - {"vss"},
        "장부 밖 노드가 예상한 VSS 저항망과 다름")
for node in missing:
    owner[node] = "vss"
    groups["vss"].add(node)
require(set(owner) == used, "전체 노드 대응 불일치")
require(len(used) == 397, "예상한 전체 노드 수와 다름")

work = Path(tempfile.mkdtemp(prefix="all_nodes_01_", dir=ROOT))
shutil.copy2(Path(__file__), work / "executed_script.py")
shutil.copy2(MODEL.parent / "weights.tsv", work / "original_weights.tsv")
(work / "scope.txt").write_text(
    "범위=기존 시점의 전체 노드 관측\n"
    "모델·자극·부하·해석 시간 설정=유지\n"
    "VSS 분할망=저항 연결로 관측 장부에 추가\n"
    "중간전압=0.9V, Q/QB 진단용이며 기존 판정 기준 또는 SNM이 아님\n"
    "전 시간 구간 여유·물리 정확도=미검증\n"
)
(work / "node_mapping.tsv").write_text(
    "원래신호\t회로노드\n" +
    "".join(f"{owner[n]}\t{n}\n" for n in sorted(used))
)

number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
value_pattern = re.compile(
    rf"^\s*(\w+)\s*=\s*({number})(?=\s|$)", re.M
)

def values(text):
    return {
        m[1].lower(): Decimal(m[2])
        for m in value_pattern.finditer(text)
    }

q_nets = {
    "a_20000_6483#", "a_19896_8041#",
    "a_20000_14483#", "a_19896_16041#",
}
status = 1
try:
    midpoint_rows = ["시험\t기존측정\t시점\t신호\t기대상태\t중간전압거리_V"]
    worst_midpoint = []
    reference_same = True

    for mode in ("write", "read"):
        print(f"{mode}: 전체 노드 관측 시작", flush=True)
        folder = work / mode
        folder.mkdir()
        original_path = BASE / mode / "test.spice"
        expected_test = {
            "write": "a106b87a47ef8f9bfba2e71ce9d1114c095028f94703f35d2130fb1a32eee630",
            "read": "eaa17667785c669330f1eeff80905807e3b03883792813e5fcfbfb9e0174d68c",
        }
        require(sha(original_path) == expected_test[mode],
                f"{mode}: 시험 해시 불일치")
        original = original_path.read_text()
        reference = values((BASE / mode / "ngspice.log").read_text())
        existing_names = re.findall(
            r"^\s*\.meas(?:ure)?\s+tran\s+(\w+)",
            original, re.I | re.M
        )
        find_rows = re.findall(
            r"^\s*\.meas(?:ure)?\s+tran\s+(\w+)\s+FIND\s+"
            r"v\(([^)]+)\)\s+AT=(\S+)",
            original, re.I | re.M
        )
        times = sorted({row[2] for row in find_rows},
                       key=lambda s: float(s[:-1]))
        xtop = next(
            line.split() for line in original.splitlines()
            if line.strip().lower().startswith("xtop ")
        )
        require(len(xtop[1:-1]) == len(ports), "상위 포트 연결 수 불일치")
        bindings = dict(zip(ports, xtop[1:-1]))

        requests, extra = [], []
        for time in times:
            for node in sorted(used):
                external = bindings.get(node)
                if external == "0":
                    requests.append((None, time, node))
                    continue
                expression = external if external else "xtop." + node
                name = f"an{len(requests):05d}"
                requests.append((name, time, node))
                extra.append(
                    f".meas tran {name} FIND v({expression}) AT={time}"
                )

        old = "/pdk/sky130A/libs.tech/ngspice/sky130.lib.spice"
        new = "/opt/pdk/sky130A/libs.tech/ngspice/sky130.lib.spice"
        require(original.count(old) == 1, "기존 모델 경로가 예상과 다름")
        require(original.count(".control") == 1, "제어 구간이 예상과 다름")
        test = original.replace(old, new).replace(
            ".control", "\n".join(extra) + "\n.control", 1
        )
        (folder / "test.spice").write_text(test)
        shutil.copy2(original_path, folder / "reference_test.spice")
        shutil.copy2(BASE / mode / "ngspice.log", folder / "reference_ngspice.log")
        shutil.copy2(BASE / mode / ".spiceinit", folder / ".spiceinit")
        shutil.copy2(MODEL, folder / "cell.spice")

        command = [
            "sudo", "docker", "run", "--rm", "--network", "none",
            "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/tmp", "-v", f"{folder}:/sim:rw",
            "-w", "/sim", "--entrypoint",
            "/opt/miniconda3/envs/skyram-193r2/bin/ngspice",
            IMAGE, "-b", "test.spice",
        ]
        (folder / "command.txt").write_text("\n".join(command) + "\n")
        with (folder / "ngspice.log").open("w") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        (folder / "exit_status.txt").write_text(f"NGSPICE_EXIT={result.returncode}\n")
        require(result.returncode == 0, f"{mode}: 해석 실행 실패")
        measured = values((folder / "ngspice.log").read_text(errors="replace"))

        for name in existing_names:
            key = name.lower()
            require(key in reference and key in measured,
                    f"{mode}: 기존 측정 누락: {name}")
            reference_same &= reference[key] == measured[key]

        samples = {}
        rows = ["시점\t신호\t노드\t전압_V\t출처"]
        for name, time, node in requests:
            if name is None:
                voltage = Decimal(0)
                origin = "상위 포트가 이상적 접지 0에 연결"
            else:
                require(name in measured, f"{mode}: 추가 측정 누락: {name}")
                voltage = measured[name]
                origin = "ngspice 측정"
            require(voltage.is_finite(), f"유한하지 않은 전압: {node}")
            samples[time, node] = voltage
            rows.append(f"{time}\t{owner[node]}\t{node}\t{voltage}\t{origin}")
        (folder / "all_nodes.tsv").write_text("\n".join(rows) + "\n")

        summary = ["시점\t신호\t노드수\t최저전압_V\t최고전압_V\t분포폭_V"]
        for time in times:
            for net, nodes in sorted(groups.items()):
                voltages = [samples[time, n] for n in nodes]
                lo, hi = min(voltages), max(voltages)
                summary.append(f"{time}\t{net}\t{len(nodes)}\t{lo}\t{hi}\t{hi-lo}")
        (folder / "net_ranges.tsv").write_text("\n".join(summary) + "\n")

        for name, expression, time in find_rows:
            node = expression.removeprefix("xtop.")
            net = owner.get(node)
            if net not in q_nets:
                continue
            high = reference[name.lower()] > Decimal("0.9")
            voltages = [samples[time, n] for n in groups[net]]
            margin = (min(voltages) - Decimal("0.9") if high
                      else Decimal("0.9") - max(voltages))
            midpoint_rows.append(
                f"{mode}\t{name}\t{time}\t{net}\t"
                f"{'HIGH' if high else 'LOW'}\t{margin}"
            )
            worst_midpoint.append(margin)

        print(f"{mode}: {len(used)}개 노드 × {len(times)}개 시점 관측 완료",
              flush=True)

    (work / "q_midpoint_distance.tsv").write_text("\n".join(midpoint_rows) + "\n")
    verdict = (
        "OBSERVATION=COMPLETE_AT_EXISTING_SAMPLE_TIMES\n"
        "ALL_CIRCUIT_NODES=397\n"
        f"REFERENCE_MEASUREMENTS_IDENTICAL={reference_same}\n"
        f"Q_MIN_MIDPOINT_DISTANCE_V={min(worst_midpoint)}\n"
        "CONTINUOUS_TIME_MARGIN=NOT_VALIDATED\n"
        "ORIGINAL_THRESHOLD_MARGIN=NOT_VALIDATED\n"
        "PHYSICAL_ACCURACY=NOT_VALIDATED\n"
    )
    status = 0 if reference_same else 2
except Exception as error:
    verdict = f"OBSERVATION=INCOMPLETE\n사유={error}\n"
finally:
    (work / "verdict.txt").write_text(verdict)
    (work / "results.sha256").write_text(
        "".join(
            f"{sha(p)}  {p.relative_to(work)}\n"
            for p in sorted(work.rglob("*"))
            if p.is_file() and p.name != "results.sha256"
        )
    )
    print("\n=== 최종 기록 ===")
    print(verdict)
    print(f"결과 폴더: {work}")

raise SystemExit(status)

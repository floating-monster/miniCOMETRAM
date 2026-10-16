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
BASE = ROOT / "rc2_candidate_hv7qNI/read"
AUDIT = ROOT / "all_nodes_01__2nonvzs"
IMAGE = os.environ.get(
    "SKYLARKRAM_IMAGE",
    "sha256:b12e325417c221e4db0d1ad09e1282edc81bf5e566c004cfef0c9415e4d75371",
)

def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

require(sha(BASE / "cell.spice") ==
        "ff2bf2feb604a07189dfd6acf93620078e17ba53cd184a621c39aa16eb8b90bf",
        "RC 모델 해시 불일치")
require(sha(BASE / "test.spice") ==
        "eaa17667785c669330f1eeff80905807e3b03883792813e5fcfbfb9e0174d68c",
        "읽기 시험 해시 불일치")

groups = defaultdict(list)
seen = set()
with (AUDIT / "node_mapping.tsv").open() as f:
    for row in csv.DictReader(f, delimiter="\t"):
        node = row["회로노드"]
        require(node not in seen, f"중복 노드: {node}")
        seen.add(node)
        groups[row["원래신호"]].append(node)
require(len(seen) == 397, "전체 노드 수 불일치")

work = Path(tempfile.mkdtemp(prefix="read_windows_01_", dir=ROOT))
shutil.copy2(Path(__file__), work / "executed_script.py")
shutil.copy2(AUDIT / "node_mapping.tsv", work / "node_mapping.tsv")
shutil.copy2(BASE / "cell.spice", work / "cell.spice")
shutil.copy2(BASE / ".spiceinit", work / ".spiceinit")
shutil.copy2(BASE / "test.spice", work / "reference_test.spice")
shutil.copy2(BASE / "ngspice.log", work / "reference_ngspice.log")

original = (BASE / "test.spice").read_text()
ports = next(
    line.split()[2:] for line in (BASE / "cell.spice").read_text().splitlines()
    if line.lower().startswith(".subckt ")
)
xtop = next(
    line.split() for line in original.splitlines()
    if line.strip().lower().startswith("xtop ")
)
require(len(ports) == len(xtop[1:-1]), "포트 수 불일치")
bindings = dict(zip(ports, xtop[1:-1]))

exports = []
mapping = ["파일\t신호\t열번호\t노드"]
file_specs = []
for index, (net, nodes) in enumerate(sorted(groups.items())):
    nodes = sorted(n for n in nodes if bindings.get(n) != "0")
    require(nodes, f"관측할 노드가 없는 신호: {net}")
    filename = f"net_{index:02d}.tsv"
    expressions = [
        f"v({bindings[n] if n in bindings else 'xtop.' + n})"
        for n in nodes
    ]
    exports.append(f"wrdata {filename} " + " ".join(expressions))
    file_specs.append((filename, net, nodes))
    for column, node in enumerate(nodes, 1):
        mapping.append(f"{filename}\t{net}\t{column}\t{node}")

(work / "wave_columns.tsv").write_text("\n".join(mapping) + "\n")
old_model = "/pdk/sky130A/libs.tech/ngspice/sky130.lib.spice"
require(original.count(old_model) == 1, "모델 경로 구문 불일치")
require(len(re.findall(r"(?m)^\s*quit\s*$", original)) == 1,
        "기존 종료 구문 불일치")
test = original.replace(
    old_model, "/opt/pdk/sky130A/libs.tech/ngspice/sky130.lib.spice"
)
# 기존 해석과 파형 저장 이후 추가 관측 파일만 출력합니다.
test = re.sub(
    r"(?m)^\s*quit\s*$",
    "\n".join(exports) + "\nquit",
    test,
)
(work / "test.spice").write_text(test)
(work / "scope.txt").write_text(
    f"이미지={IMAGE}\n"
    "모델·자극·부하·tran 설정=기존 RC2 유지\n"
    "관측=전체 노드 파형 추가 저장\n"
    "이상적 접지 포트=0V로 고정, 파형 출력에서 제외\n"
    "구간 결과=저장된 과도해석 표본 기준\n"
    "허용오차·안정 판정 기준=추가하지 않음\n"
    "물리 정확도=미검증\n"
)

windows = [
    ("읽기0_전체", 26, 30),
    ("읽기0_직전", 28, 29),
    ("읽기0_직후", 29, 29.5),
    ("읽기1_전체", 40, 44),
    ("읽기1_직전", 42, 43),
    ("읽기1_직후", 43, 43.5),
]
command = [
    "sudo", "docker", "run", "--rm", "--network", "none",
    "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev",
    "--user", f"{os.getuid()}:{os.getgid()}",
    "-e", "HOME=/tmp", "-v", f"{work}:/sim:rw", "-w", "/sim",
    "--entrypoint", "/opt/miniconda3/envs/skyram-193r2/bin/ngspice",
    IMAGE, "-b", "test.spice",
]
(work / "command.txt").write_text("\n".join(command) + "\n")

status = 1
try:
    print(f"결과 폴더: {work}", flush=True)
    print("읽기 시간 구간 관측 시작 — 실행 중 출력은 로그에 기록합니다.",
          flush=True)
    with (work / "ngspice.log").open("w") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    (work / "exit_status.txt").write_text(f"NGSPICE_EXIT={result.returncode}\n")
    require(result.returncode == 0, "ngspice 실행 실패")

    pattern = re.compile(
        r"^\s*(\w+)\s*=\s*"
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?=\s|$)",
        re.M,
    )
    def measurements(path):
        return {m[1].lower(): Decimal(m[2])
                for m in pattern.finditer(path.read_text(errors="replace"))}

    before = measurements(work / "reference_ngspice.log")
    after = measurements(work / "ngspice.log")
    names = re.findall(
        r"^\s*\.meas(?:ure)?\s+tran\s+(\w+)", original, re.I | re.M
    )
    require(len(names) == 36, "既存 측정 수 불일치".replace("既存", "기존"))
    require(all(n.lower() in before and n.lower() in after for n in names),
            "기존 측정 누락")
    identical = all(before[n.lower()] == after[n.lower()] for n in names)

    rows = [
        "구간\t신호\t표본수\t최저전압_V\t최저시점_ns\t최저노드\t"
        "최고전압_V\t최고시점_ns\t최고노드\t최대노드편차_V\t편차시점_ns"
    ]
    focus = {"vdd", "vss", "dout", "a_9600_n35917#", "a_9600_n36000#"}

    for filename, net, nodes in file_specs:
        stats = [
            {"count": 0, "lo": math.inf, "hi": -math.inf, "spread": -1}
            for _ in windows
        ]
        with (work / filename).open() as f:
            next(f)
            last_time = -math.inf
            for line in f:
                fields = line.split()
                require(len(fields) == len(nodes) + 1,
                        f"파형 열 수 불일치: {filename}")
                data = list(map(float, fields))
                require(all(math.isfinite(v) for v in data),
                        f"유한하지 않은 파형값: {filename}")
                time = data[0] * 1e9
                require(time >= last_time, f"파형 시간 역전: {filename}")
                last_time = time
                voltages = data[1:]
                # 이상적 접지 기준 포트도 VSS 분포에 포함합니다.
                sample_nodes = nodes
                if net == "vss":
                    voltages = voltages + [0.0]
                    sample_nodes = nodes + ["vss"]
                lo, hi = min(voltages), max(voltages)
                for index, (_, start, end) in enumerate(windows):
                    if not start <= time <= end:
                        continue
                    s = stats[index]
                    s["count"] += 1
                    if lo < s["lo"]:
                        s.update(lo=lo, lot=time,
                                 lon=sample_nodes[voltages.index(lo)])
                    if hi > s["hi"]:
                        s.update(hi=hi, hit=time,
                                 hin=sample_nodes[voltages.index(hi)])
                    if hi-lo > s["spread"]:
                        s.update(spread=hi-lo, st=time)

        for (label, _, _), s in zip(windows, stats):
            require(s["count"] >= 2, f"관측 구간 표본 부족: {filename} {label}")
            rows.append(
                f'{label}\t{net}\t{s["count"]}\t{s["lo"]:.10g}\t'
                f'{s["lot"]:.10g}\t{s["lon"]}\t{s["hi"]:.10g}\t'
                f'{s["hit"]:.10g}\t{s["hin"]}\t{s["spread"]:.10g}\t{s["st"]:.10g}'
            )
            if net in focus and not label.endswith("전체"):
                print(
                    f'{label} {net}: '
                    f'{s["lo"]:.7g}～{s["hi"]:.7g} V / '
                    f'최대 노드편차 {s["spread"]:.7g} V',
                    flush=True,
                )

    (work / "window_summary.tsv").write_text("\n".join(rows) + "\n")
    verdict = (
        "OBSERVATION=COMPLETE_AT_SAVED_TRANSIENT_SAMPLES\n"
        f"REFERENCE_36_MEASUREMENTS_IDENTICAL={identical}\n"
        "STABILITY=REVIEW_REQUIRED\n"
        "PHYSICAL_ACCURACY=NOT_VALIDATED\n"
    )
    status = 0 if identical else 2
except Exception as error:
    verdict = f"OBSERVATION=INCOMPLETE\n사유={error}\n"
finally:
    (work / "verdict.txt").write_text(verdict)
    with (work / "results.sha256").open("w") as f:
        for path in sorted(work.rglob("*")):
            if path.is_file() and path.name != "results.sha256":
                f.write(f"{sha(path)}  {path.relative_to(work)}\n")
    print("\n=== 최종 기록 ===")
    print(verdict)
    print(f"결과 폴더: {work}")

raise SystemExit(status)

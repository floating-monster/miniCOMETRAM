from pathlib import Path
from decimal import Decimal
import shlex

sim = Path("comet_ram_2x1.sim")
original = sim.read_text()
Path("input_original.sim").write_text(original)

ground = {}
coupling = []
for line in Path("comet_ram_2x1.ext").read_text().splitlines():
    f = shlex.split(line)
    if not f:
        continue
    if f[0] == "scale":
        if f[2] != "1":
            raise SystemExit("STOP: 예상과 다른 C scale")
    elif f[0] in ("use", "equiv", "subcap"):
        raise SystemExit(f"STOP: 추가 해석이 필요한 EXT 레코드: {f[0]}")
    elif f[0] == "node":
        name = f[1]
        if name in ground:
            raise SystemExit(f"STOP: 중복 노드: {name}")
        ground[name] = Decimal(f[3]) / 1000
    elif f[0] == "cap":
        coupling.append((f[1], f[2], Decimal(f[3]) / 1000))

# C 이외의 모든 행은 순서와 내용을 유지합니다.
kept = [
    line for line in original.splitlines()
    if not (line.split() and line.split()[0] == "C")
]
caps = []
for a, b, value in coupling:
    if a not in ground or b not in ground:
        raise SystemExit(f"STOP: 미등록 결합 노드: {a}, {b}")
    if value < 0:
        raise SystemExit("STOP: 음수 결합 C")
    if value:
        caps.append(f"C {a} {b} {value:f}")
for name, value in ground.items():
    if value < 0:
        raise SystemExit("STOP: 음수 접지 C")
    if value:
        caps.append(f"C {name} 0 {value:f}")

sim.write_text("\n".join(kept + caps) + "\n")
after_non_c = [
    line for line in sim.read_text().splitlines()
    if not (line.split() and line.split()[0] == "C")
]
assert kept == after_non_c

report = [
    "SOURCE=FLAT_EXT",
    "EXT_CAP_UNIT=aF",
    "SIM_CAP_UNIT=fF",
    "NON_C_RECORDS=UNCHANGED",
    f"PRECISE_C_RECORDS={len(caps)}",
    f"GROUND_SUM_FF={sum(ground.values()):f}",
    f"COUPLING_SUM_FF={sum(v for _, _, v in coupling):f}",
]
Path("precision_report.txt").write_text("\n".join(report) + "\n")
print("\n".join(report))

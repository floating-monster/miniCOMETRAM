from pathlib import Path
import hashlib
import os
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "sha256:b12e325417c221e4db0d1ad09e1282edc81bf5e566c004cfef0c9415e4d75371"

EXPECTED = {
    "comet_ram_2x1.gds":
        "dc81603a01f7eca84fdeded61979503e1324b70d307f3c63e5b86476d3bd535c",
    "comet_ram_2x1.spice":
        "8197748b08f8f68b905a59e132c8d66f65b18b3a1976dbd8643b3f5e128aef63",
}

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

# 기준 파일을 확인한 뒤 이번 실행의 입력 사본을 만듭니다.
for name, expected in EXPECTED.items():
    path = ROOT / "output" / name
    require(path.is_file(), f"입력 파일 없음: {path}")
    require(digest(path) == expected, f"기준 해시 불일치: {path}")

work = Path(tempfile.mkdtemp(prefix="verify_skyram_01_", dir=ROOT))
inputs = work / "inputs"
inputs.mkdir()
for name, expected in EXPECTED.items():
    path = inputs / name
    shutil.copy2(ROOT / "output" / name, path)
    require(digest(path) == expected, f"입력 사본 해시 불일치: {path}")
shutil.copy2(Path(__file__), work / "executed_script.py")

(work / "environment.txt").write_text(
    f"이미지={IMAGE}\n"
    "PDK=/opt/pdk/sky130A\n"
    "범위=기존 53T GDS의 DRC·LVS 재검증\n"
    "기능 시험 및 RC 정확도 검증=이번 실행 범위 밖\n"
)

(work / "check.tcl").write_text("""
gds read /input/comet_ram_2x1.gds
load comet_ram_2x1
select top cell
drc style drc(full)
drc check
drc catchup
set count [drc list count total]
set details [drc listall why]
set f [open /verify/drc_report.txt w]
puts $f "DRC_COUNT=$count"
puts $f "DRC_DETAILS=$details"
close $f
puts "DRC_COUNT=$count"
extract all
feedback save /verify/extraction_feedback.txt
ext2spice lvs
ext2spice -o /verify/layout_raw.spice
puts "FULL_CHECK_SCRIPT_COMPLETED"
quit -noprompt
""".lstrip())

docker = [
    "sudo", "docker", "run", "--rm", "--network", "none",
    "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev",
    "--user", f"{os.getuid()}:{os.getgid()}",
    "-e", "HOME=/tmp",
    "-v", f"{inputs}:/input:ro",
    "-v", f"{work}:/verify:rw",
    "-w", "/verify", "--entrypoint", "/bin/sh", IMAGE,
]

def run(label, command):
    print(f"{label} 시작", flush=True)
    (work / f"{label}_command.txt").write_text(
        "\n".join(docker + ["-c", command]) + "\n"
    )
    with (work / f"{label}.log").open("w") as log:
        result = subprocess.run(
            docker + ["-c", command],
            stdout=log, stderr=subprocess.STDOUT,
        )
    (work / f"{label}_exit.txt").write_text(
        f"종료코드={result.returncode}\n"
    )
    require(result.returncode == 0,
            f"{label} 실행 실패: 종료코드 {result.returncode}")
    print(f"{label} 실행 종료", flush=True)

passed = False
try:
    run("pdk", """
set -eu
readlink -f /opt/pdk/sky130A
sha256sum \
 /opt/pdk/sky130A/libs.tech/magic/sky130A.magicrc \
 /opt/pdk/sky130A/libs.tech/magic/sky130A.tech \
 /opt/pdk/sky130A/libs.tech/netgen/setup.tcl
""")

    run("magic", """
exec magic -dnull -noconsole \
 -rcfile /opt/pdk/sky130A/libs.tech/magic/sky130A.magicrc \
 /verify/check.tcl
""")
    log = (work / "magic.log").read_text(errors="replace")
    require("FULL_CHECK_SCRIPT_COMPLETED" in log,
            "Magic 완료 표식 없음")
    report = (work / "drc_report.txt").read_text()
    require(re.search(r"^DRC_COUNT=0\s*$", report, re.M),
            "DRC 0 조건 미충족")
    require((work / "layout_raw.spice").is_file(),
            "추출 회로 파일 없음")

    run("netgen", """
exec netgen -batch lvs \
 "/input/comet_ram_2x1.spice comet_ram_2x1" \
 "/verify/layout_raw.spice comet_ram_2x1" \
 /opt/pdk/sky130A/libs.tech/netgen/setup.tcl \
 /verify/lvs_report.txt
""")
    report = (work / "lvs_report.txt").read_text(errors="replace")
    require("Final result: Circuits match uniquely." in report,
            "LVS 최종 고유 일치 조건 미충족")
    require(re.search(
        r"Number of devices:\s*53\s*\|Number of devices:\s*53\b",
        report,
    ), "LVS 양쪽 53소자 조건 미확인")

    passed = True
    verdict = (
        "DRC=PASS_ZERO_ERRORS\n"
        "LVS=PASS_UNIQUE_MATCH\n"
        "DEVICE_COUNT=53_BOTH_SIDES\n"
        "OVERALL=PASS\n"
    )
except Exception as error:
    verdict = f"OVERALL=FAIL\n사유={error}\n"
finally:
    (work / "verdict.txt").write_text(verdict)
    records = []
    for path in sorted(work.rglob("*")):
        if path.is_file() and path.name != "results.sha256":
            records.append(f"{digest(path)}  {path.relative_to(work)}")
    (work / "results.sha256").write_text("\n".join(records) + "\n")
    print("\n=== 최종 판정 ===")
    print(verdict)
    print(f"결과 폴더: {work}")
    print(f"해시 장부: {work / 'results.sha256'}")

raise SystemExit(0 if passed else 1)

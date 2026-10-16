from pathlib import Path
from decimal import Decimal
import hashlib
import os
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "rc2_candidate_hv7qNI"
MODEL = ROOT / "rc1_cap_model_candidate_Cywisq/layout_rc_candidate.spice"
VERIFY = ROOT / "verify_skyram_01_8xez6s4_"
IMAGE = os.environ.get(
    "SKYLARKRAM_IMAGE",
    "sha256:b12e325417c221e4db0d1ad09e1282edc81bf5e566c004cfef0c9415e4d75371",
)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

require("OVERALL=PASS" in (VERIFY / "verdict.txt").read_text(),
        "물리 검증 PASS 기록 없음")

expected = {
    "write": "a106b87a47ef8f9bfba2e71ce9d1114c095028f94703f35d2130fb1a32eee630",
    "read": "eaa17667785c669330f1eeff80905807e3b03883792813e5fcfbfb9e0174d68c",
}
for mode, value in expected.items():
    require(sha(BASE / mode / "test.spice") == value,
            f"{mode}: 기존 시험 해시 불일치")

work = Path(tempfile.mkdtemp(prefix="rc2_skyram_01_", dir=ROOT))
shutil.copy2(Path(__file__), work / "executed_script.py")
shutil.copy2(BASE / "model_scope.txt", work / "model_scope.txt")
shutil.copy2(BASE / "node_mapping.txt", work / "node_mapping.txt")
shutil.copy2(MODEL.parent / "verdict.txt", work / "model_verdict.txt")
(work / "environment.txt").write_text(
    f"이미지={IMAGE}\n"
    f"RC 후보={MODEL}\n"
    "PDK=/opt/pdk/sky130A\n"
    "비교 기준=기존 RC2 로그의 모든 측정값과 출력 정밀도에서 수치 동일\n"
    "기존 임계값 판정 코드=미확보\n"
    "모델=CAPACITANCE_PRESERVING_WEIGHTED_APPROXIMATION\n관측=기존 대표 분할 단자\n공간 배분·타이밍 정확도·전체 분할 노드 여유=미검증\n"
)

def measures(text, names):
    values = {}
    pattern = re.compile(
        r"^\s*([A-Za-z_]\w*)\s*=\s*"
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
        r"(?=\s|$)", re.M
    )
    for match in pattern.finditer(text):
        name, value = match.groups()
        if name in names:
            require(name not in values, f"중복 측정 결과: {name}")
            values[name] = Decimal(value)
    require(set(values) == set(names),
            f"측정 결과 누락: {sorted(set(names) - set(values))}")
    require(all(v.is_finite() for v in values.values()),
            "유한하지 않은 측정값")
    return values

status = 1
try:
    all_same = True
    for mode in ("write", "read"):
        print(f"{mode} 시험 시작", flush=True)
        folder = work / mode
        folder.mkdir()

        original = (BASE / mode / "test.spice").read_text()
        old_model = "/pdk/sky130A/libs.tech/ngspice/sky130.lib.spice"
        new_model = "/opt/pdk/sky130A/libs.tech/ngspice/sky130.lib.spice"
        require(original.count(old_model) == 1,
                f"{mode}: 예상한 모델 경로가 아님")
        test = original.replace(old_model, new_model)

        (folder / "test.spice").write_text(test)
        shutil.copy2(BASE / mode / "test.spice",
                     folder / "reference_test.spice")
        shutil.copy2(BASE / mode / "ngspice.log",
                     folder / "reference_ngspice.log")
        shutil.copy2(BASE / mode / ".spiceinit", folder / ".spiceinit")
        shutil.copy2(MODEL, folder / "cell.spice")

        names = re.findall(
            r"^\s*\.meas(?:ure)?\s+tran\s+(\w+)",
            test, re.I | re.M
        )
        require(names and len(names) == len(set(names)),
                f"{mode}: 측정 선언 없음 또는 중복")

        command = [
            "sudo", "docker", "run", "--rm", "--network", "none",
            "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/tmp",
            "-v", f"{folder}:/sim:rw", "-w", "/sim",
            "--entrypoint",
            "/opt/miniconda3/envs/skyram-193r2/bin/ngspice",
            IMAGE, "-b", "test.spice",
        ]
        (folder / "command.txt").write_text("\n".join(command) + "\n")
        with (folder / "ngspice.log").open("w") as log:
            result = subprocess.run(
                command, stdout=log, stderr=subprocess.STDOUT
            )
        (folder / "exit_status.txt").write_text(
            f"NGSPICE_EXIT={result.returncode}\n"
        )
        require(result.returncode == 0, f"{mode}: ngspice 실행 실패")
        require((folder / "waveform.tsv").is_file(),
                f"{mode}: 파형 파일 없음")

        reference = measures(
            (folder / "reference_ngspice.log").read_text(), names
        )
        actual = measures(
            (folder / "ngspice.log").read_text(errors="replace"), names
        )
        rows = ["측정명\t기존값\t새값\t동일여부"]
        for name in names:
            same = reference[name] == actual[name]
            all_same = all_same and same
            rows.append(
                f"{name}\t{reference[name]}\t{actual[name]}\t{same}"
            )
        (folder / "measurement_comparison.tsv").write_text(
            "\n".join(rows) + "\n"
        )
        differences = sum(reference[n] != actual[n] for n in names)
        print(f"{mode}: 측정 {len(names)}개, 차이 {differences}개",
              flush=True)

    if all_same:
        verdict = (
            "SIMULATION=PASS\n"
            "ALL_MEASUREMENTS=IDENTICAL_TO_R8_RC2_AT_LOG_PRECISION\n"
            "RC2_REPLAY=PASS_BY_REFERENCE_MEASUREMENT_IDENTITY\n"
            "PHYSICAL_ACCURACY=NOT_VALIDATED\n"
        )
        status = 0
    else:
        verdict = (
            "SIMULATION=PASS\n"
            "MEASUREMENT_COMPARISON=DIFFERENT\n"
            "RC2_REPLAY=REVIEW_REQUIRED\n"
            "사유=기존 임계값 판정식을 확보하거나 측정 차이를 검토해야 함\n"
            "PHYSICAL_ACCURACY=NOT_VALIDATED\n"
        )
        status = 2
except Exception as error:
    verdict = f"SIMULATION=INCOMPLETE\n사유={error}\n"
finally:
    (work / "verdict.txt").write_text(verdict)
    rows = [
        f"{sha(p)}  {p.relative_to(work)}"
        for p in sorted(work.rglob("*"))
        if p.is_file() and p.name != "results.sha256"
    ]
    (work / "results.sha256").write_text("\n".join(rows) + "\n")
    print("\n=== 최종 기록 ===")
    print(verdict)
    print(f"결과 폴더: {work}")

raise SystemExit(status)

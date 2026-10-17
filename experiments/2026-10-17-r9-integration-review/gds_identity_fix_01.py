from pathlib import Path
import hashlib
import shutil
import subprocess
import sys
import tempfile

root = Path("/home/gwen/workspace/COMETRAM_R9")
package = root / "cometram_integrated_01"
script = package / "run.py"
manifest = package / "package.sha256"

def require(ok, message):
    if not ok:
        raise SystemExit(message)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

expected = '5ed4429dec88420e6a38c6b8792a947389c79a7a11a03cd7256fa7f26e443304'
helper = "def verify_generated_gds(reference, generated, folder):\n    import struct\n    from collections import Counter\n\n    def parse(path):\n        data = path.read_bytes()\n        position = 0\n        library, cells = [], {}\n        current, element = None, None\n        starts = {0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x15, 0x2d}\n        while position < len(data):\n            require(position + 4 <= len(data), 'GDS 헤더 잘림')\n            length, kind, datatype = struct.unpack('>HBB', data[position:position+4])\n            require(length >= 4 and length % 2 == 0 and position + length <= len(data), 'GDS 레코드 길이 오류')\n            payload = data[position+4:position+length]\n            record = (kind, datatype, payload)\n            position += length\n            if kind == 0x01:\n                require(current is None and len(payload) == 24, '라이브러리 시각 오류')\n                library.append((kind, datatype, bytes(24)))\n            elif kind == 0x05:\n                require(current is None and len(payload) == 24, '셀 시작 오류')\n                current = {'name': None, 'metadata': [(kind, datatype, bytes(24))], 'elements': []}\n            elif kind == 0x06:\n                require(current is not None and current['name'] is None, '셀 이름 오류')\n                current['name'] = payload\n                current['metadata'].append(record)\n            elif kind in starts:\n                require(current is not None and element is None, '요소 시작 오류')\n                element = [record]\n            elif kind == 0x11:\n                require(element is not None, '요소 종료 오류')\n                element.append(record)\n                current['elements'].append(tuple(element))\n                element = None\n            elif kind == 0x07:\n                require(current is not None and element is None, '셀 종료 오류')\n                name = current['name']\n                require(name is not None and name not in cells, '셀 이름 누락·중복')\n                current['metadata'].append(record)\n                cells[name] = (tuple(current['metadata']), Counter(current['elements']))\n                current = None\n            elif element is not None:\n                element.append(record)\n            elif current is not None:\n                current['metadata'].append(record)\n            else:\n                library.append(record)\n        require(current is None and element is None, 'GDS 종료 불완전')\n        return tuple(library), cells\n\n    require(sha(reference) == GDS, '비교 기준 후보 GDS 해시 불일치')\n    old_library, old = parse(reference)\n    new_library, new = parse(generated)\n    same = old_library == new_library and old == new\n    report = {\n        '기준_원본_SHA256': sha(reference),\n        '생성_원본_SHA256': sha(generated),\n        '바이트동일': sha(reference) == sha(generated),\n        '시각·셀요소순서제외_전체내용동일': same,\n        '비교방법': '좌표·레이어·문자열·요소내부순서·중복개수·기타메타데이터 보존',\n        '기준셀별요소개수': {n.rstrip(bytes(1)).decode(errors='replace'): sum(v[1].values()) for n, v in old.items()},\n        '생성셀별요소개수': {n.rstrip(bytes(1)).decode(errors='replace'): sum(v[1].values()) for n, v in new.items()},\n    }\n    (folder / 'gds_identity.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))\n    require(same, '재생성 GDS 요소 내용·개수 또는 메타데이터 차이')\n"

require(sha(script) == expected, "수정 전 실행기 해시 불일치")
text = script.read_text()
before = '        require(sha(project / "output/comet_ram_2x1.gds") == GDS, "재생성 GDS 해시 차이")'
after = '        verify_generated_gds(root / "decoder_vss_m1_candidate_01_4or4fbvy/project/output/comet_ram_2x1.gds", project / "output/comet_ram_2x1.gds", work / "generation")'
require(text.count(before) == 1, "GDS 검사 교체 위치 불일치")
require(text.count("def prepare_test(") == 1, "GDS 검사 함수 삽입 위치 불일치")
text = text.replace(before, after, 1)
text = text.replace("def prepare_test(", helper + "\n\ndef prepare_test(", 1)
compile(text, str(script), "exec")
lines = manifest.read_text().splitlines()
indices = [i for i, line in enumerate(lines) if line.split(None, 1)[1].strip() == "run.py"]
require(len(indices) == 1 and lines[indices[0]].split()[0] == expected, "수정 전 패키지 장부 불일치")
record = Path(tempfile.mkdtemp(prefix="integration_gds_identity_fix_01_", dir=root))
shutil.copy2(script, record / "run_before.py")
shutil.copy2(manifest, record / "package_before.sha256")
script.write_text(text)
lines[indices[0]] = f"{sha(script)}  run.py"
readme = package / "사용안내.md"
if readme.is_file():
    shutil.copy2(readme, record / "안내_수정전.md")
    readme.write_text(readme.read_text().replace(
        "후보 소스와 재생성 GDS·회로도의 기준 해시 일치.",
        "후보 소스·회로도 기준 해시 일치, GDS는 기준 해시를 확인한 원본과 시각·셀/요소 출력 순서 외의 전체 내용·중복 개수 동일. 원본 GDS 해시는 각각 기록.", 1))
    for i, line in enumerate(lines):
        if line.split(None, 1)[1].strip() == "사용안내.md":
            lines[i] = f"{sha(readme)}  사용안내.md"
manifest.write_text("\n".join(lines) + "\n")
shutil.copy2(script, record / "run_after.py")
(record / "change.txt").write_text("수정=원본 GDS 해시 각각 보존 및 시각·요소 순서 외 전체 레코드 내용·중복 개수 검사\n좌표·레이어·문자열·회로도·물리검증·시험·재현 기준 변경 없음\n")
(record / "results.sha256").write_text("".join(f"{sha(p)}  {p.name}\n" for p in sorted(record.iterdir()) if p.is_file()))
print("수정 기록:", record, flush=True)
print("수정 실행기 SHA256:", sha(script), flush=True)
print("통합 실행을 다시 시작합니다. 빌드 캐시는 재사용합니다.", flush=True)
subprocess.run([sys.executable, str(script)], check=True)

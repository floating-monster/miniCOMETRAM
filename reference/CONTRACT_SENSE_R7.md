# COMETRAM R7 센스앰프 계약

## 구현
- 코드: src/main.rs의 CometSenseAmp.
- 생성 회로: output/comet_sense_amp.spice.
- 현재 생성 파라미터: Inv::dec_params().
- 총 12T: 차동·미러·꼬리·초기화 MOS 6개와 인버터 3개.
- 입력 NMOS: W=1.6µm, L=0.15µm.
- 미러·초기화 PMOS: W=2.4µm, L=0.15µm.
- 꼬리 NMOS: W=0.42µm, L=0.6µm.
- 인버터: PMOS W=2.4µm, NMOS W=1.6µm, L=0.15µm.
- NMOS 바디는 VSS, PMOS 바디는 VDD에 연결한다.

## 인터페이스
- 단자 선언 순서: vdd vss bl br sa_en dout.
- sa_en=1: BL>BR이면 dout=1, BL<BR이면 dout=0.
- sa_en=0: dout을 0으로 초기화한다. 이전 읽기값을 보존하지 않는다.
- 동작 중 정적 전류가 흐르는 차동형 구조다.
- 입력 차이 형성 후 sa_en을 활성화하고, 안정화 후 출력을 사용한다.

## 확인한 범위
- TT, VDD=1.8V, 온도 27°C, 출력 부하 10fF.
- 공통 입력 전압 1.65/1.70/1.75V와 차이 50/100mV의
  6개 이산 조건에서 양방향 판별·초기화 통과.
- 기준: low=-0.18~0.18V, high=1.62~1.98V.
- sim_05_sense_output_restore_02: 단독 범위 시험.
- sim_06_array_sense: 기존 41T 회로와 연결한 53T 읽기 시험 통과.
- 배열 연결 시험은 읽기 후반의 큰 BL/BR 차이에서 수행했다.
- verify_sense_contract_01: Rust 생성 회로와 시험 통과 회로의
  Netgen 연결·치수 대조 통과. 물리 레이아웃 LVS는 아니다.

## 남은 검증
- 센스앰프 레이아웃, DRC·LVS, 추출 회로 재시험.
- 전체 통합 GDS와 배선 RC.
- 조기 감지 지연, 연속 입력 범위, 공정·전압·온도 변화와 소자 불일치.
- 동일 입력에서의 출력값과 최소 검출 차이는 보증하지 않는다.


## 최소 폭 수정 기록
- 기존 W=0.40µm는 Full DRC의 최소 폭 0.42µm 규칙에 미달했다.
- W=0.42µm, L=0.6µm로 수정한다.
- sim_07_sense_tail042: 단독 6조건과 배열 읽기·데이터 유지 시험 통과.
- 앞서 기록한 0.40µm 시험은 이전 설계의 근거로 보존한다.
- 0.42µm GDS의 DRC·LVS와 추출 기능 재시험 결과는 별도로 확인한다.

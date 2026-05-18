# [보고서] 데이터 특성별 Parquet 엔코더 및 OLAP 쿼리 엔진 성능 전환점(Tipping Point) 분석 PoC

## 1. 배경 및 연구 목적 (Context & Motivation)

본 연구는 대규모 조회가 발생하며 쿼리 조건이 수시로 변하는 **Read-Heavy 대시보드 서비스**를 설계하는 과정에서 시작되었다. 기존 인프라(Google BigQuery)에서 비용 통제 및 벤치마크 테스트를 목적으로 **S3(데이터 레이크) + DuckDB(임베디드 OLAP 엔진)** 조합의 아키텍처를 검토하였다.
이 과정에서 발생할 수 있는 S3 API 호출 비용 폭탄 및 네트워크 지연(Latency) 문제를 제어하기 위해, **로컬 파일 포맷(Parquet) 최적화와 직렬화/역직렬화 엔진의 내부 메커니즘을 정밀하게 프로파일링하여 아키텍처적 타협점(Tipping Point)을 도출**하는 것을 목적으로 한다.

---

## 2. 핵심 탐구 주제 및 가설 (Core Hypotheses)

1. **실행 엔진 및 스토리지 최적화:** Pandas 대비 DuckDB는 열 기반 스캔(Column Pruning) 및 조건절 푸시다운(Predicate Pushdown)을 활용하여 대규모 데이터 조회 시 메모리(Peak Memory)와 I/O 속도에서 압도적 우위를 가질 것이다.
2. **Parquet 직렬화 엔진 비교 (`pyarrow` vs `fastparquet`):**
* `pyarrow`는 C++ 백엔드 기반의 Apache Arrow 표준 엔진으로 대규모 멀티스레딩 및 문자열 처리에 강할 것이다.
* `fastparquet`은 Python 및 Numba JIT 컴파일러 기반으로 작동하므로 특정 조건에서 가벼운 오버헤드를 가질 것이다.


3. **데이터 프로필(체급)에 따른 반전:** 데이터의 크기가 작고 순수 수치형인 환경과, 데이터가 크고 지저분한 문자열(AWS CUR 데이터 스타일)이 혼합된 환경 간에는 두 엔진의 성능 판도가 뒤집히는 '전환점(Tipping Point)'이 존재할 것이다.

---

## 3. 벤치마킹 방법론의 진화 과정 (Methodology Evolution)

* **1단계: 이론적 시뮬레이션**
* 포맷별(Avro, Arrow, Parquet) 압축률과 엔진별(Pandas, DuckDB) OOM 위험도를 수학적 모델로 시뮬레이션 화면 구현.


* **2단계: UV 패키지 매니저 기반 로컬 테스트베드 구축**
* Colab의 가상화 I/O 노이즈를 배제하기 위해, 물리 디스크/메모리 제어가 가능한 로컬 환경(`uv` 패키지 매니저)에 프로젝트 세팅 및 GitHub 연동.
* `kagglehub`를 활용하여 실제 신용카드 금융 데이터셋(Kaggle Credit Card Fraud, 약 67MB CSV)을 활용한 로컬 캐싱 파이프라인 구축.


* **3단계: 가혹 조건(Heavy Scenario) 데이터 합성**
* 순수 숫자 데이터셋의 한계를 극복하기 위해, **포인터 참조 복사 연산(`[df] * 10`)과 `pd.concat(ignore_index=True)**` 조합의 고성능 메모리 최적화 기법을 사용하여 데이터를 **10배(약 285만 행)** 뻥튀기함.
* AWS CUR(비용 가시성 데이터)의 병목 특성을 모사하기 위해 고의로 **가변 길이 문자열(String) 컬럼 5개**를 강제 주입하여 스트레스 테스트 환경 구축.


* **4단계: 시각화 리포트 고도화 (2026년 표준 스택)**
* **Matplotlib + Seaborn** 조합의 최신 `bar_label` 문법을 활용한 인쇄용 정적 차트 생성.
* **Plotly Express**의 Y축 독립 스케일링(`matches=None`) 및 최신 `textfont_weight='bold'` 규격을 적용하여 인터랙티브 웹 대시보드 리포트 생성 프로세스 정립.



---

## 4. 핵심 실증 결과 및 발견 (Key Empirical Findings)

실제 로컬 환경에서 두 가지 시나리오(Light vs Heavy)를 교차 실행한 결과, 예상을 뛰어넘는 명확한 성능 역전(Crossover) 현상이 관측되었다.

### 시나리오 1: Light (저용량, 100% 순수 수치형 데이터)

* **결과:** `fastparquet`이 쓰기 속도, 파일 용량, DuckDB 읽기 속도 모두에서 `pyarrow`를 완전히 압도함.
* **원인 분석:** Kaggle 원본 데이터는 문자열이 없는 고밀도 수치형(`float64`, `int64`) 배열이다. 이 경우 `fastparquet` 내부의 **Numba JIT**가 시스템 기계어로 바로 컴파일하여 NumPy 배열을 다이렉트로 디스크에 밀어 넣으므로 오버헤드가 극도로 낮다. 반면 `pyarrow`는 PB급 분산 처리를 고려해 파일 내부 페이지마다 촘촘한 통계 메타데이터(Min/Max, Null count 등)를 심기 때문에 배보다 배꼽(메타데이터 블로트)이 더 커져 용량도 늘어나고 DuckDB가 메타데이터를 파싱하는 시간도 더 소모되었다.

### 시나리오 2: Heavy (대용량, 가변 문자열 컬럼 혼합)

* **결과:** **`pyarrow`가 쓰기 속도, 파일 압축률, DuckDB 읽기 속도 전반에서 `fastparquet`을 역전하고 완승함.**
* **원인 분석:** 파이썬 생태계가 가장 취약한 '가변 길이 문자열 데이터'가 대량으로 유입되자, `fastparquet`은 파이썬 가비지 컬렉터(GC)와 인코딩 병목으로 인해 급격히 느려졌다. 반면 `pyarrow`는 고성능 **순수 C++ 엔진**과 사전식 인코딩(Dictionary Encoding) 메타데이터를 활용해 문자열을 정교하게 압축했다. 데이터 체급이 수백 MB~GB 단위로 커지자, DuckDB 역시 `pyarrow`가 심어놓은 촘촘한 메타데이터(Row Group 스킵) 덕분에 필요한 조각만 골라 읽으며 `fastparquet` 대비 2배 이상의 조회 속도 방어력을 보여주었다.

---

## 5. 최종 확정 소스 코드 요약

PoC 과정에서 발견된 IDE 타입 추론 단절(VS Code Pylance), Plotly 스타일 감가 에러(`textfont_style='bold'` 부활 및 `textfont_weight` 수정), 그리고 변수명 오타(`PARQUET_LIGHT_FIN` ➡️ `PARQUET_LIGHT_FP`)가 모두 수정된 **엔터프라이즈 레벨의 벤치마크 코드 아티팩트**이다.

* **파일명:** `benchmark_dual_scenario.py`
* **구동 방식:** `uv run benchmark_dual_scenario.py`
* **생성 산출물:** `benchmark_dual_results.png` (정적 차트), `benchmark_dual_results.html` (동적 대시보드)

---

## 6. 향후 추가 연구 과제 (Next Steps)

본 PoC를 통해 로컬 스토리지 환경에서의 포맷팅 및 파싱 최적화 정립이 완료되었다. 이를 바탕으로 실제 상용 서비스 레벨로 확장하기 위해 다음 연구를 제안한다.

1. **S3 remote I/O 및 HTTPFS 벤치마킹:** 로컬 NVMe 디스크가 아닌, 실제 S3 환경과 DuckDB를 연동했을 때 발생하는 네트워크 Latency 환경에서의 `pyarrow` 바이트 범위 요청(Byte-Range Fetch) 성능 측정.
2. **Hot/Cold 데이터 티어링(Tiering) 아키텍처 설계:** 최근 1~3개월 데이터는 API 서버 로컬 디스크의 `.duckdb` 스토리지(Hot)에 캐싱하고, 대규모 과거 데이터 조회 시에만 S3 쿼리(Cold)를 라우팅하는 하이브리드 애플리케이션 파이프라인 구현 기법 연구.
3. **메모리 프로파일러 통합:** `tracemalloc` 외에 `memory_profiler` 또는 로컬 OS 레벨의 Peak Memory 점유율을 시각화 차트에 네 번째 메트릭으로 통합하는 연구.
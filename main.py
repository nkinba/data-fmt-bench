import kagglehub
import pandas as pd
import duckdb
import time
import os
import shutil
import numpy as np
from pathlib import Path

# 현대적 데이터 시각화 라이브러리
import matplotlib.pyplot as plt
import seaborn as sns
import plotly.express as px

# --- 경로 설정 ---
DATA_DIR = Path("data")
RAW_CSV_PATH = DATA_DIR / "creditcard.csv"

# 시나리오별 파일 경로 분리
PARQUET_LIGHT_PA = DATA_DIR / "creditcard_light_pa.parquet"
PARQUET_LIGHT_FP = DATA_DIR / "creditcard_light_fp.parquet"
PARQUET_HEAVY_PA = DATA_DIR / "creditcard_heavy_pa.parquet"
PARQUET_HEAVY_FP = DATA_DIR / "creditcard_heavy_fp.parquet"

ROW_MULTIPLIER = 10


def prepare_raw_csv():
    DATA_DIR.mkdir(exist_ok=True)
    if not RAW_CSV_PATH.exists():
        print("⏳ Kaggle에서 신용카드 데이터셋 다운로드 중...")
        cached_dir = kagglehub.dataset_download("mlg-ulb/creditcardfraud")
        cached_csv = Path(cached_dir) / "creditcard.csv"
        shutil.copy(cached_csv, RAW_CSV_PATH)


def make_dataset_heavy(df: pd.DataFrame) -> pd.DataFrame:
    print(
        f"⚙️ [Heavy 시나리오 준비] 데이터를 {ROW_MULTIPLIER}배 뻥튀기하고 문자열 컬럼을 주입합니다..."
    )
    if ROW_MULTIPLIER > 1:
        df = pd.concat([df] * ROW_MULTIPLIER, ignore_index=True)

    total_rows = len(df)
    df["cur_resource_id"] = [f"i-0fa123456789ab{i % 50000}" for i in range(total_rows)]
    df["cur_operation"] = np.random.choice(
        [
            "RunInstances",
            "DescribeInstances",
            "CreateVolume",
            "DeleteVolume",
            "AssumeRole",
        ],
        total_rows,
    )
    df["cur_cost_category"] = np.random.choice(
        ["Compute", "Storage", "Network", "Management", "Security"], total_rows
    )
    df["cur_user_tag_project"] = [
        f"project_alpha_beta_gamma_{i % 100}" for i in range(total_rows)
    ]
    df["cur_user_tag_env"] = np.random.choice(
        ["production-live", "staging-v2", "development-sandbox", "qa-testing"],
        total_rows,
    )
    return df


def test_scenario(
    df: pd.DataFrame, scenario_name: str, pa_path: Path, fp_path: Path, is_heavy: bool
) -> list:
    """특정 시나리오에 대한 쓰기/읽기 벤치마크 수행"""
    print(f"\n▶️ [{scenario_name} 시나리오] 테스트 시작...")

    # 1. pyarrow 쓰기
    start = time.time()
    df.to_parquet(pa_path, engine="pyarrow", compression="snappy")
    pa_write_time = time.time() - start
    pa_size = pa_path.stat().st_size / (1024 * 1024)

    # 2. fastparquet 쓰기
    start = time.time()
    df.to_parquet(fp_path, engine="fastparquet", compression="SNAPPY")
    fp_write_time = time.time() - start
    fp_size = fp_path.stat().st_size / (1024 * 1024)

    # 3. DuckDB 조회 쿼리 정의 (시나리오별 분기)
    if is_heavy:
        query_template = """
            SELECT cur_cost_category, cur_user_tag_env, AVG(Amount) as avg_amount, COUNT(*) as cnt
            FROM '{}'
            WHERE cur_operation IN ('RunInstances', 'CreateVolume')
            GROUP BY cur_cost_category, cur_user_tag_env
        """
    else:
        # Light는 문자열 컬럼이 없으므로 순수 수치 기반 집계 쿼리 실행
        query_template = """
            SELECT Class, AVG(Amount) as avg_amount, COUNT(*) as cnt
            FROM '{}'
            GROUP BY Class
        """

    # 4. DuckDB로 읽기 테스트
    start = time.time()
    duckdb.query(query_template.format(pa_path)).df()
    pa_read_time = time.time() - start

    start = time.time()
    duckdb.query(query_template.format(fp_path)).df()
    fp_read_time = time.time() - start

    print(
        f"   - [pyarrow]      쓰기: {pa_write_time:.3f}s, 용량: {pa_size:.1f}MB, 읽기: {pa_read_time:.4f}s"
    )
    print(
        f"   - [fastparquet]  쓰기: {fp_write_time:.3f}s, 용량: {fp_size:.1f}MB, 읽기: {fp_read_time:.4f}s"
    )

    return [
        {
            "Scenario": scenario_name,
            "Engine": "pyarrow",
            "Metric": "Write Time (s)",
            "Value": pa_write_time,
        },
        {
            "Scenario": scenario_name,
            "Engine": "fastparquet",
            "Metric": "Write Time (s)",
            "Value": fp_write_time,
        },
        {
            "Scenario": scenario_name,
            "Engine": "pyarrow",
            "Metric": "File Size (MB)",
            "Value": pa_size,
        },
        {
            "Scenario": scenario_name,
            "Engine": "fastparquet",
            "Metric": "File Size (MB)",
            "Value": fp_size,
        },
        {
            "Scenario": scenario_name,
            "Engine": "pyarrow",
            "Metric": "Read Time (s)",
            "Value": pa_read_time,
        },
        {
            "Scenario": scenario_name,
            "Engine": "fastparquet",
            "Metric": "Read Time (s)",
            "Value": fp_read_time,
        },
    ]


def generate_dual_reports(res_df: pd.DataFrame):
    print("\n==================================================")
    print("📊 통합 비교 리포트 생성 중...")
    print("==================================================")

    # --------------------------------------------------
    # 1. Seaborn (정적 이미지 리포트)
    # --------------------------------------------------
    sns.set_theme(style="whitegrid")
    fig, axes = plt.subplots(1, 3, figsize=(18, 6), sharey=False)

    metrics = ["Write Time (s)", "File Size (MB)", "Read Time (s)"]
    titles = [
        "Write Performance\n(Lower is Better)",
        "Storage Size\n(Lower is Better)",
        "DuckDB Read Performance\n(Lower is Better)",
    ]
    colors = ["#4A90E2", "#E24A4A"]  # pyarrow (Blue), fastparquet (Red)

    for i, metric in enumerate(metrics):
        sub_data = res_df[res_df["Metric"] == metric]
        ax = axes[i]

        sns.barplot(
            x="Scenario", y="Value", hue="Engine", data=sub_data, ax=ax, palette=colors
        )
        ax.set_title(titles[i], fontsize=12, fontweight="bold", pad=15)
        ax.set_ylabel("")
        ax.set_xlabel("")

        for container in ax.containers:
            ax.bar_label(
                container,
                fmt="%.3f",
                padding=3,
                fontproperties={"weight": "bold", "size": 9},
            )

        if i != 0:
            ax.get_legend().remove()
        else:
            ax.legend(title="Engine", loc="upper left")

    plt.tight_layout()
    plt.savefig("benchmark_dual_results.png", dpi=300)
    print("💾 1. 정적 이미지 차트 저장 완료: 'benchmark_dual_results.png'")

    # --------------------------------------------------
    # 2. Plotly (인터랙티브 웹 리포트)
    # --------------------------------------------------
    fig_html = px.bar(
        res_df,
        x="Scenario",
        y="Value",
        color="Engine",
        facet_col="Metric",
        barmode="group",
        color_discrete_sequence=colors,
        text_auto=".3f",
        title="<b>[Benchmark] 데이터 체급별 PyArrow vs FastParquet 성능 전환점 분석</b>",
        labels={"Value": "수치", "Scenario": "시나리오"},
    )

    fig_html.update_yaxes(matches=None)
    fig_html.update_traces(
        textposition="outside", textfont_size=11, textfont_weight="bold"
    )
    fig_html.update_layout(
        margin=dict(t=80, b=40, l=40, r=40), title_font_size=16, showlegend=True
    )

    fig_html.write_html("benchmark_dual_results.html")
    print("💾 2. 동적 웹 대시보드 리포트 저장 완료: 'benchmark_dual_results.html'")


if __name__ == "__main__":
    prepare_raw_csv()

    # 1. Light 원본 데이터 로드
    print("\n📖 원본 CSV 데이터를 Pandas로 로드 중...")
    df_light = pd.read_csv(RAW_CSV_PATH)

    # 2. Heavy 데이터 가공
    df_heavy = make_dataset_heavy(df_light.copy())

    # 3. 테스트 통합 실행 및 메트릭 수집
    all_metrics = []

    # Light 시나리오 테스트 (오타 수정 반영: PARQUET_LIGHT_FP)
    all_metrics.extend(
        test_scenario(
            df_light,
            "Light (Numeric)",
            PARQUET_LIGHT_PA,
            PARQUET_LIGHT_FP,
            is_heavy=False,
        )
    )

    # Heavy 시나리오 테스트
    all_metrics.extend(
        test_scenario(
            df_heavy,
            "Heavy (With Strings)",
            PARQUET_HEAVY_PA,
            PARQUET_HEAVY_FP,
            is_heavy=True,
        )
    )

    # 4. 시각화 리포트 뽑기
    res_df = pd.DataFrame(all_metrics)
    generate_dual_reports(res_df)

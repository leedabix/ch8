
from io import StringIO

import pandas as pd
import plotly.express as px
import requests
import streamlit as st


# ----------------------------------------
# 1. 기본 설정
# ----------------------------------------
st.title("🕒 서울 장소별 시간대 인구 분석")

st.write(
    "서울시 혼잡도 기록을 이용해 장소별로 "
    "시간대에 따른 추정 인구 상한의 평균을 비교합니다."
)

CSV_URL = (
    "https://raw.githubusercontent.com/greatsong/"
    "modudata/main/data/seoul_congestion_log.csv"
)


# ----------------------------------------
# 2. CSV 불러오기
# ----------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def load_data():
    try:
        response = requests.get(
            CSV_URL,
            timeout=20,
        )
        response.raise_for_status()

    except requests.exceptions.Timeout:
        return None, (
            "데이터 서버가 20초 이내에 응답하지 않았습니다. "
            "잠시 후 다시 시도해 주세요."
        )

    except requests.exceptions.HTTPError:
        return None, (
            f"CSV 파일을 불러오지 못했습니다. "
            f"HTTP 상태 코드: {response.status_code}. "
            "파일 주소와 공개 설정을 확인해 주세요."
        )

    except requests.exceptions.ConnectionError:
        return None, (
            "데이터 서버에 연결하지 못했습니다. "
            "인터넷 연결 상태를 확인해 주세요."
        )

    except requests.exceptions.RequestException:
        return None, (
            "데이터를 요청하는 중 오류가 발생했습니다."
        )

    try:
        text = response.content.decode("utf-8-sig")

        df = pd.read_csv(
            StringIO(text),
            dtype={"코드": "string"},
        )

    except UnicodeError:
        return None, (
            "CSV 파일을 UTF-8 형식으로 읽지 못했습니다."
        )

    except pd.errors.EmptyDataError:
        return None, "CSV 파일이 비어 있습니다."

    except pd.errors.ParserError:
        return None, (
            "CSV 파일의 형식이 올바르지 않습니다. "
            "쉼표와 열 구성을 확인해 주세요."
        )

    required_columns = [
        "시각",
        "코드",
        "지역명",
        "혼잡도",
        "인구min",
        "인구max",
        "위도",
        "경도",
    ]

    missing = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing:
        return None, (
            "CSV 파일에 필요한 열이 없습니다: "
            + ", ".join(missing)
        )

    return df, None


with st.spinner("서울 혼잡도 기록을 불러오는 중입니다..."):
    df, error = load_data()

if error:
    st.error(error)
    st.stop()

if df.empty:
    st.info("CSV 파일에 분석할 기록이 없습니다.")
    st.stop()


# ----------------------------------------
# 3. 코드 및 자료형 변환
# ----------------------------------------
# 장소 코드는 숫자로 바꾸지 않고 문자열로 유지
df["코드"] = df["코드"].astype("string").str.strip()
df["지역명"] = (
    df["지역명"].astype("string").str.strip()
)

# 시각을 날짜와 시간 형식으로 변환
df["시각"] = pd.to_datetime(
    df["시각"],
    errors="coerce",
    format="%Y-%m-%d %H:%M",
)

time_failures = int(df["시각"].isna().sum())

# 인구 데이터를 숫자로 변환
population_failures = 0

for column in ["인구min", "인구max"]:
    converted = pd.to_numeric(
        df[column],
        errors="coerce",
    )

    population_failures += int(
        converted.isna().sum()
    )

    df[column] = converted

# 위도와 경도도 숫자로 변환
coordinate_failures = 0

for column in ["위도", "경도"]:
    converted = pd.to_numeric(
        df[column],
        errors="coerce",
    )

    coordinate_failures += int(
        converted.isna().sum()
    )

    df[column] = converted

total_failures = (
    time_failures
    + population_failures
    + coordinate_failures
)


# ----------------------------------------
# 4. 전체 데이터 현황
# ----------------------------------------
st.subheader("데이터 현황")

valid_times = df["시각"].dropna()

valid_codes = df.loc[
    df["코드"].notna()
    & df["코드"].ne("").fillna(False)
]

place_count = valid_codes["코드"].nunique()

if valid_times.empty:
    st.error(
        "날짜와 시간으로 변환할 수 있는 기록이 없습니다."
    )
    st.stop()

start_date = valid_times.min().strftime("%Y-%m-%d")
end_date = valid_times.max().strftime("%Y-%m-%d")

col1, col2, col3, col4 = st.columns(4)

col1.metric("기록 시작 날짜", start_date)
col2.metric("마지막 날짜", end_date)
col3.metric("장소 수", f"{place_count:,}곳")
col4.metric("전체 행 수", f"{len(df):,}개")

st.write("**자료형 변환 결과**")

st.write(
    f"시각 변환 실패: **{time_failures:,}개**"
)

st.write(
    f"인구 변환 실패: **{population_failures:,}개**"
)

st.write(
    f"위도·경도 변환 실패: "
    f"**{coordinate_failures:,}개**"
)

st.write(
    f"변환 실패 합계: **{total_failures:,}개**"
)

st.caption(
    "빈 값이나 잘못된 형식은 결측값으로 처리합니다. "
    "숫자로 변환할 수 없는 값을 임의로 0으로 바꾸지 않습니다."
)


# ----------------------------------------
# 5. 장소 선택
# ----------------------------------------
st.divider()
st.subheader("분석할 장소 선택")

places = (
    valid_codes[["코드", "지역명"]]
    .drop_duplicates(subset=["코드"])
    .sort_values(["지역명", "코드"])
)

place_options = places["코드"].tolist()

if not place_options:
    st.warning("선택할 수 있는 장소가 없습니다.")
    st.stop()

place_names = dict(
    zip(places["코드"], places["지역명"])
)

selected_code = st.selectbox(
    "장소를 하나 선택하세요.",
    options=place_options,
    format_func=lambda code: (
        f"{place_names[code]} ({code})"
    ),
)

selected_name = place_names[selected_code]

# 장소 코드가 같은 기록만 선택
selected_df = df.loc[
    df["코드"] == selected_code
].copy()

st.write(
    f"**선택한 장소:** {selected_name}"
)


# ----------------------------------------
# 6. 중복 기록 확인 및 처리
# ----------------------------------------
st.subheader("중복 기록 확인")

# 동일한 장소 코드와 관측 시각이 같으면 중복
# 시간이 없는 기록은 중복 판정에서 제외
valid_time_mask = selected_df["시각"].notna()

duplicate_mask = (
    valid_time_mask
    & selected_df.duplicated(
        subset=["코드", "시각"],
        keep="first",
    )
)

duplicate_count = int(duplicate_mask.sum())

st.write(
    f"같은 장소와 동일한 관측 시각의 "
    f"중복 기록: **{duplicate_count:,}개**"
)

st.info(
    "중복 처리 기준: 장소 코드와 관측 시각이 "
    "모두 같다면 첫 번째 기록만 남깁니다. "
    "이 기준으로 중복을 제거한 뒤 "
    "시간대별 평균을 계산합니다."
)

if duplicate_count:
    st.warning(
        f"중복 기록 {duplicate_count:,}개를 "
        "평균 계산 전에 제외했습니다."
    )

selected_df = selected_df.loc[
    ~duplicate_mask
].copy()


# ----------------------------------------
# 7. 시간대별 데이터 준비
# ----------------------------------------
# 날짜와 시각이 올바른 기록만 사용
analysis_df = selected_df.dropna(
    subset=["시각"]
).copy()

if analysis_df.empty:
    st.info(
        "선택한 장소에 분석할 수 있는 "
        "시각 기록이 없습니다."
    )
    st.stop()

# 0~23시 중 시간 부분만 추출
analysis_df["시간대"] = (
    analysis_df["시각"].dt.hour
)

# 날짜 부분을 별도 열로 추출
analysis_df["관측날짜"] = (
    analysis_df["시각"].dt.date
)

# 인구max가 유효한 기록만 평균 계산에 사용
valid_population_df = analysis_df.dropna(
    subset=["인구max"]
).copy()

# 0~23시별 평균, 기록 수, 관측 날짜 수
hourly = (
    valid_population_df
    .groupby("시간대")
    .agg(
        평균=("인구max", "mean"),
        기록수=("인구max", "count"),
        관측날짜수=("관측날짜", "nunique"),
    )
)

# 모든 시간대를 포함하도록 재구성
# 기록이 없는 시간은 NaN으로 유지
hourly = hourly.reindex(range(24))
hourly.index.name = "시간대"

hourly = hourly.reset_index()

# 시간대별 표시 이름
hourly["시각표시"] = (
    hourly["시간대"].map(
        lambda x: f"{x:02d}시"
    )
)

# 데이터 없는 시간대 계산
missing_hours = int(
    hourly["평균"].isna().sum()
)

valid_hours = 24 - missing_hours

st.subheader("시간대별 집계 결과")

a, b = st.columns(2)

a.metric("평균을 계산한 시간대", f"{valid_hours}개")
b.metric("기록이 없는 시간대", f"{missing_hours}개")


# ----------------------------------------
# 8. 시간대별 평균 표
# ----------------------------------------
hourly_table = hourly[
    [
        "시각표시",
        "평균",
        "기록수",
        "관측날짜수",
    ]
].copy()

hourly_table.columns = [
    "시각",
    "추정 인구 상한의 평균",
    "기록 수",
    "서로 다른 관측 날짜 수",
]

st.dataframe(
    hourly_table,
    hide_index=True,
    use_container_width=True,
    column_config={
        "추정 인구 상한의 평균": (
            st.column_config.NumberColumn(
                format="%.1f"
            )
        ),
        "기록 수": st.column_config.NumberColumn(
            format="%d"
        ),
        "서로 다른 관측 날짜 수": (
            st.column_config.NumberColumn(
                format="%d"
            )
        ),
    },
)


# ----------------------------------------
# 9. Plotly 막대그래프
# ----------------------------------------
st.subheader("시간대별 추정 인구 상한의 평균")

if valid_population_df.empty:
    st.info(
        "인구max가 유효한 기록이 없어 "
        "그래프를 표시할 수 없습니다."
    )

else:
    fig = px.bar(
        hourly,
        x="시간대",
        y="평균",
        title=(
            f"{selected_name} | "
            "추정 인구 상한의 평균"
        ),
        custom_data=[
            "시각표시",
            "평균",
            "기록수",
            "관측날짜수",
        ],
        labels={
            "시간대": "시각",
            "평균": "추정 인구 상한의 평균",
        },
    )

    # 마우스 오버 정보
    fig.update_traces(
        hovertemplate=(
            "시각: %{customdata[0]}<br>"
            "추정 인구 상한의 평균: "
            "%{customdata[1]:,.1f}명<br>"
            "기록 수: %{customdata[2]:.0f}개<br>"
            "서로 다른 관측 날짜 수: "
            "%{customdata[3]:.0f}일"
            "<extra></extra>"
        )
    )

    # 0시부터 23시까지 표시
    fig.update_xaxes(
        tickmode="array",
        tickvals=list(range(24)),
        ticktext=[
            f"{hour:02d}시"
            for hour in range(24)
        ],
        title="시각",
    )

    fig.update_yaxes(
        title="추정 인구 상한의 평균",
        tickformat=",",
        rangemode="tozero",
    )

    fig.update_layout(
        height=550,
        bargap=0.2,
        margin=dict(
            l=20,
            r=20,
            t=65,
            b=30,
        ),
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
    )


# ----------------------------------------
# 10. 누락 시간 안내
# ----------------------------------------
st.write(
    f"**기록이 없는 시간대: {missing_hours}개**"
)

if missing_hours:
    missing_labels = hourly.loc[
        hourly["평균"].isna(),
        "시각표시",
    ].tolist()

    st.write(
        "기록이 없는 시각: "
        + ", ".join(missing_labels)
    )

st.caption(
    "기록이 없는 시각은 평균을 0으로 처리하지 않고 "
    "빈 값으로 유지했습니다."
)


# ----------------------------------------
# 11. 분석 결과 안내
# ----------------------------------------
st.info(
    "이 그래프의 평균은 수집된 기록 중 "
    "중복을 제거하고 인구max가 유효한 기록만으로 "
    "계산한 값입니다. "
    "시간대별 기록 수와 관측 날짜 수가 다르므로 "
    "모든 날짜를 동일하게 대표하는 평균으로 "
    "해석해서는 안 됩니다."
)

st.caption(
    "데이터 출처: 서울시 실시간 도시데이터 기록 CSV"
)

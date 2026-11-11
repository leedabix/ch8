
import hashlib
import time
import xml.etree.ElementTree as ET
from io import StringIO
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import pydeck as pdk
import requests
import streamlit as st


# ----------------------------------------
# 1. 기본 설정
# ----------------------------------------
st.set_page_config(
    page_title="서울 실시간 인구 분석",
    page_icon="🗺️",
    layout="wide",
)

st.title("🗺️ 서울 실시간 인구 분석")
st.write(
    "서울 주요 121개 장소의 실시간 혼잡도와 "
    "연령대별 인구 비율을 살펴봅니다."
)

CSV_URL = (
    "https://raw.githubusercontent.com/greatsong/"
    "modudata/main/data/seoul_area_all.csv"
)

API_BASE = "http://openapi.seoul.go.kr:8088"
KST = ZoneInfo("Asia/Seoul")
CACHE_SECONDS = 300
REQUEST_INTERVAL = 0.2

AGE_FIELDS = {
    "0~10세": "PPLTN_RATE_0",
    "10대": "PPLTN_RATE_10",
    "20대": "PPLTN_RATE_20",
    "30대": "PPLTN_RATE_30",
    "40대": "PPLTN_RATE_40",
    "50대": "PPLTN_RATE_50",
    "60대": "PPLTN_RATE_60",
    "70대 이상": "PPLTN_RATE_70",
}

AGE_COLORS = [
    "#93C5FD",
    "#60A5FA",
    "#2563EB",
    "#14B8A6",
    "#84CC16",
    "#FACC15",
    "#FB923C",
    "#EF4444",
]

CONGESTION_COLORS = {
    "여유": [34, 197, 94, 210],
    "보통": [234, 179, 8, 210],
    "약간 붐빔": [249, 115, 22, 210],
    "붐빔": [239, 68, 68, 210],
}

UNKNOWN_COLOR = [148, 163, 184, 190]


# ----------------------------------------
# 2. API 키 확인
# ----------------------------------------
try:
    api_key = st.secrets.get("SEOUL_KEY")
except Exception:
    api_key = None

if not api_key or not str(api_key).strip():
    st.error("서울시 Open API 인증키가 등록되지 않았습니다.")
    st.info(
        "스트림릿 클라우드의 Settings → Secrets를 열고 "
        'SEOUL_KEY = "발급받은_인증키" 형식으로 '
        "등록한 뒤 앱을 다시 실행하세요."
    )
    st.stop()

api_key = str(api_key).strip()

# 인증키 자체는 캐시 키나 출력값으로 사용하지 않음
key_fingerprint = hashlib.sha256(
    api_key.encode("utf-8")
).hexdigest()


# ----------------------------------------
# 3. 장소 목록 불러오기
# ----------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def load_areas():
    response = requests.get(CSV_URL, timeout=15)
    response.raise_for_status()

    text = response.content.decode("utf-8-sig")

    df = pd.read_csv(
        StringIO(text),
        dtype={"코드": "string"},
    )

    required = ["코드", "지역명", "분류", "위도", "경도"]

    if any(col not in df.columns for col in required):
        raise ValueError("필수 열이 누락되었습니다.")

    df = df[required].copy()

    for col in ["코드", "지역명", "분류"]:
        df[col] = df[col].astype("string").str.strip()

    for col in ["위도", "경도"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=required)

    df = df.loc[
        df["위도"].between(33, 39)
        & df["경도"].between(124, 132)
        & df["코드"].ne("")
    ]

    return df.drop_duplicates("코드").reset_index(drop=True)


try:
    areas = load_areas()
except requests.exceptions.Timeout:
    st.error("장소 목록 서버가 15초 안에 응답하지 않았습니다.")
    st.stop()
except requests.exceptions.RequestException:
    st.error(
        "장소 목록을 불러오지 못했습니다. "
        "인터넷 연결과 GitHub CSV 주소를 확인하세요."
    )
    st.stop()
except (ValueError, UnicodeError, pd.errors.ParserError):
    st.error(
        "CSV 파일을 읽지 못했습니다. "
        "파일 형식과 열 이름을 확인하세요."
    )
    st.stop()

if areas.empty:
    st.error("조회할 장소가 없습니다.")
    st.stop()

if len(areas) != 121:
    st.warning(
        f"장소 목록에 {len(areas)}곳이 있습니다. "
        "예상한 121곳과 다릅니다."
    )


# ----------------------------------------
# 4. 숫자 변환 함수
# ----------------------------------------
def to_number(value):
    if value is None:
        return None

    text = str(value).replace(",", "").strip()

    if not text:
        return None

    try:
        number = float(text)
        if not 0 <= number < float("inf"):
            return None
        return number
    except ValueError:
        return None


def format_population(value):
    if value is None or pd.isna(value):
        return "확인할 수 없음"
    return f"{int(value):,}명"


def format_rate(value):
    if value is None or pd.isna(value):
        return "확인할 수 없음"
    return f"{value:.1f}%"


# ----------------------------------------
# 5. XML 응답에서 인구 정보 추출
# ----------------------------------------
def parse_population_xml(content):
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None, "XML 응답을 해석할 수 없음"

    # 공식 응답 코드
    result_code = (
        root.findtext(".//RESULT/RESULT.CODE")
        or root.findtext(".//RESULT.CODE")
        or root.findtext(".//RESULT/CODE")
    )

    if result_code and result_code != "INFO-000":
        return None, f"API 처리 오류({result_code})"

    citydata = root.find(".//CITYDATA")

    if citydata is None:
        return None, "CITYDATA 항목 없음"

    population = citydata.find(
        "./LIVE_PPLTN_STTS/LIVE_PPLTN_STTS"
    )

    if population is None:
        population = citydata.find("./LIVE_PPLTN_STTS")

    if population is None:
        return None, "실시간 인구 정보 없음"

    def field(name):
        value = population.findtext(name)
        return value.strip() if value else None

    congestion = field("AREA_CONGEST_LVL")
    population_min = to_number(field("AREA_PPLTN_MIN"))
    population_max = to_number(field("AREA_PPLTN_MAX"))

    if (
        congestion is None
        and population_min is None
        and population_max is None
    ):
        return None, "혼잡도와 인구 정보가 없음"

    result = {
        "혼잡도": congestion or "확인할 수 없음",
        "인구최소": population_min,
        "인구최대": population_max,
        "비상주비율": to_number(
            field("NON_RESNT_PPLTN_RATE")
        ),
        "인구기준시각": (
            field("PPLTN_TIME") or "확인할 수 없음"
        ),
    }

    # 연령대별 인구 비율
    for label, api_field in AGE_FIELDS.items():
        result[label] = to_number(field(api_field))

    return result, None


# ----------------------------------------
# 6. 장소 한 곳 조회
# ----------------------------------------
def fetch_one_area(session, area_code, key):
    # 공식 API 요청 주소
    url = (
        f"{API_BASE}/{key}/xml/citydata/1/5/"
        f"{area_code}"
    )

    try:
        response = session.get(
            url,
            timeout=10,
            allow_redirects=False,
        )

        if response.status_code != 200:
            return None, (
                f"서버 오류(HTTP {response.status_code})"
            )

        return parse_population_xml(response.content)

    except requests.exceptions.Timeout:
        return None, "10초 안에 응답하지 않음"
    except requests.exceptions.ConnectionError:
        return None, "서버 연결 실패"
    except requests.exceptions.RequestException:
        # URL에 인증키가 있으므로 예외 원문은 출력하지 않음
        return None, "네트워크 요청 오류"


# ----------------------------------------
# 7. 121곳 조회 및 5분 캐시
# ----------------------------------------
@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def fetch_all_areas(area_records, fingerprint, _api_key):
    results = []
    failures = []

    with requests.Session() as session:
        for index, area in enumerate(area_records):
            code, name, category, latitude, longitude = area

            data, error = fetch_one_area(
                session, code, _api_key
            )

            if error:
                failures.append({
                    "코드": code,
                    "지역명": name,
                    "실패 이유": error,
                })
            else:
                results.append({
                    "코드": code,
                    "지역명": name,
                    "분류": category,
                    "위도": latitude,
                    "경도": longitude,
                    **data,
                })

            if index < len(area_records) - 1:
                time.sleep(REQUEST_INTERVAL)

    loaded_at = datetime.now(KST)
    return results, failures, loaded_at


area_records = tuple(
    (
        str(row["코드"]),
        str(row["지역명"]),
        str(row["분류"]),
        float(row["위도"]),
        float(row["경도"]),
    )
    for _, row in areas.iterrows()
)

with st.spinner("서울 121곳의 인구 데이터를 조회하고 있습니다..."):
    results, failures, loaded_at = fetch_all_areas(
        area_records,
        key_fingerprint,
        api_key,
    )


# ----------------------------------------
# 8. 조회 결과
# ----------------------------------------
st.subheader("실시간 데이터 조회 현황")

loaded_label = loaded_at.strftime("%Y-%m-%d %H:%M:%S")

st.write(
    f"**데이터 불러온 시각: {loaded_label} (한국 시간)**"
)

a, b, c = st.columns(3)

a.metric("조회 대상", len(areas))
b.metric("조회 성공", len(results))
c.metric("조회 실패", len(failures))

st.caption(
    "한 번 조회한 결과는 5분간 캐시합니다. "
    "체크박스를 변경해도 API를 다시 호출하지 않습니다."
)

if failures:
    st.warning(
        f"{len(failures)}곳의 정보를 불러오지 못했습니다. "
        "실패한 장소는 인구 0명으로 처리하지 않습니다."
    )

    with st.expander("조회 실패 장소"):
        st.dataframe(
            pd.DataFrame(failures),
            hide_index=True,
            use_container_width=True,
        )

if not results:
    st.error(
        "분석할 인구 데이터가 없습니다. "
        "API 인증키와 서버 상태를 확인하세요."
    )
    st.stop()


# ----------------------------------------
# 9. 비율 및 지도용 데이터 준비
# ----------------------------------------
df = pd.DataFrame(results)

age_labels = list(AGE_FIELDS.keys())

# 연령대 비율을 숫자로 변환
for column in age_labels:
    df[column] = pd.to_numeric(
        df[column], errors="coerce"
    )

# 청년 비율: 20대 + 30대
# 둘 중 하나라도 없으면 결측값으로 유지
df["청년비율"] = (
    df["20대"] + df["30대"]
)

# 60대 이상 비율: 60대 + 70대 이상
df["60대이상비율"] = (
    df["60대"] + df["70대 이상"]
)

youth_failures = int(df["청년비율"].isna().sum())
senior_failures = int(df["60대이상비율"].isna().sum())

st.subheader("연령대별 비율 확인")

a, b = st.columns(2)

a.metric(
    "청년 비율 계산 실패",
    f"{youth_failures}곳",
)

b.metric(
    "60대 이상 비율 계산 실패",
    f"{senior_failures}곳",
)

st.caption(
    "청년 비율은 20대와 30대 비율의 합입니다. "
    "60대 이상 비율은 60대와 70대 이상 비율의 합입니다. "
    "필요한 값이 없거나 숫자로 변환되지 않으면 "
    "0으로 바꾸지 않고 분석에서 제외합니다."
)

# 인구 범위
df["인구범위표시"] = df.apply(
    lambda row: (
        f"{format_population(row['인구최소'])}"
        " ~ "
        f"{format_population(row['인구최대'])}"
    ),
    axis=1,
)

df["비상주비율표시"] = df["비상주비율"].apply(
    format_rate
)

df["청년비율표시"] = df["청년비율"].apply(
    format_rate
)

df["인구기준시각"] = (
    df["인구기준시각"]
    .fillna("확인할 수 없음")
    .replace("", "확인할 수 없음")
)

# 점 크기: 실시간 인구 최댓값 기준
max_population = pd.to_numeric(
    df["인구최대"], errors="coerce"
).max()


def get_radius(value):
    if pd.isna(value) or pd.isna(max_population):
        return 80

    if max_population <= 0:
        return 80

    proportion = max(0, value) / max_population
    return 80 + 520 * (proportion ** 0.5)


df["radius"] = df["인구최대"].apply(get_radius)

# 기존 혼잡도 지도 색상
df["congestionColor"] = df["혼잡도"].apply(
    lambda x: CONGESTION_COLORS.get(
        x, UNKNOWN_COLOR
    )
)


# ----------------------------------------
# 10. 청년 비율 지도 색상
# ----------------------------------------
def youth_color(value):
    if pd.isna(value):
        return UNKNOWN_COLOR

    # 0%는 연한 노랑, 100%는 진한 빨강
    ratio = max(0, min(float(value) / 100, 1))

    low = [254, 240, 138]
    high = [185, 28, 28]

    return [
        int(low[i] + (high[i] - low[i]) * ratio)
        for i in range(3)
    ] + [220]


df["youthColor"] = df["청년비율"].apply(
    youth_color
)


# ----------------------------------------
# 11. 장소 분류 체크박스
# ----------------------------------------
st.subheader("지도에 표시할 장소 분류")

categories = sorted(
    df["분류"].dropna().unique()
)

selected_categories = []
columns = st.columns(3)

for index, category in enumerate(categories):
    with columns[index % 3]:
        checked = st.checkbox(
            category,
            value=True,
            key=f"category_{category}",
        )

        if checked:
            selected_categories.append(category)

filtered_df = df.loc[
    df["분류"].isin(selected_categories)
].copy()

st.write(
    f"**현재 선택된 장소: {len(filtered_df)}곳**"
)


# ----------------------------------------
# 12. 지도 생성 함수
# ----------------------------------------
def make_map(data, color_column, tooltip):
    layer = pdk.Layer(
        "ScatterplotLayer",
        data=data,
        get_position="[경도, 위도]",
        get_fill_color=color_column,
        get_radius="radius",
        radius_units="meters",
        radius_min_pixels=5,
        radius_max_pixels=38,
        pickable=True,
        auto_highlight=True,
        stroked=True,
        get_line_color=[255, 255, 255, 230],
        line_width_min_pixels=1,
    )

    view = pdk.ViewState(
        latitude=37.5665,
        longitude=126.9780,
        zoom=10.4,
        pitch=0,
    )

    return pdk.Deck(
        layers=[layer],
        initial_view_state=view,
        map_style="light",
        tooltip={
            "html": tooltip,
            "style": {
                "backgroundColor": "#20242b",
                "color": "white",
                "fontSize": "13px",
                "padding": "10px",
            },
        },
    )


# ----------------------------------------
# 13. 기존 혼잡도 지도
# ----------------------------------------
st.subheader("서울 실시간 혼잡도 지도")

st.markdown(
    "🟢 여유　🟡 보통　🟠 약간 붐빔　🔴 붐빔"
)

congestion_tooltip = (
    "<b>{지역명}</b><br/>"
    "분류: {분류}<br/>"
    "혼잡도: {혼잡도}<br/>"
    "실시간 인구: {인구범위표시}<br/>"
    "비상주 인구 비율: {비상주비율표시}<br/>"
    "인구 데이터 기준 시각: {인구기준시각}"
)

if filtered_df.empty:
    st.info("표시할 장소 분류를 선택하세요.")
else:
    st.pydeck_chart(
        make_map(
            filtered_df,
            "congestionColor",
            congestion_tooltip,
        ),
        use_container_width=True,
        height=550,
    )


# ----------------------------------------
# 14. 새 청년 비율 지도
# ----------------------------------------
st.subheader("서울 청년 인구 비율 지도")

st.write(
    "20대와 30대의 인구 비율을 합한 값으로 "
    "점의 색을 표시합니다."
)

st.caption(
    "연한 노랑은 청년 비율이 낮은 지역, "
    "진한 빨강은 청년 비율이 높은 지역입니다. "
    "회색은 청년 비율을 확인할 수 없는 지역입니다."
)

youth_tooltip = (
    "<b>{지역명}</b><br/>"
    "분류: {분류}<br/>"
    "청년 비율: {청년비율표시}<br/>"
    "실시간 인구: {인구범위표시}<br/>"
    "인구 데이터 기준 시각: {인구기준시각}"
)

if filtered_df.empty:
    st.info("표시할 장소 분류를 선택하세요.")
else:
    st.pydeck_chart(
        make_map(
            filtered_df,
            "youthColor",
            youth_tooltip,
        ),
        use_container_width=True,
        height=550,
    )

st.caption(
    "두 지도 모두 점의 크기는 실시간 인구 "
    "추정 범위의 최댓값을 기준으로 합니다."
)


# ----------------------------------------
# 15. 연령대별 상위 6곳 선정
# ----------------------------------------
st.divider()
st.header("📊 연령대별 인구 비율 비교")

# 순위를 계산할 때는 연령대별 모든 값이 있어야
# 누적 막대그래프의 구성이 완전해짐
complete_age_df = df.dropna(
    subset=age_labels
).copy()

youth_top6 = (
    complete_age_df
    .sort_values(
        ["청년비율", "코드"],
        ascending=[False, True],
    )
    .head(6)
    .copy()
)

senior_top6 = (
    complete_age_df
    .sort_values(
        ["60대이상비율", "코드"],
        ascending=[False, True],
    )
    .head(6)
    .copy()
)

st.caption(
    "상위 6곳은 조회에 성공하고 모든 연령대별 "
    "비율을 확인할 수 있는 장소에서 선정합니다. "
    "지도에서 선택한 분류와 관계없이 "
    "전체 조회 장소를 기준으로 계산합니다."
)

st.write(
    f"**연령대별 비율이 모두 유효한 장소:** "
    f"{len(complete_age_df)}곳"
)


# ----------------------------------------
# 16. 연령대별 누적 막대그래프 함수
# ----------------------------------------
def make_age_chart(top_df, title):
    if top_df.empty:
        return None

    chart_df = top_df.copy()

    # 이름이 중복되더라도 장소 코드를 통해 구분
    chart_df["장소표시"] = (
        chart_df["지역명"]
        + " ("
        + chart_df["코드"]
        + ")"
    )

    # 상위 장소부터 순서대로 표시
    place_order = chart_df["장소표시"].tolist()

    fig = go.Figure()

    for index, age in enumerate(age_labels):
        fig.add_trace(
            go.Bar(
                name=age,
                y=chart_df["장소표시"],
                x=chart_df[age],
                orientation="h",
                marker_color=AGE_COLORS[index],
                customdata=[
                    [
                        row["지역명"],
                        age,
                        row[age],
                    ]
                    for _, row in chart_df.iterrows()
                ],
                hovertemplate=(
                    "지역명: %{customdata[0]}<br>"
                    "연령대: %{customdata[1]}<br>"
                    "인구 비율: %{customdata[2]:.1f}%"
                    "<extra></extra>"
                ),
            )
        )

    fig.update_layout(
        title=title,
        barmode="stack",
        height=480,
        xaxis_title="연령대별 인구 비율(%)",
        yaxis_title="",
        xaxis=dict(range=[0, 105]),
        yaxis=dict(
            categoryorder="array",
            categoryarray=list(reversed(place_order)),
        ),
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.18,
            xanchor="left",
            x=0,
        ),
        margin=dict(
            l=20,
            r=20,
            t=85,
            b=95,
        ),
    )

    return fig


# ----------------------------------------
# 17. 두 그래프 나란히 표시
# ----------------------------------------
col1, col2 = st.columns(2)

with col1:
    st.subheader("청년 비율 상위 6곳")

    if youth_top6.empty:
        st.info("청년 비율을 비교할 데이터가 없습니다.")
    else:
        youth_fig = make_age_chart(
            youth_top6,
            f"청년 비율 TOP 6<br>"
            f"<sup>조회: {loaded_label} (KST)</sup>",
        )

        st.plotly_chart(
            youth_fig,
            use_container_width=True,
        )

        st.dataframe(
            youth_top6[
                ["지역명", "청년비율"]
            ].rename(
                columns={"청년비율": "청년 비율(%)"}
            ),
            hide_index=True,
            use_container_width=True,
        )

with col2:
    st.subheader("60대 이상 비율 상위 6곳")

    if senior_top6.empty:
        st.info("60대 이상 비율을 비교할 데이터가 없습니다.")
    else:
        senior_fig = make_age_chart(
            senior_top6,
            f"60대 이상 비율 TOP 6<br>"
            f"<sup>조회: {loaded_label} (KST)</sup>",
        )

        st.plotly_chart(
            senior_fig,
            use_container_width=True,
        )

        st.dataframe(
            senior_top6[
                ["지역명", "60대이상비율"]
            ].rename(
                columns={
                    "60대이상비율": "60대 이상 비율(%)"
                }
            ),
            hide_index=True,
            use_container_width=True,
        )


# ----------------------------------------
# 18. 분석 결과 안내
# ----------------------------------------
st.info(
    "연령대별 인구 비율은 서울시가 추정한 "
    "실시간 인구 구성 비율입니다. "
    "해당 장소에 거주하는 주민의 연령 구성과는 "
    "다를 수 있습니다."
)

st.caption(
    f"데이터 불러온 시각: {loaded_label} (한국 시간)"
)

st.caption(
    "데이터 출처: 서울특별시 실시간 도시데이터 Open API"
)


import hashlib
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pydeck as pdk
import requests
import streamlit as st


# ----------------------------------------
# 1. 기본 설정
# ----------------------------------------
st.set_page_config(
    page_title="서울 실시간 인구 혼잡도",
    page_icon="🗺️",
    layout="wide",
)

st.title("🗺️ 서울 실시간 인구 혼잡도 지도")
st.write(
    "서울시 실시간 도시데이터 API를 이용해 "
    "주요 121개 장소의 인구와 혼잡도를 확인합니다."
)

CSV_URL = (
    "https://raw.githubusercontent.com/greatsong/"
    "modudata/main/data/seoul_area_all.csv"
)

API_BASE = "http://openapi.seoul.go.kr:8088"
KST = ZoneInfo("Asia/Seoul")

# 장소별 요청 사이의 대기 시간
REQUEST_INTERVAL = 0.2

# API 응답을 기억할 시간: 5분
CACHE_SECONDS = 300

# 혼잡도별 지도 색상
COLORS = {
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
        "스트림릿 클라우드에서 앱의 "
        "Settings → Secrets를 열고 "
        "다음과 같이 인증키를 등록하세요.\n\n"
        'SEOUL_KEY = "발급받은_인증키"\n\n'
        "설정을 저장한 뒤 앱을 다시 실행하면 됩니다."
    )
    st.stop()

api_key = str(api_key).strip()

# 캐시 구분에만 사용할 키의 해시값
# 실제 인증키를 화면이나 로그에 표시하지 않음
key_fingerprint = hashlib.sha256(
    api_key.encode("utf-8")
).hexdigest()


# ----------------------------------------
# 3. 장소 목록 CSV 불러오기
# ----------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def load_areas():
    response = requests.get(CSV_URL, timeout=15)
    response.raise_for_status()

    # UTF-8 BOM이 있어도 열 이름을 정상적으로 읽음
    from io import StringIO

    df = pd.read_csv(
        StringIO(response.content.decode("utf-8-sig")),
        dtype={"코드": "string"},
    )

    required = ["코드", "지역명", "분류", "위도", "경도"]

    missing = [col for col in required if col not in df.columns]

    if missing:
        raise ValueError("CSV에 필요한 열이 없습니다.")

    df = df[required].copy()
    df["코드"] = df["코드"].astype("string").str.strip()
    df["지역명"] = df["지역명"].astype("string").str.strip()
    df["분류"] = df["분류"].astype("string").str.strip()

    df["위도"] = pd.to_numeric(df["위도"], errors="coerce")
    df["경도"] = pd.to_numeric(df["경도"], errors="coerce")

    df = df.dropna(subset=required)
    df = df.loc[
        df["위도"].between(33, 39)
        & df["경도"].between(124, 132)
    ]

    df = df.drop_duplicates(subset=["코드"])
    return df.reset_index(drop=True)


try:
    areas = load_areas()
except (requests.exceptions.RequestException, ValueError):
    st.error(
        "장소 목록 CSV를 불러오지 못했습니다. "
        "인터넷 연결이나 CSV 주소 및 열 이름을 확인하세요."
    )
    st.stop()
except Exception:
    st.error("장소 목록을 처리하는 중 문제가 발생했습니다.")
    st.stop()

if areas.empty:
    st.warning("CSV에서 조회할 장소를 찾지 못했습니다.")
    st.stop()

if len(areas) != 121:
    st.warning(
        f"장소 목록에서 {len(areas)}곳을 확인했습니다. "
        "예상한 121곳과 다르므로 CSV 내용을 확인하세요."
    )


# ----------------------------------------
# 4. 숫자 변환 함수
# ----------------------------------------
def to_number(value):
    if value is None:
        return None

    value = str(value).replace(",", "").strip()

    if not value:
        return None

    try:
        return float(value)
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
def parse_population_xml(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None, "XML 응답을 해석할 수 없음"

    # 공식 응답 코드 확인
    result_code = (
        root.findtext(".//RESULT/RESULT.CODE")
        or root.findtext(".//RESULT/CODE")
        or root.findtext(".//RESULT.CODE")
    )

    if result_code and result_code != "INFO-000":
        return None, f"API 처리 오류({result_code})"

    citydata = root.find(".//CITYDATA")

    if citydata is None:
        return None, "CITYDATA 항목이 없음"

    # 공식 XML은 LIVE_PPLTN_STTS가 중첩된 구조
    population = citydata.find(
        "./LIVE_PPLTN_STTS/LIVE_PPLTN_STTS"
    )

    if population is None:
        population = citydata.find("./LIVE_PPLTN_STTS")

    if population is None:
        return None, "실시간 인구 정보가 없음"

    def field(name):
        text = population.findtext(name)
        return text.strip() if text else None

    congestion = field("AREA_CONGEST_LVL")
    population_min = to_number(field("AREA_PPLTN_MIN"))
    population_max = to_number(field("AREA_PPLTN_MAX"))
    non_resident = to_number(field("NON_RESNT_PPLTN_RATE"))

    # API 기준 시각만 사용하고 조회 시각으로 대체하지 않음
    population_time = field("PPLTN_TIME")

    if not population_time:
        population_time = "확인할 수 없음"

    # 인구 정보가 전혀 없으면 실패로 처리
    if (
        congestion is None
        and population_min is None
        and population_max is None
    ):
        return None, "혼잡도와 인구 정보가 없음"

    return {
        "혼잡도": congestion or "확인할 수 없음",
        "인구최소": population_min,
        "인구최대": population_max,
        "비상주비율": non_resident,
        "인구기준시각": population_time,
    }, None


# ----------------------------------------
# 6. 장소 한 곳의 API 데이터 요청
# ----------------------------------------
def fetch_one_area(session, area_code, key):
    # 공식 요청 구조:
    # /KEY/xml/citydata/1/5/AREA_NM
    # AREA_NM에는 장소 코드도 입력 가능
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
                f"서버 응답 오류(HTTP {response.status_code})"
            )

        return parse_population_xml(response.content)

    except requests.exceptions.Timeout:
        return None, "10초 안에 응답하지 않음"

    except requests.exceptions.ConnectionError:
        return None, "서버에 연결할 수 없음"

    except requests.exceptions.RequestException:
        # URL에 인증키가 포함될 수 있어
        # 예외 원문은 출력하지 않음
        return None, "API 요청 중 네트워크 오류"


# ----------------------------------------
# 7. 121곳 순차 조회 및 5분 캐시
# ----------------------------------------
@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def fetch_all_areas(area_records, key_fingerprint, _api_key):
    results = []
    failures = []

    # 실제 데이터 수집 시작 시각
    started_at = datetime.now(KST)

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

            # 마지막 요청 뒤에는 기다리지 않음
            if index < len(area_records) - 1:
                time.sleep(REQUEST_INTERVAL)

    loaded_at = datetime.now(KST)

    return results, failures, started_at, loaded_at


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

with st.spinner(
    f"서울 {len(areas)}곳의 실시간 인구 정보를 조회하고 있습니다. "
    "최초 조회 시 시간이 걸릴 수 있습니다."
):
    results, failures, started_at, loaded_at = fetch_all_areas(
        area_records,
        key_fingerprint,
        api_key,
    )


# ----------------------------------------
# 8. 조회 결과 요약
# ----------------------------------------
st.subheader("실시간 조회 현황")

st.write(
    "**데이터 불러온 시각:** "
    f"{loaded_at.strftime('%Y-%m-%d %H:%M:%S')} "
    "(한국 시간)"
)

st.caption(
    "API 응답은 5분 동안 캐시합니다. "
    "분류 체크박스를 변경해도 다시 조회하지 않습니다. "
    "각 장소의 인구 기준 시각은 지도에서 별도로 확인할 수 있습니다."
)

col1, col2, col3 = st.columns(3)

col1.metric("조회 대상", len(areas))
col2.metric("조회 성공", len(results))
col3.metric("조회 실패", len(failures))

if failures:
    st.warning(
        f"{len(failures)}곳의 정보를 불러오지 못했습니다. "
        "실패한 장소는 인구 0명으로 처리하지 않고 "
        "지도에서도 제외했습니다."
    )

    with st.expander("조회 실패 장소 확인"):
        st.dataframe(
            pd.DataFrame(failures),
            hide_index=True,
            use_container_width=True,
        )

if not results:
    st.error(
        "표시할 실시간 인구 정보가 없습니다. "
        "인증키, API 사용 권한, 서버 상태를 확인하세요."
    )
    st.stop()


# ----------------------------------------
# 9. 지도 데이터 준비
# ----------------------------------------
df = pd.DataFrame(results)

df["인구최소"] = pd.to_numeric(
    df["인구최소"], errors="coerce"
)
df["인구최대"] = pd.to_numeric(
    df["인구최대"], errors="coerce"
)
df["비상주비율"] = pd.to_numeric(
    df["비상주비율"], errors="coerce"
)

# 혼잡도에 따라 초록색부터 빨간색 사용
df["color"] = df["혼잡도"].apply(
    lambda level: COLORS.get(level, UNKNOWN_COLOR)
)

# 인구 최댓값에 비례해 원의 면적을 조절
# 지도에서 보기 좋도록 반지름에는 제곱근 비율 적용
valid_population = df["인구최대"].dropna()

max_population = (
    valid_population.max()
    if not valid_population.empty
    else None
)


def get_radius(value):
    if pd.isna(value) or max_population is None:
        return 80

    if max_population <= 0:
        return 80

    proportion = max(0, value) / max_population

    return 80 + 520 * (proportion ** 0.5)


df["radius"] = df["인구최대"].apply(get_radius)

# 지도 툴팁용 문자열
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

df["인구기준시각"] = (
    df["인구기준시각"]
    .fillna("확인할 수 없음")
    .replace("", "확인할 수 없음")
)


# ----------------------------------------
# 10. 장소 분류 체크박스
# ----------------------------------------
st.subheader("장소 분류 선택")

st.write(
    "표시할 분류를 선택하세요. "
    "공원만 선택하거나 발달상권만 선택할 수도 있습니다."
)

categories = sorted(df["분류"].dropna().unique())

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
    f"**현재 지도에 표시할 장소:** "
    f"{len(filtered_df)}곳"
)


# ----------------------------------------
# 11. 혼잡도 색상 안내
# ----------------------------------------
st.markdown(
    """
    **혼잡도 색상**

    🟢 여유　 🟡 보통　 🟠 약간 붐빔　 🔴 붐빔

    회색은 혼잡도 등급을 확인할 수 없는 장소입니다.
    """
)

st.caption(
    "원의 크기는 실시간 인구 최댓값을 기준으로 "
    "조절합니다. 인구 최댓값이 없는 장소는 "
    "기본 크기로 표시합니다."
)


# ----------------------------------------
# 12. 서울 지도 표시
# ----------------------------------------
st.subheader("서울 실시간 인구 지도")

if filtered_df.empty:
    st.info(
        "선택한 분류에 해당하는 장소가 없습니다. "
        "하나 이상의 분류를 선택해 주세요."
    )

else:
    layer = pdk.Layer(
        "ScatterplotLayer",
        data=filtered_df,
        get_position="[경도, 위도]",
        get_fill_color="color",
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

    view_state = pdk.ViewState(
        latitude=37.5665,
        longitude=126.9780,
        zoom=10.4,
        pitch=0,
    )

    tooltip = {
        "html": (
            "<b>{지역명}</b><br/>"
            "분류: {분류}<br/>"
            "혼잡도: {혼잡도}<br/>"
            "실시간 인구: {인구범위표시}<br/>"
            "비상주 인구 비율: {비상주비율표시}<br/>"
            "인구 데이터 기준 시각: {인구기준시각}"
        ),
        "style": {
            "backgroundColor": "#20242b",
            "color": "white",
            "fontSize": "13px",
            "padding": "10px",
        },
    }

    deck = pdk.Deck(
        layers=[layer],
        initial_view_state=view_state,
        tooltip=tooltip,
        map_style="light",
    )

    st.pydeck_chart(
        deck,
        use_container_width=True,
        height=650,
    )


# ----------------------------------------
# 13. 추가 안내
# ----------------------------------------
st.info(
    "실시간 인구는 정확한 인원수가 아니라 "
    "서울시가 추정한 인구 범위입니다. "
    "각 장소의 인구 데이터 기준 시각이 서로 다를 수 있으므로 "
    "지도에서 확인해 주세요."
)

st.caption(
    "데이터 출처: 서울특별시 실시간 도시데이터 Open API"
)

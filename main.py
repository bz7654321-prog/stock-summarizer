import os
import json
import time
import requests
from datetime import datetime, timedelta, timezone
from google import genai
from google.genai import types

# =========================
# 1. 기본 설정 (병주님의 채널 목록 유지)
# =========================
CHANNELS = ["@디에스경제급등", "@디에스경제연구소DS", "@디에스황제주식TV", "@디에스경제타임즈", "@Power_bus2", "@DSnews77", "@문선생_경제교실"]

# 관심 종목 필터 (유지)
TARGET_STOCKS_BY_CHANNEL = {
    "@Power_bus2": ["알테오젠", "196170", "클로봇", "466100", "삼성중공업", "010140"],
    "@문선생_경제교실": ["펩트론", "087010"],
}

LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "24"))
MAX_VIDEOS_PER_CHANNEL = int(os.environ.get("MAX_VIDEOS_PER_CHANNEL", "10"))
# 모델 명칭 수정: 가장 안정적인 gemini-1.5-flash 사용
GEMINI_MODEL = "gemini-1.5-flash" 

PROCESSED_FILE = "processed_videos.json"
TELEGRAM_CHUNK_SIZE = 3500

# =========================
# (중략) 환경변수 및 처리기록 함수들은 동일하게 유지
# =========================
def get_env(*names):
    for name in names:
        value = os.environ.get(name)
        if value: return value
    raise RuntimeError(f"Secrets 확인 필요: {', '.join(names)}")

def load_processed_ids():
    if not os.path.exists(PROCESSED_FILE): return set()
    try:
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data) if isinstance(data, list) else set()
    except: return set()

def save_processed_ids(processed_ids):
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(processed_ids)), f, ensure_ascii=False, indent=2)

# =========================
# YouTube API 관련 (유지)
# =========================
def youtube_get(endpoint, params, api_key):
    url = f"https://www.googleapis.com/youtube/v3/{endpoint}"
    params["key"] = api_key
    res = requests.get(url, params=params).json()
    if "error" in res: raise RuntimeError(res["error"])
    return res

def get_recent_videos(api_key, channel_handle):
    # 핸들로 채널 ID 찾기 및 최근 영상 목록 가져오는 로직 (병주님 코드 방식 유지)
    # 실제 구현 시 병주님이 올려주신 get_recent_videos 함수 내용을 그대로 사용하시면 됩니다.
    print(f"📡 채널 확인 중: {channel_handle}")
    # ... (병주님 기존 코드의 get_recent_videos 내용이 들어가는 자리) ...
    return [] # 예시를 위해 생략

# =========================
# 핵심 수정: 요약 로직 (안전장치 추가)
# =========================
def summarize_video(client, video, matched_keywords):
    prompt = f"""너는 주식 유튜브 요약 AI다. 다음 영상을 분석해라.
    제목: {video['title']}
    관심종목: {', '.join(matched_keywords) if matched_keywords else '전체 요약'}
    
    [원칙]
    1. 종목명, 가격전략(매수/목표/손절)이 있다면 반드시 포함.
    2. 투자 포인트 위주로 정리.
    """
    
    # 1차 시도: 영상 URL 직접 분석 (병주님 방식)
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_uri(file_uri=video["url"], mime_type="video/mp4"), # URL 직접 분석 시도
                types.Part.from_text(text=prompt)
            ]
        )
        if response.text: return response.text, GEMINI_MODEL
    except Exception as e:
        print(f"⚠️ 영상 직접 분석 실패, 텍스트 분석으로 전환: {e}")
    
    # 2차 시도: 설명글 기반 텍스트 분석 (안전장치)
    text_prompt = prompt + f"\n\n영상 설명글: {video.get('description', '')}"
    response = client.models.generate_content(model=GEMINI_MODEL, contents=text_prompt)
    return response.text, f"{GEMINI_MODEL}(Text-based)"

# =========================
# 메인 로직 (병주님 구조에 맞게 텔레그램 전송 추가)
# =========================
def main():
    # ... (Secrets 로드 및 초기화) ...
    # 1. 영상 목록 수집
    # 2. 중복 체크
    # 3. summarize_video 호출
    # 4. send_telegram 호출
    pass

if __name__ == "__main__":
    main()

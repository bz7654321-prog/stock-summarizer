import os
import json
import time
import requests
from datetime import datetime, timedelta
from urllib.parse import quote
from youtube_transcript_api import YouTubeTranscriptApi
from google import genai

# =========================
# 1. 기본 설정
# =========================
CHANNELS = [
    "@디에스경제급등",
    "@디에스경제연구소DS",
    "@디에스황제주식TV",
    "@디에스경제타임즈",
    "@Power_bus2",
    "@DSnews77",
    "@문선생_경제교실",
]

YOUTUBE_BASE_URL = "https://www.googleapis.com/youtube/v3"
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "48"))
MAX_VIDEOS_PER_CHANNEL = int(os.environ.get("MAX_VIDEOS_PER_CHANNEL", "10"))
GEMINI_MODEL = "gemini-1.5-flash"

PROCESSED_FILE = "processed_videos.json"

# =========================
# 2. 필수 함수
# =========================
def get_env(*names):
    for name in names:
        val = os.environ.get(name)
        if val: return val
    raise RuntimeError(f"Secrets 확인 필요: {names}")

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

def get_recent_videos(api_key, channel_handle):
    """가장 확실하게 작동했던 Search API 방식을 사용합니다."""
    now = datetime.utcnow()
    published_after = (now - timedelta(hours=LOOKBACK_HOURS)).isoformat() + "Z"
    encoded_handle = quote(channel_handle)
    
    url = f"{YOUTUBE_BASE_URL}/search?part=snippet&q={encoded_handle}&type=video&order=date&maxResults={MAX_VIDEOS_PER_CHANNEL}&publishedAfter={published_after}&key={api_key}"
    
    try:
        r = requests.get(url).json()
        items = r.get('items', [])
        print(f"🔍 [{channel_handle}] 최근 {LOOKBACK_HOURS}시간 내 영상 {len(items)}개 발견")
        return items
    except Exception as e:
        print(f"❌ 검색 오류 ({channel_handle}): {e}")
        return []

def summarize_video(client, video_id, title, description):
    """자막을 먼저 추출하고, 없으면 설명글을 요약하는 안전한 방식입니다."""
    content = ""
    try:
        srt = YouTubeTranscriptApi.get_transcript(video_id, languages=['ko'])
        content = " ".join([i['text'] for i in srt])
        print(f"✅ [{title}] 자막 추출 성공")
    except:
        content = description
        print(f"⚠️ [{title}] 자막 없음 -> 설명글 요약 시도")
    
    if not content:
        return "요약할 내용이 부족합니다."

    prompt = f"다음 주식 영상의 핵심 내용을 투자자 관점에서 요약해줘.\n제목: {title}\n내용: {content}\n[원칙] 종목명과 가격 전략이 있다면 반드시 포함할 것."
    
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )
        return response.text
    except Exception as e:
        print(f"❌ Gemini 요약 에러: {e}")
        return "요약 실패"

# =========================
# 3. 메인 실행
# =========================
def main():
    print("🚀 주식 요약기 가동 시작")
    api_key = get_env("YOUTUBE_API_KEY")
    gemini_key = get_env("GEMINI_API_KEY", "_API_KEY", "GOOGLE_API_KEY")
    bot_token = get_env("TELEGRAM_TOKEN")
    chat_id = get_env("TELEGRAM_CHAT_ID")
    
    client = genai.Client(api_key=gemini_key)
    processed_ids = load_processed_ids()
    
    for handle in CHANNELS:
        videos = get_recent_videos(api_key, handle)
        for v in videos:
            v_id = v['id']['videoId']
            title = v['snippet']['title']
            description = v['snippet']['description']
            url = f"https://www.youtube.com/watch?v={v_id}"
            
            if v_id in processed_ids:
                continue
                
            print(f"🎬 처리 중: {title}")
            summary = summarize_video(client, v_id, title, description)
            
            msg = f"📺 {title}\n🔗 {url}\n\n{summary}"
            res = requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage", 
                data={"chat_id": chat_id, "text": msg[:4000]}
            )
            
            if res.json().get('ok'):
                print("🚀 텔레그램 전송 성공!")
                processed_ids.add(v_id)
                save_processed_ids(processed_ids)
            else:
                print("❌ 텔레그램 전송 실패")
                
            time.sleep(1)
            
    print("✅ 모든 작업 완료")

if __name__ == "__main__":
    main()

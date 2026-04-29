import os
import json
import time
import requests
from datetime import datetime, timedelta, timezone

from youtube_transcript_api import YouTubeTranscriptApi
from google import genai
from google.genai import types

# =========================
# 1. 기본 설정
# =========================

CHANNELS = [
    "@디에스경제연구소DS",
    
]

TARGET_STOCKS_BY_CHANNEL = {
    "@디에스경제연구소DS": [],
  
}

YOUTUBE_BASE_URL = "https://www.googleapis.com/youtube/v3"

LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "24"))
MAX_VIDEOS_PER_CHANNEL = int(os.environ.get("MAX_VIDEOS_PER_CHANNEL", "10"))

# 💡 외부 환경변수 무시하고 무조건 설정된 모델로 강제 고정!
# (Pro의 깊은 분석을 원하시면 "gemini-1.5-pro", 빠른 속도를 원하시면 "gemini-2.5-flash"로 수정하세요)
GEMINI_MODEL = "gemini-2.5-flash"

PROCESSED_FILE = "processed_videos.json"
TELEGRAM_CHUNK_SIZE = 3500

# =========================
# 2. 예외 및 환경변수
# =========================

class QuotaExceededError(Exception):
    pass

def get_env(*names):
    for name in names:
        value = os.environ.get(name)
        if value: return value
    raise RuntimeError(f"Secrets에 다음 값 중 하나가 필요합니다: {', '.join(names)}")

# =========================
# 3. 데이터 처리 및 API 통신
# =========================

def load_processed_ids():
    if not os.path.exists(PROCESSED_FILE): return set()
    try:
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data) if isinstance(data, list) else set()
    except: return set()

def save_processed_ids(processed_ids):
    try:
        with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(processed_ids)), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 처리 기록 저장 실패: {e}")

def youtube_get(endpoint, params, api_key):
    params = dict(params)
    params["key"] = api_key
    url = f"{YOUTUBE_BASE_URL}/{endpoint}"
    response = requests.get(url, params=params, timeout=20).json()
    if "error" in response: raise RuntimeError(f"YouTube API 오류: {response.get('error')}")
    return response

def get_channel_info(api_key, channel_handle):
    h = channel_handle.replace("@", "").strip()
    try:
        data = youtube_get("channels", {"part": "snippet,contentDetails", "forHandle": h}, api_key)
        if data.get("items"): return data["items"][0]
    except Exception as e:
        print(f"⚠️ 채널 핸들 조회 실패: {channel_handle} / {e}")
    return None

def get_recent_videos(api_key, channel_handle):
    channel = get_channel_info(api_key, channel_handle)
    if not channel: return []
    channel_title = channel["snippet"]["title"]
    uploads_playlist_id = channel["contentDetails"]["relatedPlaylists"]["uploads"]
    print(f"\n📡 채널 확인: {channel_title}")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    videos = []
    data = youtube_get("playlistItems", {"part": "snippet,contentDetails", "playlistId": uploads_playlist_id, "maxResults": MAX_VIDEOS_PER_CHANNEL}, api_key)

    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        content_details = item.get("contentDetails", {})
        video_id = content_details.get("videoId")
        published_at = content_details.get("videoPublishedAt") or snippet.get("publishedAt")
        
        if not video_id or not published_at: continue
        pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if pub_dt < cutoff: continue

        videos.append({
            "channel_handle": channel_handle,
            "channel_title": channel_title,
            "video_id": video_id,
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "published_at": published_at,
            "url": f"https://www.youtube.com/watch?v={video_id}",
        })
    return videos

# =========================
# 4. 종목 필터 및 프롬프트
# =========================

def clean_text(text): return text.replace("\n", " ").replace("\r", " ").replace("\t", " ").strip() if text else ""
def normalize_text(text): return clean_text(text).lower().replace(" ", "")
def get_target_keywords(channel_handle): return TARGET_STOCKS_BY_CHANNEL.get(channel_handle, [])
def channel_has_filter(channel_handle): return len(get_target_keywords(channel_handle)) > 0

def quick_match_by_title_description(channel_handle, title, description):
    keywords = get_target_keywords(channel_handle)
    if not keywords: return []
    combined = normalize_text(f"{title} {description}")
    return [kw for kw in keywords if normalize_text(kw) in combined]

def make_video_prompt(video, matched_keywords):
    target_keywords = get_target_keywords(video["channel_handle"])
    filter_instruction = f"[관심 종목 필터]\n이 채널은 아래 관심 종목이 영상에서 실제로 다뤄질 때만 요약한다.\n관심 종목: {', '.join(target_keywords)}\n영상 전체를 확인한 뒤, 관심 종목이 실질적으로 다뤄지지 않았다면 다른 설명 없이 정확히 아래 문장만 출력해라.\nSKIP_VIDEO_NO_TARGET_STOCK" if target_keywords else "[관심 종목 필터]\n이 채널은 전체 영상 요약 대상이다.\n영상에서 핵심 추천 종목 또는 분석 종목만 중심으로 요약해라."

    return f"""
너는 주식 유튜브 영상을 투자자 관점에서 요약하는 AI다.
{filter_instruction}

[요약 원칙]
1. 설명글이나 제목만 보고 요약하지 말고, 영상에서 실제로 말한 내용을 기준으로 정리해라.
2. 종목명, 종목코드, 매수가, 목표가, 손절가, 지지선, 저항선이 나오면 반드시 적어라.
3. 가격이 영상에서 나오지 않았다면 "영상 내 언급 없음"이라고 적어라.
4. 종목코드를 확신하지 못하면 "종목코드 확인 필요"라고 적어라.
5. 단순 언급 종목을 길게 나열하지 마라.
6. 추천 또는 집중 분석한 핵심 종목만 자세히 정리해라.
7. 영상에서 나온 시간대를 가능하면 [MM:SS] 형식으로 붙여라.
8. 과장성 표현은 그대로 믿지 말고 리스크에 따로 적어라.
9. 결과는 깔끔한 투자 브리핑 형식으로 작성해라.
""".strip()

# =========================
# 5. 하이브리드 요약 로직
# =========================

def summarize_video_pro(client, video, matched_keywords):
    prompt = make_video_prompt(video, matched_keywords)
    video_id = video["video_id"]
    video_url = video["url"]
    
    # 1. 자막 추출 시도
    transcript_text = ""
    try:
        srt = YouTubeTranscriptApi.get_transcript(video_id, languages=['ko'])
        transcript_text = " ".join([i['text'] for i in srt])
        print("✅ 자막 추출 성공 (데이터 경량화)")
    except:
        print("⚠️ 자막 없음 -> 유튜브 링크 직접 분석 모드 전환")
        transcript_text = None

    # 2. 구글 API 할당량 보호를 위해 영상당 30초 대기 (매우 중요!)
    print(f"⏳ 구글 API 서버 할당량 보호를 위해 30초 대기 중...")
    time.sleep(30)

    try:
        if transcript_text:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt + f"\n\n[영상 스크립트 내용]\n{transcript_text[:20000]}",
                config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=3500)
            )
            return getattr(response, "text", "요약 실패"), f"{GEMINI_MODEL} (자막분석)"
        else:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    types.Part.from_uri(file_uri=video_url, mime_type="video/mp4"),
                    types.Part.from_text(text=prompt)
                ],
                config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=3500)
            )
            return getattr(response, "text", "요약 실패"), f"{GEMINI_MODEL} (영상직접분석)"
            
    except Exception as e:
        error_text = str(e).lower()
        if "429" in error_text or "quota" in error_text:
            raise QuotaExceededError(str(e))
        raise RuntimeError(f"모델 요약 에러: {e}")

# =========================
# 6. 텔레그램 전송 (긴 글 쪼개기 적용)
# =========================

def split_message(text, max_len=3500):
    chunks = []
    while len(text) > max_len:
        # 문맥이 끊기지 않도록 최대한 줄바꿈에서 자르기
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()
    if text:
        chunks.append(text)
    return chunks

def send_telegram(bot_token, chat_id, text):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    chunks = split_message(text)
    
    for i, chunk in enumerate(chunks, start=1):
        # 내용이 길어서 쪼개진 경우 메시지 맨 위에 [1/2] 번호 달아주기
        if len(chunks) > 1:
            chunk = f"[{i}/{len(chunks)}]\n\n{chunk}"
            
        try:
            requests.post(url, data={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}, timeout=20)
            time.sleep(1) # 연속 전송 시 텔레그램 서버가 화내지 않게 1초 쉬기
        except Exception as e:
            print(f"❌ 텔레그램 전송 에러: {e}")
            return False
            
    return True

# =========================
# 7. 메인 실행
# =========================

def main():
    print(f"🚀 주식 영상 요약 시작 ({GEMINI_MODEL} + 30초 딜레이 적용)")
    
    youtube_api_key = get_env("YOUTUBE_API_KEY")
    gemini_key = get_env("GEMINI_API_KEY", "_API_KEY", "GOOGLE_API_KEY")
    telegram_token = get_env("TELEGRAM_TOKEN")
    telegram_chat_id = get_env("TELEGRAM_CHAT_ID")

    client = genai.Client(api_key=gemini_key)
    processed_ids = load_processed_ids()
    all_videos = []

    for channel in CHANNELS:
        try:
            all_videos.extend(get_recent_videos(youtube_api_key, channel))
        except Exception as e:
            print(f"❌ {channel} 검색 실패: {e}")

    unique_videos = []
    seen = set()
    for v in all_videos:
        if v["video_id"] not in seen:
            seen.add(v["video_id"])
            unique_videos.append(v)

    print(f"\n📦 확인 대상 영상: {len(unique_videos)}개")

    for video in unique_videos:
        video_id = video["video_id"]
        if video_id in processed_ids:
            continue

        print("\n" + "=" * 60)
        print(f"🎬 처리 중: [{video['channel_title']}] {video['title']}")
        matched_keywords = quick_match_by_title_description(video["channel_handle"], video["title"], video.get("description", ""))

        try:
            summary, used_method = summarize_video_pro(client, video, matched_keywords)

            if "SKIP_VIDEO_NO_TARGET_STOCK" in summary:
                print("⏭️ 관심 종목이 없어서 건너뜁니다.")
            else:
                final_message = f"📺 {video['title']}\n🔗 {video['url']}\n🤖 {used_method}\n\n{summary}"
                if send_telegram(telegram_token, telegram_chat_id, final_message):
                    print("🚀 텔레그램 전송 완료!")
                
            processed_ids.add(video_id)
            save_processed_ids(processed_ids)
            
        except QuotaExceededError:
            print("🛑 구글 할당량 초과(429)! 오늘 몫을 다 썼거나 딜레이가 더 필요합니다. 중단합니다.")
            break
        except Exception as e:
            print(f"❌ 요약 실패: {e}")

    print("\n✅ 모든 작업 완료")

if __name__ == "__main__":
    main()

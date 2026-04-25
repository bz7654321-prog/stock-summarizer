import os
import json
import time
import requests
from datetime import datetime, timedelta, timezone

from google import genai
from google.genai import types
from youtube_transcript_api import YouTubeTranscriptApi


# =========================
# 1. 설정
# =========================

CHANNELS = [
    "@디에스경제급등",
    "@디에스경제연구소DS",
    "@디에스황제주식TV",
    "@디에스경제타임즈",
    "@Power_bus2",
    "@DSnews77",
]

YOUTUBE_BASE_URL = "https://www.googleapis.com/youtube/v3"

# 최근 몇 시간 안의 영상을 볼지
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "48"))

# 채널당 최대 몇 개 영상까지 확인할지
MAX_VIDEOS_PER_CHANNEL = int(os.environ.get("MAX_VIDEOS_PER_CHANNEL", "10"))

# 기본 Gemini 모델
# 필요하면 Secrets/환경변수에 GEMINI_MODEL=gemini-2.5-flash 이런 식으로 직접 지정 가능
DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# 이미 보낸 영상 저장 파일
PROCESSED_FILE = os.environ.get("PROCESSED_FILE", "processed_videos.json")

# 텔레그램 메시지 길이 제한 대비
TELEGRAM_CHUNK_SIZE = 3500


# =========================
# 2. 공통 유틸
# =========================

def get_env(*names):
    """
    여러 환경변수 이름 중 먼저 발견되는 값을 반환.
    예: get_env("GEMINI_API_KEY", "_API_KEY")
    """
    for name in names:
        value = os.environ.get(name)
        if value:
            return value

    joined = ", ".join(names)
    raise RuntimeError(f"Secrets/환경변수에 다음 값 중 하나가 필요합니다: {joined}")


def parse_youtube_datetime(value):
    """
    YouTube API의 ISO 시간 문자열을 datetime으로 변환.
    """
    if not value:
        return None

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def clean_text(text):
    if not text:
        return ""

    return (
        text.replace("\n", " ")
        .replace("\r", " ")
        .replace("\t", " ")
        .strip()
    )


def load_processed_ids():
    """
    이미 텔레그램으로 보낸 영상 ID 목록을 불러온다.
    RESET_PROCESSED=1 을 환경변수에 넣으면 기록 무시 가능.
    """
    if os.environ.get("RESET_PROCESSED") == "1":
        print("⚠️ RESET_PROCESSED=1 설정됨: 기존 전송 기록을 무시합니다.")
        return set()

    if not os.path.exists(PROCESSED_FILE):
        return set()

    try:
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return set(data)

        return set()
    except Exception as e:
        print(f"⚠️ 전송 기록 파일 읽기 실패: {e}")
        return set()


def save_processed_ids(processed_ids):
    try:
        with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(processed_ids)), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 전송 기록 저장 실패: {e}")


# =========================
# 3. YouTube API
# =========================

def youtube_get(endpoint, params, api_key):
    """
    YouTube Data API GET 요청.
    """
    params = dict(params)
    params["key"] = api_key

    url = f"{YOUTUBE_BASE_URL}/{endpoint}"

    try:
        response = requests.get(url, params=params, timeout=20)
    except Exception as e:
        raise RuntimeError(f"YouTube API 요청 실패: {e}")

    try:
        data = response.json()
    except Exception:
        raise RuntimeError(f"YouTube API 응답을 JSON으로 읽을 수 없습니다: {response.text[:500]}")

    if response.status_code != 200 or "error" in data:
        raise RuntimeError(f"YouTube API 오류: {data.get('error', data)}")

    return data


def get_channel_info(api_key, channel_ref):
    """
    채널 핸들(@...) 또는 채널 ID(UC...)로 채널 정보를 찾는다.
    """
    channel_ref = channel_ref.strip()

    attempts = []

    if channel_ref.startswith("UC"):
        attempts.append(("id", channel_ref))
    else:
        attempts.append(("forHandle", channel_ref))

        if channel_ref.startswith("@"):
            attempts.append(("forHandle", channel_ref[1:]))

    for param_name, param_value in attempts:
        try:
            data = youtube_get(
                "channels",
                {
                    "part": "snippet,contentDetails",
                    param_name: param_value,
                },
                api_key,
            )

            items = data.get("items", [])

            if items:
                return items[0]

        except Exception as e:
            print(f"⚠️ [{channel_ref}] channels.list 시도 실패: {param_name}={param_value} / {e}")

    # 마지막 fallback: 검색으로 채널 찾기
    try:
        data = youtube_get(
            "search",
            {
                "part": "snippet",
                "q": channel_ref,
                "type": "channel",
                "maxResults": 1,
            },
            api_key,
        )

        items = data.get("items", [])

        if not items:
            return None

        channel_id = items[0]["snippet"]["channelId"]

        data = youtube_get(
            "channels",
            {
                "part": "snippet,contentDetails",
                "id": channel_id,
            },
            api_key,
        )

        items = data.get("items", [])

        if items:
            print(f"⚠️ [{channel_ref}] 정확 핸들 조회 실패 -> 검색 결과 채널 사용")
            return items[0]

    except Exception as e:
        print(f"⚠️ [{channel_ref}] 채널 검색 fallback 실패: {e}")

    return None


def get_recent_videos(api_key, channel_ref):
    """
    채널의 업로드 재생목록에서 최근 LOOKBACK_HOURS 시간 안의 영상만 가져온다.
    """
    channel_info = get_channel_info(api_key, channel_ref)

    if not channel_info:
        print(f"❌ [{channel_ref}] 채널을 찾지 못했습니다.")
        return []

    channel_title = channel_info.get("snippet", {}).get("title", channel_ref)

    uploads_playlist_id = (
        channel_info
        .get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads")
    )

    if not uploads_playlist_id:
        print(f"❌ [{channel_ref}] 업로드 재생목록 ID를 찾지 못했습니다.")
        return []

    print(f"\n📡 채널 확인: {channel_title} ({channel_ref})")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    videos = []
    next_page_token = None
    hit_old_video = False

    while len(videos) < MAX_VIDEOS_PER_CHANNEL:
        params = {
            "part": "snippet,contentDetails",
            "playlistId": uploads_playlist_id,
            "maxResults": min(50, MAX_VIDEOS_PER_CHANNEL - len(videos)),
        }

        if next_page_token:
            params["pageToken"] = next_page_token

        data = youtube_get("playlistItems", params, api_key)

        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            content_details = item.get("contentDetails", {})

            video_id = content_details.get("videoId")
            title = snippet.get("title", "")
            description = snippet.get("description", "")
            published_at = content_details.get("videoPublishedAt") or snippet.get("publishedAt")

            if not video_id:
                continue

            if title in ["Private video", "Deleted video"]:
                continue

            published_dt = parse_youtube_datetime(published_at)

            if not published_dt:
                continue

            if published_dt < cutoff:
                hit_old_video = True
                continue

            videos.append({
                "channel_ref": channel_ref,
                "channel_title": channel_title,
                "video_id": video_id,
                "title": title,
                "description": description,
                "published_at": published_at,
                "url": f"https://www.youtube.com/watch?v={video_id}",
            })

        next_page_token = data.get("nextPageToken")

        if hit_old_video or not next_page_token:
            break

    print(f"🔍 [{channel_title}] 최근 {LOOKBACK_HOURS}시간 영상: {len(videos)}개 발견")

    return videos


# =========================
# 4. 유튜브 자막
# =========================

def transcript_items_to_text(items):
    """
    youtube-transcript-api 결과를 문자열로 합친다.
    구버전 dict 형식과 신버전 객체 형식 둘 다 대응.
    """
    parts = []

    for item in items:
        if isinstance(item, dict):
            text = item.get("text", "")
        else:
            text = getattr(item, "text", "")

        text = clean_text(text)

        if text:
            parts.append(text)

    return " ".join(parts).strip()


def get_transcript_text(video_id):
    """
    자막 추출.
    한국어 우선, 없으면 영어, 그래도 없으면 번역 가능한 자막을 한국어로 번역 시도.
    """
    language_attempts = [
        ["ko"],
        ["ko-KR"],
        ["ko", "ko-KR"],
        ["en"],
        ["en-US"],
        ["ko", "ko-KR", "en", "en-US"],
    ]

    # 구버전 방식
    for languages in language_attempts:
        try:
            items = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
            text = transcript_items_to_text(items)

            if text:
                return text

        except Exception:
            pass

    # 신버전 방식 대응
    for languages in language_attempts:
        try:
            api = YouTubeTranscriptApi()
            items = api.fetch(video_id, languages=languages)
            text = transcript_items_to_text(items)

            if text:
                return text

        except Exception:
            pass

    # 번역 가능한 자막이 있으면 한국어 번역 시도
    try:
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        except Exception:
            api = YouTubeTranscriptApi()
            transcript_list = api.list(video_id)

        for transcript in transcript_list:
            try:
                if getattr(transcript, "is_translatable", False):
                    translated = transcript.translate("ko")
                    items = translated.fetch()
                    text = transcript_items_to_text(items)

                    if text:
                        return text
            except Exception:
                continue

    except Exception:
        pass

    return ""


# =========================
# 5. Gemini 요약
# =========================

def build_prompt(video):
    title = video["title"]
    channel_title = video["channel_title"]
    published_at = video["published_at"]
    video_url = video["url"]
    content = video["content"]

    # 너무 긴 자막은 자르기
    content = content[:25000]

    return f"""
너는 주식 유튜브 영상을 정리하는 투자 보조 AI다.
아래 영상 내용을 투자자 관점에서 한국어로 정리해라.

[매우 중요한 원칙]
1. 영상에서 직접 말한 내용과 네가 추론한 내용을 구분해라.
2. 종목명과 종목코드를 함부로 지어내지 마라.
3. 종목코드를 확신하지 못하면 반드시 "종목코드 확인 필요"라고 써라.
4. 매수 추천처럼 단정하지 마라.
5. "급등", "상한가", "세력", "작전", "재료" 같은 표현이 나오면 과장 가능성을 따로 표시해라.
6. 투자자가 실제로 확인해야 할 체크포인트를 마지막에 정리해라.
7. 내용이 부족하면 억지로 요약하지 말고 "내용 부족"이라고 표시해라.

[출력 형식]

📌 영상 정보
- 채널:
- 제목:
- 게시일:
- 링크:

🧾 한 줄 요약

📈 언급 종목
- 종목명 / 종목코드 / 영상에서 언급된 이유
- 종목코드가 불확실하면 "확인 필요"

🔥 상승 논리
- 영상에서 제시한 상승 근거

⚠️ 리스크
- 과장 가능성
- 확인되지 않은 주장
- 투자자가 조심해야 할 부분

✅ 투자자 체크포인트
- 실제 매수 전 확인할 것
- 차트/거래량/공시/실적/테마 지속성 관점

[영상 정보]
채널: {channel_title}
제목: {title}
게시일: {published_at}
링크: {video_url}

[영상 내용]
{content}
""".strip()


def get_model_candidates():
    """
    모델 404 오류가 나면 다음 모델로 자동 시도.
    """
    candidates = [
        DEFAULT_GEMINI_MODEL,
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-3-flash-preview",
    ]

    unique = []

    for model_name in candidates:
        if model_name and model_name not in unique:
            unique.append(model_name)

    return unique


def summarize_with_gemini(client, prompt):
    """
    Gemini 요약.
    특정 모델이 404 나면 fallback 모델을 순서대로 시도한다.
    """
    last_error = None

    for model_name in get_model_candidates():
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=2500,
                ),
            )

            text = getattr(response, "text", "")

            if text and text.strip():
                return text.strip(), model_name

            last_error = RuntimeError(f"{model_name} 모델 응답이 비어 있습니다.")

        except Exception as e:
            last_error = e
            error_text = str(e).lower()

            if (
                "404" in error_text
                or "not found" in error_text
                or "not supported" in error_text
                or "not_found" in error_text
            ):
                print(f"⚠️ Gemini 모델 사용 실패: {model_name} -> 다음 모델 시도")
                continue

            raise

    raise RuntimeError(f"모든 Gemini 모델 시도 실패: {last_error}")


# =========================
# 6. 텔레그램
# =========================

def split_message(text, max_len=TELEGRAM_CHUNK_SIZE):
    """
    텔레그램 메시지 길이 제한 때문에 긴 메시지를 나눈다.
    """
    chunks = []

    while len(text) > max_len:
        split_at = text.rfind("\n", 0, max_len)

        if split_at == -1:
            split_at = max_len

        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()

    if text:
        chunks.append(text)

    return chunks


def send_telegram(bot_token, chat_id, text):
    send_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    chunks = split_message(text)

    for idx, chunk in enumerate(chunks, start=1):
        if len(chunks) > 1:
            chunk = f"[{idx}/{len(chunks)}]\n\n{chunk}"

        try:
            response = requests.post(
                send_url,
                data={
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                },
                timeout=20,
            )
        except Exception as e:
            print(f"❌ 텔레그램 요청 실패: {e}")
            return False

        try:
            result = response.json()
        except Exception:
            print(f"❌ 텔레그램 응답 JSON 오류: {response.text[:500]}")
            return False

        if not result.get("ok"):
            print(f"❌ 텔레그램 전송 실패: {result.get('description')}")
            return False

        time.sleep(0.5)

    return True


# =========================
# 7. 메인 실행
# =========================

def main():
    print("🚀 주식 유튜브 요약 프로그램 시작")

    youtube_api_key = get_env("YOUTUBE_API_KEY")
    gemini_api_key = get_env("GEMINI_API_KEY", "_API_KEY", "GOOGLE_API_KEY")
    telegram_token = get_env("TELEGRAM_TOKEN")
    telegram_chat_id = get_env("TELEGRAM_CHAT_ID")

    client = genai.Client(api_key=gemini_api_key)

    processed_ids = load_processed_ids()

    total_found = 0
    total_sent = 0
    total_skipped = 0
    total_failed = 0

    all_videos = []

    for channel_ref in CHANNELS:
        try:
            videos = get_recent_videos(youtube_api_key, channel_ref)
            all_videos.extend(videos)
        except Exception as e:
            total_failed += 1
            print(f"❌ [{channel_ref}] 영상 목록 가져오기 실패: {e}")

    # 같은 영상 중복 제거
    unique_videos = []
    seen_ids = set()

    for video in all_videos:
        video_id = video["video_id"]

        if video_id in seen_ids:
            continue

        seen_ids.add(video_id)
        unique_videos.append(video)

    # 최신 영상부터 처리
    unique_videos.sort(key=lambda x: x.get("published_at", ""), reverse=True)

    total_found = len(unique_videos)

    print(f"\n📦 전체 수집 영상: {total_found}개")

    for video in unique_videos:
        video_id = video["video_id"]
        title = video["title"]
        video_url = video["url"]
        channel_title = video["channel_title"]

        print("\n" + "=" * 80)
        print(f"🎬 처리 중: [{channel_title}] {title}")
        print(f"🔗 {video_url}")

        if video_id in processed_ids:
            total_skipped += 1
            print("⏭️ 이미 전송한 영상이라 건너뜀")
            continue

        transcript_text = get_transcript_text(video_id)

        if transcript_text:
            content = transcript_text
            print("✅ 자막 추출 성공")
        else:
            content = video.get("description", "")
            print("⚠️ 자막 없음 -> 설명글 사용")

        content = clean_text(content)

        if not content or len(content) < 20:
            total_skipped += 1
            print("⚠️ 요약할 내용이 너무 적어 건너뜀")
            continue

        video["content"] = content

        try:
            prompt = build_prompt(video)
            summary, used_model = summarize_with_gemini(client, prompt)

            message = f"""
📺 {title}
📡 {channel_title}
🔗 {video_url}
🤖 사용 모델: {used_model}

{summary}
""".strip()

            ok = send_telegram(telegram_token, telegram_chat_id, message)

            if ok:
                total_sent += 1
                processed_ids.add(video_id)
                save_processed_ids(processed_ids)
                print("🚀 텔레그램 전송 완료")
            else:
                total_failed += 1
                print("❌ 텔레그램 전송 실패")

        except Exception as e:
            total_failed += 1
            print(f"❌ 요약/전송 중 오류 발생: {e}")

    print("\n" + "=" * 80)
    print("✅ 실행 완료")
    print(f"📦 발견 영상: {total_found}개")
    print(f"🚀 전송 완료: {total_sent}개")
    print(f"⏭️ 건너뜀: {total_skipped}개")
    print(f"❌ 실패: {total_failed}개")


if __name__ == "__main__":
    main()

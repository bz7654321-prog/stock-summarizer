import os
import json
import time
import requests
from datetime import datetime, timedelta, timezone

from google import genai
from google.genai import types
from youtube_transcript_api import YouTubeTranscriptApi


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
]

YOUTUBE_BASE_URL = "https://www.googleapis.com/youtube/v3"

# 최근 몇 시간 안의 영상을 가져올지
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "48"))

# 채널당 최대 몇 개 영상까지 확인할지
MAX_VIDEOS_PER_CHANNEL = int(os.environ.get("MAX_VIDEOS_PER_CHANNEL", "10"))

# Gemini 모델
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# 이미 처리한 영상 저장 파일
PROCESSED_FILE = "processed_videos.json"

# 텔레그램 메시지 길이 제한 대응
TELEGRAM_CHUNK_SIZE = 3500


# =========================
# 2. 환경변수 가져오기
# =========================

def get_env(*names):
    """
    여러 환경변수 이름 중 존재하는 값을 가져온다.
    예: get_env("GEMINI_API_KEY", "_API_KEY", "GOOGLE_API_KEY")
    """
    for name in names:
        value = os.environ.get(name)
        if value:
            return value

    raise RuntimeError(f"Secrets에 다음 값 중 하나가 필요합니다: {', '.join(names)}")


# =========================
# 3. 이미 처리한 영상 관리
# =========================

def load_processed_ids():
    """
    이미 요약 전송한 영상 ID를 불러온다.
    단, GitHub Actions에서는 실행마다 파일이 사라질 수 있다.
    """
    if os.environ.get("RESET_PROCESSED") == "1":
        print("⚠️ RESET_PROCESSED=1: 기존 처리 기록 무시")
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
        print(f"⚠️ 처리 기록 파일 읽기 실패: {e}")
        return set()


def save_processed_ids(processed_ids):
    """
    처리 완료한 영상 ID 저장.
    """
    try:
        with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(processed_ids)), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 처리 기록 저장 실패: {e}")


# =========================
# 4. YouTube API
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
        raise RuntimeError(f"YouTube 응답 JSON 변환 실패: {response.text[:500]}")

    if response.status_code != 200 or "error" in data:
        raise RuntimeError(f"YouTube API 오류: {data.get('error', data)}")

    return data


def get_channel_info(api_key, channel_handle):
    """
    @핸들로 채널 정보를 찾는다.
    실패하면 검색 API로 fallback한다.
    """
    handle = channel_handle.strip()

    attempts = []

    if handle.startswith("@"):
        attempts.append(handle)
        attempts.append(handle[1:])
    else:
        attempts.append(handle)

    # 1차: forHandle로 정확 조회
    for h in attempts:
        try:
            data = youtube_get(
                "channels",
                {
                    "part": "snippet,contentDetails",
                    "forHandle": h,
                },
                api_key,
            )

            items = data.get("items", [])

            if items:
                return items[0]

        except Exception as e:
            print(f"⚠️ 채널 핸들 조회 실패: {channel_handle} / {e}")

    # 2차: 채널 검색 fallback
    try:
        data = youtube_get(
            "search",
            {
                "part": "snippet",
                "q": channel_handle,
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
            print(f"⚠️ [{channel_handle}] 정확 핸들 조회 실패 → 검색 결과 채널 사용")
            return items[0]

    except Exception as e:
        print(f"⚠️ 채널 검색 실패: {channel_handle} / {e}")

    return None


def parse_youtube_time(value):
    """
    YouTube 시간 문자열을 datetime으로 변환.
    """
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_recent_videos(api_key, channel_handle):
    """
    채널 업로드 재생목록에서 최근 영상 가져오기.
    """
    channel = get_channel_info(api_key, channel_handle)

    if not channel:
        print(f"❌ 채널을 찾지 못함: {channel_handle}")
        return []

    channel_title = channel.get("snippet", {}).get("title", channel_handle)

    uploads_playlist_id = (
        channel
        .get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads")
    )

    if not uploads_playlist_id:
        print(f"❌ 업로드 재생목록 없음: {channel_title}")
        return []

    print(f"\n📡 채널 확인: {channel_title}")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    videos = []
    next_page_token = None

    while len(videos) < MAX_VIDEOS_PER_CHANNEL:
        params = {
            "part": "snippet,contentDetails",
            "playlistId": uploads_playlist_id,
            "maxResults": min(50, MAX_VIDEOS_PER_CHANNEL - len(videos)),
        }

        if next_page_token:
            params["pageToken"] = next_page_token

        data = youtube_get("playlistItems", params, api_key)

        old_video_found = False

        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            content_details = item.get("contentDetails", {})

            video_id = content_details.get("videoId")
            title = snippet.get("title", "")
            description = snippet.get("description", "")
            published_at = content_details.get("videoPublishedAt") or snippet.get("publishedAt")

            if not video_id or not published_at:
                continue

            if title in ["Private video", "Deleted video"]:
                continue

            published_dt = parse_youtube_time(published_at)

            if published_dt < cutoff:
                old_video_found = True
                continue

            videos.append({
                "channel_handle": channel_handle,
                "channel_title": channel_title,
                "video_id": video_id,
                "title": title,
                "description": description,
                "published_at": published_at,
                "url": f"https://www.youtube.com/watch?v={video_id}",
            })

        next_page_token = data.get("nextPageToken")

        if old_video_found or not next_page_token:
            break

    print(f"🔍 최근 {LOOKBACK_HOURS}시간 영상 {len(videos)}개 발견")

    return videos


# =========================
# 5. 자막 가져오기
# =========================

def clean_text(text):
    if not text:
        return ""

    return (
        text.replace("\n", " ")
        .replace("\r", " ")
        .replace("\t", " ")
        .strip()
    )


def transcript_to_text(items):
    """
    youtube-transcript-api 결과를 문자열로 변환.
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


def get_video_transcript(video_id):
    """
    유튜브 자막을 가져온다.
    한국어 우선, 없으면 영어 시도.
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
            transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
            text = transcript_to_text(transcript)

            if text:
                return text, "자막 기반"

        except Exception:
            pass

    # 신버전 방식
    for languages in language_attempts:
        try:
            api = YouTubeTranscriptApi()
            transcript = api.fetch(video_id, languages=languages)
            text = transcript_to_text(transcript)

            if text:
                return text, "자막 기반"

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
                    text = transcript_to_text(items)

                    if text:
                        return text, "번역 자막 기반"

            except Exception:
                continue

    except Exception:
        pass

    return "", "자막 없음"


# =========================
# 6. Gemini 요약 프롬프트
# =========================

def make_summary_prompt(video, content, source_type):
    title = video["title"]
    channel_title = video["channel_title"]
    url = video["url"]
    published_at = video["published_at"]

    # 너무 긴 자막은 잘라서 보냄
    content = content[:25000]

    return f"""
너는 주식 유튜브 영상을 투자자 관점에서 요약하는 AI다.

아래 영상 내용을 바탕으로 반드시 '영상별 요약문'을 작성해라.
특히 추천종목, 매수가, 목표가, 매도 목표가, 손절가, 보유 전략이 나오면 절대 빠뜨리지 말고 정리해라.

[매우 중요한 원칙]
1. 영상에서 실제로 나온 내용과 네 추론을 반드시 구분해라.
2. 종목명과 종목코드를 함부로 지어내지 마라.
3. 종목코드를 확신하지 못하면 "종목코드 확인 필요"라고 적어라.
4. 매수가, 목표가, 손절가, 매도 목표가는 영상에서 직접 언급된 경우에만 적어라.
5. 가격이 영상에 없으면 절대 계산해서 만들지 말고 "영상 내 언급 없음"이라고 적어라.
6. 추천종목이 명확히 나오면 반드시 따로 정리해라.
7. 추천종목과 단순 언급 종목을 구분해라.
8. "급등", "상한가", "세력", "작전", "재료", "대장주" 같은 표현은 과장 가능성을 따로 표시해라.
9. 매수 추천처럼 단정하지 말고, "영상에서 주장한 내용"으로 표현해라.
10. 내용이 부족하면 "내용 부족"이라고 솔직히 써라.
11. 마지막에는 투자자가 실제로 확인해야 할 체크포인트를 적어라.

[출력 형식]

📌 영상 정보
- 채널:
- 제목:
- 게시일:
- 요약 근거:
- 링크:

🧾 핵심 요약
- 이 영상이 말하는 핵심을 3~5줄로 정리

⭐ 추천 종목 정리

1) 종목명:
- 종목코드:
- 추천 여부:
- 추천 이유:
- 관련 테마:
- 매수가 / 진입가:
- 1차 목표가:
- 2차 목표가:
- 최종 매도 목표가:
- 손절가:
- 보유 기간 / 매매 관점:
- 단기 / 스윙 / 중기 구분:
- 영상에서 강조한 핵심 포인트:
- 불확실한 부분:

※ 영상에 가격이 나오지 않으면 반드시 "영상 내 언급 없음"이라고 써라.
※ 종목이 여러 개 나오면 종목별로 반복해서 정리해라.
※ 단순 언급 종목과 실제 추천 종목을 반드시 구분해라.

📈 단순 언급 종목
- 추천까지는 아니지만 영상에서 언급된 종목
- 종목명 / 종목코드 / 언급 이유

💰 매매 전략 요약
- 영상에서 제시한 매수 전략:
- 영상에서 제시한 매도 전략:
- 분할매수 여부:
- 분할매도 여부:
- 추격매수 주의 여부:
- 손절 기준:
- 목표가 도달 시 대응:
- 단기/스윙/중기 관점:

🔥 상승 논리
- 영상에서 제시한 상승 근거:
- 재료:
- 테마:
- 수급:
- 차트상 근거:
- 실적 또는 뉴스 근거:

⚠️ 리스크
- 과장 가능성:
- 확인이 필요한 주장:
- 이미 많이 오른 종목인지 여부:
- 거래량 급감 위험:
- 테마 소멸 위험:
- 투자자가 조심해야 할 부분:

✅ 투자자 체크포인트
- 공시:
- 실적:
- 거래량:
- 차트:
- 수급:
- 테마 지속성:
- 유튜버 주장과 실제 데이터가 맞는지 확인할 부분:

[영상 정보]
채널: {channel_title}
제목: {title}
게시일: {published_at}
요약 근거: {source_type}
링크: {url}

[영상 내용]
{content}
""".strip()


def get_model_candidates():
    """
    Gemini 모델 404 오류 대비 후보 목록.
    """
    candidates = [
        GEMINI_MODEL,
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.5-flash-lite",
    ]

    unique = []

    for model_name in candidates:
        if model_name and model_name not in unique:
            unique.append(model_name)

    return unique


def summarize_video(client, video, content, source_type):
    """
    Gemini로 영상 요약.
    """
    prompt = make_summary_prompt(video, content, source_type)

    last_error = None

    for model_name in get_model_candidates():
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=3000,
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
                print(f"⚠️ Gemini 모델 사용 실패: {model_name} → 다음 모델 시도")
                continue

            raise

    raise RuntimeError(f"모든 Gemini 모델 시도 실패: {last_error}")


# =========================
# 7. 텔레그램 전송
# =========================

def split_message(text, max_len=TELEGRAM_CHUNK_SIZE):
    """
    텔레그램 메시지 길이 제한 때문에 긴 메시지를 나눔.
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
    """
    텔레그램 메시지 전송.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    chunks = split_message(text)

    for i, chunk in enumerate(chunks, start=1):
        if len(chunks) > 1:
            chunk = f"[{i}/{len(chunks)}]\n\n{chunk}"

        try:
            response = requests.post(
                url,
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
            print(f"❌ 텔레그램 응답 오류: {response.text[:500]}")
            return False

        if not result.get("ok"):
            print(f"❌ 텔레그램 전송 실패: {result.get('description')}")
            return False

        time.sleep(0.5)

    return True


# =========================
# 8. 메인 실행
# =========================

def main():
    print("🚀 주식 유튜브 영상별 요약 시작")

    youtube_api_key = get_env("YOUTUBE_API_KEY")
    gemini_api_key = get_env("GEMINI_API_KEY", "_API_KEY", "GOOGLE_API_KEY")
    telegram_token = get_env("TELEGRAM_TOKEN")
    telegram_chat_id = get_env("TELEGRAM_CHAT_ID")

    client = genai.Client(api_key=gemini_api_key)

    processed_ids = load_processed_ids()

    all_videos = []

    for channel in CHANNELS:
        try:
            videos = get_recent_videos(youtube_api_key, channel)
            all_videos.extend(videos)
        except Exception as e:
            print(f"❌ 채널 처리 실패: {channel} / {e}")

    # 중복 제거
    unique_videos = []
    seen = set()

    for video in all_videos:
        video_id = video["video_id"]

        if video_id in seen:
            continue

        seen.add(video_id)
        unique_videos.append(video)

    # 최신순 정렬
    unique_videos.sort(key=lambda x: x.get("published_at", ""), reverse=True)

    print(f"\n📦 요약 대상 영상: {len(unique_videos)}개")

    sent_count = 0
    skipped_count = 0
    failed_count = 0

    for video in unique_videos:
        video_id = video["video_id"]
        title = video["title"]
        url = video["url"]
        channel_title = video["channel_title"]

        print("\n" + "=" * 80)
        print(f"🎬 영상 처리 중: [{channel_title}] {title}")
        print(f"🔗 {url}")

        if video_id in processed_ids:
            print("⏭️ 이미 요약 전송한 영상이라 건너뜀")
            skipped_count += 1
            continue

        transcript, source_type = get_video_transcript(video_id)

        if transcript:
            content = transcript
            print("✅ 자막 확보 완료")
        else:
            description = clean_text(video.get("description", ""))

            if len(description) >= 30:
                content = description
                source_type = "설명글 기반"
                print("⚠️ 자막 없음 → 설명글 기반으로 요약")
            else:
                message = f"""
📺 유튜브 영상 요약 불가

📡 채널: {channel_title}
📌 제목: {title}
🔗 링크: {url}

⚠️ 이 영상은 자막이 없고 설명글도 부족해서 실제 영상 내용을 요약하지 못했습니다.
자막이 제공되면 추천종목, 매수가, 목표가 등을 다시 요약할 수 있습니다.
""".strip()

                send_telegram(telegram_token, telegram_chat_id, message)

                processed_ids.add(video_id)
                save_processed_ids(processed_ids)

                skipped_count += 1
                continue

        try:
            summary, used_model = summarize_video(client, video, content, source_type)

            final_message = f"""
📺 유튜브 영상 요약

🤖 사용 모델: {used_model}

{summary}
""".strip()

            ok = send_telegram(telegram_token, telegram_chat_id, final_message)

            if ok:
                print("🚀 영상별 요약 전송 완료")
                processed_ids.add(video_id)
                save_processed_ids(processed_ids)
                sent_count += 1
            else:
                print("❌ 텔레그램 전송 실패")
                failed_count += 1

        except Exception as e:
            print(f"❌ 요약 실패: {e}")
            failed_count += 1

    print("\n" + "=" * 80)
    print("✅ 실행 완료")
    print(f"🚀 요약 전송: {sent_count}개")
    print(f"⏭️ 건너뜀: {skipped_count}개")
    print(f"❌ 실패: {failed_count}개")


if __name__ == "__main__":
    main()

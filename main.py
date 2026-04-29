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
    "@문선생_경제교실",
]

# 채널별 관심 종목 필터
# 빈 리스트 [] = 해당 채널의 모든 영상 요약
# 종목명이 들어가 있으면 해당 종목이 제목/설명/자막에 있을 때만 요약
TARGET_STOCKS_BY_CHANNEL = {
    "@디에스경제급등": [],
    "@디에스경제연구소DS": [],
    "@디에스황제주식TV": [],
    "@디에스경제타임즈": [],
    "@DSnews77": [],
    "@Power_bus2": ["알테오젠", "196170", "클로봇", "466100", "삼성중공업", "010140"],
    "@문선생_경제교실": ["펩트론", "087010"], 
}

YOUTUBE_BASE_URL = "https://www.googleapis.com/youtube/v3"

LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "24"))
MAX_VIDEOS_PER_CHANNEL = int(os.environ.get("MAX_VIDEOS_PER_CHANNEL", "10"))
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

PROCESSED_FILE = "processed_videos.json"
TELEGRAM_CHUNK_SIZE = 3500


# =========================
# 2. 환경변수
# =========================

def get_env(*names):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value

    raise RuntimeError(f"Secrets에 다음 값 중 하나가 필요합니다: {', '.join(names)}")


# =========================
# 3. 처리 기록
# =========================

def load_processed_ids():
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
    try:
        with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(processed_ids)), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 처리 기록 저장 실패: {e}")


# =========================
# 4. YouTube API
# =========================

def youtube_get(endpoint, params, api_key):
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
    handle = channel_handle.strip()

    attempts = []

    if handle.startswith("@"):
        attempts.append(handle)
        attempts.append(handle[1:])
    else:
        attempts.append(handle)

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
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_recent_videos(api_key, channel_handle):
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
# 5. 자막
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
    language_attempts = [
        ["ko"],
        ["ko-KR"],
        ["ko", "ko-KR"],
        ["en"],
        ["en-US"],
        ["ko", "ko-KR", "en", "en-US"],
    ]

    for languages in language_attempts:
        try:
            transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
            text = transcript_to_text(transcript)

            if text:
                return text, "자막 기반"

        except Exception:
            pass

    for languages in language_attempts:
        try:
            api = YouTubeTranscriptApi()
            transcript = api.fetch(video_id, languages=languages)
            text = transcript_to_text(transcript)

            if text:
                return text, "자막 기반"

        except Exception:
            pass

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
# 6. 종목 필터
# =========================

def normalize_text(text):
    return clean_text(text).lower().replace(" ", "")


def get_target_keywords(channel_handle):
    return TARGET_STOCKS_BY_CHANNEL.get(channel_handle, [])


def find_matched_keywords(channel_handle, title, description, transcript_text=""):
    keywords = get_target_keywords(channel_handle)

    if not keywords:
        return []

    combined = normalize_text(f"{title} {description} {transcript_text}")

    matched = []

    for keyword in keywords:
        key = normalize_text(keyword)

        if key and key in combined:
            matched.append(keyword)

    return matched


def channel_has_filter(channel_handle):
    return len(get_target_keywords(channel_handle)) > 0


# =========================
# 7. Gemini 요약
# =========================

def make_summary_prompt(video, content, source_type, matched_keywords):
    title = video["title"]
    channel_title = video["channel_title"]
    url = video["url"]
    published_at = video["published_at"]

    content = content[:25000]

    target_info = ", ".join(matched_keywords) if matched_keywords else "전체 영상 요약"

    return f"""
너는 주식 유튜브 영상을 바쁜 투자자를 위해 아주 깔끔하고 직관적으로 요약하는 AI다.
스마트폰 메신저(텔레그램)로 읽기 편하도록 쓸데없는 말은 모두 빼고 핵심만 출력해라.

[이번 요약의 관심 종목]
{target_info}

[매우 중요한 작성 원칙]
1. 종목코드는 절대 적지 마라. 종목명만 사용해라.
2. 영상에 나오지 않은 내용(가격, 실적, 수급 등)은 억지로 지어내거나 "영상 내 언급 없음", "확인 필요"라고 적지 마라. 정보가 없으면 해당 항목(줄) 자체를 그냥 삭제해라.
3. 관심 종목이 지정된 경우, 해당 종목의 내용을 최우선으로 자세히 적어라.
4. "급등", "상한가", "작전" 등 자극적인 단어는 순화하되, 유튜버가 강조하는 "세력목표가", "세력 매집 단가", "세력목표가격" 같은 구체적인 명칭과 가격은 절대 자체 검열하지 말고 있는 그대로 확실하게 적어라.
5. 유튜버들이 매수가/매도가를 특정 가격으로 딱 자르지 않고 "OOO원 아래에서 모아가라(매수)", "OOO원 부근에서 분할 매도 고려해라"처럼 범위나 조건으로 말하는 경우가 아주 많다. 이 뉘앙스를 절대 누락하지 말고 유튜버가 말한 그대로 살려서 적어라.
6. 영상 정보(채널명, 링크 등)는 출력하지 마라.
7. 단순 언급된 종목은 구구절절 설명하지 말고 한 줄에 묶어서 간단히 나열해라.

[출력 형식]

🧾 핵심 요약
- 이 영상의 진짜 목적과 핵심을 2~3줄로 아주 간결하게 요약.

🎯 (관심 종목이 있을 때만 출력, 없으면 이 섹션 삭제)
- 매칭 종목: {target_info}
- 관련 브리핑: 영상에서 언급된 내용 핵심 요약

⭐ 추천 및 핵심 분석 종목 (종목코드 생략)
[종목명]
- 추천/분석 이유: (1~2문장으로 간결하게)
- 가격 전략: 진입가 [OO원 이하 매수 등] / 목표가 [OO원 부근 매도 등] / 세력목표가 [가격] / 손절가 [가격] (※ 가격 언급이 있을 때만 작성, 없으면 이 줄 삭제)
- 투자 관점: 단기/스윙/중장기 등 (※ 언급 있을 때만 작성)

(※ 핵심 종목이 여러 개면 위 폼을 반복, 최대 3개까지만)

📈 단순 언급 종목
- 종목명, 종목명, 종목명 (※ 쉼표로 나열, 구체적 설명 생략)

💡 주요 매매 전략 및 상승 논리
- 영상에서 강조하는 시장 테마, 차트 흐름, 핵심 재료를 2~3개 불릿으로 요약.
- (언급이 없으면 이 섹션 삭제)

⚠️ 투자 주의사항 (리스크)
- 영상에서 조심하라고 한 점이나, 유튜버의 주장에서 투자자가 주의/검증해야 할 포인트 1~2가지.
- (언급이 없으면 이 섹션 삭제)

[영상 내용]
{content}
""".strip()


def get_model_candidates():
    candidates = [
        GEMINI_MODEL,
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
    ]

    unique = []

    for model_name in candidates:
        if model_name and model_name not in unique:
            unique.append(model_name)

    return unique


def summarize_video(client, video, content, source_type, matched_keywords):
    prompt = make_summary_prompt(video, content, source_type, matched_keywords)

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
# 8. 텔레그램
# =========================

def split_message(text, max_len=TELEGRAM_CHUNK_SIZE):
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
# 9. 메인 실행
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

    unique_videos = []
    seen = set()

    for video in all_videos:
        video_id = video["video_id"]

        if video_id in seen:
            continue

        seen.add(video_id)
        unique_videos.append(video)

    unique_videos.sort(key=lambda x: x.get("published_at", ""), reverse=True)

    print(f"\n📦 확인 대상 영상: {len(unique_videos)}개")

    sent_count = 0
    skipped_count = 0
    failed_count = 0

    for video in unique_videos:
        video_id = video["video_id"]
        title = video["title"]
        url = video["url"]
        channel_title = video["channel_title"]
        channel_handle = video["channel_handle"]
        description = clean_text(video.get("description", ""))

        print("\n" + "=" * 80)
        print(f"🎬 영상 처리 중: [{channel_title}] {title}")
        print(f"🔗 {url}")

        if video_id in processed_ids:
            print("⏭️ 이미 처리한 영상이라 건너뜁니다.")
            skipped_count += 1
            continue

        transcript = ""
        source_type = "자막 없음"

        # 필터 채널은 먼저 제목/설명에서 관심 종목 확인
        matched_keywords = find_matched_keywords(channel_handle, title, description)

        # 제목/설명에서 관심 종목이 안 잡힌 경우, 자막까지 확인
        if channel_has_filter(channel_handle) and not matched_keywords:
            transcript, source_type = get_video_transcript(video_id)

            if transcript:
                print("✅ 자막 확보 완료 - 관심 종목 필터 확인용")
                matched_keywords = find_matched_keywords(channel_handle, title, description, transcript)
            else:
                print("⚠️ 자막 없음 - 제목/설명 기준으로만 필터 판단")

            if not matched_keywords:
                print(f"⏭️ 관심 종목 없음 → 요약하지 않음: {get_target_keywords(channel_handle)}")
                processed_ids.add(video_id)
                save_processed_ids(processed_ids)
                skipped_count += 1
                continue

        # 필터 없는 채널이거나 관심 종목이 잡힌 채널은 요약 진행
        if not transcript:
            transcript, source_type = get_video_transcript(video_id)

        if transcript:
            content = transcript
            print("✅ 자막 기반 요약 진행")
        else:
            if len(description) >= 30:
                content = description
                source_type = "설명글 기반"
                print("⚠️ 자막 없음 → 설명글 기반으로 요약")
            else:
                print("⚠️ 자막과 설명글 부족 → 요약 불가")
                processed_ids.add(video_id)
                save_processed_ids(processed_ids)
                skipped_count += 1
                continue

        # 필터 있는 채널인데 자막을 뒤늦게 얻은 경우 다시 매칭 확인
        if channel_has_filter(channel_handle):
            matched_keywords = find_matched_keywords(channel_handle, title, description, content)

            if not matched_keywords:
                print(f"⏭️ 관심 종목 없음 → 요약하지 않음: {get_target_keywords(channel_handle)}")
                processed_ids.add(video_id)
                save_processed_ids(processed_ids)
                skipped_count += 1
                continue

        try:
            summary, used_model = summarize_video(client, video, content, source_type, matched_keywords)

            matched_text = ", ".join(matched_keywords) if matched_keywords else "전체 요약"

            final_message = f"""
📺 {title}
📡 {channel_title}
🔗 {url}
🎯 매칭 종목: {matched_text}
🧾 요약 근거: {source_type}
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

        # ==============================================================
        # 💡 [바로 이 부분!] 구글 API 과부하를 막기 위해 10초를 대기합니다.
        # ==============================================================
        print("⏳ 구글 API 과부하 방지를 위해 10초 대기 중...")
        time.sleep(10)

    print("\n" + "=" * 80)
    print("✅ 실행 완료")
    print(f"🚀 요약 전송: {sent_count}개")
    print(f"⏭️ 건너뜀: {skipped_count}개")
    print(f"❌ 실패: {failed_count}개")


if __name__ == "__main__":
    main()

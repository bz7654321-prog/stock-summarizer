import os
import json
import time
import requests
from datetime import datetime, timedelta, timezone

from google import genai
from google.genai import types


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

# 빈 리스트 [] = 해당 채널은 모든 영상 요약
# 종목명이 있으면 해당 종목 중심으로만 요약
TARGET_STOCKS_BY_CHANNEL = {
    "@디에스경제급등": [],
    "@디에스경제연구소DS": [],
    "@디에스황제주식TV": [],
    "@디에스경제타임즈": [],
    "@DSnews77": [],

    "@Power_bus2": [
        "알테오젠", "196170",
        "클로봇", "466100",
        "삼성중공업", "010140",
    ],

    "@문선생_경제교실": [
        "펩트론", "087010",
    ],
}

YOUTUBE_BASE_URL = "https://www.googleapis.com/youtube/v3"

LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "24"))
MAX_VIDEOS_PER_CHANNEL = int(os.environ.get("MAX_VIDEOS_PER_CHANNEL", "10"))

# 유튜브 URL 직접 분석 품질을 높이기 위해 기본값을 3 Flash Preview로 둠
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

PROCESSED_FILE = "processed_videos.json"
TELEGRAM_CHUNK_SIZE = 3500


# =========================
# 2. 예외
# =========================

class QuotaExceededError(Exception):
    pass


# =========================
# 3. 환경변수
# =========================

def get_env(*names):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value

    raise RuntimeError(f"Secrets에 다음 값 중 하나가 필요합니다: {', '.join(names)}")


# =========================
# 4. 처리 기록
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
# 5. YouTube API
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

    # 1차: 핸들 정확 조회
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

    # 2차: 검색 fallback
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
# 6. 종목 필터
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


def normalize_text(text):
    return clean_text(text).lower().replace(" ", "")


def get_target_keywords(channel_handle):
    return TARGET_STOCKS_BY_CHANNEL.get(channel_handle, [])


def channel_has_filter(channel_handle):
    return len(get_target_keywords(channel_handle)) > 0


def quick_match_by_title_description(channel_handle, title, description):
    keywords = get_target_keywords(channel_handle)

    if not keywords:
        return []

    combined = normalize_text(f"{title} {description}")
    matched = []

    for keyword in keywords:
        key = normalize_text(keyword)

        if key and key in combined:
            matched.append(keyword)

    return matched


# =========================
# 7. Gemini 영상 직접 분석 프롬프트
# =========================

def make_video_prompt(video, matched_keywords):
    title = video["title"]
    channel_title = video["channel_title"]
    published_at = video["published_at"]
    channel_handle = video["channel_handle"]

    target_keywords = get_target_keywords(channel_handle)

    if target_keywords:
        target_text = ", ".join(target_keywords)
        filter_instruction = f"""
[관심 종목 필터]
이 채널은 아래 관심 종목이 영상에서 실제로 다뤄질 때만 요약한다.

관심 종목:
{target_text}

영상 전체를 확인한 뒤, 관심 종목이 실질적으로 다뤄지지 않았다면 다른 설명 없이 정확히 아래 문장만 출력해라.

SKIP_VIDEO_NO_TARGET_STOCK

관심 종목이 다뤄졌다면, 관심 종목을 중심으로 요약하되 영상에서 함께 제시한 비교 종목이나 관련 종목은 필요한 만큼만 짧게 포함해라.
"""
    else:
        filter_instruction = """
[관심 종목 필터]
이 채널은 전체 영상 요약 대상이다.
영상에서 핵심 추천 종목 또는 분석 종목만 중심으로 요약해라.
"""

    return f"""
너는 주식 유튜브 영상을 투자자 관점에서 요약하는 AI다.
이 요청은 유튜브 링크를 직접 분석하는 요청이다.
영상의 음성, 자막, 화면에 나온 정보까지 참고해서 요약해라.

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
10. 영상 정보는 출력하지 마라. 제목, 채널, 링크는 메시지 상단에 따로 붙는다.

[원하는 출력 형식]

🧾 핵심 요약
- 영상 전체 핵심을 3~5줄로 정리

1. 시장 배경
- 영상에서 제시한 시장 상황, 해외 증시, 섹터 분위기, 테마 배경을 정리
- 가능하면 시간대 표시

2. 핵심 종목 분석

① 종목명 / 종목코드
- 추천 이유:
- 핵심 재료:
- 차트/수급 포인트:
- 가격 전략:
  - 1차 매수:
  - 2차 매수:
  - 목표가:
  - 손절가:
  - 지지선:
  - 저항선:
- 매매 관점:
- 확인할 리스크:

② 종목명 / 종목코드
- 추천 이유:
- 핵심 재료:
- 차트/수급 포인트:
- 가격 전략:
  - 1차 매수:
  - 2차 매수:
  - 목표가:
  - 손절가:
  - 지지선:
  - 저항선:
- 매매 관점:
- 확인할 리스크:

※ 핵심 종목이 1개면 1개만 작성.
※ 핵심 종목이 3개 이상이면 3개까지 자세히 작성.
※ 단순 언급 종목은 자세히 쓰지 말 것.

3. 단순 언급 종목
- 꼭 필요한 경우에만 최대 5개까지
- 종목명 / 언급 이유 한 줄

4. 투자 포인트 및 결론
- 영상에서 가장 강조한 매매 포인트
- 바로 추격매수인지, 눌림목인지, 돌파매수인지 구분
- 투자자가 실제 확인해야 할 것

⚠️ 주의할 점
- 유튜버 주장 중 검증 필요한 부분
- 이미 오른 종목인지 여부
- 손절 기준 미제시 여부
- 테마 과열 가능성

[참고용 정보 - 출력하지 말 것]
채널: {channel_title}
채널 핸들: {channel_handle}
제목: {title}
게시일: {published_at}
""".strip()


# =========================
# 8. Gemini 호출
# =========================

def get_model_candidates():
    candidates = [
        GEMINI_MODEL,
        "gemini-3-flash-preview",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ]

    unique = []

    for model_name in candidates:
        if model_name and model_name not in unique:
            unique.append(model_name)

    return unique


def is_quota_error(error):
    error_text = str(error).lower()
    return (
        "429" in error_text
        or "resource_exhausted" in error_text
        or "quota" in error_text
        or "rate limit" in error_text
    )


def summarize_video_by_youtube_url(client, video, matched_keywords):
    prompt = make_video_prompt(video, matched_keywords)
    video_url = video["url"]

    last_error = None

    for model_name in get_model_candidates():
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=types.Content(
                    parts=[
                        types.Part(
                            file_data=types.FileData(
                                file_uri=video_url
                            )
                        ),
                        types.Part(text=prompt),
                    ]
                ),
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=3500,
                ),
            )

            text = getattr(response, "text", "")

            if text and text.strip():
                return text.strip(), model_name

            last_error = RuntimeError(f"{model_name} 모델 응답이 비어 있습니다.")

        except Exception as e:
            last_error = e
            error_text = str(e).lower()

            if is_quota_error(e):
                raise QuotaExceededError(str(e))

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
# 9. 텔레그램
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
# 10. 메인 실행
# =========================

def main():
    print("🚀 주식 유튜브 영상 직접 분석 요약 시작")

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
            print("⏭️ 이미 처리한 영상이라 건너뜀")
            skipped_count += 1
            continue

        matched_keywords = quick_match_by_title_description(channel_handle, title, description)

        if channel_has_filter(channel_handle):
            target_text = ", ".join(get_target_keywords(channel_handle))
            print(f"🎯 관심 종목 필터 채널: {target_text}")

            if matched_keywords:
                print(f"✅ 제목/설명에서 관심 종목 감지: {', '.join(matched_keywords)}")
            else:
                print("🔎 제목/설명에서는 관심 종목 미감지. Gemini가 영상 직접 확인 후 SKIP 여부 판단")

        else:
            print("🎯 전체 요약 채널")

        try:
            summary, used_model = summarize_video_by_youtube_url(
                client=client,
                video=video,
                matched_keywords=matched_keywords,
            )

            if summary.strip().startswith("SKIP_VIDEO_NO_TARGET_STOCK"):
                print("⏭️ 영상 내 관심 종목 없음 → 전송하지 않음")
                processed_ids.add(video_id)
                save_processed_ids(processed_ids)
                skipped_count += 1
                continue

            matched_text = ", ".join(matched_keywords) if matched_keywords else (
                "Gemini 영상 직접 분석" if not channel_has_filter(channel_handle) else "영상 내 관심 종목 확인"
            )

            final_message = f"""
📺 {title}
📡 {channel_title}
🔗 {url}
🎯 매칭 기준: {matched_text}
🧾 요약 근거: Gemini 영상 직접 분석
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

        except QuotaExceededError as e:
            print("❌ Gemini 사용량 한도 초과. 이번 실행은 여기서 중단합니다.")
            print(str(e)[:1000])
            failed_count += 1
            break

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

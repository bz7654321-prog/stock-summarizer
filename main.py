import os
import requests
from datetime import datetime, timedelta
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
from urllib.parse import quote

# 1. 구독할 채널 핸들 목록 (한글 핸들 포함)
CHANNELS = [
    "@디에스경제급등", 
    "@디에스경제연구소DS", 
    "@디에스황제주식TV", 
    "@디에스경제타임즈", 
    "@Power_bus2", 
    "@DSnews77"
]

def get_video_list(api_key, channel_handle):
    """최근 24시간 내 올라온 영상을 검색합니다."""
    now = datetime.utcnow()
    delta = 24  # 최근 하루 동안의 영상
    published_after = (now - timedelta(hours=delta)).isoformat() + "Z"
    
    # 한글 핸들 깨짐 방지를 위해 인코딩 처리
    encoded_handle = quote(channel_handle)
    url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q={encoded_handle}&type=video&order=date&maxResults=3&publishedAfter={published_after}&key={api_key}"
    
    try:
        r = requests.get(url).json()
        items = r.get('items', [])
        print(f"🔍 [{channel_handle}] 검색 결과: {len(items)}개 발견")
        return items
    except Exception as e:
        print(f"❌ 유튜브 검색 중 오류: {e}")
        return []

def main():
    # 2. 깃허브 Secrets에서 설정값 불러오기
    api_key = os.environ.get("YOUTUBE_API_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    bot_token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    # 3. AI 모델 설정 (에러 방지를 위해 호환성 높은 명칭 사용)
    try:
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
    except:
        model = genai.GenerativeModel('gemini-pro')

    for handle in CHANNELS:
        videos = get_video_list(api_key, handle)
        for v in videos:
            v_id = v['id']['videoId']
            title = v['snippet']['title']
            description = v['snippet']['description']
            
            content = ""
            # 자막 추출 시도 (한국어)
            try:
                srt = YouTubeTranscriptApi.get_transcript(v_id, languages=['ko'])
                content = " ".join([i['text'] for i in srt])
                print(f"✅ [{title}] 자막 추출 성공")
            except:
                # 자막 없을 시 영상 설명글로 대체
                content = description
                print(f"⚠️ [{title}] 자막 없음 -> 설명글로 요약 시도")
            
            if content:
                try:
                    # AI에게 요약 요청
                    prompt = f"다음 주식 영상의 핵심 내용을 투자자 관점에서 요약해줘.\n제목: {title}\n내용: {content}\n[원칙] 종목번호 정확도 엄수, 중복 정보 통합."
                    response = model.generate_content(prompt)
                    
                    # 4. 텔레그램 메시지 전송
                    msg = f"📺 {title}\n\n{response.text}"
                    send_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                    res = requests.post(send_url, data={'chat_id': chat_id, 'text': msg}).json()
                    
                    if res.get('ok'):
                        print(f"🚀 [{title}] 전송 완료!")
                    else:
                        print(f"❌ 전송 실패: {res.get('description')}")
                except Exception as e:
                    print(f"❌ AI 요약/전송 오류: {e}")

if __name__ == "__main__":
    main()

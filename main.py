import os
import requests
from datetime import datetime, timedelta
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
from urllib.parse import quote

# 1. 구독할 채널 목록
CHANNELS = [
    "@디에스경제급등", 
    "@디에스경제연구소DS", 
    "@디에스황제주식TV", 
    "@디에스경제타임즈", 
    "@Power_bus2", 
    "@DSnews77"
]

def get_video_list(api_key, channel_handle):
    now = datetime.utcnow()
    delta = 48  # 테스트를 위해 이틀치 영상을 긁어옵니다.
    published_after = (now - timedelta(hours=delta)).isoformat() + "Z"
    
    encoded_handle = quote(channel_handle)
    url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q={encoded_handle}&type=video&order=date&maxResults=3&publishedAfter={published_after}&key={api_key}"
    
    try:
        r = requests.get(url).json()
        items = r.get('items', [])
        print(f"🔍 [{channel_handle}] 검색 결과: {len(items)}개 발견")
        return items
    except Exception as e:
        print(f"❌ 유튜브 검색 오류: {e}")
        return []

def main():
    # Secrets에서 정보 가져오기
    api_key = os.environ.get("YOUTUBE_API_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    bot_token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    # 2. 가장 안정적인 AI 모델 명칭으로 설정
    try:
        genai.configure(api_key=gemini_key)
        # 404 에러 방지를 위해 가장 범용적인 'gemini-1.5-flash' 사용
        model = genai.GenerativeModel('gemini-1.5-flash')
    except:
        model = genai.GenerativeModel('gemini-pro')

    for handle in CHANNELS:
        videos = get_video_list(api_key, handle)
        for v in videos:
            v_id = v['id']['videoId']
            title = v['snippet']['title']
            description = v['snippet']['description']
            
            content = ""
            try:
                # 자막 시도
                srt = YouTubeTranscriptApi.get_transcript(v_id, languages=['ko'])
                content = " ".join([i['text'] for i in srt])
                print(f"✅ [{title}] 자막 추출 성공")
            except:
                # 자막 없으면 설명글 시도
                content = description
                print(f"⚠️ [{title}] 자막 없음 -> 설명글 사용")
            
            if content:
                try:
                    # 요약 요청
                    prompt = f"다음 주식 영상의 핵심 내용을 투자자 관점에서 요약해줘.\n제목: {title}\n내용: {content}\n[원칙] 종목번호 정확도 엄수."
                    response = model.generate_content(prompt)
                    
                    # 3. 텔레그램 전송
                    msg = f"📺 {title}\n\n{response.text}"
                    send_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                    res = requests.post(send_url, data={'chat_id': chat_id, 'text': msg}).json()
                    
                    if res.get('ok'):
                        print(f"🚀 [{title}] 전송 완료!")
                    else:
                        print(f"❌ 전송 실패: {res.get('description')}")
                except Exception as e:
                    print(f"❌ 요약 중 오류 발생: {e}")

if __name__ == "__main__":
    main()

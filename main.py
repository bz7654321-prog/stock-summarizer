import os
import requests
from datetime import datetime, timedelta
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai

# --- [설정 부분] 요약하고 싶은 채널 ID ---
CHANNELS = ["@Samsun_Stock", "@e_best_stock"] 
# ----------------------------------------

def get_video_list(api_key, channel_handle):
    now = datetime.utcnow()
    
    # 실행 시간에 맞춰 '몇 시간 전' 영상을 가져올지 동적으로 계산하여 중복 방지
    if now.hour == 10:   # 19시 실행 (07시~19시 사이의 12시간 분량)
        delta = 12
    elif now.hour == 13: # 22시 실행 (19시~22시 사이의 3시간 분량)
        delta = 3
    elif now.hour == 22: # 07시 실행 (22시~07시 사이의 9시간 분량)
        delta = 9
    else:
        delta = 24       # 수동 실행 시 기본값 (24시간)

    published_after = (now - timedelta(hours=delta)).isoformat() + "Z"
    
    url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q={channel_handle}&type=video&order=date&maxResults=5&publishedAfter={published_after}&key={api_key}"
    
    try:
        r = requests.get(url).json()
        return r.get('items', [])
    except:
        return []

def main():
    api_key = os.environ["YOUTUBE_API_KEY"]
    gemini_key = os.environ["GEMINI_API_KEY"]
    bot_token = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    genai.configure(api_key=gemini_key)
    model = genai.GenerativeModel('gemini-1.5-flash')

    for handle in CHANNELS:
        videos = get_video_list(api_key, handle)
        for v in videos:
            v_id = v['id']['videoId']
            title = v['snippet']['title']
            
            try:
                # 자막 추출
                srt = YouTubeTranscriptApi.get_transcript(v_id, languages=['ko'])
                text = " ".join([i['text'] for i in srt])
                
                # AI 요약 프롬프트 (정확도 및 중복 제거 강화)
                prompt = f"""다음 주식 영상의 핵심 내용을 투자자 관점에서 요약해줘.
                제목: {title}
                내용: {text}

                [요약 원칙]
                1. 데이터 정리 시 속도보다 정확도를 최우선으로 하여 두 번 이상 검토할 것.
                2. 종목번호는 반드시 KRX(한국거래소) 최신 상장 정보와 대조하여 정확도를 확인할 것. 불확실하면 추측하지 말고 종목번호를 기재하지 말 것.
                3. 한 종목에 대해 중복되는 정보나 반복되는 분석이 있다면 하나로 통합하여 간결하게 정리할 것."""
                
                response = model.generate_content(prompt)
                
                # 텔레그램 전송
                msg = f"📺 {title}\n\n{response.text}"
                send_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                requests.post(send_url, data={'chat_id': chat_id, 'text': msg})
                
            except Exception as e:
                print(f"[{title}] 처리 중 오류 또는 자막 없음")

if __name__ == "__main__":
    main()

import os
import requests
from datetime import datetime, timedelta
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
from urllib.parse import quote

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
    delta = 24
    published_after = (now - timedelta(hours=delta)).isoformat() + "Z"
    encoded_handle = quote(channel_handle)
    
    # snippet 정보를 가져와서 자막이 없더라도 '설명'을 활용할 수 있게 함
    url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q={encoded_handle}&type=video&order=date&maxResults=3&publishedAfter={published_after}&key={api_key}"
    
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
            description = v['snippet']['description'] # 자막 대용으로 사용
            
            content = ""
            try:
                # 1 순위: 자막 추출 시도
                srt = YouTubeTranscriptApi.get_transcript(v_id, languages=['ko'])
                content = " ".join([i['text'] for i in srt])
                print(f"✅ [{title}] 자막 추출 성공")
            except:
                # 2 순위: 자막 없으면 영상 설명으로 대체
                content = description
                print(f"⚠️ [{title}] 자막 없음 -> 설명글로 요약 시도")
            
            if content:
                prompt = f"제목: {title}\n내용: {content}\n위 주식 영상의 핵심을 투자자 관점에서 요약해줘. 종목번호 정확도 필수."
                try:
                    response = model.generate_content(prompt)
                    msg = f"📺 {title}\n\n{response.text}"
                    send_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                    requests.post(send_url, data={'chat_id': chat_id, 'text': msg})
                    print(f"🚀 전송 완료: {title}")
                except Exception as e:
                    print(f"❌ AI 요약 에러: {e}")

if __name__ == "__main__":
    main()

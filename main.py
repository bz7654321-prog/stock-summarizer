import os
import requests
from datetime import datetime, timedelta
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
from urllib.parse import quote

CHANNELS = ["@디에스경제급등", "@디에스경제연구소DS", "@디에스황제주식TV", "@디에스경제타임즈", "@Power_bus2", "@DSnews77"]

def get_video_list(api_key, channel_handle):
    now = datetime.utcnow()
    delta = 24
    published_after = (now - timedelta(hours=delta)).isoformat() + "Z"
    encoded_handle = quote(channel_handle)
    url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q={encoded_handle}&type=video&order=date&maxResults=3&publishedAfter={published_after}&key={api_key}"
    try:
        r = requests.get(url).json()
        items = r.get('items', [])
        print(f"🔍 [{channel_handle}] 검색 결과: {len(items)}개 발견")
        return items
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
            
            # 자막 시도 -> 안되면 설명 시도 -> 그래도 안되면 포기
            content = ""
            try:
                srt = YouTubeTranscriptApi.get_transcript(v_id, languages=['ko'])
                content = " ".join([i['text'] for i in srt])
            except:
                content = v['snippet']['description']
            
            if content:
                try:
                    prompt = f"제목: {title}\n내용: {content}\n핵심 요약해줘."
                    response = model.generate_content(prompt)
                    msg = f"📺 {title}\n\n{response.text}"
                    send_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                    res = requests.post(send_url, data={'chat_id': chat_id, 'text': msg}).json()
                    
                    if res.get('ok'):
                        print(f"🚀 [{title}] 전송 성공!")
                    else:
                        print(f"❌ [{title}] 전송 실패: {res.get('description')}")
                except Exception as e:
                    print(f"❌ AI 오류: {e}")

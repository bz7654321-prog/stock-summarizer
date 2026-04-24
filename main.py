import os
import requests
from datetime import datetime, timedelta
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
from urllib.parse import quote # 한글 주소 변환용

# --- [설정 부분] 한글 핸들도 그대로 넣으시면 됩니다 ---
CHANNELS = [
    "@디에스경제급등",
    "@디에스경제연구소DS",
    "@디에스황제주식TV",
    "@디에스경제타임즈",
    "@Power_bus2",
    "@DSnews77"
]
# -------------------------------------------------------

def get_video_list(api_key, channel_handle):
    now = datetime.utcnow()
    delta = 24 # 최근 24시간 내 영상 조회

    published_after = (now - timedelta(hours=delta)).isoformat() + "Z"
    
    # 한글 핸들을 컴퓨터가 이해할 수 있게 변환 (예: @디에스 -> %40%EB%94%94...)
    encoded_handle = quote(channel_handle)
    
    url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q={encoded_handle}&type=video&order=date&maxResults=3&publishedAfter={published_after}&key={api_key}"
    
    try:
        r = requests.get(url).json()
        if 'items' in r and len(r['items']) > 0:
            print(f"✅ [{channel_handle}] 영상 {len(r['items'])}개 발견!")
            return r['items']
        else:
            print(f"⚠️ [{channel_handle}] 최근 영상이 없거나 검색되지 않음")
            return []
    except Exception as e:
        print(f"❌ 에러 발생: {e}")
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
                # 자막 추출 (한국어 최우선)
                srt = YouTubeTranscriptApi.get_transcript(v_id, languages=['ko'])
                text = " ".join([i['text'] for i in srt])
                
                prompt = f"""다음 주식 영상의 핵심 내용을 투자자 관점에서 요약해줘.
                제목: {title}
                내용: {text}
                [원칙] 종목번호 정확도 엄수, 중복 정보 통합."""
                
                response = model.generate_content(prompt)
                
                msg = f"📺 {title}\n\n{response.text}"
                send_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                requests.post(send_url, data={'chat_id': chat_id, 'text': msg})
                print(f"🚀 [{title}] 전송 완료!")
            except:
                print(f"⏭️ 자막 없음 건너뜀: {title}")

if __name__ == "__main__":
    main()

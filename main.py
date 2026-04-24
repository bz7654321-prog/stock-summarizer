import os
import requests
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai

# --- [설정 부분] 요약하고 싶은 채널 ID를 여기에 넣으세요 ---
# 채널 주소가 youtube.com/@XXXX 라면 @XXXX 부분을 넣으면 됩니다.
CHANNELS = ["@Samsun_Stock", "@e_best_stock"] # 예시입니다. 원하는 채널로 바꾸세요!
# -------------------------------------------------------

def get_video_list(api_key, channel_handle):
    # 채널 핸들로 최근 영상 가져오기
    url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q={channel_handle}&type=video&order=date&maxResults=1&key={api_key}"
    r = requests.get(url).json()
    return r.get('items', [])

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
                # 자막 가져오기
                srt = YouTubeTranscriptApi.get_transcript(v_id, languages=['ko'])
                text = " ".join([i['text'] for i in srt])
                
                # Gemini 요약 (주식 특화 프롬프트)
                prompt = f"다음 주식 영상의 핵심 내용을 투자자 관점에서 요약해줘.\n제목: {title}\n내용: {text}"
                response = model.generate_content(prompt)
                
                # 텔레그램 전송
                msg = f"📺 {title}\n\n{response.text}"
                send_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                requests.post(send_url, data={'chat_id': chat_id, 'text': msg})
            except:
                print(f"자막이 없는 영상입니다: {title}")

if __name__ == "__main__":
    main()

name: 주식 유튜브 요약 실행

on:
  workflow_dispatch:

  schedule:
    # 한국시간 20:00 = UTC 11:00
    - cron: "0 11 * * *"

    # 한국시간 22:00 = UTC 13:00
    - cron: "0 13 * * *"

    # 한국시간 07:00 = UTC 22:00
    - cron: "0 22 * * *"

permissions:
  contents: write

concurrency:
  group: stock-youtube-summary
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest

    env:
      YOUTUBE_API_KEY: ${{ secrets.YOUTUBE_API_KEY }}
      GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
      _API_KEY: ${{ secrets._API_KEY }}
      GOOGLE_API_KEY: ${{ secrets.GOOGLE_API_KEY }}
      TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}

      # 중복 방지는 processed_videos.json이 담당함.
      # 혹시 실행 시간이 밀려도 영상을 놓치지 않도록 24시간으로 넉넉하게 설정.
      LOOKBACK_HOURS: "24"

      # 채널당 확인할 최대 영상 수
      MAX_VIDEOS_PER_CHANNEL: "10"

      GEMINI_MODEL: "gemini-2.5-flash"

    steps:
      - name: 코드 가져오기
        uses: actions/checkout@v4
        with:
          persist-credentials: true

      - name: 파이썬 설치
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: 파이썬 버전 확인
        run: |
          python --version
          pip --version

      - name: 패키지 설치
        run: |
          python -m pip install --upgrade pip
          pip uninstall -y google-generativeai || true
          pip install -U google-genai requests youtube-transcript-api

      - name: Secrets 확인
        run: |
          python - <<'PY'
          import os

          required = [
              "YOUTUBE_API_KEY",
              "TELEGRAM_TOKEN",
              "TELEGRAM_CHAT_ID",
          ]

          gemini_keys = [
              "GEMINI_API_KEY",
              "_API_KEY",
              "GOOGLE_API_KEY",
          ]

          missing = []

          for key in required:
              if not os.environ.get(key):
                  missing.append(key)

          if not any(os.environ.get(key) for key in gemini_keys):
              missing.append("GEMINI_API_KEY 또는 _API_KEY 또는 GOOGLE_API_KEY")

          if missing:
              print("❌ 누락된 Secrets:")
              for item in missing:
                  print("-", item)
              raise SystemExit(1)

          print("✅ 필수 Secrets 확인 완료")
          PY

      - name: 주식 유튜브 요약 실행
        run: |
          python main.py

      - name: 처리 기록 저장
        if: always()
        run: |
          if [ -f processed_videos.json ]; then
            git config user.name "github-actions[bot]"
            git config user.email "github-actions[bot]@users.noreply.github.com"

            git add processed_videos.json

            if git diff --cached --quiet; then
              echo "변경된 처리 기록 없음"
            else
              git commit -m "Update processed videos"
              git push
            fi
          else
            echo "processed_videos.json 파일이 없습니다."
          fi

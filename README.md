# Steam Review Collector

## 🌐 Live Site

분석 결과는 다음 URL에서 확인 가능:

**🔗 https://nolf001.github.io/Steam-Review-/reports/**

| 게임 | 분석 결과 |
|---|---|
| The Witcher 3: Wild Hunt | [분석 보기](https://nolf001.github.io/Steam-Review-/reports/witcher3.html) |
| Crimson Desert | [분석 보기](https://nolf001.github.io/Steam-Review-/reports/crimson_desert.html) |

---

Steam Web API를 사용해 특정 게임의 **모든 유저 리뷰**를 CSV로 수집하는 Python 스크립트.

---

## 설치

```bash
pip install -r requirements.txt
```

> `tqdm`은 선택 사항입니다. 설치하지 않아도 동작하며, 대신 콘솔 텍스트로 진행률을 출력합니다.

---

## 사용법

```bash
python collect.py <APP_ID> [옵션]
```

### 옵션

| 인자 | 기본값 | 설명 |
|------|--------|------|
| `appid` | (필수) | Steam App ID |
| `--lang` | `all` | 언어 필터: `korean` / `english` / `all` |
| `--output` | `reviews_{appid}.csv` | 출력 CSV 파일명 |
| `--max-pages` | 무제한 | 최대 수집 페이지 수 |

### 실행 예시

```bash
# 스카이림 전체 리뷰 수집
python collect.py 489830

# 스카이림 전체 리뷰 (파일명 지정)
python collect.py 489830 --lang all --output skyrim_reviews.csv

# 위쳐 3 한국어 리뷰만, 최대 100페이지
python collect.py 292030 --lang korean --max-pages 100

# 호라이즌 포비든 웨스트 영어 리뷰
python collect.py 2420110 --lang english --output horizon_en.csv
```

---

## 주요 게임 App ID

| 게임 | App ID |
|------|--------|
| 스카이림 Special Edition | `489830` |
| 위쳐 3 Wild Hunt | `292030` |
| 호라이즌 포비든 웨스트 | `2420110` |
| 드래곤즈 도그마 2 | `2054970` |
| 어쌔신 크리드 오디세이 | `812140` |
| 붉은사막 | `1419160` |

App ID는 Steam 게임 페이지 URL에서 확인할 수 있습니다.  
예: `https://store.steampowered.com/app/489830/` → App ID = `489830`

---

## 출력 파일

| 파일 | 설명 |
|------|------|
| `reviews_{appid}.csv` | 최종 수집 결과 (UTF-8-BOM, 엑셀 호환) |
| `reviews_{appid}_checkpoint.csv` | 100페이지마다 갱신되는 중간 저장 파일 |
| `reviews_{appid}_summary.json` | 게임 전체 리뷰 통계 (총 리뷰 수, 긍정/부정 비율 등) |
| `reviews_{appid}_errors.log` | 오류 로그 |

### CSV 컬럼 목록

| 컬럼 | 설명 |
|------|------|
| `recommendationid` | 리뷰 고유 ID |
| `author_steamid` | 작성자 Steam ID |
| `author_playtime_forever_min` | 총 플레이타임 (분) |
| `author_playtime_at_review_min` | 리뷰 작성 시점 플레이타임 (분) |
| `author_num_games_owned` | 작성자 보유 게임 수 |
| `author_num_reviews` | 작성자가 쓴 총 리뷰 수 |
| `language` | 리뷰 언어 |
| `review` | 리뷰 본문 |
| `timestamp_created` | 작성 시각 (Unix timestamp) |
| `timestamp_updated` | 수정 시각 (Unix timestamp) |
| `timestamp_created_dt` | 작성 시각 (YYYY-MM-DD HH:MM:SS UTC) |
| `timestamp_updated_dt` | 수정 시각 (YYYY-MM-DD HH:MM:SS UTC) |
| `voted_up` | True=추천, False=비추천 |
| `votes_up` | 동의한 유저 수 |
| `votes_funny` | 재밌다고 투표한 수 |
| `weighted_vote_score` | Steam 가중 점수 |
| `comment_count` | 댓글 수 |
| `steam_purchase` | Steam에서 구매 여부 |
| `received_for_free` | 무료 수령 여부 |
| `written_during_early_access` | 얼리액세스 중 작성 여부 |

---

## Rate Limit 주의사항

Steam API는 공개 엔드포인트이지만 과도한 요청 시 **HTTP 429** (Too Many Requests)를 반환합니다.

| 상황 | 대응 |
|------|------|
| 정상 요청 간격 | 1.5초 대기 |
| HTTP 429 수신 | 30초 대기 후 재시도 |
| 429가 3회 연속 | 수집 중단 후 저장 |
| HTTP 5xx 수신 | 지수 백오프 (2→4→8초) 후 재시도 |

---

## 예상 소요 시간

수집 속도는 요청당 약 **1.5초 + 응답 시간** 기준입니다.

| 리뷰 수 | 페이지 수 (100개/페이지) | 예상 시간 |
|---------|------------------------|----------|
| 1,000개 | 10페이지 | ~2분 |
| 10,000개 | 100페이지 | ~15분 |
| 50,000개 | 500페이지 | ~75분 |
| 100,000개 | 1,000페이지 | ~2.5시간 |

> 스카이림·위쳐 3처럼 리뷰가 수십만 개인 게임은 수 시간이 걸릴 수 있습니다.  
> `--max-pages`로 수집량을 제한하거나, `Ctrl+C`로 중단해도 수집된 데이터는 저장됩니다.

---

## Ctrl+C 안전 중단

수집 도중 언제든 `Ctrl+C`를 누르면 현재까지 수집한 데이터를 저장하고 통계를 출력한 뒤 종료합니다.

```
^C
[Interrupted] Ctrl+C detected. Saving collected data...
============================================================
  Final file saved → skyrim_reviews.csv
============================================================
  Total reviews collected :  8,500
  Recommended (voted_up)  :  7,923 (93.2%)
  Not recommended         :    577 (6.8%)
  Avg playtime            :  3241 min (54.0 h)
  Total elapsed           : 765.3s (12.8 min)
============================================================
```

---

## GitHub Pages 배포

여러 게임의 분석 결과를 한 사이트에 누적해서 동료들과 공유할 수 있습니다.

### 초기 설정 (1회만)

```bash
# 1. GitHub에서 새 repository 생성 (Public 권장, 이름: steam-review-collector)

# 2. 로컬 Git 초기화
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/{username}/steam-review-collector.git
git push -u origin main

# 3. GitHub Pages 활성화
# Repository → Settings → Pages
# Source: Deploy from a branch → Branch: main / (root) → Save

# 4. 약 2분 후 접근 가능
# https://{username}.github.io/steam-review-collector/reports/
```

### 새 게임 분석 추가 워크플로우

```bash
# 1. 리뷰 수집
python collect.py <APP_ID> --lang all --max-pages 500 --output {game}_reviews.csv

# 2. 분석 (--slug 지정 → reports/{slug}.html 자동 생성)
python analyze.py {game}_reviews.csv --game-name "Full Game Name" --slug {short-name}

# 3. GitHub Pages 배포 (index.html 갱신 + git push)
python publish.py
```

**예시:**
```bash
python collect.py 489830 --lang all --output skyrim_reviews.csv
python analyze.py skyrim_reviews.csv --game-name "Skyrim Special Edition" --slug skyrim
python publish.py
# → https://{username}.github.io/steam-review-collector/reports/
```

### publish.py 옵션

```bash
python publish.py                           # index.html 갱신 + git commit + push
python publish.py --dry-run                 # index.html 갱신만 (git 작업 없음)
python publish.py --message "Add Skyrim"    # 커스텀 커밋 메시지
```

### 분석된 게임 목록

| 게임 | Steam 등급 | URL |
|------|-----------|-----|
| The Witcher 3: Wild Hunt | Overwhelmingly Positive | `/reports/witcher3.html` |
| Crimson Desert | Very Positive | `/reports/crimson_desert.html` |
| (추가 예정) | — | — |

### 폴더 구조

```
steam-review-collector/
├── reports/
│   ├── assets/
│   │   └── style.css          # 공통 Steam 다크 테마 CSS
│   ├── index.html             # 대시보드 (publish.py가 자동 갱신)
│   ├── witcher3.html          # 위쳐 3 분석 결과
│   ├── witcher3.json          # 위쳐 3 메타데이터
│   ├── crimson_desert.html    # 붉은사막 분석 결과
│   └── crimson_desert.json    # 붉은사막 메타데이터
├── analyze.py                 # 분석 스크립트 (Claude API)
├── collect.py                 # 리뷰 수집 스크립트
├── publish.py                 # GitHub Pages 배포 스크립트
├── requirements.txt
└── .gitignore                 # CSV 파일 제외
```

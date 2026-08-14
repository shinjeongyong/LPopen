#!/usr/bin/env python3
"""
LP 발매정보 수집기 (Phase 1: 해외 / Discogs)

- Discogs 검색 API에서 최근 바이닐(LP) 발매를 장르별로 긁어옵니다.
- 기존 data.json 과 합쳐서 중복을 제거하고,
  '우리가 처음 발견한 날짜(first_seen)'를 각 판마다 기록합니다.
  -> Discogs 는 '발매일' 검색을 지원하지 않기 때문에,
     매일 돌면서 '오늘 새로 보인 판'을 first_seen 으로 잡는 방식입니다.
- 오래된 항목은 정리해서 data.json 이 무한정 커지지 않게 합니다.

환경변수:
  DISCOGS_TOKEN  (필수) - Discogs 개인 액세스 토큰. 절대 코드에 직접 넣지 마세요.

로컬 테스트:
  DISCOGS_TOKEN=xxxxx python collect.py
"""

import os
import sys
import json
import time
import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ─────────────────────────────────────────────────────────────
# 설정 (여기만 바꾸면 됩니다)
# ─────────────────────────────────────────────────────────────

# 이 앱을 식별하는 고유 User-Agent (Discogs 필수 요구사항).
# github 주소는 본인 저장소로 바꿔도 되고 그대로 둬도 동작합니다.
USER_AGENT = "LPReleaseTracker/1.0 +https://github.com/shinsegaefood/lp-tracker"

# 긁어올 장르 (Discogs 상위 장르 이름). 필요에 따라 추가/삭제하세요.
GENRES = [
    "Electronic",
    "Rock",
    "Jazz",
    "Funk / Soul",
    "Hip Hop",
    "Pop",
    "Classical",
    "Reggae",
]

# 장르당 몇 페이지까지 가져올지 (1페이지 = 최대 100개).
PAGES_PER_GENRE = 2
PER_PAGE = 100

# data.json 을 몇 개까지 유지할지 (오래된/초과분은 정리).
MAX_ITEMS = 3000
# first_seen 이 이 일수보다 오래된 항목은 정리.
KEEP_DAYS = 90

DATA_FILE = "data.json"
API_BASE = "https://api.discogs.com/database/search"

TODAY = datetime.date.today().isoformat()


# ─────────────────────────────────────────────────────────────
# Discogs 호출
# ─────────────────────────────────────────────────────────────

def discogs_get(params, token):
    """Discogs 검색 API 한 번 호출. 실패 시 None 반환."""
    query = urlencode(params)
    url = f"{API_BASE}?{query}"
    req = Request(url, headers={
        "User-Agent": USER_AGENT,                 # 없으면 거절/과도한 throttle
        "Authorization": f"Discogs token={token}",  # 검색은 인증 필수
    })
    try:
        with urlopen(req, timeout=30) as resp:
            # 남은 요청 수를 보고 rate limit 근처면 잠깐 쉼
            remaining = resp.headers.get("X-Discogs-Ratelimit-Remaining")
            if remaining is not None and remaining.isdigit() and int(remaining) < 3:
                time.sleep(5)
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 429:  # rate limit 초과
            print("  · 429 rate limit — 60초 대기", flush=True)
            time.sleep(60)
            return None
        print(f"  · HTTP {e.code} 오류: {e.reason}", flush=True)
        return None
    except URLError as e:
        print(f"  · 네트워크 오류: {e.reason}", flush=True)
        return None
    except Exception as e:  # noqa
        print(f"  · 예외: {e}", flush=True)
        return None


def parse_title(raw):
    """Discogs 검색 title 은 'Artist - Title' 형식. 아티스트/제목 분리."""
    if raw and " - " in raw:
        artist, _, title = raw.partition(" - ")
        return artist.strip(), title.strip()
    return "", (raw or "").strip()


def to_release(item):
    """Discogs 검색 결과 한 항목 -> 우리 표준 형식."""
    rid = item.get("id")
    if not rid:
        return None
    artist, title = parse_title(item.get("title", ""))
    labels = item.get("label") or []
    formats = item.get("format") or []
    uri = item.get("uri") or f"/release/{rid}"
    return {
        "id": f"discogs-{rid}",
        "source": "discogs",
        "region": "overseas",
        "title": title,
        "artist": artist,
        "label": labels[0] if labels else "",
        "genres": item.get("genre") or [],
        "styles": item.get("style") or [],
        "year": item.get("year") or "",
        "format": ", ".join(formats) if formats else "Vinyl",
        "country": item.get("country") or "",
        "catno": item.get("catno") or "",
        "cover": item.get("cover_image") or item.get("thumb") or "",
        "buy_name": "Discogs",
        "buy_url": "https://www.discogs.com" + uri,
    }


# ─────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────

def load_existing():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {r["id"]: r for r in data.get("releases", [])}
    except Exception as e:  # noqa
        print(f"기존 data.json 읽기 실패 (새로 시작): {e}", flush=True)
        return {}


def prune(by_id):
    """오래된 항목 / 초과분 정리."""
    cutoff = (datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    kept = [r for r in by_id.values() if r.get("first_seen", TODAY) >= cutoff]
    # first_seen 최신순 -> 초과분 잘라내기
    kept.sort(key=lambda r: (r.get("first_seen", ""), r.get("title", "")), reverse=True)
    return kept[:MAX_ITEMS]


def main():
    token = os.environ.get("DISCOGS_TOKEN")
    if not token:
        print("ERROR: DISCOGS_TOKEN 환경변수가 없습니다.", file=sys.stderr)
        sys.exit(1)

    existing = load_existing()
    print(f"기존 항목: {len(existing)}개", flush=True)

    current_year = datetime.date.today().year
    seen_now = {}
    new_count = 0

    for genre in GENRES:
        print(f"[{genre}] 수집 중...", flush=True)
        for page in range(1, PAGES_PER_GENRE + 1):
            params = {
                "type": "release",
                "format": "Vinyl",
                "genre": genre,
                "year": current_year,
                "per_page": PER_PAGE,
                "page": page,
            }
            data = discogs_get(params, token)
            if not data:
                break
            results = data.get("results", [])
            if not results:
                break
            for item in results:
                rel = to_release(item)
                if not rel:
                    continue
                rid = rel["id"]
                if rid in seen_now:
                    continue
                if rid in existing:
                    # 이미 알던 판 -> first_seen 유지
                    rel["first_seen"] = existing[rid].get("first_seen", TODAY)
                else:
                    rel["first_seen"] = TODAY
                    new_count += 1
                seen_now[rid] = rel
            time.sleep(1.2)  # rate limit 여유 (60/분 제한)

    # 기존 것 중 이번에 안 나온 것도 유지 (정리 대상은 prune 에서)
    merged = dict(existing)
    merged.update(seen_now)

    releases = prune(merged)
    out = {
        "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "count": len(releases),
        "new_today": sum(1 for r in releases if r.get("first_seen") == TODAY),
        "releases": releases,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"완료: 전체 {len(releases)}개 / 오늘 새로 {out['new_today']}개 / 저장 {DATA_FILE}", flush=True)


if __name__ == "__main__":
    main()

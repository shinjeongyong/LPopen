#!/usr/bin/env python3
"""
LP 발매정보 수집기
  Phase 1: 해외 / Discogs
  Phase 2: 국내 / 김밥레코즈 (gimbabrecords.com, Cafe24)

- 두 소스를 각각 긁어 기존 data.json 과 합치고, 중복을 제거하며,
  '우리가 처음 발견한 날짜(first_seen)'를 각 판마다 기록합니다.
  -> 두 소스 모두 '정확한 발매일'을 주지 않으므로,
     매일 돌면서 '오늘 새로 보인 판'을 신보(NEW)로 잡는 방식입니다.
- 한 소스가 실패해도 다른 소스는 계속 돕니다(복원력).
- 표준 라이브러리만 사용합니다(외부 패키지 설치 불필요).

환경변수:
  DISCOGS_TOKEN  (해외 수집에만 필요) - Discogs 개인 액세스 토큰.
                 없으면 해외는 건너뛰고 국내만 수집합니다.

로컬 테스트:
  DISCOGS_TOKEN=xxxxx python collect.py     # 둘 다
  python collect.py                          # 국내(김밥)만
"""

import os
import re
import sys
import json
import time
import html
import datetime
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ─────────────────────────────────────────────────────────────
# 공통 설정
# ─────────────────────────────────────────────────────────────

USER_AGENT = "LPReleaseTracker/1.0 +https://github.com/shinjeongyong/LPopen"

DATA_FILE = "data.json"
MAX_ITEMS = 3000     # data.json 을 몇 개까지 유지할지
KEEP_DAYS = 90       # first_seen 이 이 일수보다 오래된 항목은 정리

TODAY = datetime.date.today().isoformat()


# ─────────────────────────────────────────────────────────────
# Phase 1 · 해외 / Discogs
# ─────────────────────────────────────────────────────────────

DISCOGS_API = "https://api.discogs.com/database/search"
DISCOGS_GENRES = [
    "Electronic", "Rock", "Jazz", "Funk / Soul",
    "Hip Hop", "Pop", "Classical", "Reggae",
]
DISCOGS_PAGES_PER_GENRE = 2
DISCOGS_PER_PAGE = 100


def discogs_get(params, token):
    """Discogs 검색 API 한 번 호출. 실패 시 None."""
    url = f"{DISCOGS_API}?{urlencode(params)}"
    req = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Authorization": f"Discogs token={token}",
    })
    try:
        with urlopen(req, timeout=30) as resp:
            remaining = resp.headers.get("X-Discogs-Ratelimit-Remaining")
            if remaining is not None and remaining.isdigit() and int(remaining) < 3:
                time.sleep(5)
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 429:
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


def discogs_parse_title(raw):
    """Discogs title 은 'Artist - Title' 형식."""
    if raw and " - " in raw:
        artist, _, title = raw.partition(" - ")
        return artist.strip(), title.strip()
    return "", (raw or "").strip()


def discogs_to_release(item):
    rid = item.get("id")
    if not rid:
        return None
    artist, title = discogs_parse_title(item.get("title", ""))
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


def collect_discogs(token, existing, seen_now):
    """해외 수집. seen_now 에 채워넣음."""
    current_year = datetime.date.today().year
    for genre in DISCOGS_GENRES:
        print(f"[해외/{genre}] 수집 중...", flush=True)
        for page in range(1, DISCOGS_PAGES_PER_GENRE + 1):
            data = discogs_get({
                "type": "release",
                "format": "Vinyl",
                "genre": genre,
                "year": current_year,
                "per_page": DISCOGS_PER_PAGE,
                "page": page,
            }, token)
            if not data:
                break
            results = data.get("results", [])
            if not results:
                break
            for item in results:
                rel = discogs_to_release(item)
                if rel:
                    register(rel, existing, seen_now)
            time.sleep(1.2)


# ─────────────────────────────────────────────────────────────
# Phase 2 · 국내 / 김밥레코즈 (Cafe24)
# ─────────────────────────────────────────────────────────────

GIMBAB_BASE = "https://gimbabrecords.com"
GIMBAB_VINYL_CATE = 25          # 바이닐 상위 카테고리
GIMBAB_PAGES_PER_GENRE = 1      # 장르당 신상품 몇 페이지 (신보는 1페이지로 충분)
GIMBAB_SORT_NEW = 5             # sort_method=5 → 신상품순

# 김밥 바이닐 하위 장르 이름 → 화면에 쓸 장르(해외와 통일).
# 실행 시 실제 카테고리 번호는 자동으로 찾고, 이름만 여기서 맞춥니다.
# 키는 소문자·공백정리 후 비교합니다.
GIMBAB_GENRE_MAP = {
    "korean": "Korean",
    "pop/rock": "Rock",
    "electronic/dance": "Electronic",
    "r&b/soul/funk": "Funk / Soul",
    "hip hop": "Hip Hop",
    "jazz/ blues": "Jazz",
    "jazz/blues": "Jazz",
    "classical/crossover": "Classical",
    "reggae": "Reggae",
    "soundtracks": "Soundtracks",
    "french": "French",
    "brazilian": "Brazilian",
    "latin": "Latin",
    "european / african / asian": "World",
    "holiday": "Holiday",
    "japanese": "Japanese",
    # ESSENTIAL, Library 는 장르가 아니라 큐레이션이라 제외.
}


def http_get(url):
    """평범한 GET. 실패 시 빈 문자열."""
    req = Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (compatible; LPReleaseTracker/1.0; "
                       "+https://github.com/shinjeongyong/LPopen)"),
        "Accept-Language": "ko,en;q=0.8",
    })
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read()
            # Cafe24 는 utf-8. 혹시 모를 깨짐은 무시하고 디코드.
            return raw.decode("utf-8", errors="replace")
    except HTTPError as e:
        print(f"  · HTTP {e.code} 오류: {e.reason}", flush=True)
    except URLError as e:
        print(f"  · 네트워크 오류: {e.reason}", flush=True)
    except Exception as e:  # noqa
        print(f"  · 예외: {e}", flush=True)
    return ""


def _norm(s):
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


def gimbab_discover_genres():
    """
    바이닐 상위 페이지에서 하위 장르 메뉴(이름+cate_no)를 읽어옴.
    반환: [(cate_no, 화면장르), ...]
    실패하면 빈 리스트 -> 호출부에서 상위 카테고리 통째로 폴백.
    """
    url = f"{GIMBAB_BASE}/product/list.html?cate_no={GIMBAB_VINYL_CATE}"
    doc = http_get(url)
    if not doc:
        return []
    found = {}
    # <a ... href=".../list.html?cate_no=NN...">장르이름</a>
    for m in re.finditer(
        r'href="[^"]*?list\.html\?cate_no=(\d+)[^"]*?"[^>]*>([^<]+)</a>', doc):
        cate_no = m.group(1)
        name = _norm(m.group(2)).lower().rstrip("()").strip()
        if not name:
            continue
        disp = GIMBAB_GENRE_MAP.get(name)
        if disp and cate_no not in found:
            found[cate_no] = disp
    return list(found.items())


# 상품 목록에서 상품 하나 = Cafe24 반복 마커 'xans-record-' 로 구분
_PROD_URL = re.compile(r'/product/[^"\']*?/(\d+)/category/(\d+)/display/', re.I)
_PROD_URL_Q = re.compile(r'/product/detail\.html\?product_no=(\d+)', re.I)
_ALT = re.compile(r'\balt="([^"]*)"')
_PRICE = re.compile(r'([0-9]{1,3}(?:,[0-9]{3})+)\s*원')
_IMG = re.compile(r'\b(?:src|data-original|ec-data-src)="([^"]+?\.(?:jpg|jpeg|png|gif)[^"]*)"', re.I)


def _parse_format_from_title(title):
    """제목 끝 괄호에서 LP/Vinyl 포맷 힌트만 추출. 없으면 'Vinyl'."""
    fmts = []
    for grp in re.findall(r"\(([^()]*)\)", title):
        if re.search(r"\b(\d?LP|Vinyl|EP|7\"|10\"|12\")\b", grp, re.I):
            fmts.append(_norm(grp))
    return ", ".join(fmts) if fmts else "Vinyl"


def gimbab_parse_list(doc, genre):
    """목록 HTML → 표준 레코드 리스트."""
    out = []
    chunks = doc.split("xans-record-")
    for chunk in chunks[1:]:
        m = _PROD_URL.search(chunk) or _PROD_URL_Q.search(chunk)
        if not m:
            continue
        product_no = m.group(1)

        # 상품명: 큰 이미지의 alt 가 상품명. 가장 그럴듯한(가장 긴) alt 선택.
        alts = [_norm(a) for a in _ALT.findall(chunk)]
        alts = [a for a in alts if a and a not in ("추천", "New", "품절", "NEW", "Sold Out")]
        name = max(alts, key=len) if alts else ""
        if not name:
            continue

        # 커버 이미지
        cover = ""
        im = _IMG.search(chunk)
        if im:
            cover = im.group(1)
            if cover.startswith("//"):
                cover = "https:" + cover

        # 가격 (있으면 저장; 화면 표시는 index.html 별도)
        pm = _PRICE.search(chunk)
        price = pm.group(1) + "원" if pm else ""

        # 'Artist / Album (...)' 분리
        if " / " in name:
            artist, _, title = name.partition(" / ")
            artist, title = _norm(artist), _norm(title)
        else:
            artist, title = "", name

        buy_url = urljoin(
            GIMBAB_BASE,
            f"/product/detail.html?product_no={product_no}&cate_no={GIMBAB_VINYL_CATE}")

        out.append({
            "id": f"gimbab-{product_no}",
            "source": "gimbab",
            "region": "korea",
            "title": title,
            "artist": artist,
            "label": "",
            "genres": [genre] if genre else [],
            "styles": [],
            "year": "",
            "format": _parse_format_from_title(name),
            "country": "KR",
            "catno": "",
            "cover": cover,
            "price": price,
            "buy_name": "김밥레코즈",
            "buy_url": buy_url,
        })
    return out


def collect_gimbab(existing, seen_now):
    """국내(김밥) 수집. seen_now 에 채워넣음."""
    genres = gimbab_discover_genres()
    if genres:
        print(f"[국내/김밥] 바이닐 장르 {len(genres)}개 발견", flush=True)
        targets = genres
    else:
        # 폴백: 장르 못 찾으면 바이닐 전체를 장르 없이 긁음
        print("[국내/김밥] 장르 자동발견 실패 → 바이닐 전체 신상품만 수집", flush=True)
        targets = [(str(GIMBAB_VINYL_CATE), "")]

    for cate_no, genre in targets:
        label = genre or "바이닐 전체"
        print(f"[국내/김밥/{label}] 수집 중...", flush=True)
        for page in range(1, GIMBAB_PAGES_PER_GENRE + 1):
            url = (f"{GIMBAB_BASE}/product/list.html?cate_no={cate_no}"
                   f"&sort_method={GIMBAB_SORT_NEW}&page={page}")
            doc = http_get(url)
            if not doc:
                break
            items = gimbab_parse_list(doc, genre)
            if not items:
                break
            for rel in items:
                # 같은 판이 여러 장르에 걸치면 장르를 합쳐줌
                rid = rel["id"]
                if rid in seen_now and genre:
                    g = seen_now[rid].get("genres", [])
                    if genre not in g:
                        g.append(genre)
                    continue
                register(rel, existing, seen_now)
            time.sleep(1.0)  # 예의상 간격


# ─────────────────────────────────────────────────────────────
# 병합 · 저장
# ─────────────────────────────────────────────────────────────

def register(rel, existing, seen_now):
    """first_seen 을 붙여 seen_now 에 등록."""
    rid = rel["id"]
    if rid in seen_now:
        return
    if rid in existing:
        rel["first_seen"] = existing[rid].get("first_seen", TODAY)
    else:
        rel["first_seen"] = TODAY
    seen_now[rid] = rel


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
    cutoff = (datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    kept = [r for r in by_id.values() if r.get("first_seen", TODAY) >= cutoff]
    kept.sort(key=lambda r: (r.get("first_seen", ""), r.get("title", "")), reverse=True)
    return kept[:MAX_ITEMS]


def main():
    existing = load_existing()
    print(f"기존 항목: {len(existing)}개", flush=True)

    seen_now = {}

    # 해외 (토큰 있을 때만)
    token = os.environ.get("DISCOGS_TOKEN")
    if token:
        try:
            collect_discogs(token, existing, seen_now)
        except Exception as e:  # noqa
            print(f"해외 수집 중 오류(건너뜀): {e}", flush=True)
    else:
        print("DISCOGS_TOKEN 없음 → 해외 수집 건너뜀 (국내만 진행)", flush=True)

    # 국내 (김밥)
    try:
        collect_gimbab(existing, seen_now)
    except Exception as e:  # noqa
        print(f"국내 수집 중 오류(건너뜀): {e}", flush=True)

    if not seen_now:
        print("수집 결과 없음 — 기존 data.json 유지, 종료", flush=True)
        return

    # 이번에 안 나온 기존 항목도 유지 (정리는 prune 에서)
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

    kr = sum(1 for r in releases if r.get("region") == "korea")
    ov = sum(1 for r in releases if r.get("region") == "overseas")
    print(f"완료: 전체 {len(releases)}개 (해외 {ov} / 국내 {kr}) / "
          f"오늘 새로 {out['new_today']}개 / 저장 {DATA_FILE}", flush=True)


if __name__ == "__main__":
    main()

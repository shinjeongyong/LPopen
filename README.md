# LP 발매정보 트래커

매일 자동으로 LP(바이닐) 발매정보를 모아서 웹으로 보여주는 개인 앱입니다.
**Phase 1**: 해외 발매(Discogs) + 전체리스트 / 장르별 / 검색 / 구매링크.

```
매일 새벽(GitHub Actions) → Discogs에서 신보 수집 → data.json 갱신 → 웹페이지가 읽어서 표시
```

서버 필요 없음, 전부 무료(GitHub Pages + Actions).

---

## 파일 구성

| 파일 | 역할 |
|---|---|
| `index.html` | 화면 (전체리스트·장르·검색·구매링크). 이거 하나가 앱 |
| `collect.py` | Discogs에서 데이터 긁어 `data.json` 만드는 수집기 |
| `data.json` | 수집된 발매정보 (지금은 미리보기용 샘플이 들어있음) |
| `.github/workflows/collect.yml` | 매일 자동 실행 설정 |

파이썬 외부 라이브러리 설치 불필요 (표준 라이브러리만 사용).

---

## 설치 (10분)

### 1. GitHub 저장소 만들기
- 새 저장소 생성 후 이 폴더의 파일을 전부 올립니다.
- 저장소를 `공개(Public)`로 두면 Pages가 무료입니다.

### 2. Discogs 토큰 발급
1. https://www.discogs.com 로그인 → Settings → **Developers**
2. **Generate new token** 클릭 → 나온 토큰 문자열 복사
   (개인 액세스 토큰. 앱 만들 필요 없이 이거 하나면 됩니다.)

### 3. 토큰을 저장소에 숨기기 ⚠️ 중요
> 토큰은 **절대 코드나 data.json에 넣지 마세요.** 아래처럼 Secrets에만 넣습니다.

- 저장소 → **Settings → Secrets and variables → Actions → New repository secret**
- Name: `DISCOGS_TOKEN`
- Value: 방금 복사한 토큰 → 저장

### 4. 자동 실행 켜기
- 저장소 → **Actions** 탭 → 워크플로우 활성화
- **`LP 발매정보 수집`** 선택 → **Run workflow** 로 한 번 손수 실행
- 몇 분 뒤 `data.json`이 실제 데이터로 갱신되면 성공.
- 이후 매일 새벽 7시(KST)에 자동으로 돕니다.

### 5. 웹페이지 켜기 (GitHub Pages)
- 저장소 → **Settings → Pages**
- Source: `Deploy from a branch` → Branch: `main` / `/ (root)` → Save
- 잠시 뒤 `https://<아이디>.github.io/<저장소이름>/` 으로 접속되면 끝.

---

## 커스터마이즈

`collect.py` 위쪽 설정만 건드리면 됩니다.

- `GENRES` — 긁어올 장르 목록 (Electronic, Rock, Jazz, Hip Hop …)
- `PAGES_PER_GENRE` — 장르당 가져올 페이지 수 (1페이지 = 100장)
- `KEEP_DAYS` — 며칠 지난 항목까지 유지할지 (기본 90일)
- `collect.yml`의 `cron` — 자동 실행 시간

---

## 참고: 데이터의 한계 (해외/Discogs)

Discogs API는 "발매일" 검색을 지원하지 않습니다. 그래서 이 앱은
**매일 돌면서 "그날 처음 발견한 판"을 신보(NEW)로 표시**하는 방식입니다.
정확한 실제 발매일순은 아니지만, 매일 쌓이면서 새로 등장하는 판이 위로 올라옵니다.

## 다음 단계 (Phase 2)

국내 레코드샵(김밥레코즈·도프레코드·향뮤직·예스24 LP 등)의 신보 페이지를
하나씩 붙여서 `data.json`에 `region: "korea"`로 합칩니다.
화면은 이미 `국내/해외` 필터가 준비돼 있어 데이터만 추가하면 바로 보입니다.

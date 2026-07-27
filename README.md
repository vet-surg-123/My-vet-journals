# 소동물 논문 큐레이션 (Vet Journal List)

익명의 **소동물 수의학 논문 자동 큐레이션 웹사이트**.
PubMed에서 매일 아침 자동으로 논문을 수집 → 분야·저널로 분류 → 웹 목록으로 표시합니다.

```
① 매일 자동 실행(GitHub Actions)
        │
② 데이터 수집(fetch_papers.py → PubMed)
        │
③ 데이터 파일(papers.json)
        │
④ 웹 화면(index.html)
```

데이터(수집·분류 로직)와 화면(디자인)이 분리되어 있어 따로 수정할 수 있습니다.

---

## 파일 구성

| 파일 | 역할 |
|------|------|
| `index.html` | 웹 화면. `papers.json` 을 읽어 목록·필터·검색을 표시 |
| `fetch_papers.py` | 수집기. PubMed에서 논문을 가져와 분류·요약해 `papers.json` 생성 |
| `papers.json` | 데이터 파일. 자동 실행 때마다 새로 채워짐 (처음엔 비어 있음) |
| `.github/workflows/update_papers.yml` | 매일 아침 7시(KST) 자동 실행 |

---

## 배포 방법 (GitHub Pages — 무료, 권장)

1. GitHub에서 새 저장소(repository)를 만들고 이 폴더의 파일을 **폴더 구조 그대로** 올립니다.
   - `.github/workflows/update_papers.yml` 경로가 반드시 유지되어야 합니다.
2. 저장소 **Settings → Pages** 에서 Source를 `main` 브랜치 `/ (root)` 로 지정 → 사이트 주소가 생깁니다.
3. **Settings → Actions → General → Workflow permissions** 를 `Read and write permissions` 로 설정.
4. **Actions 탭 → Update Papers Daily → Run workflow** 로 한 번 수동 실행하면 `papers.json` 이 채워지고 화면에 논문이 나타납니다.
   - 이후에는 매일 아침 7시(KST)에 자동 갱신됩니다.

### (선택) AI 한국어 요약 켜기
- 수집기는 `ANTHROPIC_API_KEY` 가 있을 때만 각 논문의 한국어 요약을 생성합니다(없으면 요약란은 비어 있고 나머지는 정상).
- **Settings → Secrets and variables → Actions → New repository secret** 에서
  이름 `ANTHROPIC_API_KEY`, 값에 API 키를 넣으면 다음 실행부터 요약이 채워집니다. (실행당 소액 비용)
- 이미 요약된 논문은 캐시되어 재요약하지 않습니다.

---

## 내 PC에서 한 번에 데이터 채우기 (선택)

배포 없이 지금 바로 목록을 보고 싶으면, 인터넷 되는 PC에서:

```bash
python fetch_papers.py          # papers.json 생성
# (요약도 원하면)  ANTHROPIC_API_KEY=sk-... python fetch_papers.py
python -m http.server 8000      # 그 후 브라우저에서 http://localhost:8000
```

> 참고: 이 사이트를 만든 클라우드 환경에서는 PubMed(NCBI) 접속이 차단되어 있어
> `papers.json` 을 미리 채우지 못했습니다. GitHub Actions나 내 PC에서는 정상 동작합니다.

---

## 수집 기간·누적·중복 제거

- **시작일 고정**: 기본적으로 **2026-01-01부터 오늘까지** 모읍니다(`START_DATE`).
- **매일 아침 누적**: 실행할 때마다 결과를 기존 `papers.json` 에 **병합(추가)** 합니다.
- **중복 없음**: 논문 고유번호(PMID) 기준으로 합치므로 같은 논문이 두 번 들어가지 않습니다.
- **안전장치**: 어쩌다 수집이 0편이어도(네트워크 오류 등) 기존 누적분을 **덮어쓰지 않고 그대로 유지**합니다.

개수·기간·속도는 환경변수로 조절합니다(코드 수정 불필요).

| 환경변수 | 뜻 | 기본값 |
|---|---|---|
| `START_DATE` | 수집 시작일(고정). `2026-01-01` 또는 `2026/01/01` | `2026-01-01` |
| `INCREMENTAL_DAYS` | `0`=매번 시작일~오늘 전체 조회(누락 없음). 속도용으로 `30` 등 가능 | `0` |
| `MAX_RESULTS` | 최대 개수. `0` = 제한 없음(전부, 수만 개) | `0` |
| `ABSTRACT_MAXLEN` | 초록 저장 최대 글자수. `0` = 전체 | `0` |
| `NCBI_API_KEY` | 있으면 3→10 req/s 로 3배 빨라짐(없어도 동작) | (없음) |

**내 PC에서 첫 백필 예시** (2026-01-01부터 전부, 초록 800자로 용량 절약):
```bash
START_DATE=2026-01-01 ABSTRACT_MAXLEN=800 python fetch_papers.py
```

> 매일 자동 실행(GitHub Actions)은 기본이 `INCREMENTAL_DAYS=0` 이라 매일 **2026-01-01~오늘 전체를 다시 조회 후 병합**합니다.
> 이미 있는 논문은 요약까지 캐시되어 재작업하지 않으므로 빠르고, 새 논문만 추가됩니다.
> 조회량을 줄이고 싶으면 `INCREMENTAL_DAYS=30` 처럼 최근 창만 조회해도 누적분은 그대로 유지됩니다.

> ⚠️ **용량 주의**: 논문이 수만 개면 `papers.json` 이 수십 MB가 될 수 있습니다. 화면은 60개씩 "더 보기"로 나눠 그려 느려지지 않지만, 파일이 크면 첫 로딩이 느려지고 GitHub 파일 한도(100MB)도 있으니 대량일수록 `ABSTRACT_MAXLEN=800` 을 권장합니다.

---

## 규칙 요약 (수정하려면 `fetch_papers.py` 안의 🔧 표시)

- **대상**: 소동물(dogs, cat, feline, canine, small animal …), **2026-01-01부터 누적**(조절 가능), 수집량 제한 없음, PMID 기준 중복 제거
- **제외 종**: 돼지·소·말·양·가금·어류·설치류 등 생산·실험동물
- **제외 주제**: 심장(cardiology)·치과(dentistry) 관련 논문은 통째로 제외
- **저널 화이트리스트**: JVIM, JSAP, JAVMA, JFMS, Vet Surg, VCOT, VRU, Vet Comp Oncol, AJVR, Vet J, BMC Vet Res, Animals (Basel), Front Vet Sci
- **분야 분류 (8개)**:
  - **신경종양**을 **최우선**으로 분류: (신경 단어) AND (종양 단어), 또는 `glioma·meningioma·schwannoma·spinal tumor` 등 고유 단어 단독
  - 그다음 순서대로: 신경외과 → 정형외과 → 외과 → 해부 → 영상 → 내과 → 종양 (위에서 처음 걸리는 하나로 확정)
- **미분류(기타)** 논문은 화면에 넣지 않음
- **익명 유지**: 병원·기관·개인 정보, 브랜딩 요소 없음

## 화면 기능
- 분야별(신경종양 포함 8개) / 저널별 필터, 제목·초록·요약 검색
- 60개씩 "**더 보기**" 페이지네이션 (수만 개 대응)
- 최근 14일 논문에 **NEW** 뱃지
- 읽음 표시(내 브라우저에만 저장), 초록 보기, 제목 → PubMed 원문 링크

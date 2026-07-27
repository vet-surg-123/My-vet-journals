# -*- coding: utf-8 -*-
"""
소동물 논문 큐레이션 — 논문 수집기 (대용량판)
PubMed에서 '소동물 임상/외과' 논문을 수집 → 분야 분류 → (선택) 한국어 요약 → papers.json 저장.

★ '🔧' 표시가 '내가 나중에 고치는 곳' 입니다.
★ 수만 개까지 지원: history server + retstart 페이지네이션으로 9,999개 한계를 넘김.

■ 환경변수로 조절 (없으면 기본값)
    LOOKBACK_DAYS   수집 기간(일).            기본 60.   예) 대량 백필: LOOKBACK_DAYS=3650 (10년)
    MAX_RESULTS     최대 수집 개수.           기본 0(=제한 없음, 전부)
    ABSTRACT_MAXLEN 초록 저장 최대 글자수.    기본 0(=전체).  용량 줄이려면 예) 800
    NCBI_API_KEY    NCBI 키(있으면 3→10 req/s 로 빨라짐, 없어도 동작)
    ANTHROPIC_API_KEY  한국어 요약용(없으면 요약만 생략)
"""

import urllib.request
import urllib.parse
import json
import time
import os
import re
from datetime import datetime, timedelta

# ── 환경변수 ────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NCBI_API_KEY      = os.environ.get("NCBI_API_KEY", "")

# 🔧 수집 시작일(고정). 이 날짜부터 오늘까지 모음. (예전의 '최근 N일' 방식 대체)
#    형식 2026-01-01 또는 2026/01/01 모두 OK. env START_DATE 로 덮어쓸 수 있음.
START_DATE = os.environ.get("START_DATE", "2026-01-01").replace("-", "/")
# 🔧 증분 수집 창(일). 0이면 매번 START_DATE~오늘 전체를 조회(권장, 누락 없음).
#    속도가 필요하면 예) 30 → 최근 30일만 조회하고 기존 데이터에 '추가'로 병합.
INCREMENTAL_DAYS = int(os.environ.get("INCREMENTAL_DAYS", "0"))
# 🔧 최대 수집 개수 — 0이면 제한 없음(조건 맞는 것 전부, 수만 개 가능)
MAX_RESULTS    = int(os.environ.get("MAX_RESULTS", "0"))
# 🔧 초록 저장 최대 글자수 — 0이면 전체 저장. 수만 개면 파일이 커지니 800 등으로 제한 권장
ABSTRACT_MAXLEN = int(os.environ.get("ABSTRACT_MAXLEN", "0"))

# 🔧 한 번에 받아오는 배치 크기 (URL/응답 크기 안전값)
BATCH = 200
# NCBI 예의상 딜레이: 키 있으면 10 req/s, 없으면 3 req/s
DELAY = 0.11 if NCBI_API_KEY else 0.34

# 🔧 소동물 한정 키워드 (제목/초록에 하나라도 있어야 수집)
SMALL_ANIMAL_TERMS = ["dogs", "dog", "cats", "cat", "feline", "canine", "small animal"]

# 🔧 제외할 종(種): 이 단어가 제목/초록에 있으면 아예 수집 안 함 (생산·실험동물 노이즈 제거)
EXCLUDE_SPECIES = [
    "swine", "pig", "piglet", "porcine", "poultry", "chicken", "broiler", "hen", "rooster",
    "cattle", "bovine", "calf", "cow", "buffalo", "sheep", "ovine", "goat", "caprine",
    "horse", "equine", "foal", "fish", "shrimp", "rabbit", "mouse", "mice", "murine", "rat",
]

# 🔧 주제 제외: 이 단어가 제목/초록에 있으면 논문을 버림 (심장·치과 제외 요청 반영)
EXCLUDE_TOPIC_KEYWORDS = [
    # 심장(cardiology) 제거
    "cardiac", "cardiolog", "cardiomyopath", "mitral", "myxomatous",
    "echocardiograph", "congestive heart", "valvular",
    # 치과(dentistry) 제거
    "dental", "periodontal", "endodontic", "dentistry", "tooth", "teeth", "malocclusion",
]

# 🔧 '기타(어디에도 안 걸린 논문)'를 버릴지. True면 화면에서 사라짐.
DROP_OTHER = True

# 🔧 저널 화이트리스트 (이 저널만 수집).
JOURNAL_WHITELIST = [
    "J Vet Intern Med",            # 내과 (JVIM)
    "J Small Anim Pract",          # 소동물 임상 (JSAP)
    "J Am Vet Med Assoc",          # JAVMA
    "J Feline Med Surg",           # 고양이
    "Vet Surg",                    # 외과
    "Vet Comp Orthop Traumatol",   # 정형 (VCOT)
    "Vet Radiol Ultrasound",       # 영상 (VRU)
    "Vet Comp Oncol",              # 종양
    "Am J Vet Res",                # AJVR
    "Vet J",                       # The Veterinary Journal
    "BMC Vet Res",                 # BMC
    "Animals (Basel)",             # Animals (MDPI)
    "Front Vet Sci",               # Frontiers
]
USE_WHITELIST = True

# 🔧 분야 우선순위 = 아래 순서. 위에서 처음 걸리는 분야로 확정.
#    (신경종양은 아래 CATEGORY_KEYWORDS 보다 먼저, categorize()에서 특별 규칙으로 처리)
CATEGORY_KEYWORDS = {
    # 신경외과 (종양이 아닌 신경 논문)  ※ 'cranial'(방향어)은 일부러 미포함
    "neurosurgery": ["neurosurgery", "neuro", "spinal", "brain", "vestibular", "disc",
                     "cerebell", "myelopathy", "hemilaminectomy", "intervertebral",
                     "trigeminal", "cranial nerve", "nerve root", "brachial plexus"],
    # 관절경 (정형외과보다 먼저 검사 → 관절경 논문을 따로 분리)
    #   ※ 'arthroscopy/arthroscopic/arthroscope'만 사용. 'arthro'(단독)는 arthrodesis·
    #     arthroplasty·arthritis 까지 잡아 오분류되므로 일부러 뺌.
    "arthroscopy":  ["arthroscopy", "arthroscopic", "arthroscope", "arthroscopically"],
    # 정형외과 (관절경 외 정형)
    "orthopedics":  ["fracture", "orthopedic", "osteotomy", "luxation", "cruciate", "tplo",
                     "patellar", "arthro", "arthrodesis",
                     "arthroplasty", "osteosynthesis"],
    # 외과 (일반 수술)
    "surgery":      ["surgery", "surgical", "laparotomy", "laparoscopy", "excision",
                     "resection", "repair", "anastomosis", "celiotomy", "thoracotomy",
                     "herniorrhaphy", "wound", "incision"],
    # 해부
    "anatomy":      ["anatomy", "anatomic", "anatomical", "morphometric", "morphometry",
                     "cadaver", "cadaveric", "dissection"],
    # 영상
    "imaging":      ["radiograph", "radiographic", "radiography", "mri", "magnetic resonance",
                     "ct", "computed tomography", "ultrasound", "ultrasonographic",
                     "ultrasonography", "imaging"],
    # 내과
    "internal":     ["renal", "kidney", "hepatic", "liver", "endocrine", "diabetes",
                     "pancreatitis", "gastrointestinal", "gastro", "enteropathy", "azotemia",
                     "hypothyroid", "hyperadrenocorticism", "immune-mediated", "proteinuria"],
    # 종양 (신경 아닌 종양)
    "oncology":     ["tumor", "tumour", "cancer", "carcinoma", "sarcoma", "osteosarcoma",
                     "lymphoma", "mast cell", "melanoma", "adenoma", "neoplasia",
                     "neoplastic", "oncology", "oncologic", "malignant", "metastasis",
                     "metastatic"],
}

# 🔧 신경종양(0순위) = '신경계 종양'을 분명히 뜻하는 구체적 표현이 있을 때만.
#   ※ 예전엔 (신경 단어) AND (종양 단어) 방식이라, 'cranial'(=머리쪽 방향, 예: cranial
#     mediastinum)·'nerve' 같은 흔한 단어가 초록의 종양 감별진단과 겹쳐 대량 오분류됐음.
#     그래서 아래처럼 신경계 종양임이 확실한 표현(entity/phrase)만 사용한다.
NEUROONCO_PHRASES = [
    # 신경계 종양 고유 명칭
    "glioma", "glioblastoma", "meningioma", "astrocytoma", "ependymoma", "oligodendroglioma",
    "medulloblastoma", "gliomatosis", "ganglioglioma", "neurocytoma", "meningeal sarcoma",
    "schwannoma", "neurofibroma", "neurofibrosarcoma",
    "nerve sheath tumor", "nerve sheath tumour", "peripheral nerve sheath",
    "choroid plexus tumor", "choroid plexus tumour", "choroid plexus carcinoma",
    "choroid plexus papilloma",
    # '부위 + 종양' 조합 표현
    "brain tumor", "brain tumour", "brain neoplas", "cerebral tumor", "cerebral neoplas",
    "intracranial tumor", "intracranial tumour", "intracranial neoplas",
    "spinal cord tumor", "spinal cord tumour", "spinal cord neoplas",
    "spinal tumor", "spinal tumour", "vertebral tumor", "vertebral tumour", "vertebral neoplas",
    "pituitary tumor", "pituitary tumour", "pituitary adenoma", "pituitary macroadenoma",
    "pituitary carcinoma", "pituitary neoplas",
    "cns lymphoma", "spinal lymphoma", "central nervous system lymphoma",
    "cns neoplas", "central nervous system neoplas",
]

CATEGORY_LABELS = {
    "neurooncology": "신경종양",
    "neurosurgery": "신경외과", "arthroscopy": "관절경", "orthopedics": "정형외과",
    "surgery": "외과", "anatomy": "해부", "imaging": "영상", "internal": "내과",
    "oncology": "종양",
}

# 🔧 '단어 전체 일치'로만 인정할 짧은 토큰 (부분일치 오분류 방지). ct/mri는 자동(3글자↓).
WHOLE_WORD_TOKENS = {"disc", "cns"}

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


def fmt_date(item):
    """PubMed 날짜를 (표시용, 정렬용)으로 정규화.
    - sortpubdate(항상 YYYY/MM/DD)를 우선 사용 → 월·일이 항상 나옴.
    - 정렬용은 YYYY-MM-DD 형식(문자열로 비교해도 날짜순).
    """
    raw = item.get("sortpubdate") or item.get("epubdate") or item.get("pubdate") or ""
    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", raw)          # 숫자형 2026/07/15
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y} {MONTHS[mo - 1]} {d:02d}", f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.match(r"(\d{4})(?:\s+([A-Za-z]{3,}))?(?:\s+(\d{1,2}))?", raw)  # 문자형 2026 Jul 15
    if m:
        y = int(m.group(1))
        mon, d = m.group(2), m.group(3)
        mo = MONTHS.index(mon[:3].title()) + 1 if mon and mon[:3].title() in MONTHS else 0
        disp = str(y)
        if mo:
            disp += f" {MONTHS[mo - 1]}"
        if d:
            disp += f" {int(d):02d}"
        sortk = f"{y:04d}-{mo:02d}-{int(d) if d else 0:02d}"
        return disp, sortk
    return raw, raw


def _api(url):
    """모든 eutils URL 뒤에 api_key 붙임 (있을 때만)."""
    if NCBI_API_KEY:
        url += ("&" if "?" in url else "?") + "api_key=" + NCBI_API_KEY
    return url


# 🔧 요약 스타일 — 여기(프롬프트)만 고치면 요약 문체가 바뀜. (한글+영어 전문용어 병기)
def summarize_ko(title, abstract):
    if not ANTHROPIC_API_KEY or not abstract:        # 키 없으면 요약 생략
        return ""
    prompt = (
        "너는 전문 수의사를 위해 논문 초록을 아주 짧게 요약한다.\n"
        "요구사항:\n"
        "1) 보자마자 무슨 내용인지 알 수 있게 1~2문장으로 핵심만 (연구 대상·중재·결론 위주).\n"
        "2) 의학·해부·수술·질환·약물 등 '명사'와 고유 용어는 반드시 영어 원어 그대로 쓴다 (한글 번역·괄호 병기 금지).\n"
        "3) 동작을 나타내는 '동사·서술'은 한국어로 자연스럽게 푼다.\n"
        "   예) 'remove the brachialis tendon' → 'brachialis tendon 제거',\n"
        "       'treated with TPLO' → 'TPLO로 치료',\n"
        "       'diagnosed with intracranial meningioma' → 'intracranial meningioma 진단'.\n"
        "4) 인사말·군더더기 없이 요약 문장만 출력.\n\n"
        f"제목: {title}\n\n초록: {abstract}"
    )
    try:
        payload = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 240,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=payload,
            headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_API_KEY,
                     "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode())["content"][0]["text"].strip()
    except Exception as e:
        print(f"  요약 오류: {e}")
        return ""


def match_kw(text, kw):
    kw = kw.lower()
    if len(kw) <= 3 or kw in WHOLE_WORD_TOKENS:      # 짧은 약어 → 단어 전체 일치
        return re.search(r"\b" + re.escape(kw) + r"\b", text) is not None
    return kw in text


def categorize(title, abstract):
    text = (title + " " + (abstract or "")).lower()
    # 0순위: 신경계 종양 (구체적 표현이 있을 때만 — 느슨한 AND 폐기)
    if any(match_kw(text, k) for k in NEUROONCO_PHRASES):
        return "neurooncology"
    # 1순위~: 나머지 분야 (사전 순서대로 검사)
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(match_kw(text, k) for k in kws):
            return cat
    return "other"


def is_excluded_topic(title, abstract):
    text = (title + " " + (abstract or "")).lower()
    return any(match_kw(text, k) for k in EXCLUDE_TOPIC_KEYWORDS)


def journal_ok(source):
    s = (source or "").lower()
    if USE_WHITELIST:
        return any(w.lower() in s for w in JOURNAL_WHITELIST)
    return True


def fetch_json(url):
    req = urllib.request.Request(_api(url), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode())


def fetch_text(url):
    req = urllib.request.Request(_api(url), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode()


def load_previous_summaries():
    cache = {}
    if os.path.exists("papers.json"):
        try:
            for p in json.load(open("papers.json", encoding="utf-8")).get("papers", []):
                if p.get("summary_ko"):
                    cache[p["id"]] = p["summary_ko"]
        except Exception:
            pass
    return cache


def load_existing_papers():
    """기존 papers.json 의 논문 전체를 {id: paper} 로 로드 (누적·병합용)."""
    existing = {}
    if os.path.exists("papers.json"):
        try:
            for p in json.load(open("papers.json", encoding="utf-8")).get("papers", []):
                if p.get("id"):
                    existing[p["id"]] = p
        except Exception:
            pass
    return existing


def build_query():
    date_to = datetime.now().strftime("%Y/%m/%d")
    if INCREMENTAL_DAYS > 0:
        date_from = (datetime.now() - timedelta(days=INCREMENTAL_DAYS)).strftime("%Y/%m/%d")
    else:
        date_from = START_DATE
    sa = " OR ".join(f'"{t}"[tiab]' for t in SMALL_ANIMAL_TERMS)
    q = f"({sa})"
    if USE_WHITELIST and JOURNAL_WHITELIST:
        q += " AND (" + " OR ".join(f'"{j}"[jour]' for j in JOURNAL_WHITELIST) + ")"
    q += f' AND ("{date_from}"[PDAT] : "{date_to}"[PDAT])'
    if EXCLUDE_SPECIES:
        q += " NOT (" + " OR ".join(f'"{t}"[tiab]' for t in EXCLUDE_SPECIES) + ")"
    return q


def esearch_history(query):
    """usehistory=y 로 검색 → (총 개수, WebEnv, QueryKey) 반환. 실제 ID는 안 받음."""
    term = urllib.parse.quote(query)
    url = f"{BASE}esearch.fcgi?db=pubmed&term={term}&retmax=0&usehistory=y&retmode=json&sort=date"
    res = fetch_json(url).get("esearchresult", {})
    count = int(res.get("count", "0"))
    return count, res.get("webenv", ""), res.get("querykey", "")


def _strip(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def parse_details(xml):
    """efetch XML → {pmid: {abstract, affiliations, keywords, mesh}}."""
    out = {}
    for block in re.findall(r"<PubmedArticle>(.*?)</PubmedArticle>", xml, re.DOTALL):
        pmid_m = re.search(r"<PMID[^>]*>(\d+)</PMID>", block)
        if not pmid_m:
            continue
        pmid = pmid_m.group(1)
        # 초록 (구조화 초록이면 라벨도 앞에 붙임)
        parts = []
        for m in re.finditer(r"<AbstractText([^>]*)>(.*?)</AbstractText>", block, re.DOTALL):
            attrs, body = m.group(1), _strip(m.group(2))
            lbl = re.search(r'Label="([^"]+)"', attrs)
            parts.append((lbl.group(1) + ": " + body) if lbl and body else body)
        abstract = " ".join(p for p in parts if p).strip()
        # 저자 소속 (중복 제거, 순서 유지)
        affs = []
        for a in re.findall(r"<Affiliation>(.*?)</Affiliation>", block, re.DOTALL):
            t = _strip(a)
            if t and t not in affs:
                affs.append(t)
        # 저자 키워드
        kws = [k for k in (_strip(x) for x in
               re.findall(r"<Keyword[^>]*>(.*?)</Keyword>", block, re.DOTALL)) if k]
        # MeSH 주제어
        mesh = [m for m in (_strip(x) for x in
                re.findall(r"<DescriptorName[^>]*>(.*?)</DescriptorName>", block, re.DOTALL)) if m]
        # 중복 제거
        kws = list(dict.fromkeys(kws))
        mesh = list(dict.fromkeys(mesh))
        out[pmid] = {"abstract": abstract, "affiliations": affs[:12],
                     "keywords": kws, "mesh": mesh[:25]}
    return out


def get_papers():
    query = build_query()
    count, webenv, qkey = esearch_history(query)
    if MAX_RESULTS > 0:
        count = min(count, MAX_RESULTS)
    print(f"  검색 결과: {count}편  (배치 {BATCH}, {'API키 있음' if NCBI_API_KEY else '키 없음'})")
    if not count:
        return []

    cache = load_previous_summaries()
    papers = []
    for start in range(0, count, BATCH):
        n = min(BATCH, count - start)
        common = f"db=pubmed&query_key={qkey}&WebEnv={webenv}&retstart={start}&retmax={n}"
        # 초록·소속·키워드(XML)
        try:
            xml = fetch_text(f"{BASE}efetch.fcgi?{common}&retmode=xml&rettype=abstract")
            details = parse_details(xml)
        except Exception as e:
            print(f"  efetch 오류(start={start}): {e}"); details = {}
        time.sleep(DELAY)
        # 메타(JSON)
        try:
            summ = fetch_json(f"{BASE}esummary.fcgi?{common}&retmode=json")
            result = summ.get("result", {})
        except Exception as e:
            print(f"  esummary 오류(start={start}): {e}"); result = {}
        time.sleep(DELAY)

        uids = result.get("uids", [])
        for pid in uids:
            item = result.get(pid)
            if not item:
                continue
            source = item.get("source", "")
            if not journal_ok(source):
                continue
            det = details.get(pid, {})
            abstract = det.get("abstract", "")
            title = item.get("title", "").replace("<b>", "").replace("</b>", "").strip()
            if is_excluded_topic(title, abstract):      # 심장·치과 등 제외
                continue
            category = categorize(title, abstract)
            if DROP_OTHER and category == "other":       # 기타 버림
                continue

            if ABSTRACT_MAXLEN > 0 and abstract:
                abstract = abstract[:ABSTRACT_MAXLEN]

            summary_ko = cache.get(pid)
            if summary_ko is None:
                summary_ko = summarize_ko(title, abstract)
                if ANTHROPIC_API_KEY:
                    time.sleep(0.25)

            disp_date, sort_date = fmt_date(item)
            papers.append({
                "id": pid,
                "title": title,
                "authors": ", ".join(a.get("name", "") for a in item.get("authors", [])[:10]),
                "journal": source,
                "date": disp_date,       # 표시용 (항상 월·일 포함)
                "sortdate": sort_date,   # 정렬용 YYYY-MM-DD
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
                "abstract": abstract,
                "affiliations": det.get("affiliations", []),
                "keywords": det.get("keywords", []),
                "mesh": det.get("mesh", []),
                "category": category,
                "summary_ko": summary_ko,
            })
        print(f"  진행: {min(start + n, count)}/{count}  (수집 {len(papers)}편)")
    return papers


def main():
    window = f"최근 {INCREMENTAL_DAYS}일(증분)" if INCREMENTAL_DAYS > 0 else f"{START_DATE}~오늘"
    print(f"Fetching...  (기간 {window}, MAX_RESULTS={MAX_RESULTS or '무제한'})")

    existing = load_existing_papers()      # 기존 누적분
    try:
        new_papers = get_papers()
    except Exception as e:
        print(f"수집 오류: {e}")
        new_papers = []

    # ── 병합 + 중복 제거 (PMID 기준) ──────────────────────────
    merged = dict(existing)                 # 기존 것 유지 (수집 실패해도 안 지워짐)
    added = 0
    for p in new_papers:
        if p["id"] not in merged:
            added += 1
        merged[p["id"]] = p                 # 같은 PMID면 최신 것으로 갱신 → 중복 없음
    papers = list(merged.values())
    # 정렬: sortdate(YYYY-MM-DD) 기준 최신순. (예전 데이터엔 sortdate 없을 수 있어 date로 폴백)
    papers.sort(key=lambda x: x.get("sortdate") or x.get("date", ""), reverse=True)

    from collections import Counter
    print(f"기존 {len(existing)}편 + 이번 신규 {added}편 → 총 {len(papers)}편 (중복 제거됨)")
    print("분야별:", {CATEGORY_LABELS.get(k, k): v for k, v in Counter(p["category"] for p in papers).items()})

    output = {
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "total": len(papers),
        "categories": CATEGORY_LABELS,
        "papers": papers,
    }
    with open("papers.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"완료! 총 {len(papers)}편 → papers.json")


if __name__ == "__main__":
    main()

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
    GEMINI_API_KEY     한국어 요약용(무료·권장). aistudio.google.com 에서 발급.
    ANTHROPIC_API_KEY  한국어 요약용(유료·선택). GEMINI 키가 있으면 그쪽을 우선 사용.
      · 요약 우선순위: GEMINI(무료) → ANTHROPIC(유료) → 둘 다 없으면 요약 생략
"""

import urllib.request
import urllib.parse
import json
import time
import os
import re
from datetime import datetime, timedelta

# ── 환경변수 ────────────────────────────────────────────────
#  요약 제공자: GEMINI(무료) 우선 → 없으면 ANTHROPIC(유료) → 둘 다 없으면 요약 생략
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")        # 🔧 무료: aistudio.google.com
GEMINI_MODEL      = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")     # (선택) 유료
NCBI_API_KEY      = os.environ.get("NCBI_API_KEY", "")

# 🔧 수집 시작일(고정). 이 날짜부터 오늘까지 모음. (예전의 '최근 N일' 방식 대체)
#    형식 2020-01-01 또는 2020/01/01 모두 OK. env START_DATE 로 덮어쓸 수 있음.
#    ※ '모든 분야 2020년부터' 요청 반영 → 기본 시작일을 2020-01-01로 변경.
START_DATE = os.environ.get("START_DATE", "2020-01-01").replace("-", "/")
# 🔧 증분 수집 창(일). 0이면 매번 START_DATE~오늘 전체를 조회(권장, 누락 없음).
#    속도가 필요하면 예) 30 → 최근 30일만 조회하고 기존 데이터에 '추가'로 병합.
INCREMENTAL_DAYS = int(os.environ.get("INCREMENTAL_DAYS", "0"))
# 🔧 특정 분야만 더 오래전부터 수집(백필). 양이 적은 분야를 과거까지 채운다.
#    ※ 이제 본검색 START_DATE 자체가 2020-01-01이라 '전 분야'가 2020년부터 잡힘 →
#      분야별 백필은 중복이므로 기본 꺼둠(""). 필요하면 env BACKFILL_CATS="cardiac,..." 로 켤 수 있음.
BACKFILL_START_DATE = os.environ.get("BACKFILL_START_DATE", "2020-01-01").replace("-", "/")
BACKFILL_CATS = [c.strip() for c in
                 os.environ.get("BACKFILL_CATS", "").split(",")
                 if c.strip()]
# 🔧 최대 수집 개수 — 0이면 제한 없음(조건 맞는 것 전부, 수만 개 가능)
MAX_RESULTS    = int(os.environ.get("MAX_RESULTS", "0"))
# 🔧 초록 저장 최대 글자수 — 0이면 전체 저장. 수만 개면 파일이 커지니 800 등으로 제한 권장
ABSTRACT_MAXLEN = int(os.environ.get("ABSTRACT_MAXLEN", "0"))
# 🔧 1회 실행당 '요약 API 호출' 상한 (무료 한도 초과·과금 방지 안전장치).
#    0=무제한. Gemini 무료 한도가 하루 ~1,500건이라 기본 1400으로 여유. 남은 논문은 다음 실행 때 채워짐.
MAX_SUMMARIES = int(os.environ.get("MAX_SUMMARIES", "1400"))
# 🔧 자동 요약 켜기/끄기 마스터 스위치. 기본 0=꺼짐(요약 안 함, 훨씬 빠름).
#    ★ 최종 단계에서 켜려면: 이 줄의 기본값 "0"→"1" 로 바꾸거나, 워크플로우 env 로 ENABLE_SUMMARY=1 지정.
ENABLE_SUMMARY = os.environ.get("ENABLE_SUMMARY", "0") != "0"

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

# 🔧 주제 제외: 이 단어가 제목/초록에 있으면 논문을 버림 (치과 제외 요청 반영)
#   ※ 심장(cardiology)은 예전엔 제외였으나, '심장' 분야 신설로 제외 목록에서 제거함.
EXCLUDE_TOPIC_KEYWORDS = [
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
    "Vet Rec",                     # Veterinary Record (BVA, MEDLINE 색인)
    "Case Rep Vet Med",            # Case Reports in Veterinary Medicine (증례 전문지, PMC 2016~)
    "J Vet Cardiol",               # 심장 (Journal of Veterinary Cardiology)
    "Vet Anaesth Analg",           # 마취 (Veterinary Anaesthesia and Analgesia)
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
    # 인공관절 (관절 치환술 — 정형외과보다 먼저 검사해서 따로 분리)
    #   ※ recall 우선: THR/TKR/TER 등 약어(3글자)는 자동으로 '단어 전체 일치' 처리됨.
    #     'excision arthroplasty'(FHO, 임플란트 아님)도 arthroplasty로 잡히는 점은 감안.
    "jointreplacement": ["arthroplasty", "hemiarthroplasty",
                         "joint replacement", "hip replacement", "knee replacement",
                         "elbow replacement", "shoulder replacement", "total joint replacement",
                         "joint prosthesis", "prosthetic joint", "endoprosthesis",
                         "total hip", "total knee", "total elbow",
                         "acetabular cup", "femoral stem", "joint resurfacing",
                         "thr", "tha", "tkr", "tka", "ter"],
    # 정형외과 (관절경·인공관절 외 정형)
    "orthopedics":  ["fracture", "orthopedic", "osteotomy", "luxation", "cruciate", "tplo",
                     "patellar", "arthro", "arthrodesis", "osteosynthesis"],
    # 심장 (내과+외과 통합) — '외과·영상·내과'보다 먼저 검사해 심장 논문을 따로 분리.
    #   ※ 'ventricular'(뇌실과 겹침)·'valve'(타 장기 판막) 단독은 오분류되므로 일부러 미포함.
    "cardiac":      [# 내과·전반
                     "cardiac", "cardiolog", "cardiovascular", "myocard", "pericard", "endocard",
                     "cardiomyopath", "myxomatous", "mitral", "tricuspid", "valvular",
                     "echocardiograph", "echocardiogram", "congestive heart", "heart failure", "chf",
                     "arrhythmia", "atrial fibrillation", "tachycardia", "bradycardia",
                     "patent ductus", "pda", "pulmonic stenosis", "subaortic", "aortic stenosis",
                     "septal defect", "vsd", "asd", "pacemaker", "pulmonary hypertension",
                     # 외과·중재시술
                     "cardiac surgery", "cardiothoracic", "cardiovascular surgery",
                     "open heart", "cardiopulmonary bypass",
                     "mitral valve repair", "mitral valve replacement", "valve replacement",
                     "valvuloplasty", "annuloplasty", "balloon valvuloplasty",
                     "pericardiectomy", "pericardiocentesis",
                     "pda ligation", "ductal occlusion", "ductal occluder", "amplatz", "acdo", "occluder",
                     "pacemaker implantation", "cardiac catheterization",
                     "transcatheter", "interventional cardiology"],
    # 마취·진통 — '외과'보다 먼저 검사(수술 방법 논문에 밀리지 않게).
    "anesthesia":   ["anesthesia", "anaesthesia", "anesthetic", "anaesthetic",
                     "anesthesiolog", "anaesthesiolog", "sedation", "sedative",
                     "analgesia", "analgesic", "antinociception", "nociception",
                     "propofol", "alfaxalone", "isoflurane", "sevoflurane", "ketamine",
                     "medetomidine", "midazolam", "fentanyl", "buprenorphine", "butorphanol",
                     "methadone", "opioid", "epidural", "nerve block", "local anesthetic",
                     "lidocaine", "bupivacaine", "premedication", "inhalant anesthetic",
                     "neuromuscular block", "capnograph", "minimum alveolar concentration"],
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
    # 지주막 게실/낭종 (종양은 아니나 신경계 점거병변 — 신경종양 그룹에 포함 요청).
    #   ※ 'cyst·pseudocyst·diverticulum' 단독은 신장·간·췌장·소화기 등 타과와 겹치므로 절대 미포함.
    #     반드시 신경계 부위어(subarachnoid·arachnoid·meningeal)와 결합된 구절로만 매칭.
    "subarachnoid diverticulum", "subarachnoid diverticula",
    "arachnoid diverticulum", "arachnoid diverticula",
    "subarachnoid cyst", "arachnoid cyst",           # 'intra-arachnoid cyst'·'spinal arachnoid cyst'도 포함됨
    "subarachnoid pseudocyst", "arachnoid pseudocyst",
    "meningeal cyst", "leptomeningeal cyst", "meningeal pseudocyst",
]

CATEGORY_LABELS = {
    "neurooncology": "신경종양",
    "neurosurgery": "신경외과", "arthroscopy": "관절경", "jointreplacement": "인공관절",
    "orthopedics": "정형외과", "cardiac": "심장", "anesthesia": "마취",
    "surgery": "외과", "anatomy": "해부", "imaging": "영상",
    "internal": "내과", "oncology": "종양",
}

# 🔧 '단어 전체 일치'로만 인정할 짧은 토큰 (부분일치 오분류 방지). ct/mri는 자동(3글자↓).
WHOLE_WORD_TOKENS = {"disc", "cns"}

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


def _parse_date(raw):
    """날짜 문자열 하나를 (표시용, 정렬용, 완전도) 로 파싱. 실패 시 None.
    완전도: 2=일까지, 1=월까지, 0=연도만."""
    raw = (raw or "").strip()
    if not raw:
        return None
    m = re.match(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", raw)     # 2026/05/26 · 2026-05-26
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12:
            return f"{y} {MONTHS[mo - 1]} {d:02d}", f"{y:04d}-{mo:02d}-{d:02d}", 2
    m = re.match(r"(\d{4})(?:\s+([A-Za-z]{3,}))?(?:\s+(\d{1,2}))?", raw)  # 2026 May 26 · 2026 Jul · 2026
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
        comp = 2 if d else (1 if mo else 0)
        return disp, sortk, comp
    return None


def fmt_date(item, det=None):
    """(표시용, 정렬용) 날짜 = '온라인 최초 공개일' 우선.
    우선순위: ①ArticleDate[Electronic] → ②epubdate → ③History epublish
              → ④sortpubdate/pubdate(발간호, 전자 날짜 없을 때만).
    수의사가 실제로 읽는 early access(온라인) 날짜를 기준으로 표시·정렬한다."""
    det = det or {}
    candidates = [
        det.get("article_electronic"),   # ① 온라인 최초 공개일 (efetch)
        item.get("epubdate"),            # ② 전자출판일 (esummary)
        det.get("epublish"),             # ③ History 전자출판 이력 (efetch)
        item.get("sortpubdate"),         # ④ 발간호 날짜 (esummary)
        item.get("pubdate"),
    ]
    parsed = [_parse_date(c) for c in candidates]
    for pr in parsed:                    # 월 이상 정보가 있는 첫 후보 사용
        if pr and pr[2] >= 1:
            return pr[0], pr[1]
    for pr in parsed:                    # 그래도 없으면 연도만이라도
        if pr:
            return pr[0], pr[1]
    raw = item.get("sortpubdate") or ""
    return raw, raw


def _api(url):
    """모든 eutils URL 뒤에 api_key 붙임 (있을 때만)."""
    if NCBI_API_KEY:
        url += ("&" if "?" in url else "?") + "api_key=" + NCBI_API_KEY
    return url


# 🔧 요약 스타일 — 이 프롬프트만 고치면 문체가 바뀜. (명사=영어 원어, 동사=한국어)
def _summary_prompt(title, abstract):
    return (
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


def _summarize_gemini(prompt):
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 256, "temperature": 0.3},
    }).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.loads(r.read().decode())
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def _summarize_anthropic(prompt):
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


def summarize_ko(title, abstract):
    """GEMINI(무료) 우선 → ANTHROPIC(유료) → 둘 다 없으면 생략."""
    if not abstract:
        return ""
    prompt = _summary_prompt(title, abstract)
    try:
        if GEMINI_API_KEY:
            return _summarize_gemini(prompt)
        if ANTHROPIC_API_KEY:
            return _summarize_anthropic(prompt)
        return ""
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


def build_query_dated(date_from, extra_terms=None):
    """소동물+화이트리스트+종제외 기본 쿼리. date_from~오늘 범위.
    extra_terms 를 주면 그 키워드(OR) 를 반드시 포함(AND)하도록 좁힌다."""
    date_to = datetime.now().strftime("%Y/%m/%d")
    sa = " OR ".join(f'"{t}"[tiab]' for t in SMALL_ANIMAL_TERMS)
    q = f"({sa})"
    if extra_terms:
        q += " AND (" + " OR ".join(f'"{t}"[tiab]' for t in extra_terms) + ")"
    if USE_WHITELIST and JOURNAL_WHITELIST:
        q += " AND (" + " OR ".join(f'"{j}"[jour]' for j in JOURNAL_WHITELIST) + ")"
    q += f' AND ("{date_from}"[PDAT] : "{date_to}"[PDAT])'
    if EXCLUDE_SPECIES:
        q += " NOT (" + " OR ".join(f'"{t}"[tiab]' for t in EXCLUDE_SPECIES) + ")"
    return q


def build_query():
    """본검색: 모든 분야, START_DATE(기본 2026-01-01)~오늘."""
    if INCREMENTAL_DAYS > 0:
        date_from = (datetime.now() - timedelta(days=INCREMENTAL_DAYS)).strftime("%Y/%m/%d")
    else:
        date_from = START_DATE
    return build_query_dated(date_from)


def category_search_terms(cat):
    """백필 검색에 쓸 그 분야의 키워드 목록. (신경종양은 별도 표현 목록 사용)"""
    if cat == "neurooncology":
        return NEUROONCO_PHRASES
    return CATEGORY_KEYWORDS.get(cat, [])


def build_backfill_query(cat):
    """분야 백필: 그 분야 키워드 포함 + BACKFILL_START_DATE(기본 2020)~오늘."""
    return build_query_dated(BACKFILL_START_DATE, extra_terms=category_search_terms(cat))


def esearch_history(query):
    """usehistory=y 로 검색 → (총 개수, WebEnv, QueryKey) 반환. 실제 ID는 안 받음."""
    term = urllib.parse.quote(query)
    url = f"{BASE}esearch.fcgi?db=pubmed&term={term}&retmax=0&usehistory=y&retmode=json&sort=date"
    res = fetch_json(url).get("esearchresult", {})
    count = int(res.get("count", "0"))
    return count, res.get("webenv", ""), res.get("querykey", "")


def _strip(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _ymd(sub):
    """<Year>/<Month>/<Day> 조각 → 'YYYY/MM/DD' (일 없으면 01). 실패 시 ''."""
    y = re.search(r"<Year>(\d{4})</Year>", sub)
    mo = re.search(r"<Month>(\d{1,2})</Month>", sub)
    d = re.search(r"<Day>(\d{1,2})</Day>", sub)
    if y and mo and d:
        return f"{int(y.group(1)):04d}/{int(mo.group(1)):02d}/{int(d.group(1)):02d}"
    if y and mo:
        return f"{int(y.group(1)):04d}/{int(mo.group(1)):02d}/01"
    return ""


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
        # 온라인 최초 공개일: <ArticleDate DateType="Electronic"> (연·월·일)
        art_el = ""
        am = re.search(r'<ArticleDate[^>]*DateType="Electronic"[^>]*>(.*?)</ArticleDate>',
                       block, re.DOTALL)
        if am:
            art_el = _ymd(am.group(1))
        # History 전자출판 이력: <PubMedPubDate PubStatus="epublish">
        epub = ""
        hm = re.search(r'<PubMedPubDate[^>]*PubStatus="epublish"[^>]*>(.*?)</PubMedPubDate>',
                       block, re.DOTALL)
        if hm:
            epub = _ymd(hm.group(1))
        out[pmid] = {"abstract": abstract, "affiliations": affs[:12],
                     "keywords": kws, "mesh": mesh[:25],
                     "article_electronic": art_el, "epublish": epub}
    return out


def _collect_one(query, only_cat, cache, collected, summ_state):
    """query 로 검색해 논문을 collected(dict: pid→paper)에 채운다.
    only_cat 이 주어지면 그 분야로 분류된 논문만 담는다(인공관절 백필용).
    cache/summ_state 는 검색들 사이에 공유(요약 상한·중복호출 방지)."""
    count, webenv, qkey = esearch_history(query)
    if MAX_RESULTS > 0:
        count = min(count, MAX_RESULTS)
    print(f"  검색 결과: {count}편  (배치 {BATCH}, {'API키 있음' if NCBI_API_KEY else '키 없음'})")
    if not count:
        return
    added_here = 0
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
            if pid in collected:            # 다른 검색에서 이미 처리됨 → 중복 방지
                continue
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
            if only_cat and category != only_cat:       # 백필: 해당 분야만 담기
                continue
            if DROP_OTHER and category == "other":       # 기타 버림
                continue

            if ABSTRACT_MAXLEN > 0 and abstract:
                abstract = abstract[:ABSTRACT_MAXLEN]

            summary_ko = cache.get(pid)
            if summary_ko is None:
                can_summarize = (ENABLE_SUMMARY and (GEMINI_API_KEY or ANTHROPIC_API_KEY) and abstract
                                 and (MAX_SUMMARIES == 0 or summ_state["n"] < MAX_SUMMARIES))
                if can_summarize:
                    summary_ko = summarize_ko(title, abstract)
                    summ_state["n"] += 1
                    # 무료 Gemini는 분당 요청 제한(RPM)이 있어 넉넉히 쉼. Anthropic은 짧게.
                    if GEMINI_API_KEY:
                        time.sleep(4.2)
                    elif ANTHROPIC_API_KEY:
                        time.sleep(0.25)
                else:
                    summary_ko = ""   # 상한 도달/키 없음 → 이번엔 비움 (다음 실행 때 채워짐)

            disp_date, sort_date = fmt_date(item, det)
            collected[pid] = {
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
            }
            added_here += 1
        print(f"  진행: {min(start + n, count)}/{count}  (이 검색 담김 {added_here}편, 누적 {len(collected)}편)")


def get_papers():
    # 검색 작업 목록: (쿼리, 이 분야만, 라벨)
    jobs = [(build_query(), None, "본검색")]
    for cat in BACKFILL_CATS:
        if not category_search_terms(cat):
            print(f"  [백필 건너뜀] 알 수 없는 분야: {cat}")
            continue
        label = f"{CATEGORY_LABELS.get(cat, cat)} 백필 {BACKFILL_START_DATE}~오늘"
        jobs.append((build_backfill_query(cat), cat, label))

    cache = load_previous_summaries()
    collected = {}                 # pid → paper (검색 간 중복 자동 제거)
    summ_state = {"n": 0}          # 요약 호출 수(모든 검색 합산, 상한 공유)
    for query, only_cat, label in jobs:
        print(f"  ─ [{label}] 검색 시작")
        try:
            _collect_one(query, only_cat, cache, collected, summ_state)
        except Exception as e:
            print(f"  [{label}] 검색 오류: {e}")
    # 이번 실행에서 실제로 API를 호출해 '새로' 요약한 건수 (기존 요약은 재사용, 호출 안 함)
    if not ENABLE_SUMMARY:
        print("  자동 요약: 꺼짐(ENABLE_SUMMARY=0) — 요약 없이 수집만 (나중에 1로 켜면 활성화)")
    print(f"  요약 API 신규 호출: {summ_state['n']}건 (기존 요약은 재사용, 재호출 없음)")
    return list(collected.values())


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

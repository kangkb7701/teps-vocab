# -*- coding: utf-8 -*-
"""
해커스 텝스 단어암기장 PDF -> vocab.json 파서

3개 PDF를 파싱한다:
  1) [해커스 텝스 베이직] 리스닝 단어암기장  (포맷 B: 번호/□단어/한글뜻)
  2) 해커스 텝스 베이직 리딩_단어암기장       (포맷 B)
  3) 해커스 텝스 리딩_단어암기장              (포맷 A: 번호/단어[발음]★/품사.한글뜻)

발음기호는 특수 폰트라 깨지므로 제거. ★(*) 개수 = 출제 빈도로 활용.
결과: vocab.json (word/pos/meaning/freq/source/level 필드, 중복 제거)
"""
import fitz, glob, json, re, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

# --- 파일별 메타 ---
FILES = {
    "리스닝 단어암기장":       {"tag": "basic_listening", "level": "basic",    "fmt": "B"},
    "베이직 리딩_단어암기장":  {"tag": "basic_reading",   "level": "basic",    "fmt": "B"},
    "텝스 리딩_단어암기장":    {"tag": "adv_reading",     "level": "advanced", "fmt": "A"},
}

def match_meta(path):
    name = os.path.basename(path)
    # 고급 리딩("해커스 텝스 리딩_단어암기장")과 베이직 리딩 구분
    if "베이직 리딩" in path:
        return FILES["베이직 리딩_단어암기장"]
    if "리스닝" in path:
        return FILES["리스닝 단어암기장"]
    if "텝스 리딩_단어암기장" in path or ("리딩" in path and "베이직" not in path):
        return FILES["텝스 리딩_단어암기장"]
    return None

# --- 헤더/푸터 잡음 라인 필터 ---
HEADER_SUBSTR = [
    "저작권자", "HackersIngang", "MP3", "의 개수가 많을수록",
    "어휘 영역 기출 단어", "문법 영역 기출 단어", "단어암기장",
    "잘 외워지지 않는", "Daily Checkup", "복제",
]
def is_header(line):
    s = line.strip()
    if not s:
        return True
    for sub in HEADER_SUBSTR:
        if sub in s:
            return True
    if s.startswith("*파일명"):
        return True
    if s.startswith("Chapter"):
        return True
    if s.startswith("Part "):
        return True
    # 섹션 헤더 "Day 05" / 페이지 footer "D AY 1 0 -" 형태
    collapsed = re.sub(r"[\s\-]", "", s).upper()
    if re.fullmatch(r"DAY\d+", collapsed):
        return True
    return False

def entry_start(line):
    """항목 시작 라인 판정.
    고급 리딩: '61\\t' 또는 '100\\t scarcity[..]*' (번호 뒤 탭, 100+는 단어가 같은 줄).
    베이직  : '1' 처럼 탭 없는 순수 정수.
    반환: (번호, 같은 줄에 붙은 headword 또는 "") 또는 None
    """
    m = re.match(r"^(\d+)\t[ ]*(.*)$", line)
    if m:
        return m.group(1), m.group(2).strip()
    if re.fullmatch(r"\d+", line.strip()):
        return line.strip(), ""
    return None

CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

HANGUL = re.compile(r"[가-힣]")
POS_RE = re.compile(r"\b(phr|adv|prep|conj|pron|int|ad|v|n|a)\.")
PHON_RE = re.compile(r"\[[^\]]*\]?")  # 발음기호 대괄호(닫힘 없어도)

def clean_word(w):
    return re.sub(r"\s+", " ", w).strip()

def process_block(block, fmt):
    """block: 한 단어 엔트리의 라인 리스트(번호 다음부터 다음 번호 전까지)"""
    # 하이픈 줄바꿈 처리하며 join
    blob = ""
    for ln in block:
        ln = ln.strip()
        if not ln:
            continue
        if blob.endswith("-"):
            blob += ln
        elif blob:
            blob += " " + ln
        else:
            blob = ln
    blob = CTRL_RE.sub("", blob)
    blob = blob.replace("□", "").strip()
    blob = PHON_RE.sub("", blob)       # 발음기호 먼저 제거(그 안의 * 글리프가 빈도로 오인됨)
    freq = blob.count("*")             # 남은 *(출제 빈도 마커)만 카운트
    blob = blob.replace("*", "").strip()
    blob = re.sub(r"\s+", " ", blob)

    pos = ""
    word = ""
    meaning = ""

    m = POS_RE.search(blob)
    if m and m.start() > 0:
        word = blob[:m.start()].strip()
        rest = blob[m.start():].strip()
        # rest = "v. 폐지하다" 형태. 품사 토큰 분리
        pm = re.match(r"([A-Za-z]+\.)\s*(.*)", rest, re.S)
        if pm:
            pos = pm.group(1)
            meaning = pm.group(2).strip()
        else:
            meaning = rest
    else:
        # POS 없음(베이직) → 첫 한글에서 분리
        hm = HANGUL.search(blob)
        if hm:
            start = hm.start()
            # "dozen 12개"처럼 한글 바로 앞 숫자는 뜻("12개")에 속함
            while start > 0 and blob[start - 1].isdigit():
                start -= 1
            word = blob[:start].strip()
            meaning = blob[start:].strip()
        else:
            word = blob.strip()
            meaning = ""

    # 단어 정제: 남은 대괄호 잔여물/특수문자 제거
    word = re.sub(r"[\[\]]", "", word).strip()
    word = clean_word(word)
    meaning = clean_word(meaning)
    return word, pos, meaning, freq

def parse_pdf(path):
    meta = match_meta(path)
    if not meta:
        return []
    fmt = meta["fmt"]
    doc = fitz.open(path)
    # 전체 라인 수집(페이지별로 헤더/페이지번호 제거)
    entries = []
    for pno in range(doc.page_count):
        raw = doc[pno].get_text().split("\n")
        lines = [ln for ln in raw if not is_header(ln)]
        i = 0
        n = len(lines)
        while i < n:
            es = entry_start(lines[i])
            if es is None:
                i += 1
                continue
            num, inline = es
            i += 1
            block = []
            if inline:
                # 번호와 headword가 같은 줄 (100+ 케이스)
                block.append(inline)
            else:
                # 다음 라인이 headword. 페이지 번호면(다음이 또 항목시작/헤더) 스킵
                if i >= n:
                    break
                if entry_start(lines[i]) is not None or is_header(lines[i]):
                    continue
                block.append(lines[i])
                i += 1
            while i < n and entry_start(lines[i]) is None:
                block.append(lines[i])
                i += 1
            word, pos, meaning, freq = process_block(block, fmt)
            if word and re.search(r"[A-Za-z]", word) and meaning:
                entries.append({
                    "word": word, "pos": pos, "meaning": meaning,
                    "freq": freq, "source": meta["tag"], "level": meta["level"],
                })
    doc.close()
    return entries

def main():
    all_entries = []
    for pdf in glob.glob("**/*.pdf", recursive=True):
        if "Daily Checkup" in pdf:
            continue  # 예문 해석 모음 → 단어 소스 아님
        ents = parse_pdf(pdf)
        print(f"{os.path.basename(pdf)}: {len(ents)} entries")
        all_entries.extend(ents)

    # 중복 제거(소문자 단어 기준). 뜻 병합, freq 최대, level은 basic 우선 유지 정보 보존
    merged = {}
    order = []
    for e in all_entries:
        key = e["word"].lower()
        if key not in merged:
            merged[key] = e.copy()
            merged[key]["meanings"] = [e["meaning"]] if e["meaning"] else []
            merged[key]["sources"] = [e["source"]]
            order.append(key)
        else:
            m = merged[key]
            if e["meaning"] and e["meaning"] not in m["meanings"]:
                m["meanings"].append(e["meaning"])
            if e["source"] not in m["sources"]:
                m["sources"].append(e["source"])
            m["freq"] = max(m["freq"], e["freq"])
            # 품사 비어있으면 채움
            if not m["pos"] and e["pos"]:
                m["pos"] = e["pos"]
            # basic 소스가 있으면 level=basic 유지(더 기초)
            if e["level"] == "basic":
                m["level"] = "basic"

    result = []
    for key in order:
        m = merged[key]
        m["meaning"] = " / ".join(m["meanings"]) if m["meanings"] else m["meaning"]
        m.pop("meanings", None)
        result.append(m)

    # 우선순위 정렬: 기초(everyday 고빈도) → 고급 3★ → 2★ → 1★ → 0
    def priority(e):
        if e["level"] == "basic":
            tier = 0
        else:
            tier = {3: 1, 2: 2, 1: 3}.get(e["freq"], 4)
        return (tier, -e["freq"])
    result.sort(key=priority)

    with open("vocab.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    print(f"\nTOTAL unique words: {len(result)}")
    # 통계
    from collections import Counter
    lv = Counter(e["level"] for e in result)
    fr = Counter(e["freq"] for e in result)
    print("level:", dict(lv))
    print("freq :", dict(sorted(fr.items())))
    print("\n--- 샘플(앞 8개) ---")
    for e in result[:8]:
        print(f"  {e['word']:<28} {e['pos']:<5} {e['meaning'][:40]}  [★{e['freq']} {e['level']}]")
    print("\n--- 샘플(중간 8개) ---")
    mid = len(result)//2
    for e in result[mid:mid+8]:
        print(f"  {e['word']:<28} {e['pos']:<5} {e['meaning'][:40]}  [★{e['freq']} {e['level']}]")

if __name__ == "__main__":
    main()

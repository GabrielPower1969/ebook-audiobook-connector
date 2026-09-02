"""Align book paragraphs to transcript word timings.

Strategy: find n-grams that occur exactly once in both token streams (n = 6,4,3,2, refined inside
gaps), keep the longest monotonic chain (LIS), then map each paragraph's first/last token to a time.
A paragraph counts as aligned only if it contains at least one anchored token (headings may sit
within 3 tokens of one). Works for any language whose words are space-separated; CJK is tokenized
per character.
"""
from __future__ import annotations
import bisect, re

NUM = {"0":"zero","1":"one","2":"two","3":"three","4":"four","5":"five","6":"six","7":"seven","8":"eight","9":"nine","10":"ten"}
CJK = re.compile(r"[぀-ヿ㐀-鿿]")

def tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[\w'’]+|[぀-ヿ㐀-鿿]", text):
        if CJK.match(w): out.append(w); continue
        w = re.sub(r"[^\w]", "", w.lower().replace("’", "'"))
        if w: out.append(NUM.get(w, w))
    return out

def _anchors(bt, tt, b0, b1, t0, t1, n):
    if b1 - b0 < n or t1 - t0 < n: return []
    bg, tg = {}, {}
    for i in range(b0, b1 - n + 1):
        k = tuple(bt[i:i+n]); bg[k] = -1 if k in bg else i
    for i in range(t0, t1 - n + 1):
        k = tuple(tt[i:i+n]); tg[k] = -1 if k in tg else i
    cand = sorted((bi, tg[k]) for k, bi in bg.items() if bi >= 0 and tg.get(k, -1) >= 0)
    tails, prev, tv = [], [None] * len(cand), []
    for j, (_, ti) in enumerate(cand):          # LIS on transcript index
        pos = bisect.bisect_left(tv, ti)
        if pos == len(tails): tails.append(j); tv.append(ti)
        else: tails[pos] = j; tv[pos] = ti
        prev[j] = tails[pos - 1] if pos else None
    out, j = [], tails[-1] if tails else None
    while j is not None: out.append(cand[j]); j = prev[j]
    return out[::-1]

def _refine(bt, tt, b0, b1, t0, t1, ns=(6, 4, 3, 2)):
    a = _anchors(bt, tt, b0, b1, t0, t1, ns[0])
    if len(ns) == 1: return a
    res, pb, pt = [], b0, t0
    for bi, ti in a + [(b1, t1)]:
        if bi - pb > 12 and ti - pt > 12: res += _refine(bt, tt, pb, bi, pt, ti, ns[1:])
        if bi < b1: res.append((bi, ti))
        pb, pt = bi + 1, ti + 1
    return res

def align(paras: list[dict], transcripts: list[dict]) -> dict:
    """paras: [{id, tag, text}], transcripts: [{file, words:[{w,s,e}]}] → {files, paras:[None|{f,s,e,d}], stats}"""
    bt, bpara = [], []
    for p in paras:
        for w in tokens(p["text"]): bt.append(w); bpara.append(p["id"])
    tt, tinfo = [], []
    for fi, tr in enumerate(transcripts):
        for w in tr["words"]:
            for tok in tokens(w["w"]): tt.append(tok); tinfo.append((fi, w["s"], w["e"]))
    A = _refine(bt, tt, 0, len(bt), 0, len(tt)); ab = [x[0] for x in A]

    def map_idx(bi):
        k = bisect.bisect_left(ab, bi)
        if k < len(A) and A[k][0] == bi: return A[k][1], 0
        if k == 0 or k == len(A): return None
        (b0, t0), (b1, t1) = A[k-1], A[k]
        dist = min(bi - b0, b1 - bi)
        if dist > 60 or b1 - b0 > 200 or t1 - t0 > 400: return None
        return min(max(t0 + round((bi - b0) * (t1 - t0) / max(1, b1 - b0)), t0), t1), dist

    first, last = {}, {}
    for i, pid in enumerate(bpara): first.setdefault(pid, i); last[pid] = i
    out = []
    for p in paras:
        pid = p["id"]
        if pid not in first: out.append(None); continue
        m, m2 = map_idx(first[pid]), map_idx(last[pid])
        has_anchor = bisect.bisect_right(ab, last[pid]) > bisect.bisect_left(ab, first[pid])
        if m is None or not (has_anchor or (p["tag"] != "p" and m[1] <= 3)): out.append(None); continue
        fi, s, _ = tinfo[m[0]]
        e = tinfo[m2[0]][2] if m2 and tinfo[m2[0]][0] == fi else None
        out.append({"f": fi, "s": round(s, 2), "e": round(e, 2) if e else None, "d": m[1]})
    stats = {"book_tokens": len(bt), "audio_tokens": len(tt), "anchors": len(A),
             "aligned": sum(1 for x in out if x), "exact": sum(1 for x in out if x and x["d"] == 0), "paragraphs": len(out)}
    return {"files": [t["file"] for t in transcripts], "paras": out, "stats": stats}

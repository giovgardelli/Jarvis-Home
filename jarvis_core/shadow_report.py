"""
shadow_report.py — are the local experiments ready to switch on?

Reads the shadow logs written while Jarvis runs normally and checks them
against the switch-on rules (improve speed or privacy, never regress either):

  Track A — local routing classifier (router_shadow.csv)
    switch on when, above some confidence level, it agrees with Claude on
    ≥ 98% of requests (over at least 30 of them)

  Track B — local speech-to-text (stt_shadow.csv)
    switch on when it's at least as fast as Deepgram (median) and leads to the
    same routing decision on ≥ 97% of commands (over at least 30)

Run from the repo root:  python3 -m jarvis_core.shadow_report
"""
import csv
import os
import re
import statistics
from collections import Counter

from jarvis_core.config import home_path

MIN_ROWS = 30
CLASSIFIER_AGREEMENT_TARGET = 0.98
STT_SAME_DECISION_TARGET = 0.97
CONFIDENCE_LEVELS = [0.6, 0.7, 0.8, 0.9, 0.95]


def _rows(path):
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def _words(text):
    return re.findall(r"[a-z0-9']+", (text or "").lower().replace("’", "'"))


def word_error_rate(reference, hypothesis):
    """Word-level edit distance / reference length (0 = identical)."""
    ref, hyp = _words(reference), _words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def _pct(x):
    return f"{x * 100:.0f}%"


def routing_summary(path):
    rows = _rows(path)
    return {"total": len(rows), "by_method": Counter(r.get("method", "?") for r in rows)}


def classifier_summary(path, min_similarity):
    rows = [r for r in _rows(path) if r.get("predicted")]
    levels = []
    for level in CONFIDENCE_LEVELS:
        handled = [r for r in rows
                   if r["predicted"] != "jarvis"
                   and float(r["confidence"]) >= level and float(r["similarity"]) >= min_similarity]
        agree = sum(r["predicted"] == r["claude_agent"] for r in handled)
        levels.append({"confidence": level, "handled": len(handled),
                       "coverage": len(handled) / len(rows) if rows else 0.0,
                       "agreement": agree / len(handled) if handled else 0.0})
    ready = [lv for lv in levels if lv["handled"] >= MIN_ROWS and lv["agreement"] >= CLASSIFIER_AGREEMENT_TARGET]
    return {"rows": len(rows), "levels": levels, "recommended": ready[0] if ready else None}


def stt_summary(path):
    all_rows = _rows(path)
    rows = [r for r in all_rows if not r.get("error") and r.get("deepgram_text")]
    errors = Counter(r["error"][:80] for r in all_rows if r.get("error"))
    if not rows:
        return {"rows": 0, "errors": errors}
    dg = [float(r["deepgram_seconds"]) for r in rows]
    lo = [float(r["local_seconds"]) for r in rows]
    decided = [r for r in rows if r.get("deepgram_decision") and r.get("local_decision")]
    same = sum(r["deepgram_decision"] == r["local_decision"] for r in decided)
    summary = {
        "rows": len(rows), "errors": errors,
        "deepgram_median": statistics.median(dg), "local_median": statistics.median(lo),
        "deepgram_p90": sorted(dg)[int(0.9 * (len(dg) - 1))], "local_p90": sorted(lo)[int(0.9 * (len(lo) - 1))],
        "identical": sum(_words(r["deepgram_text"]) == _words(r["local_text"]) for r in rows) / len(rows),
        "word_error_rate": statistics.mean(word_error_rate(r["deepgram_text"], r["local_text"]) for r in rows),
        "same_decision": same / len(decided) if decided else 0.0,
    }
    summary["faster"] = summary["local_median"] <= summary["deepgram_median"]
    summary["ready"] = (len(rows) >= MIN_ROWS and summary["faster"]
                        and summary["same_decision"] >= STT_SAME_DECISION_TARGET)
    return summary


def report(routing_log=None, router_shadow=None, stt_shadow=None, min_similarity=None):
    routing_log = routing_log or home_path("routing_log.csv")
    router_shadow = router_shadow or home_path("router_shadow.csv")
    stt_shadow = stt_shadow or home_path("stt_shadow.csv")
    if min_similarity is None:
        min_similarity = float(os.environ.get("ROUTER_CLASSIFIER_MIN_SIMILARITY", "0.5"))
    out = []

    rs = routing_summary(routing_log)
    methods = ", ".join(f"{m}: {n}" for m, n in rs["by_method"].most_common())
    out += ["== Routing log ==", f"{rs['total']} requests logged ({methods or 'none'})", ""]

    cs = classifier_summary(router_shadow, min_similarity)
    out += ["== Track A: local routing classifier (speed) ==",
            f"{cs['rows']} requests compared with Claude"]
    if cs["rows"]:
        out.append("  confidence ≥   handled locally   agrees with Claude")
        for lv in cs["levels"]:
            out.append(f"     {lv['confidence']:.2f}         {lv['handled']:>4} ({_pct(lv['coverage'])})"
                       f"          {_pct(lv['agreement']) if lv['handled'] else '—'}")
    rec = cs["recommended"]
    if rec:
        out.append(f"READY: set ROUTER_CLASSIFIER=on and ROUTER_CLASSIFIER_MIN_CONFIDENCE={rec['confidence']} "
                   f"— {_pct(rec['coverage'])} of these requests would skip the Claude routing call "
                   f"at {_pct(rec['agreement'])} agreement.")
    else:
        out.append(f"NOT YET: needs ≥ {MIN_ROWS} requests at ≥ {_pct(CLASSIFIER_AGREEMENT_TARGET)} agreement "
                   "at some confidence level. Keep using Jarvis normally.")
    out.append("")

    ss = stt_summary(stt_shadow)
    out += ["== Track B: local speech-to-text (privacy) ==", f"{ss['rows']} commands compared with Deepgram"]
    for err, n in ss.get("errors", {}).items():
        out.append(f"  {n} failed: {err}")
    if ss["rows"]:
        out += [f"  speed (median / slowest 10%): Deepgram {ss['deepgram_median']:.2f}s / {ss['deepgram_p90']:.2f}s,"
                f" local {ss['local_median']:.2f}s / {ss['local_p90']:.2f}s",
                f"  identical transcripts: {_pct(ss['identical'])}   word differences vs Deepgram: "
                f"{_pct(ss['word_error_rate'])}",
                f"  same routing decision: {_pct(ss['same_decision'])}"]
        if ss["ready"]:
            out.append("READY: set LOCAL_STT=on — your recordings stay on the Pi, no slower, same decisions.")
        else:
            reasons = []
            if ss["rows"] < MIN_ROWS:
                reasons.append(f"only {ss['rows']} of {MIN_ROWS} commands so far")
            if not ss["faster"]:
                reasons.append("slower than Deepgram")
            if ss["same_decision"] < STT_SAME_DECISION_TARGET:
                reasons.append(f"same decision {_pct(ss['same_decision'])} < {_pct(STT_SAME_DECISION_TARGET)}")
            out.append("NOT YET: " + "; ".join(reasons) + ".")
    elif not ss.get("errors"):
        out.append("No data yet — set LOCAL_STT=shadow in ~/.jarvis_env (after installing Moonshine).")
    return "\n".join(out)


if __name__ == "__main__":
    print(report())

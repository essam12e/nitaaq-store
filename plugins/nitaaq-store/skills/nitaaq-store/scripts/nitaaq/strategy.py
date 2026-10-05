"""Business strategist, deterministic part.

The strategist never works alone and never brings new numbers. It takes
findings from at least two specialists that passed review, picks a few
priorities, says what not to do now, and lists the assumptions it rests on.
Two findings that cite the same evidence are one piece of support, not two.
Seasons (Ramadan, National Day, White Friday) are context the merchant can
weigh, not evidence.
"""

from __future__ import annotations

import re

from .arabic import to_western_digits

MIN_AGENTS = 2
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
FORECAST = re.compile(r"(ستزيد|سيزيد|بتزيد|بيزيد|راح تزيد|راح يزيد|سترتفع|سيرتفع|بترتفع|بيرتفع|ستتضاعف|will increase|will grow)"
                      r"[^.؟!\n]{0,40}?\d+\s*%", re.I)


def _passing(findings: list[dict], review: dict) -> list[dict]:
    ok = {r["finding_id"] for r in review.get("results", []) if r["status"] == "pass"}
    return [f for f in findings if f.get("finding_id") in ok and f.get("agent") not in ("reviewer", "business_strategist")]


def _support_key(f: dict) -> tuple:
    ent = f.get("entity") or {}
    return (ent.get("type"), str(ent.get("id"))) if ent.get("id") else ("metric", f.get("metric"))


def synthesize(findings: list[dict], review: dict, *, top: int = 3) -> dict:
    """Priorities from reviewed specialist findings. Refuses with fewer than two specialists."""
    good = _passing(findings, review)
    agents = sorted({f["agent"] for f in good})
    if len(agents) < MIN_AGENTS:
        return {"kind": "strategy", "status": "insufficient", "agents": agents,
                "reason_ar": f"الاستراتيجي يحتاج نتائج مراجَعة من تخصصين على الأقل؛ المتاح: {len(agents)}."}
    groups: dict = {}
    for f in good:
        groups.setdefault(_support_key(f), []).append(f)
    options = []
    for key, fs in groups.items():
        lead = min(fs, key=lambda f: (PRIORITY_RANK.get(f.get("priority"), 3),
                                      CONFIDENCE_RANK.get((f.get("confidence") or {}).get("level"), 3)))
        refs = [frozenset(f.get("evidence_refs") or []) for f in fs]
        independent = len({r for r in refs if r})  # findings citing the same evidence count once
        options.append({
            "source_findings": [f["finding_id"] for f in fs], "agents": sorted({f["agent"] for f in fs}),
            "priority": lead.get("priority"), "confidence": (lead.get("confidence") or {}).get("level"),
            "summary_ar": lead.get("interpretation_ar", ""), "proposed_action_ref": lead.get("proposed_action_ref"),
            "independent_support": independent, "same_evidence": independent < len(fs),
            "assumptions_ar": sorted({x for f in fs for x in f.get("limitations_ar") or []})[:4],
        })
    options.sort(key=lambda o: (PRIORITY_RANK.get(o["priority"], 3), CONFIDENCE_RANK.get(o["confidence"], 3),
                                -o["independent_support"], o["source_findings"][0]))
    now, later = options[:top], options[top:]
    return {"kind": "strategy", "status": "ok", "agents": agents, "priorities": now,
            "not_now": [{"source_findings": o["source_findings"], "summary_ar": o["summary_ar"],
                         "why_ar": "أولوية أو ثقة أقل؛ نرجع له بعد ما نخلص الأولويات."} for o in later],
            "scenarios_ar": "لا توقعات رقمية؛ أي أثر يُقاس بعد التنفيذ بنفس طريقة القياس.",
            "excluded": sorted({f.get("finding_id") for f in findings} - {f["finding_id"] for f in good})}


def check_plan(plan: dict, findings: list[dict], review: dict) -> list[dict]:
    """The strategist's own written plan: every point cites passing findings, no new numbers, no forecasts."""
    issues = []
    good = {f["finding_id"]: f for f in _passing(findings, review)}
    if len({good[i]["agent"] for o in plan.get("priorities", []) for i in o.get("source_findings", []) if i in good}) < MIN_AGENTS \
            and plan.get("status") == "ok":
        issues.append({"check": "single_specialist", "message_ar": "الخطة مبنية على تخصص واحد."})
    for o in plan.get("priorities", []) + plan.get("not_now", []):
        src = [good.get(i) for i in o.get("source_findings", [])]
        if not src or None in src:
            issues.append({"check": "unsupported_point", "point": o.get("summary_ar", "")[:80],
                           "message_ar": "نقطة بدون استنتاج مراجَع تستند عليه."})
            continue
        known = set()
        for f in src:
            text = " ".join([f.get("interpretation_ar", "")] + [str(x.get(k)) for x in f.get("observed") or []
                                                                for k in ("current", "baseline")])
            known |= set(NUMBER.findall(to_western_digits(text)))
        text = o.get("summary_ar", "") + " " + o.get("why_ar", "")
        new = [n for n in NUMBER.findall(to_western_digits(text)) if n not in known]
        if new:
            issues.append({"check": "new_number", "numbers": new, "message_ar": "رقم ما ورد في أي استنتاج مراجَع."})
        if FORECAST.search(text):
            issues.append({"check": "forecast", "message_ar": "توقع رقمي بدون افتراضات؛ الاستراتيجي يقدّم سيناريوهات فقط."})
    return issues


def plan_markdown_ar(plan: dict) -> str:
    if plan["status"] != "ok":
        return plan["reason_ar"]
    out = ["## الأولويات"]
    for n, o in enumerate(plan["priorities"], 1):
        support = "دليل واحد" if o["independent_support"] < 2 else f"{o['independent_support']} أدلة مستقلة"
        out.append(f"{n}. {o['summary_ar']} ({support}؛ من: {'، '.join(o['agents'])})")
    if plan["not_now"]:
        out += ["", "## ما نأجله الحين"]
        out += [f"- {o['summary_ar']}" for o in plan["not_now"]]
    out += ["", plan["scenarios_ar"]]
    return "\n".join(out)

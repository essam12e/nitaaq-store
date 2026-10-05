"""Phase 6: retention (segments, consent, drafts, send gate) and the business strategist."""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from _path import ROOT, SKILL

from nitaaq import approvals, evidence, retention, review, routing, strategy

CLI = SKILL / "scripts" / "nitaaq_cli.py"
AS_OF = "2026-10-01"


def d(days_ago):
    return (date(2026, 10, 1) - timedelta(days=days_ago)).isoformat() + " 12:00"


def orders():
    rows, n = [], 0

    def add(c, days_ago, total="100", status="تم التنفيذ"):
        nonlocal n
        n += 1
        rows.append({"id": f"o{n}", "customer_id": c, "date": d(days_ago), "total": total, "status": status})
    add("new1", 5)
    add("one1", 80)
    add("rep1", 10, "900"); add("rep1", 100, "900")
    add("rep2", 20); add("rep2", 200)
    add("risk1", 90); add("risk1", 150)
    add("gone1", 300); add("gone1", 400)
    add("x", 3, status="ملغي")  # cancelled: no segment
    rows.append({"id": "o99", "date": d(4), "total": "50", "status": "تم التنفيذ"})  # no customer id
    return rows


CUSTOMERS = [
    {"id": "risk1", "mobile": "0551234567", "consent": {"whatsapp": True}},
    {"id": "rep1", "mobile": "0551111111", "whatsapp_consent": "yes"},
    {"id": "rep2", "mobile": "0552222222"},  # consent not recorded
    {"id": "gone1", "mobile": "0553333333", "accepts_marketing": False},
    {"id": "one1", "email": "a@b.c", "accepts_marketing": True, "unsubscribed": True},
]


class SegmentTests(unittest.TestCase):
    def test_segments(self):
        s = retention.segments(orders(), as_of=AS_OF)
        got = {k: v["ids"] for k, v in s["segments"].items()}
        self.assertEqual(got, {"new": ["new1"], "one_time": ["one1"], "active_repeat": ["rep1", "rep2"],
                               "at_risk": ["risk1"], "lapsed": ["gone1"]})
        self.assertEqual(s["vip_ids"], ["rep1"])
        self.assertEqual(s["orders_without_customer_id"], 1)
        self.assertNotIn("insufficient_history", s["flags"])
        short = retention.segments([o for o in orders() if o["date"] >= d(60)], as_of=AS_OF)
        self.assertIn("insufficient_history", short["flags"])

    def test_finding_review(self):
        with tempfile.TemporaryDirectory() as t:
            store = evidence.EvidenceStore(t, "S1")
            ev, recs = evidence.make_evidence("S1", {"kind": "mcp", "operation": "orders.list"}, orders(), records_total=13)
            store.save(ev, recs)
            f = retention.segments_finding(retention.segments(recs, as_of=AS_OF), ev)
            self.assertEqual(review.review_finding(f, store)["status"], "pass")
            bad = dict(f, interpretation_ar="أرسلنا رسالة واتساب للعملاء المنقطعين.")
            self.assertIn("claimed_message_sent", [i["code"] for i in review.review_finding(bad, store)["issues"]])
            f["observed"][0]["current"] = "7"
            self.assertEqual(review.review_finding(f, store)["status"], "fail")


class ConsentAndMessageTests(unittest.TestCase):
    def test_audience_keeps_recorded_consent_only(self):
        s = retention.segments(orders(), as_of=AS_OF)
        a = retention.audience(s, "active_repeat", CUSTOMERS, "whatsapp")
        self.assertEqual(a["eligible_ids"], ["rep1"])
        self.assertEqual(a["excluded"], {"unknown_consent": 1})
        self.assertEqual(retention.audience(s, "lapsed", CUSTOMERS, "whatsapp")["excluded"], {"no_consent": 1})
        self.assertEqual(retention.audience(s, "one_time", CUSTOMERS, "email")["excluded"], {"unsubscribed": 1})
        self.assertEqual(retention.audience(s, "new", CUSTOMERS, "sms")["excluded"], {"not_in_customers": 1})
        self.assertEqual(retention.audience(s, "active_repeat", CUSTOMERS, "whatsapp", vip_only=True)["eligible_ids"], ["rep1"])
        self.assertNotIn("055", json.dumps(a, default=str))  # ids only, never contacts

    def test_check_message(self):
        facts = {"price": "199", "sale_price": "149"}
        ok = retention.check_message("هلا {name}، اشتقنا لك! عطر العود صار بـ 149 ريال. للإلغاء ارسل ايقاف", "sms", facts=facts)
        self.assertEqual(ok["issues"], [])
        self.assertEqual(ok["sms_parts"], 2 if len("هلا {name}، اشتقنا لك! عطر العود صار بـ 149 ريال. للإلغاء ارسل ايقاف") > 70 else 1)
        bad = retention.check_message("آخر فرصة! خصم 40% على كل شي {coupon}", "whatsapp", facts=facts)
        got = {i["check"] for i in bad["issues"]}
        self.assertEqual(got, {"no_opt_out", "urgency_needs_proof", "unknown_placeholder", "unverified_number"})
        self.assertEqual(retention.sms_parts("ا" * 70), 1)
        self.assertEqual(retention.sms_parts("ا" * 71), 2)


class SendGateTests(unittest.TestCase):
    TEXT = "هلا {name}، رجعنا لك بعطور جديدة. للإلغاء ارسل ايقاف"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ap = approvals.ApprovalStore.for_store(self.tmp.name, "S1")
        customers = CUSTOMERS + [{"id": "c9", "mobile": "0559999999", "consent": {"whatsapp": True}}]
        self.customers = customers
        aud = {"eligible_ids": ["risk1", "rep1", "c9"], "channel": "whatsapp", "segment": "at_risk"}
        self.prop = retention.send_proposal(aud, self.TEXT, store_id="S1")
        self.rec = self.ap.grant("S1", "messages.send", self.prop["items"], proposal_id="mp1")

    def tearDown(self):
        self.tmp.cleanup()

    def gate(self, **kw):
        args = dict(store_id="S1", channel="whatsapp", text=self.TEXT, recipient_ids=["risk1", "rep1", "c9"],
                    customers_now=self.customers, sender_available=True)
        args.update(kw)
        return retention.send_gate(self.ap, self.rec["approval_id"], **args)

    def test_passes_with_approval_consent_and_sender(self):
        g = self.gate()
        self.assertTrue(g["ok"])
        self.assertEqual(g["allowed"], ["risk1", "rep1", "c9"])

    def test_no_sender_no_send(self):
        self.assertEqual(self.gate(sender_available=False)["allowed"], [])

    def test_changed_text_or_new_recipient_needs_new_approval(self):
        self.assertEqual(self.gate(text=self.TEXT + "!")["reasons"], ["payload_changed"])
        extra = self.customers + [{"id": "z", "mobile": "1", "consent": {"whatsapp": True}}]
        self.assertIn("entity_not_approved", self.gate(recipient_ids=["risk1", "z"], customers_now=extra)["reasons"])

    def test_withdrawn_consent_drops_out(self):
        now = [dict(c, consent={"whatsapp": False}) if c["id"] == "c9" else c for c in self.customers]
        g = self.gate(customers_now=now)
        self.assertEqual((g["ok"], g["allowed"], g["blocked"]), (True, ["risk1", "rep1"], ["c9"]))

    def test_other_store_or_used(self):
        self.assertIn("wrong_store", retention.send_gate(self.ap, self.rec["approval_id"], store_id="S2", channel="whatsapp",
                                                         text=self.TEXT, recipient_ids=["rep1"],
                                                         customers_now=self.customers, sender_available=True)["reasons"])
        self.ap.mark_used(self.rec["approval_id"])
        self.assertIn("used", self.gate()["reasons"])


def finding(fid, agent, priority="high", level="medium", entity=None, refs=("ev1",), current="12", text="نص"):
    return {"finding_id": fid, "agent": agent, "priority": priority, "confidence": {"level": level},
            "entity": entity, "metric": f"m_{agent}", "evidence_refs": list(refs), "interpretation_ar": text,
            "observed": [{"label_ar": "x", "current": current, "baseline": None}], "limitations_ar": [f"قيد {fid}"]}


class StrategyTests(unittest.TestCase):
    def rv(self, fs, failed=()):
        return {"results": [{"finding_id": f["finding_id"], "status": "fail" if f["finding_id"] in failed else "pass"} for f in fs]}

    def test_needs_two_specialists(self):
        fs = [finding("a", "pricing"), finding("b", "pricing")]
        self.assertEqual(strategy.synthesize(fs, self.rv(fs))["status"], "insufficient")
        fs.append(finding("c", "growth"))
        self.assertEqual(strategy.synthesize(fs, self.rv(fs, failed={"c"}))["status"], "insufficient")  # failed review

    def test_priorities_support_and_checks(self):
        oud = {"type": "product", "id": "p1"}
        fs = [finding("a", "pricing", entity=oud, refs=("ev_p",), text="سعر عطر العود تحت التكلفة بـ 12 ريال"),
              finding("b", "customer_intelligence", entity=oud, refs=("ev_r",), text="شكاوى الجودة على عطر العود"),
              finding("c", "store_analytics", entity=oud, refs=("ev_p",)),
              finding("d", "growth", priority="medium", level="low"),
              finding("e", "cro", priority="low"), finding("f", "seo_geo", priority="medium"),
              finding("g", "tracking", priority="medium")]
        plan = strategy.synthesize(fs, self.rv(fs))
        self.assertEqual(plan["status"], "ok")
        top = plan["priorities"][0]
        self.assertEqual(top["source_findings"], ["a", "b", "c"])
        self.assertEqual(top["independent_support"], 2)  # a and c cite the same evidence
        self.assertTrue(top["same_evidence"])
        self.assertTrue(plan["not_now"])
        self.assertEqual(strategy.check_plan(plan, fs, self.rv(fs)), [])
        plan["priorities"][0]["summary_ar"] = "لو نزلنا السعر بتزيد المبيعات 30% خلال شهر"
        got = {i["check"] for i in strategy.check_plan(plan, fs, self.rv(fs))}
        self.assertEqual(got, {"new_number", "forecast"})
        plan["priorities"].append({"source_findings": ["zz"], "summary_ar": "توسع للإمارات"})
        self.assertIn("unsupported_point", {i["check"] for i in strategy.check_plan(plan, fs, self.rv(fs))})
        self.assertIn("الأولويات", strategy.plan_markdown_ar(strategy.synthesize(fs, self.rv(fs))))


class Phase6RoutingCliTests(unittest.TestCase):
    def test_routing(self):
        r = routing.route("أبي استراتيجية لمتجري السنة الجاية", {"orders.list"})
        st = {s["agent"]: s for s in r["stages"]}
        self.assertEqual(r["specialists_to_run"], ["store_analytics", "growth", "business_strategist"])
        self.assertEqual(st["business_strategist"]["depends_on"], ["store_analytics", "growth"])
        alone = routing.route("أبي استراتيجية لمتجري", {"reports.sales"})  # only store analytics can run
        st = {s["agent"]: s for s in alone["stages"]}["business_strategist"]
        self.assertEqual((st["status"], st["missing"]), ("blocked", ["two_specialists"]))
        self.assertNotIn("business_strategist", alone["specialists_to_run"])
        send = routing.route("ارسل واتساب للعملاء القدام", {"orders.list", "customers.list"})
        self.assertEqual((send["specialists_to_run"], send["execution_path"]), (["email_retention"], "message_send_gate"))

    def test_cli_flow(self):
        with tempfile.TemporaryDirectory() as t:
            Path(t, "orders.json").write_text(json.dumps(orders(), ensure_ascii=False), encoding="utf-8")
            Path(t, "customers.json").write_text(json.dumps(CUSTOMERS, ensure_ascii=False), encoding="utf-8")

            def run(*args):
                r = subprocess.run([sys.executable, str(CLI), *args], cwd=t, capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(r.returncode, 0, r.stderr)
                return json.loads(r.stdout)
            seg = run("retention", "segments", "--store-id", "S1", "--orders", "orders.json", "--orders-total", "13", "--as-of", AS_OF)
            self.assertEqual(seg["review"]["counts"], {"pass": 1})
            self.assertNotIn("ids", seg["segments"]["at_risk"])
            text = "هلا {name}، اشتقنا لك. للإلغاء ارسل ايقاف"
            p = run("retention", "propose", "--store-id", "S1", "--orders", "orders.json", "--as-of", AS_OF,
                    "--customers", "customers.json", "--segment", "at_risk", "--channel", "whatsapp", "--text", text)
            self.assertEqual(p["proposal"]["operation"], "messages.send")
            Path(t, "items.json").write_text(json.dumps(p["proposal"]["items"]), encoding="utf-8")
            g = run("approvals", "grant", "--store-id", "S1", "--op", "messages.send", "--items", "items.json")
            gate = ["send-gate", "--store-id", "S1", "--approval-id", g["approval_id"], "--channel", "whatsapp",
                    "--text", text, "--recipients", "risk1", "--customers", "customers.json"]
            self.assertEqual(run(*gate)["allowed"], [])  # no sender in this session
            self.assertEqual(run(*gate, "--sender-available")["allowed"], ["risk1"])
            bad = run("retention", "propose", "--store-id", "S1", "--orders", "orders.json", "--as-of", AS_OF,
                      "--customers", "customers.json", "--segment", "at_risk", "--channel", "whatsapp", "--text", "آخر فرصة!")
            self.assertIsNone(bad["proposal"])

    def test_generated_agents_read_only(self):
        for name in ("email-retention", "business-strategist"):
            md = (ROOT / "plugins" / "nitaaq-store" / "agents" / f"nitaaq-{name}.md").read_text(encoding="utf-8")
            self.assertIn("mcp__*", md, name)
            toml = (ROOT / "adapters" / "codex" / "custom-agents" / f"nitaaq-{name}.toml").read_text(encoding="utf-8")
            self.assertIn('sandbox_mode = "read-only"', toml, name)


if __name__ == "__main__":
    unittest.main()

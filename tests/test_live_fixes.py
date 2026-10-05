"""Fixes found by running "why are sales down?" read-only on a live Salla store."""

import json
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from _path import SKILL

from nitaaq import evidence, findings, metrics, review

CLI = SKILL / "scripts" / "nitaaq_cli.py"
CUR, BASE = ("2026-09-07", "2026-10-04"), ("2026-08-10", "2026-09-06")
DONE, PENDING = "تم التنفيذ", "بإنتظار المراجعة"


def orders():
    rows = [
        ("1", "2026-09-10 10:00", "500", DONE, "الرياض"), ("2", "2026-09-20 10:00", "300", DONE, "جدة"),
        ("3", "2026-09-25 10:00", "200", PENDING, "جدة"), ("4", "2026-10-01 10:00", "222", "ملغي", "الدمام"),
        ("5", "2026-08-12 10:00", "400", DONE, "جدة"), ("6", "2026-08-20 10:00", "380", DONE, "الدمام"),
        ("7", "2026-08-25 10:00", "210", PENDING, "الرياض"),
    ]
    return [{"id": i, "date": d, "total": t, "status": s, "city": c} for i, d, t, s, c in rows]


STATUSES = [DONE, PENDING]


class Shares(unittest.TestCase):
    def test_hidden_when_total_change_is_tiny(self):
        d = metrics.decompose(orders(), CUR, BASE, "city", statuses=STATUSES)  # 1000 vs 990: +1%
        self.assertFalse(d["shares_shown"])
        self.assertTrue(all(r["share_of_change_pct"] is None for r in d["rows"]))
        self.assertIn("ما لها معنى", d["shares_note_ar"])

    def test_shown_when_change_is_real(self):
        rows = orders() + [{"id": "9", "date": "2026-09-30 10:00", "total": "900", "status": DONE, "city": "الرياض"}]
        d = metrics.decompose(rows, CUR, BASE, "city", statuses=[DONE])
        self.assertTrue(d["shares_shown"])
        self.assertEqual(sum(r["share_of_change_pct"] for r in d["rows"]), Decimal("100.0"))

    def test_where_finding_never_prints_none_percent(self):
        ev, _ = evidence.make_evidence("s1", {"kind": "mcp", "operation": "orders.list"}, orders(), period=[BASE[0], CUR[1]],
                                       records_total=7)
        cmp = metrics.compare_periods(orders(), CUR, BASE, statuses=STATUSES)
        dec = metrics.decompose(orders(), CUR, BASE, "city", statuses=STATUSES)
        f = findings.where_finding(dec, ev, cmp)
        self.assertNotIn("None", f["interpretation_ar"])


class ReportRows(unittest.TestCase):
    def test_overlapping_rows_show_amounts_only_biggest_movers_first(self):
        cur = [{"c": "كل المنتجات", "v": 2877}, {"c": "كؤوس", "v": 1050}, {"c": "اثقال", "v": 18}]
        base = [{"c": "كل المنتجات", "v": 2258}, {"c": "كؤوس", "v": 136}, {"c": "اثقال", "v": 733}]
        d = metrics.decompose_rows(cur, base, "c", "v", exclude=["كل المنتجات"], overlapping=True)
        self.assertEqual([r["value"] for r in d["rows"]], ["كؤوس", "اثقال"])
        self.assertEqual(d["rows"][1]["change"], Decimal("-715"))
        self.assertIsNone(d["rows"][0]["share_of_change_pct"])
        self.assertIn("أكثر من تصنيف", d["shares_note_ar"])

    def test_cli(self):
        with tempfile.TemporaryDirectory() as t:
            a, b = Path(t, "a.json"), Path(t, "b.json")
            a.write_text(json.dumps({"rows": [{"c": "x", "v": "10"}]}), encoding="utf-8")
            b.write_text(json.dumps([{"c": "x", "v": "5"}]), encoding="utf-8")
            p = subprocess.run([sys.executable, str(CLI), "metrics", "decompose-rows", "--current-rows", str(a),
                                "--baseline-rows", str(b), "--label", "c", "--value", "v"], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout)["rows"][0]["change"], "5")


class Reconcile(unittest.TestCase):
    def test_gap_explained_by_status(self):
        r = metrics.reconcile_report(orders(), CUR, "800", statuses=STATUSES)
        self.assertEqual(r["gap"], Decimal("200.00"))
        self.assertEqual(r["explained_by"], {"excluded_in_report": [PENDING], "included_in_report": []})
        self.assertIn(PENDING, r["explanation_ar"])

    def test_report_rounding_matches(self):
        self.assertTrue(metrics.reconcile_report(orders(), CUR, "1000.4", statuses=STATUSES)["matches"])

    def test_unexplained_gap_is_said_so(self):
        r = metrics.reconcile_report(orders(), CUR, "640", statuses=STATUSES)
        self.assertIsNone(r["explained_by"])
        self.assertIn("ما قدرنا نفسره", r["explanation_ar"])

    def test_status_counted_in_report_but_not_by_us(self):
        r = metrics.reconcile_report(orders(), CUR, "1222", statuses=STATUSES)
        self.assertEqual(r["explained_by"]["included_in_report"], ["ملغي"])


class SalesChangeCli(unittest.TestCase):
    def run_cli(self, root, *extra):
        f = Path(root, "orders.json")
        f.write_text(json.dumps(orders(), ensure_ascii=False), encoding="utf-8")
        p = subprocess.run([sys.executable, str(CLI), "sales-change", "--store-id", "s1", "--orders", str(f),
                            "--current", ",".join(CUR), "--baseline", ",".join(BASE), "--total", "7",
                            "--statuses", ",".join(STATUSES), "--now", "2026-10-05T12:00:00+03:00",
                            "--root", str(Path(root, ".nitaaq")), *extra], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_source_check_finding_passes_review_and_flags_direction(self):
        with tempfile.TemporaryDirectory() as t:
            out = self.run_cli(t, "--reported-current", "800,2", "--reported-baseline", "780,2")
            src = next(f for f in out["findings"] if f["finding_id"] == "f_src")
            self.assertEqual({r["finding_id"]: r["status"] for r in out["review"]["results"]}["f_src"], "pass")
            self.assertIn(PENDING, src["interpretation_ar"])
            self.assertEqual(out["comparison"]["source_check"]["current"]["gap"], "200.00")
            # tampered numbers are caught on recompute
            src["observed"][1]["current"] = "1"
            store = evidence.EvidenceStore(Path(t, ".nitaaq"), "s1")
            self.assertEqual(review.review_finding(src, store)["status"], "fail")

    def test_report_lists_unknowns_without_a_saved_plan(self):
        with tempfile.TemporaryDirectory() as t:
            out = self.run_cli(t)
            f = Path(t, "sc.json")
            f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
            p = subprocess.run([sys.executable, str(CLI), "report", "--question", "ليش المبيعات نازلة؟", "--findings", str(f),
                                "--root", str(Path(t, ".nitaaq"))], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("الزيارات", p.stdout)
            self.assertIn("الإعلانات", p.stdout)


if __name__ == "__main__":
    unittest.main()

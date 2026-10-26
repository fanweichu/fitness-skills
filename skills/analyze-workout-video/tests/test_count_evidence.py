"""Structural regressions with synthetic video; not a perception benchmark."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from count_evidence import load_receipt, validate_evidence, validate_manifest, validate_report_evidence, digest
from extract_review_frames import extract, plan_frames


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class CountEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="count-evidence-tests-")
        cls.root = Path(cls.tmp.name)
        cls.window = cls.root / "synthetic.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=320x180:rate=10", "-t", "12", "-c:v", "libx264",
                        "-threads", "1", str(cls.window)], check=True, timeout=30)
        stat = cls.window.stat()
        fingerprint = "stat-sha256:" + hashlib.sha256(
            f'{cls.window}\0{stat.st_size}\0{stat.st_mtime_ns}'.encode()).hexdigest()
        marker = {"complete": True, "coarse_only": False,
                  "purpose": "source_local_exact_verification", "sampling_fps": 10,
                  "source_path": str(cls.window), "source_fingerprint": fingerprint,
                  "source_start_s": 0, "source_end_s": 12,
                  "validation": {"frame_count": 120, "width": 320, "height": 180}}
        write(cls.window.with_suffix(".complete.json"), marker)
        cls.receipt_path = extract(cls.window, cls.root / "frames", file="fixture",
                                   start=0, end=10.9, step=.1)
        cls.receipt = load_receipt(cls.receipt_path)

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.root))
        self.ep = self.work / "evidence.json"

    def evidence(self):
        return {"schema_version": 1, "method": "visual_cycle_sequence", "set_id": "row-1",
                "exercise_id": "row.backpack", "set": 1, "confidence": "high",
                "coverage_complete": True, "receipts": {"w": str(self.receipt_path)},
                "segments": [{"file": "fixture", "start_s": 1, "end_s": 7}],
                "review": {"reviewer": "synthetic-test-not-a-visual-review",
                           "cycle_definition": "boundary-opposite-boundary",
                           "positive_cues": ["fixture cue A", "fixture cue B"],
                           "excluded_lookalike": "fixture setup",
                           "viewed_pages": {"w": [p["id"] for p in self.receipt["pages"]]}},
                "context": {"before": "w/f000000", "after": "w/f000090",
                            "after_observation": "synthetic reset fixture"},
                "events": [{"id": f"r{i+1}", "status": "complete",
                            "frames": [f"w/f{n:06d}" for n in (10+20*i,20+20*i,30+20*i)],
                            "phase_labels": ["boundary", "opposite", "boundary"],
                            "observation": "synthetic complete cycle fixture"} for i in range(3)]}

    def validate(self, data): return validate_evidence(write(self.ep, data))

    def bad(self, data, pattern):
        with self.assertRaisesRegex(ValueError, pattern): self.validate(data)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS/"analysis_manifest.py"),
                               str(self.work/"manifest.json"), *args], capture_output=True, text=True)

    def manifest(self, data=None):
        write(self.ep, data or self.evidence())
        result = self.cli("--candidate-set", "row-1", "--evidence", f"row-1={self.ep}", "--assert-final-ready")
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.work/"manifest.json"

    def report(self):
        self.manifest()
        return {"schema_version": 3, "evidence_manifest": "manifest.json",
                "report": {"date": "2000-01-01", "overall_confidence": "high"},
                "sources": [{**self.receipt["source"], "duration_s": 12}],
                "exercises": [{"exercise_id": "row.backpack", "name": "背包划船", "sets": 1,
                               "reps": [3], "total": 3, "confidence": "high"}],
                "timeline": [{"evidence_set_id": "row-1", "exercise_id": "row.backpack",
                              "exercise": "背包划船", "set": 1, "file": "fixture", "start_s": 1,
                              "end_s": 7, "completed": 3, "partial": 0, "uncertain": 0,
                              "confidence": "high"}], "issues": []}

    def test_110_frames_paginate_without_96_frame_truncation(self):
        self.assertEqual(len(self.receipt["frames"]), 110)
        self.assertEqual([len(x["frame_ids"]) for x in self.receipt["pages"]], [48,48,14])
        self.assertEqual(self.receipt["frames"][-1]["source_s"], 10.9)

    def test_truncated_receipts_and_pages_fail(self):
        for mutation in ("frames", "pages"):
            with self.subTest(mutation=mutation):
                r = copy.deepcopy(self.receipt); r[mutation].pop()
                p = write(self.receipt_path.parent/f"bad-{mutation}.json",r)
                with self.assertRaises(ValueError): load_receipt(p)

    def test_changed_frame_asset_fails(self):
        r = copy.deepcopy(self.receipt); r["frames"][0]["sha256"] = "0"*64
        with self.assertRaisesRegex(ValueError, "changed evidence"):
            load_receipt(write(self.receipt_path.parent/"bad-hash.json",r))

    def test_coarse_receipt_fails(self):
        r = copy.deepcopy(self.receipt); r["window"]["completion"]["coarse_only"] = True
        with self.assertRaisesRegex(ValueError, "coarse"):
            load_receipt(write(self.receipt_path.parent/"bad-coarse.json",r))

    def test_frame_selection_includes_end(self):
        self.assertEqual(plan_frames([i/10 for i in range(120)],0,0,10.9,1)[-1],109)
        with self.assertRaises(ValueError): plan_frames([0,.1],0,0,20,1)

    def test_counts_derived_and_no_fixed_twelve(self):
        for n in (1,2,3):
            e = self.evidence(); e["events"] = e["events"][:n]; e["segments"][0]["end_s"] = 1+2*n
            self.assertEqual(self.validate(e)["completed"], n)

    def test_missing_return_is_partial_not_complete(self):
        e = self.evidence(); event = e["events"][-1]
        event.update(status="partial",reason="return not observed",phase_labels=["boundary","opposite","interrupted"])
        result = self.validate(e); self.assertEqual((result["completed"], result["partial"]),(2,1))
        event["status"] = "complete"; self.bad(e,"return not confirmed")

    def test_setup_excluded(self):
        e = self.evidence(); e["disposition"] = "non_set"
        for event in e["events"]: event.update(status="excluded",reason="equipment setup")
        r = self.validate(e); self.assertEqual((r["completed"],r["excluded"]),(0,3))

    def test_v2_working_set_requires_context_intent_gate(self):
        e = self.evidence(); e["schema_version"] = 2
        self.bad(e, "full-context")
        e["classification_audit"] = {
            "full_context_reviewed": True,
            "intent": "working_set",
            "closest_lookalike": "equipment adjustment",
            "discriminator": "same load path repeats without touching straps or clothing",
            "stable_configuration": True,
            "consistent_cycle_path": True,
            "setup_or_adjustment_observed": False,
            "novel_or_one_off": False,
        }
        self.assertEqual(self.validate(e)["completed"], 3)
        e["classification_audit"]["setup_or_adjustment_observed"] = True
        self.bad(e, "adjustment")

    def test_v2_working_set_needs_two_cycles(self):
        e = self.evidence(); e["schema_version"] = 2; e["events"] = e["events"][:1]
        e["segments"][0]["end_s"] = 3
        e["context"]["after"] = "w/f000040"
        e["classification_audit"] = {
            "full_context_reviewed": True,
            "intent": "working_set",
            "closest_lookalike": "equipment adjustment",
            "discriminator": "repeated load path",
            "stable_configuration": True,
            "consistent_cycle_path": True,
            "setup_or_adjustment_observed": False,
            "novel_or_one_off": False,
        }
        self.bad(e, "at least two")

    def test_v2_one_off_needs_agreeing_second_pass(self):
        e = self.evidence(); e["schema_version"] = 2
        e["classification_audit"] = {
            "full_context_reviewed": True,
            "intent": "working_set",
            "closest_lookalike": "equipment adjustment",
            "discriminator": "same load path repeats without adjustment",
            "stable_configuration": True,
            "consistent_cycle_path": True,
            "setup_or_adjustment_observed": False,
            "novel_or_one_off": True,
        }
        self.bad(e, "second pass")
        e["classification_audit"]["second_pass"] = {
            "wide_context_reviewed": True,
            "agrees_with_exercise_id": True,
            "setup_adjustment_ruled_out": True,
        }
        self.assertEqual(self.validate(e)["completed"], 3)

    def test_duplicate_cycle_fails(self):
        e = self.evidence(); e["events"][1]["frames"] = e["events"][0]["frames"]
        self.bad(e,"overlapping")

    def test_skipped_middle_cycle_fails(self):
        e = self.evidence(); e["events"].pop(1); self.bad(e,"gap")

    def test_unseen_page_fails(self):
        e = self.evidence(); e["review"]["viewed_pages"]["w"] = ["p001"]
        self.bad(e,"not on a reviewed page")

    def test_context_and_boundary_failures(self):
        e = self.evidence(); e["context"]["after"] = "w/f000060"; self.bad(e,"post-set")
        e = self.evidence(); e["segments"][0]["end_s"] = 11.5; self.bad(e,"beyond extracted")

    def test_uncertainty_confidence(self):
        e = self.evidence(); e["events"][-1].update(status="uncertain",reason="occluded return")
        self.bad(e,"high confidence")
        e["confidence"] = "medium"; self.assertEqual(self.validate(e)["uncertain"],1)

    def test_incomplete_coverage_needs_reason(self):
        e = self.evidence(); e["coverage_complete"] = False; self.bad(e,"concrete reason")
        e.update(unresolved_reason="source ended",confidence="medium")
        self.assertFalse(self.validate(e)["coverage_complete"])

    def test_per_side_counts_not_doubled(self):
        e = self.evidence(); e["counting_mode"] = "per_side"
        events = []
        for side in ("left","right"):
            for event in copy.deepcopy(e["events"]):
                event.update(id=side+event["id"],side=side); events.append(event)
        e["events"] = events
        result = self.validate(e)
        self.assertEqual(result["completed"],3)
        self.assertEqual(result["reps_per_side"],{"left":3,"right":3})

    def test_cross_file_cycle_counted_once(self):
        r = copy.deepcopy(self.receipt); r["source"]["file"] = "second"
        rp = write(self.receipt_path.parent/"second-source-receipt.json",r)
        e = self.evidence(); e["receipts"]["b"] = str(rp)
        e["review"]["viewed_pages"]["b"] = [p["id"] for p in r["pages"]]
        e["segments"] = [{"file":"fixture","start_s":1,"end_s":5},
                         {"file":"second","start_s":0,"end_s":2}]
        e["events"][-1]["frames"] = ["w/f000050","b/f000010","b/f000020"]
        e["context"]["after"] = "b/f000030"
        result = self.validate(e)
        self.assertEqual(result["completed"],3)
        self.assertEqual(len(result["segments"]),2)

    def test_registered_pages_are_portable_and_not_double_charged(self):
        p = self.cli("--reviewed-pages", f"{self.receipt_path}#p001,p002,p003")
        self.assertEqual(p.returncode,0,p.stderr)
        path = self.manifest(); m = json.loads(path.read_text())
        self.assertTrue(all(not Path(a["path"]).is_absolute() for a in m["visible_pages"].values()))
        self.assertEqual(validate_manifest(path)[1]["model_visible_frames"],110)
        result = self.cli("--final-count","row-1=999")
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(validate_manifest(path)[0]["row-1"]["completed"],3)

    def test_generated_realpath_alias_deduplicates(self):
        page = self.receipt_path.parent/"page-001.jpg"
        alias = self.work/"alias.jpg"; alias.symlink_to(page)
        p = self.cli("--generated",str(alias),"--generated",str(page))
        self.assertEqual(p.returncode,0,p.stderr)
        path = self.manifest(); m = json.loads(path.read_text())
        self.assertEqual(m["counts"]["generated_contact_sheets"],3)
        self.assertTrue(all(not Path(x).is_absolute() for x in m["generated_contact_sheets"]))

    def test_excluded_context_refs_validated(self):
        e = self.evidence(); e["context"]["excluded_context"] = [
            {"frames":["w/f999999"],"observation":"setup","reason":"outside work"}]
        self.bad(e,"reviewed page")
        e["context"]["excluded_context"][0]["frames"] = ["w/f000020"]
        self.bad(e,"in-set exclusion")

    def test_summary_per_set_matches_evidence(self):
        d = self.report(); d["exercises"][0]["exercise_id"] = "other.exercise"
        with self.assertRaisesRegex(ValueError,"summary exercise"):
            validate_report_evidence(d,self.work/"report.json")

    def test_fake_claim_999_fails(self):
        e = self.evidence(); e["claimed_count"] = 999; self.bad(e,"claimed count")
        result = self.cli("--candidate-set","row-1","--count-evidence",
                          "row-1=visual_cycle_sequence:missing-evidence.json","--final-count","row-1=999")
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.work/"manifest.json").exists())

    def test_manifest_counts_budget_and_mutation(self):
        path = self.manifest(); results,cost = validate_manifest(path)
        self.assertEqual(results["row-1"]["completed"],3)
        self.assertEqual(cost["model_visible_frames"],110)
        self.assertEqual(json.loads(path.read_text())["counts"]["generated_contact_sheets"],3)
        m = json.loads(path.read_text()); m["budget"]["max_frames"] = 100
        with self.assertRaisesRegex(ValueError,"budget"): validate_manifest(write(path,m))
        m["budget"]["max_frames"] = 1200; write(path,m)
        e = self.evidence(); e["review"]["reviewer"] = "changed"; write(self.ep,e)
        with self.assertRaisesRegex(ValueError,"changed"): validate_manifest(path)

    def test_renderer_reopens_evidence_and_rejects_999(self):
        d = self.report(); d["timeline"][0]["completed"] = 999
        with self.assertRaisesRegex(ValueError,"completed differs"):
            validate_report_evidence(d,self.work/"report.json")

    def test_renderer_rejects_changed_source_and_boundary(self):
        d = self.report(); d["sources"][0]["fingerprint"] = "wrong"
        with self.assertRaisesRegex(ValueError,"source identity"):
            validate_report_evidence(d,self.work/"report.json")
        d = self.report(); d["timeline"][0]["end_s"] = 6
        with self.assertRaisesRegex(ValueError,"boundaries"):
            validate_report_evidence(d,self.work/"report.json")

    def test_legacy_render_requires_explicit_flag(self):
        d = self.report(); d.pop("evidence_manifest"); d["schema_version"] = 2
        with self.assertRaisesRegex(ValueError,"explicit --allow-legacy"):
            validate_report_evidence(d,self.work/"report.json")
        validate_report_evidence(d,self.work/"report.json",allow_legacy=True)
        self.assertIn("历史兼容", d["_verification_notice"])
        d["schema_version"] = 3
        with self.assertRaises(ValueError): validate_report_evidence(d,self.work/"report.json",True)

    def test_strict_report_cli(self):
        report = write(self.work/"report.json",self.report()); out = self.work/"report.html"
        p = subprocess.run([sys.executable,str(SCRIPTS/"render_html_report.py"),str(report),str(out)],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn("逐次记录",out.read_text())

    def test_schema4_issue_needs_problem_gate(self):
        d = self.report(); d["schema_version"] = 4
        d["issues"] = [{"file":"fixture", "timestamp":"00:02.0", "exercise":"背包划船",
                        "observation":"support is soft", "scan_range":"00:01-00:03",
                        "correction":"change support", "image":str(self.receipt_path.parent/"page-001.jpg")}]
        report = write(self.work/"issue-report.json", d); out = self.work/"issue-report.html"
        p = subprocess.run([sys.executable,str(SCRIPTS/"render_html_report.py"),str(report),str(out)],capture_output=True,text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("problem basis", p.stderr)
        d["issues"][0]["problem_gate"] = {
            "basis":"visible_technique_failure",
            "criterion":"support remains stable through the repetition",
            "observed_violation":"support visibly slides",
            "observed_effect":"rear foot shifts and balance is interrupted",
        }
        report = write(self.work/"issue-report.json", d)
        p = subprocess.run([sys.executable,str(SCRIPTS/"render_html_report.py"),str(report),str(out)],capture_output=True,text=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_incomplete_report_is_lower_bound(self):
        d = self.report(); e = self.evidence()
        e.update(coverage_complete=False,unresolved_reason="video ended",confidence="medium")
        self.manifest(e); d["timeline"][0]["confidence"] = "medium"
        d["report"]["overall_confidence"] = d["exercises"][0]["confidence"] = "medium"
        validate_report_evidence(d,self.work/"report.json")
        self.assertTrue(d["_incomplete_counts"])


if __name__ == "__main__": unittest.main()

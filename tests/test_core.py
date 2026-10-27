#!/usr/bin/env python3
"""Deterministic core tests. Run with: python3 -m unittest discover tests"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
_TMP_BASE = Path("/tmp/opencode")
_TMP_BASE.mkdir(parents=True, exist_ok=True)
_TMP = tempfile.mkdtemp(prefix="preflight-tests-", dir=str(_TMP_BASE))
os.environ["PREFLIGHT_DB"] = str(Path(_TMP) / "test.db")

import companion  # noqa: E402
import coverage  # noqa: E402
import db  # noqa: E402
import export  # noqa: E402
import extract  # noqa: E402
import pipeline  # noqa: E402
import plan as plan_mod  # noqa: E402

BRIEF = """ENG 204 — Design Task 4: Bridge Deck Load Path
Produce a 4-page design memo for a simply supported pedestrian bridge deck.
The memo must include a load-path diagram, a free-body diagram, and a table of member forces.
Assume a deck span of 12 m and a uniform pedestrian load of 5 kPa.
Check the maximum bending moment against the steel section capacity.
State all units and sign conventions.
Identify at least two failure modes and one mitigation for each.
The memo must end with a recommendation of no more than 150 words.
Submit as PDF by Friday at 17:00.
"""

DRAFT = """The bridge deck transfers pedestrian load to the supports through the main girders.
The span is 12 m and the load is 5 kPa.
A load path diagram is included.
The maximum moment was compared with the capacity.
The recommendation is to use the larger section.
"""


def clear_tables() -> None:
    conn = db.connect()
    try:
        for table in (
            "feedback", "messages", "submissions", "audits", "memory",
            "network_log", "assignments", "courses",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


class ExtractTests(unittest.TestCase):
    def test_requirements_have_quotes_and_source_refs(self):
        result = extract.extract(BRIEF, source="brief.md", today=date(2026, 10, 26))
        self.assertGreaterEqual(len(result["requirements"]), 4)
        for req in result["requirements"]:
            self.assertTrue(req["source_ref"].startswith("brief.md:L"))
            self.assertIn(req["evidence"].lower()[:20], BRIEF.lower())
            self.assertIn(req["kind"], {"deliverable", "constraint", "acceptance", "process", "admin"})

    def test_weekday_deadline(self):
        result = extract.extract(BRIEF, today=date(2026, 10, 26))
        self.assertEqual(result["deadline"], "2026-10-30T17:00")

    def test_vague_language_is_flagged(self):
        result = extract.extract(
            "Use appropriate safety factors and a reasonable margin. Include several tests.",
            today=date(2026, 10, 26),
        )
        terms = {item["term"].lower() for item in result["ambiguities"]}
        self.assertIn("appropriate", terms)
        self.assertIn("reasonable", terms)
        self.assertGreaterEqual(len(result["ambiguities"]), 2)

    def test_no_invented_requirements(self):
        result = extract.extract("The deadline is Friday.", today=date(2026, 10, 26))
        for req in result["requirements"]:
            self.assertIn(req["evidence"].lower()[:12], "the deadline is friday.")


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.reqs = extract.extract(BRIEF, today=date(2026, 10, 26))["requirements"]

    def test_coverage_marks_evidence(self):
        result = coverage.check(self.reqs, DRAFT)
        self.assertTrue(result["has_draft"])
        statuses = {item["status"] for item in result["items"]}
        self.assertTrue(statuses & {"addressed", "partial"})
        self.assertIn("missing", statuses)

    def test_missing_draft_is_not_addressed(self):
        result = coverage.check(self.reqs, "")
        self.assertEqual(result["counts"]["addressed"], 0)
        self.assertEqual(result["counts"]["missing"], len(self.reqs))

    def test_contradiction_detected(self):
        reqs = [{
            "id": "R1", "text": "Use at least 5 load steps between 0 and 20 kg.",
            "kind": "constraint", "status": "clear", "evidence": "Use at least 5 load steps.",
            "source_ref": "brief:L1",
        }]
        result = coverage.check(reqs, "We used 3 load steps in the test.")
        self.assertEqual(result["items"][0]["status"], "contradicted")

    def test_diff_compares_versions(self):
        before = coverage.check(self.reqs, "A short draft.")
        after = coverage.check(self.reqs, DRAFT)
        delta = coverage.diff(before["items"], after["items"])
        self.assertIsInstance(delta["improved"], list)


class PlanTests(unittest.TestCase):
    def setUp(self):
        result = extract.extract(BRIEF, today=date(2026, 10, 26))
        cov = coverage.check(result["requirements"], DRAFT)
        self.report = {
            "requirements": result["requirements"],
            "coverage": cov,
            "contradictions": result["contradictions"],
            "ambiguities": result["ambiguities"],
        }

    def test_exactly_three_actions(self):
        plan = plan_mod.deterministic_plan(self.report)
        self.assertEqual(len(plan["actions"]), 3)
        self.assertTrue(plan["risk"])
        for action in plan["actions"]:
            self.assertLessEqual(len(action["text"].split()), 15)
            self.assertGreater(action["minutes"], 0)

    def test_integrity_filter_rejects_solution(self):
        self.assertIsNone(plan_mod.sanitize_action("Here is the solution: sigma = M*y/I."))
        self.assertIsNone(plan_mod.sanitize_action("```python\ndef solve(): pass\n```"))
        self.assertIsNotNone(plan_mod.sanitize_action("Check the units before the final table."))

    def test_merge_keeps_three_and_filters(self):
        model_plan = {
            "actions": [
                "Resolve the conflicting span values first.",
                "Here is the answer: use a 300 mm section.",
                "Check the unit table before submitting.",
            ],
            "risk": "The draft claims safety without a calculation.",
        }
        merged = plan_mod.merge(model_plan, self.report)
        self.assertEqual(len(merged["actions"]), 3)
        for action in merged["actions"]:
            self.assertNotIn("here is the answer", action["text"].lower())

    def test_fallback_is_three(self):
        plan = plan_mod.deterministic_plan({})
        self.assertEqual(len(plan["actions"]), 3)


class CompanionTests(unittest.TestCase):
    def test_deadline_states(self):
        now = datetime(2026, 10, 26, 12, 0).astimezone()
        self.assertEqual(companion.deadline_state("", now)["state"], "none")
        self.assertEqual(companion.deadline_state("2026-10-25T12:00", now)["state"], "overdue")
        self.assertEqual(companion.deadline_state("2026-10-26T20:00", now)["state"], "today")
        self.assertEqual(companion.deadline_state("2026-10-27T20:00", now)["state"], "soon")
        self.assertEqual(companion.deadline_state("2026-10-30T20:00", now)["state"], "week")
        self.assertEqual(companion.deadline_state("2026-11-20T20:00", now)["state"], "later")

    def test_pattern_needs_two_submissions(self):
        item = {"id": "R1", "text": "Acceptance criterion", "kind": "acceptance", "status": "missing"}
        one = [{"coverage": [item]}]
        two = [{"coverage": [item]}, {"coverage": [item]}]
        self.assertEqual(companion.derive_patterns(one, persist=False), [])
        patterns = companion.derive_patterns(two, persist=False)
        self.assertTrue(any("Acceptance" in pattern["text"] for pattern in patterns))

    def test_rolling_summary(self):
        messages = [
            {"role": "user", "content": "How should I handle the deadline and the testing plan?"},
            {"role": "coach", "content": "Start with the gap."},
        ] * 6
        summary = companion.rolling_summary(messages, window=8)
        self.assertIn("Earlier", summary)

    def test_fallback_chat_is_grounded(self):
        assignment = {"title": "Lab", "deadline": "2026-10-27T17:00", "status": "active"}
        report = {"coverage": {"gaps": [{"id": "R1", "status": "missing"}]}}
        audit = {"report": report, "plan": {"actions": [{"text": "Fix the gap first.", "minutes": 20}]}}
        reply = companion.fallback_reply(assignment, audit, "What should I do first?")
        self.assertIn("Fix the gap first", reply)

    def test_guard_blocks_false_ready(self):
        report = {"coverage": {
            "counts": {"addressed": 1, "partial": 1, "missing": 1, "contradicted": 0},
            "gaps": [{"id": "R2", "status": "partial"}, {"id": "R3", "status": "missing"}],
        }}
        audit = {"report": report}
        guarded = companion.guard_reply("You're almost there! Ready to submit.", audit, "Am I ready?")
        self.assertIn("Not yet", guarded)
        self.assertIn("R3", guarded)
        ready = {"report": {"coverage": {"counts": {"addressed": 3}, "gaps": []}}}
        self.assertEqual(
            companion.guard_reply("You're ready to submit.", ready, "Am I ready?"),
            "You're ready to submit.",
        )

    def test_coverage_question_uses_report(self):
        report = {"coverage": {
            "counts": {"addressed": 0, "partial": 1, "missing": 2, "contradicted": 0},
            "total": 3, "has_draft": True,
            "items": [
                {"id": "R1", "status": "missing", "text": "Submit as PDF by 17:00."},
                {"id": "R2", "status": "missing", "text": "Include a load path diagram."},
                {"id": "R3", "status": "partial", "text": "Keep the recommendation under 150 words."},
            ],
            "gaps": [
                {"id": "R1", "status": "missing", "text": "Submit as PDF by 17:00."},
                {"id": "R2", "status": "missing", "text": "Include a load path diagram."},
                {"id": "R3", "status": "partial", "text": "Keep the recommendation under 150 words."},
            ],
        }}
        audit = {"report": report}
        reply = companion.coverage_reply(audit)
        self.assertIn("Assignment checklist", reply)
        self.assertIn("not a grade", reply)
        self.assertIn("R1 missing", reply)
        self.assertIn("R3 partial", reply)
        self.assertLess(reply.index("R1 missing"), reply.index("R3 partial"))
        self.assertTrue(companion.is_coverage_question("What is missing from the draft?"))
        self.assertFalse(companion.is_coverage_question("What should I do first?"))

    def test_readiness_reply_uses_coverage(self):
        report = {"coverage": {
            "counts": {"addressed": 2, "partial": 0, "missing": 1, "contradicted": 0},
            "total": 3, "has_draft": True,
            "items": [
                {"id": "R1", "status": "addressed"},
                {"id": "R2", "status": "addressed"},
                {"id": "R4", "status": "missing", "text": "Submit as PDF."},
            ],
            "gaps": [{"id": "R4", "status": "missing", "text": "Submit as PDF."}],
        }}
        audit = {"report": report}
        reply = companion.readiness_reply(audit)
        self.assertIn("Not yet", reply)
        self.assertIn("R4 missing", reply)
        self.assertTrue(companion.is_readiness_question("Am I ready to submit?"))
        clear = {"report": {"coverage": {
            "counts": {"addressed": 1}, "total": 1, "has_draft": True,
            "items": [{"id": "R1", "status": "addressed"}], "gaps": [],
        }}}
        self.assertIn("Checklist clear", companion.readiness_reply(clear))

    def test_plan_reply_uses_saved_actions(self):
        plan = {"actions": [
            {"text": "Add the PDF format line.", "minutes": 10},
            {"text": "Add the load-path diagram.", "minutes": 20},
            {"text": "List two failure modes.", "minutes": 15},
        ], "risk": "The PDF rule is missing."}
        reply = companion.plan_reply({"plan": plan})
        self.assertIn("1) Add the PDF format line.", reply)
        self.assertIn("2) Add the load-path diagram.", reply)
        self.assertIn("Risk: The PDF rule is missing.", reply)
        self.assertTrue(companion.is_plan_question("What should I do first?"))
        self.assertTrue(companion.is_plan_question("What is next?"))
        self.assertFalse(companion.is_plan_question("Can you write the conclusion?"))

    def test_deadline_reply_uses_state_and_gaps(self):
        assignment = {"deadline": "2026-10-29T17:00"}
        item = {"id": "R4", "status": "missing", "text": "Submit as PDF."}
        audit = {"report": {"coverage": {
            "counts": {"missing": 1, "addressed": 1}, "total": 2, "has_draft": True,
            "items": [item, {"id": "R1", "status": "addressed"}], "gaps": [item],
        }}}
        reply = companion.deadline_reply(assignment, audit)
        self.assertIn("Due", reply)
        self.assertIn("R4", reply)
        self.assertIn("assignment checklist", reply)
        self.assertTrue(companion.is_deadline_question("What is the deadline risk?"))
        self.assertFalse(companion.is_deadline_question("Can you write the method?"))

    def test_guard_allows_advice_about_unresolved_items(self):
        item = {"id": "R4", "status": "missing", "text": "Submit as PDF."}
        coverage = {
            "counts": {"missing": 1}, "total": 1, "has_draft": True,
            "items": [item], "gaps": [item],
        }
        self.assertFalse(companion.claims_unresolved_covered(
            "Ensure all requirements are addressed before submitting.", coverage
        ))
        self.assertTrue(companion.claims_unresolved_covered(
            "All requirements are covered, so you can submit.", coverage
        ))

    def test_work_request_gets_planning_only_refusal(self):
        audit = {"report": {"coverage": {
            "counts": {"missing": 1, "partial": 1}, "total": 2, "has_draft": True,
            "items": [
                {"id": "R1", "status": "partial", "text": "Keep it under 150 words."},
                {"id": "R4", "status": "missing", "text": "Submit as PDF."},
            ],
            "gaps": [
                {"id": "R1", "status": "partial", "text": "Keep it under 150 words."},
                {"id": "R4", "status": "missing", "text": "Submit as PDF."},
            ],
        }}}
        self.assertTrue(companion.is_work_request(
            "Can you write the recommendation section for me?"
        ))
        self.assertTrue(companion.is_work_request("Write the conclusion for me."))
        self.assertTrue(companion.is_work_request("Please solve the problem."))
        self.assertTrue(companion.is_work_request(
            "What would a strong recommendation section look like?"
        ))
        self.assertTrue(companion.is_work_request("Show me an example recommendation."))
        self.assertFalse(companion.is_work_request("What does a good memo include?"))
        self.assertFalse(companion.is_work_request("What should I do first?"))
        self.assertFalse(companion.is_work_request("How should I structure the recommendation?"))
        reply = companion.work_refusal_reply(audit)
        self.assertIn("can't write", reply)
        self.assertIn("Start with R4", reply)
        self.assertNotIn("Start with R1", reply)
        self.assertIn("not an engineering correctness check", reply)

    def test_ghostwritten_model_reply_is_replaced(self):
        sample = (
            "Certainly! Here's a sample recommendation section you can use:\n\n"
            "**Recommendation**\n\nBased on the analysis, the maximum bending moment "
            "is within the steel section capacity limits. The failure modes identified are:\n"
            "- Failure Mode 1: Overloading of the deck.\n"
            "- Mitigation: Reinforce the deck with additional supports.\n\n"
            "This recommendation should be included at the end of your memo."
        )
        audit = {"report": {"coverage": {
            "counts": {"missing": 1}, "total": 2, "has_draft": True,
            "items": [{"id": "R4", "status": "missing"}],
            "gaps": [{"id": "R4", "status": "missing"}],
        }}}
        self.assertTrue(companion.looks_ghostwritten(sample))
        reply, filtered = companion.sanitize_model_reply(sample, audit)
        self.assertTrue(filtered)
        self.assertIn("can't write", reply)
        self.assertNotIn("Failure Mode 1", reply)
        self.assertNotIn("**Recommendation**", reply)
        inline_heading = (
            "A strong recommendation section should summarize your findings clearly. "
            "For example:\n\n---\n\n**Recommendation:**\n\nBased on the load path diagram "
            "and member force table, use steel sections with an allowable bending stress of "
            "120 kPa. This ensures the maximum bending moment does not exceed capacity and "
            "mitigates potential failure modes such as excessive deflection or collapse.\n\n"
            "This keeps your answer within the brief word limit."
        )
        self.assertTrue(companion.looks_ghostwritten(inline_heading))
        self.assertTrue(companion.sanitize_model_reply(inline_heading, audit)[1])
        self.assertFalse(companion.looks_ghostwritten(
            "## Next steps\n\nStart with R4, then rerun the preflight and re-check R5."
        ))

    def test_integrity_filter_keeps_refusal_and_planning(self):
        refusal = (
            "I'm sorry, but I cannot complete this assignment for you. My role is to help "
            "with planning, prioritisation, and deadline triage."
        )
        planning = (
            "Start with R4: add the PDF format line, then rerun the preflight. "
            "After that, address R5's failure modes and mitigations."
        )
        self.assertFalse(companion.looks_ghostwritten(refusal))
        self.assertFalse(companion.looks_ghostwritten(planning))
        self.assertEqual(companion.sanitize_model_reply(planning, {}), (planning, False))

    def test_guard_replaces_false_coverage_claim(self):
        item = {"id": "R1", "status": "missing", "text": "Submit as PDF by 17:00."}
        report = {"coverage": {
            "counts": {"addressed": 1, "partial": 0, "missing": 1, "contradicted": 0},
            "total": 2, "has_draft": True, "items": [item], "gaps": [item],
        }}
        audit = {"report": report}
        guarded = companion.guard_reply(
            "R1 is covered because your draft mentions a PDF.", audit, "Is R1 covered?"
        )
        self.assertIn("Assignment checklist", guarded)
        self.assertIn("R1 missing", guarded)
        self.assertEqual(
            companion.guard_reply("R1 is not covered yet.", audit, "What is the status?"),
            "R1 is not covered yet.",
        )

    def test_context_marks_coverage_status(self):
        assignment = {"title": "Bridge", "course_code": "ENG 204", "deadline": "", "brief_text": "BRIEF"}
        report = {
            "requirements": [{"id": "R1", "text": "Submit as PDF."}],
            "coverage": {
                "counts": {"missing": 1},
                "items": [{"id": "R1", "status": "missing", "text": "Submit as PDF."}],
                "gaps": [{"id": "R1", "status": "missing", "text": "Submit as PDF."}],
            },
        }
        context = companion.context_block(assignment, {"report": report}, [], [])
        self.assertIn("- R1 [missing] Submit as PDF.", context)
        self.assertIn("RULE: the statuses above are final", context)


class StorageTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        clear_tables()

    def test_assignment_audit_memory_persist(self):
        course = db.create_course("ENG 204", "Engineering Design")
        assignment = db.create_assignment(
            "Bridge", BRIEF, course_id=course["id"], deadline="2026-10-30T17:00", draft_text=DRAFT,
        )
        report = pipeline.build_report(assignment, BRIEF, DRAFT, source="brief.md")
        plan = plan_mod.deterministic_plan(report)
        audit = db.save_audit(assignment["id"], report, plan, False, "test", 12)
        db.add_message(assignment["id"], "coach", "Start with the missing unit.")
        db.add_memory("pattern", "Units were missing twice.", course_id=course["id"])

        loaded = db.get_assignment(assignment["id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded["audits"]), 1)
        self.assertEqual(loaded["audits"][0]["id"], audit["id"])
        self.assertEqual(len(loaded["messages"]), 1)
        self.assertEqual(len(db.list_memory(course["id"], assignment["id"])), 1)
        self.assertEqual(db.stats()["assignments"], 1)

    def test_submission_versions(self):
        assignment = db.create_assignment("A", BRIEF)
        db.save_submission(assignment["id"], DRAFT, [], [], {})
        second = db.save_submission(assignment["id"], DRAFT, [], [], {})
        self.assertEqual(second["version"], 2)
        loaded = db.get_assignment(assignment["id"])
        self.assertEqual(loaded["status"], "submitted")
        self.assertEqual(len(loaded["submissions"]), 2)

    def test_update_ignores_none(self):
        assignment = db.create_assignment("A", BRIEF)
        updated = db.update_assignment(assignment["id"], title=None, deadline="2026-11-01T17:00")
        self.assertEqual(updated["title"], "A")
        self.assertEqual(updated["deadline"], "2026-11-01T17:00")

    def test_export_markdown(self):
        assignment = db.create_assignment("Bridge", BRIEF, draft_text=DRAFT)
        report = pipeline.build_report(assignment, BRIEF, DRAFT)
        plan = plan_mod.deterministic_plan(report)
        audit = db.save_audit(assignment["id"], report, plan, False, "test", 5)
        loaded = db.get_assignment(assignment["id"])
        text = export.markdown(loaded, audit, [], [])
        self.assertIn("# Preflight report", text)
        self.assertIn("Requirements", text)
        self.assertIn("Integrity note", text)
        payload = json.loads(export.as_json(loaded, audit))
        self.assertTrue(payload["local_only"])
        self.assertEqual(payload["assignment"]["title"], "Bridge")

    def test_privacy_defaults(self):
        db.init_db()
        self.assertEqual(db.get_setting("web_enabled", "false"), "false")
        self.assertEqual(db.list_network(), [])

    def test_preflight_adopts_detected_deadline(self):
        assignment = db.create_assignment("Bridge", BRIEF)
        expected = extract.extract(BRIEF)["deadline"]
        result = pipeline.run(assignment, BRIEF, DRAFT, deadline=None, use_model=False)
        self.assertTrue(expected)
        self.assertEqual(result["assignment"]["deadline"], expected)
        self.assertEqual(result["report"]["deadline"], expected)


if __name__ == "__main__":
    unittest.main()

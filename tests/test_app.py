"""End-to-end app flow with Claude mocked (needs Playwright's Chromium for PDF rendering).

Profile → job → analyse → build (Strict) → quality gate → recruiter view → save application →
My applications → open it again. Runs in a temporary folder via RESUME_TAILOR_HOME, so your
own settings, profile and applications are never touched.

Run: python -m unittest tests.test_app
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from streamlit.testing.v1 import AppTest  # noqa: E402

import engines  # noqa: E402
import main  # noqa: E402
import store  # noqa: E402
from fixtures import SCENARIOS, analysis, claude_like_resume, master_text  # noqa: E402

SC = SCENARIOS[5]  # accountant: proves nothing is software-specific
JOB = ("Accountant at Acme Ltd. Required: Xero, month-end close, accounts payable, VAT returns. "
       "Nice to have: SAP, IFRS 16. Hybrid, London.")
CALLS: list[str] = []


def fake_run(config, system, content, schema=None):
    schema = schema or main.RESPONSE_SCHEMA
    if schema is main.ANALYSIS_SCHEMA:
        CALLS.append("analysis")
        return analysis(SC), "mock"
    if schema is main.RECRUITER_SCHEMA:
        CALLS.append("recruiter")
        return main.normalize({"first_impression": "Clear accountant profile", "fit": "promising",
                               "ratings": [{"criterion": "Relevance to the job", "rating": 4, "comment": "Good"}],
                               "improvements": ["Name the number of entities in the summary"],
                               "interview_questions": ["How do you run a month-end close?"]},
                              main.RECRUITER_SCHEMA), "mock"
    revision = "<ats_report>" in content
    CALLS.append("revision" if revision else "tailor")
    resume = claude_like_resume(SC, fabricate=not revision)  # first draft overclaims; the revision is honest
    return main.normalize({"resume": resume, "job_keywords": SC[3] + SC[4] + ["Accountant"]},
                          main.RESPONSE_SCHEMA), "mock"


class TestAppFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["RESUME_TAILOR_HOME"] = self.tmp.name
        Path(self.tmp.name, "settings.json").write_text(json.dumps({
            "engine": "Claude Code — your Claude plan (automatic)", "remember": True, "content_mode": "Strict"}))
        self._run, self._find = engines.run, engines.find_claude_cli
        engines.run, engines.find_claude_cli = fake_run, (lambda: "claude-mock")
        CALLS.clear()

    def tearDown(self):
        engines.run, engines.find_claude_cli = self._run, self._find
        os.environ.pop("RESUME_TAILOR_HOME", None)
        self.tmp.cleanup()

    def button(self, at, label):
        return next(b for b in at.button if b.label.startswith(label))

    def text(self, at):
        return " ".join(str(getattr(e, "value", "")) for kind in (at.markdown, at.caption, at.error, at.warning,
                                                                   at.success, at.info) for e in kind)

    def test_full_flow(self):
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=240)
        at.run()
        at.text_area(key="paste_master").input(master_text(SC))
        at.text_area(key="job_text").input(JOB)
        at.text_input(key="job_title").input("Accountant")
        at.radio(key="job_type").set_value("Local / Onsite")
        at.run()
        self.button(at, "🔍 Analyse the job").click().run()
        self.assertIn("analysis", CALLS)
        self.assertIn("Needs your confirmation", self.text(at))
        self.button(at, "✨ Build my resume").click().run()
        self.assertIn("tailor", CALLS)
        self.assertIn("revision", CALLS, "serious gate issues must be sent to Claude in auto-improve")

        versions = list(at.session_state["versions"])
        first, last = versions[0]["result"], versions[-1]["result"]
        skills = " ".join(last["resume"]["skills"]).lower()
        for s in SC[4]:
            self.assertNotIn(s.lower(), " ".join(first["resume"]["skills"]).lower(),
                             "Strict clean-up must drop unsupported skills from the first draft")
            self.assertNotIn(s.lower(), skills)
        self.assertTrue(any("doesn't show" in f for f in first["fixes"]))
        self.assertNotIn("🛑 Quality gate", self.text(at))
        self.assertTrue(Path(self.tmp.name, "data", "master_resume.txt").is_file())

        # A hand edit that overclaims trips the gate and blocks downloads until reviewed
        edited = copy.deepcopy(last["resume"])
        edited["experience"][0]["bullets"].append("Led the SAP S/4HANA migration for 3 entities")
        at.text_area(key=f"edit_{len(versions)}").input(json.dumps(edited))
        self.button(at, "Apply edits").click().run()
        self.assertIn("Quality gate", self.text(at))
        self.assertIn("Review the quality gate's serious issues", self.text(at))
        at.checkbox(key=f"gate_ack_{len(at.session_state['versions'])}").check().run()
        self.assertNotIn("Review the quality gate's serious issues", self.text(at))

        # Recruiter view
        self.button(at, "👔 Review as a recruiter").click().run()
        self.assertIn("recruiter", CALLS)
        self.assertIn("Clear accountant profile", self.text(at))

        # Save application, then find it under My applications and reopen it
        at.text_input(key="app_company").input("Acme Ltd")
        at.run()
        self.button(at, "💾 Save application").click().run()
        apps = store.list_applications(Path(self.tmp.name, "data"))
        self.assertEqual(len(apps), 1)
        saved = store.load_application(Path(self.tmp.name, "data"), apps[0]["id"])
        self.assertEqual(saved["company"], "Acme Ltd")
        self.assertEqual(saved["master"], master_text(SC))
        self.assertTrue(store.file_bytes(Path(self.tmp.name, "data"), apps[0]["id"], "resume.pdf").startswith(b"%PDF"))
        self.assertTrue(any(q["severity"] == "serious" for q in saved["quality"]))

        at.radio(key="view").set_value("💼 My applications").run()
        self.assertIn("Acme Ltd", " ".join(e.value for e in at.subheader))
        self.button(at, "✏️ Open in the editor").click().run()
        self.assertEqual(at.session_state["view"], "✨ Tailor a resume")
        self.assertEqual(len(at.session_state["versions"]), len(saved["versions"]))
        self.assertFalse(at.exception, at.exception)


if __name__ == "__main__":
    unittest.main()

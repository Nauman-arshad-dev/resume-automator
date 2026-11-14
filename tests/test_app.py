"""End-to-end app flow with Claude mocked (needs Playwright's Chromium for PDF rendering).

Profile → job → Create → questions → Build → check & download → recruiter view → save application →
My applications → open it again, plus the acceptance tests that need the whole app: the location line
across three jobs, saved answers, New job and Reset. Runs in a temporary folder via RESUME_TAILOR_HOME,
so your own settings, profile and applications are never touched.

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
import test_guard  # noqa: E402
from fixtures import SCENARIOS, analysis, claude_like_resume, master_text  # noqa: E402

SC = SCENARIOS[5]  # accountant: proves nothing is software-specific
JOB = ("Accountant at Acme Ltd. Required: Xero, month-end close, accounts payable, VAT returns. "
       "Nice to have: SAP, IFRS 16. Hybrid, London.")
CALLS: list[str] = []
QUESTIONS: list[dict] = []  # job-specific profile questions the mocked analysis returns
SENT: dict[str, str] = {}   # the last request content per call type
LEAKY: list[bool] = []      # when set, "Claude" returns test_guard's leaky resume for a software profile


def fake_run(config, system, content, schema=None):
    schema = schema or main.RESPONSE_SCHEMA
    if schema is main.ANALYSIS_SCHEMA:
        CALLS.append("analysis")
        if LEAKY:
            return {**test_guard.G2I_ANALYSIS, "skills": [
                {"skill": s, "group": "A", "category": "essential"} for s in ("React", "Node.js", "Java")]}, "mock"
        return {**analysis(SC), "profile_questions": list(QUESTIONS)}, "mock"
    if schema is main.PROFILE_SCHEMA:
        CALLS.append("profile")
        SENT["profile"] = content
        merged = "CONTACT\nJane Doe\n\n" + content.split("<current_profile>")[1].split("</current_profile>")[0]
        return main.normalize({"profile": merged + "\n- Closed the books for 99 entities",
                               "changes": ["Merged the LinkedIn export"], "questions": []},
                              main.PROFILE_SCHEMA), "mock"
    if schema is main.RECRUITER_SCHEMA:
        CALLS.append("recruiter")
        return main.normalize({"first_impression": "Clear accountant profile", "fit": "promising",
                               "ratings": [{"criterion": "Relevance to the job", "rating": 4, "comment": "Good"}],
                               "improvements": ["Name the number of entities in the summary"],
                               "interview_questions": ["How do you run a month-end close?"]},
                              main.RECRUITER_SCHEMA), "mock"
    revision = "<ats_report>" in content
    CALLS.append("revision" if revision else "tailor")
    SENT["revision" if revision else "tailor"] = content
    if LEAKY:
        return main.normalize({"resume": test_guard.claude_like(), "job_keywords": ["React", "Node.js"]},
                              main.RESPONSE_SCHEMA), "mock"
    resume = claude_like_resume(SC, fabricate=not revision)  # first draft overclaims; the revision is honest
    return main.normalize({"resume": resume, "job_keywords": SC[3] + SC[4] + ["Accountant"]},
                          main.RESPONSE_SCHEMA), "mock"


class AppCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name, "data")
        os.environ["RESUME_TAILOR_HOME"] = self.tmp.name
        Path(self.tmp.name, "settings.json").write_text(json.dumps({
            "engine": "Claude Code — your Claude plan (automatic)", "remember": True}))
        self._run, self._find = engines.run, engines.find_claude_cli
        engines.run, engines.find_claude_cli = fake_run, (lambda: "claude-mock")
        CALLS.clear()
        QUESTIONS.clear()
        SENT.clear()
        LEAKY.clear()

    def tearDown(self):
        engines.run, engines.find_claude_cli = self._run, self._find
        os.environ.pop("RESUME_TAILOR_HOME", None)
        self.tmp.cleanup()

    def app(self) -> AppTest:
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=240)
        at.run()
        return at

    def button(self, at, label):
        return next(b for b in at.button if b.label.startswith(label))

    def pdf_download_disabled(self, at) -> bool:
        button = next(d for d in at.get("download_button") if d.proto.label.startswith("⬇️ Download PDF"))
        return button.proto.disabled

    def text(self, at):
        return " ".join(str(getattr(e, "value", "")) for kind in (at.markdown, at.caption, at.error, at.warning,
                                                                   at.success, at.info) for e in kind)

    def profile(self, at, text):
        at.text_area(key="paste_master").input(text).run()

    def job(self, at, text, job_type, **fields):
        at.text_area(key="job_text").input(text)
        at.radio(key="job_type").set_value(job_type)
        at.run()
        for key, value in fields.items():
            at.text_input(key=key).input(value)
        at.run()

    def resume(self, at) -> dict:
        return at.session_state["versions"][-1]["result"]["resume"]


class TestAppFlow(AppCase):
    def test_full_flow(self):
        at = self.app()
        self.profile(at, master_text(SC))
        self.job(at, JOB, "Local / Onsite")
        self.button(at, "✨ Create my resume").click().run()
        self.assertIn("analysis", CALLS)
        self.assertNotIn("tailor", CALLS, "skills to confirm: the build waits for the answers")
        self.assertIn("Do you have these?", self.text(at))
        self.assertNotIn("Mark unanswered as Basic", [b.label for b in at.button], "no bulk confirmation")

        self.button(at, "✨ Build my resume").click().run()
        self.assertIn("tailor", CALLS)
        self.assertIn("revision", CALLS, "flagged items are sent to Claude first")
        versions = list(at.session_state["versions"])
        for s in SC[4]:
            self.assertNotIn(s.lower(), " ".join(versions[0]["result"]["resume"]["skills"]).lower())
        self.assertIn("Ready to send", self.text(at))
        self.assertTrue(Path(self.data, "master_resume.txt").is_file())

        # A hand edit that overclaims is flagged and blocks the download until it is removed or kept
        edited = copy.deepcopy(self.resume(at))
        edited["experience"][0]["bullets"].append("Led the SAP S/4HANA migration for 3 entities")
        at.text_area(key=f"edit_{len(versions)}").input(json.dumps(edited))
        self.button(at, "Apply edits").click().run()
        self.assertIn("to check before you download", self.text(at))
        self.assertTrue(self.pdf_download_disabled(at))
        self.button(at, "🗑️ Remove it").click().run()
        self.assertNotIn("SAP S/4HANA", str(self.resume(at)))
        while "to check before you download" in self.text(at):
            self.button(at, "✅ It's true, keep it").click().run()
        self.assertFalse(self.pdf_download_disabled(at))

        # Recruiter view
        self.button(at, "👔 Review as a recruiter").click().run()
        self.assertIn("recruiter", CALLS)
        self.assertIn("Clear accountant profile", self.text(at))

        # Save application, then find it under My applications and reopen it
        at.text_input(key="app_company").input("Acme Ltd")
        at.run()
        self.button(at, "💾 Save application").click().run()
        apps = store.list_applications(self.data)
        self.assertEqual(len(apps), 1)
        saved = store.load_application(self.data, apps[0]["id"])
        self.assertEqual(saved["company"], "Acme Ltd")
        self.assertTrue(store.file_bytes(self.data, apps[0]["id"], "resume.pdf").startswith(b"%PDF"))
        at.radio(key="view").set_value("💼 My applications").run()
        self.assertIn("Acme Ltd", " ".join(e.value for e in at.subheader))
        self.button(at, "✏️ Open in the editor").click().run()
        self.assertEqual(len(at.session_state["versions"]), len(saved["versions"]))

        # A browser refresh restores the job; New job clears it
        again = self.app()
        self.assertEqual(again.session_state["job_text"], JOB)
        self.assertIn("Welcome back", self.text(again))
        self.button(again, "🆕 New job").click().run()
        self.assertFalse(Path(self.data, "workspace.json").exists())
        self.assertFalse(at.exception, at.exception)
        self.assertFalse(again.exception, again.exception)


class TestAcceptance(AppCase):
    def test_1_remote_then_new_zealand_then_remote(self):
        LEAKY.append(True)
        at = self.app()
        self.profile(at, test_guard.MASTER)  # carries an old "Open to relocation to Auckland..." line
        lines = []
        for text, job_type, fields in (
                ("Remote Full Stack Developer at Acme. React, Node.js, Java.", "Remote", {"timezone": "UTC+5"}),
                ("Full Stack Developer in Auckland, New Zealand. React, Node.js, Java.", "Relocation",
                 {"location": "Auckland, New Zealand"}),
                ("Remote Java Developer at Beta. React, Node.js, Java.", "Remote", {"timezone": "UTC+5"})):
            self.job(at, text, job_type, **fields)
            self.button(at, "✨ Create my resume").click().run()  # nothing to ask: one click builds it
            lines.append(self.resume(at)["availability"])
        self.assertEqual(lines, ["Open to remote work (UTC+5)",
                                 "Open to relocation to Auckland, New Zealand (AEWV sponsorship)",
                                 "Open to remote work (UTC+5)"])
        final = json.dumps(self.resume(at))
        for word in ("AEWV", "Auckland", "Wellington"):
            self.assertNotIn(word, final)
        self.assertNotIn("AEWV", SENT["tailor"], "Claude never sees the old location line")
        self.assertNotIn("<current_resume>", SENT["tailor"], "a new build never starts from an earlier resume")
        self.assertIn("github.com/sam-rivera", self.resume(at)["contact"])
        self.assertFalse(at.exception, at.exception)

    def test_answers_are_saved_sent_and_kept_by_new_job(self):
        QUESTIONS.append({"question": "Baker & Co: how many entities did you close each month?",
                          "requirement": "month-end close", "example": "3 entities in 5 days"})
        at = self.app()
        self.profile(at, master_text(SC))
        self.job(at, JOB, "Local / Onsite")
        self.button(at, "✨ Create my resume").click().run()
        at.text_input(key="pq_0").input("3 entities, closed in 5 working days").run()
        self.button(at, "✨ Build my resume").click().run()
        self.assertIn("3 entities, closed in 5 working days", SENT["tailor"])
        self.assertIn("<candidate_answers>", SENT["tailor"])
        self.assertEqual(store.load_answers(self.data)[0]["answer"], "3 entities, closed in 5 working days")
        self.button(at, "🆕 New job").click().run()
        self.assertEqual(len(store.load_answers(self.data)), 1, "New job keeps saved answers")
        self.assertTrue(Path(self.data, "master_resume.txt").is_file(), "New job keeps the profile")
        self.assertNotIn("Ready to send", self.text(at), "New job clears the previous resume")
        self.assertFalse(at.exception, at.exception)

    def test_8_reset_clears_everything_new_job_keeps_profile_answers_and_skills(self):
        at = self.app()
        self.profile(at, master_text(SC))
        self.job(at, JOB, "Local / Onsite")
        self.button(at, "✨ Create my resume").click().run()
        at.radio(key="confirm_sap").set_value("Basic").run()
        store.add_answers(self.data, [("q", "I closed 3 entities")])
        self.button(at, "🆕 New job").click().run()
        for name in ("master_resume.txt", "skill_confirmations.json", "profile_answers.json"):
            self.assertTrue(Path(self.data, name).is_file(), f"New job keeps {name}")
        self.assertFalse(at.text_area(key="job_text").value)
        self.button(at, "Yes, reset everything").click().run()
        for name in ("master_resume.txt", "skill_confirmations.json", "profile_answers.json", "workspace.json"):
            self.assertFalse(Path(self.data, name).exists(), f"Reset removes {name}")
        self.assertFalse(store.profile_history(self.data))
        self.assertFalse(at.exception, at.exception)

    def test_profile_fixes_old_location_line_and_missing_link(self):
        at = self.app()
        self.profile(at, test_guard.MASTER.replace(" | github.com/sam-rivera", ""))
        self.assertIn("line from an earlier tailored resume", self.text(at))
        self.button(at, "🧹 Remove it from my profile").click().run()
        saved = Path(self.data, "master_resume.txt").read_text(encoding="utf-8")
        self.assertNotIn("AEWV", saved)
        at.text_input(key="new_link").input("github.com/sam-rivera").run()
        self.button(at, "➕ Add link").click().run()
        saved = Path(self.data, "master_resume.txt").read_text(encoding="utf-8")
        self.assertIn("sam.rivera@example.com | linkedin.com/in/sam-rivera | github.com/sam-rivera", saved)
        self.assertFalse(at.exception, at.exception)

    def test_profile_builder(self):
        at = self.app()
        self.profile(at, master_text(SC))
        self.button(at, "🪄 Merge into my profile").click().run()
        self.assertIn("profile", CALLS)
        self.assertIn("not in your files or answers", self.text(at), "a number Claude added must be flagged")
        profile_file = Path(self.data, "master_resume.txt")
        self.assertEqual(profile_file.read_text(encoding="utf-8"), master_text(SC), "nothing saved before review")
        self.button(at, "💾 Save this profile").click().run()
        self.assertTrue(profile_file.read_text(encoding="utf-8").startswith("CONTACT\nJane Doe"))
        self.assertTrue(store.profile_history(self.data), "the old profile is kept")
        self.assertFalse(at.exception, at.exception)


if __name__ == "__main__":
    unittest.main()

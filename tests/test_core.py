"""Business-logic tests (no Claude calls, no browser). Run: python -m unittest discover tests"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import evidence  # noqa: E402
import main  # noqa: E402
import match  # noqa: E402
import quality  # noqa: E402
import review  # noqa: E402
import store  # noqa: E402
from fixtures import SCENARIOS, analysis, claude_like_resume, master_text  # noqa: E402


def skill_names(resume: dict) -> list[str]:
    return [item for _, item in review.skill_items(resume)]


class TestEvidence(unittest.TestCase):
    def test_synonyms_and_spelling(self):
        self.assertTrue(evidence.mentions("Built apps in JS and React.js", "JavaScript"))
        self.assertTrue(evidence.mentions("Search engine optimisation (SEO) audits", "Search engine optimization"))
        self.assertTrue(evidence.mentions("Excel pivot tables", "Microsoft Excel"))
        self.assertTrue(evidence.mentions("Designed REST APIs", "RESTful APIs"))
        self.assertFalse(evidence.mentions("go-to-market plans", "Golang"))   # ambiguous short alias
        self.assertFalse(evidence.mentions("Scrum ceremonies", "Agile"))      # related is not the same

    def test_status_levels(self):
        master = "Accountant. Month-end close in Xero. Reconciled accounts payable."
        conf = {"sap": {"skill": "SAP", "level": "Basic"}, "ifrs": {"skill": "IFRS", "level": "Learning"},
                "oracle": {"skill": "Oracle", "level": "No"}}
        self.assertEqual(evidence.status("Xero", master, conf), "verified")
        self.assertEqual(evidence.status("AP", master, conf), "verified")
        self.assertEqual(evidence.status("SAP", master, conf), "confirmed")
        self.assertEqual(evidence.status("IFRS", master, conf), "unsupported")
        self.assertEqual(evidence.status("Oracle", master, conf), "excluded")
        self.assertEqual(evidence.status("NetSuite", master, conf), "needs confirmation")
        self.assertTrue(evidence.allowed("SAP", master, conf, "Strict"))
        self.assertFalse(evidence.allowed("NetSuite", master, conf, "Strict"))
        self.assertTrue(evidence.allowed("NetSuite", master, conf, "Flexible"))
        self.assertFalse(evidence.allowed("Oracle", master, conf, "Flexible"))

    def test_rewording_is_not_fabrication(self):
        """Found in live runs: truthful rephrasings must pass, missing tools and figures must not."""
        master = ("Built Power BI dashboards. Wrote Terraform modules for VPCs. Managed budgets up to $2M. "
                  "Weekly stakeholder updates. Grew organic traffic 120%. 60 tickets a day in Zendesk, 96% CSAT.")
        for term in ("VPC", "Dashboard development", "Budget management", "stakeholder reporting", "High CSAT"):
            self.assertIn(evidence.status(term, master, {}), evidence.BACKED, term)
        for term in ("Kubernetes", "IFRS 16", "budget management up to $5M", "Next.js", "Paid and organic growth"):
            self.assertEqual(evidence.status(term, master, {}), "needs confirmation", term)


class TestScenarios(unittest.TestCase):
    """The same rules across ten professions and levels, with no hard-coded assumptions."""

    def test_strict_never_keeps_unsupported_skills(self):
        for sc in SCENARIOS:
            with self.subTest(job=sc[0]):
                master, have, missing = master_text(sc), sc[3], sc[4]
                fixed, notes = review.fix_resume(claude_like_resume(sc), have + missing, {}, master, "Strict")
                names = [n.lower() for n in skill_names(fixed)]
                for s in missing:
                    self.assertNotIn(s.lower(), names, f"{s} should be removed in Strict mode")
                for s in have:
                    self.assertIn(s.lower(), names, f"{s} is in the profile and must stay")
                self.assertTrue(any("doesn't show" in n for n in notes))

    def test_confirmed_skill_is_kept_in_strict(self):
        sc = SCENARIOS[5]  # accountant
        conf = {sc[4][0].lower(): {"skill": sc[4][0], "level": "Basic"}}
        fixed, _ = review.fix_resume(claude_like_resume(sc), sc[3] + sc[4], conf, master_text(sc), "Strict")
        names = [n.lower() for n in skill_names(fixed)]
        self.assertIn(sc[4][0].lower(), names)
        self.assertNotIn(sc[4][1].lower(), names)

    def test_flexible_adds_everything_except_no(self):
        for sc in SCENARIOS:
            with self.subTest(job=sc[0]):
                conf = {sc[4][1].lower(): {"skill": sc[4][1], "level": "No"}}
                base = claude_like_resume(sc, fabricate=False)
                fixed, _ = review.fix_resume(base, sc[3] + sc[4], conf, master_text(sc), "Flexible")
                names = [n.lower() for n in skill_names(fixed)]
                self.assertIn(sc[4][0].lower(), names)
                self.assertNotIn(sc[4][1].lower(), names)

    def test_quality_gate_flags_fabrication(self):
        for sc in SCENARIOS:
            with self.subTest(job=sc[0]):
                master = master_text(sc)
                resume = claude_like_resume(sc)
                issues = quality.gate(resume, master, {}, analysis(sc), {"content_mode": "Strict"}, sc[3] + sc[4])
                serious = [i for i in issues if i.severity == quality.SERIOUS]
                self.assertTrue(any(i.area == "Evidence" for i in serious), [i.message for i in issues])
                # the honest version passes the evidence checks
                honest = claude_like_resume(sc, fabricate=False)
                issues = quality.gate(honest, master, {}, analysis(sc), {"content_mode": "Strict"}, sc[3] + sc[4])
                self.assertFalse([i for i in issues if i.severity == quality.SERIOUS], [i.message for i in issues])

    def test_flexible_mode_downgrades_evidence_to_warnings(self):
        sc = SCENARIOS[0]
        issues = quality.gate(claude_like_resume(sc), master_text(sc), {}, analysis(sc),
                              {"content_mode": "Flexible"}, sc[3] + sc[4])
        self.assertTrue(any(i.area == "Evidence" and i.severity == quality.WARNING for i in issues))
        self.assertFalse(any(i.area == "Evidence" and i.severity == quality.SERIOUS for i in issues))

    def test_job_match_weights_and_synonyms(self):
        for sc in SCENARIOS:
            with self.subTest(job=sc[0]):
                honest = claude_like_resume(sc, fabricate=False)
                m = match.job_match(honest, analysis(sc), sc[0], master=master_text(sc))
                self.assertEqual(m.counts["essential"], (len(sc[3]), len(sc[3])))
                self.assertEqual(m.counts["preferred"][0], 0)
                self.assertTrue(m.title_match)
                self.assertGreaterEqual(m.overall, 70)
                self.assertEqual({r.status for r in m.missing}, {"needs confirmation"})
        # a requirement written differently in the job is still found
        sc = SCENARIOS[2]
        a = analysis(sc)
        a["skills"][0]["skill"] = "Structured Query Language"
        a["skills"][0]["synonyms"] = ["SQL"]
        self.assertTrue(match.job_match(claude_like_resume(sc, False), a, sc[0]).requirements[0].found)

    def test_bullet_trace(self):
        sc = SCENARIOS[1]
        rows = evidence.trace_bullets(claude_like_resume(sc), master_text(sc), sc[3] + sc[4])
        fabricated = [r for r in rows if r["unsupported_terms"]]
        self.assertEqual(len(fabricated), 1)
        self.assertIn(sc[4][0], fabricated[0]["unsupported_terms"])
        real = [r for r in rows if r["where"] != "Summary" and not r["unsupported_terms"]]
        self.assertTrue(all(r["status"] == "verified" for r in real), real)


class TestQualityGate(unittest.TestCase):
    def setUp(self):
        self.sc = SCENARIOS[0]
        self.master = master_text(self.sc)

    def gate(self, resume):
        return quality.gate(resume, self.master, {}, None, {"content_mode": "Strict"}, self.sc[3])

    def test_dates(self):
        r = claude_like_resume(self.sc, False)
        r["experience"][0]["dates"] = "Mar 2022 – Jan 2020"
        self.assertTrue(any("ends before it starts" in i.message for i in self.gate(r)))
        r["experience"][0]["dates"] = "Jan 2099 – Present"
        self.assertTrue(any("future" in i.message for i in self.gate(r)))

    def test_contact_duplicates_stuffing(self):
        r = claude_like_resume(self.sc, False)
        r["contact"] = ["London"]
        r["experience"][0]["bullets"].append(r["experience"][0]["bullets"][0] + " daily")
        r["summary"] = "Java Java engineer. " + "Java expert. " * 5
        msgs = [i.message for i in self.gate(r)]
        self.assertTrue(any("No email" in m for m in msgs))
        self.assertTrue(any("nearly the same" in m for m in msgs))
        self.assertTrue(any("keyword stuffing" in m for m in msgs))
        self.assertTrue(any("Repeated word" in m for m in msgs))

    def test_invented_number_is_serious(self):
        r = claude_like_resume(self.sc, False)
        r["experience"][0]["bullets"][0] = "Built Java 17 and Spring Boot payment services for 9M users"
        serious = [i.message for i in self.gate(r) if i.severity == quality.SERIOUS]
        self.assertTrue(any("9" in m and "Numbers" in m for m in serious))


class TestUserReportedFixes(unittest.TestCase):
    """From a real VentureDev 'Senior Full Stack Developer' build."""

    def setUp(self):
        self.resume = main.normalize({
            "name": "N", "headline": "Senior Full-Stack Developer | Node.js", "contact": ["a@b.com"],
            "summary": "Senior Full-Stack Developer with 5+ years.",
            "skills": ["Backend: Node.js", "Tools & Technologies: Backend API development, Performance optimization"],
            "experience": [{"title": "Freelance Full-Stack Developer", "company": "Fiverr", "location": "",
                            "dates": "Aug 2021 – Present", "bullets": [
                                "Launched three production SaaS applications (Tracio, Social Cleaner Pro) covering "
                                "real-time monitoring, subscription billing and analytics."]}],
            "projects": [{"name": "Tracio", "bullets": [
                "Launched as a production SaaS with real-time monitoring, subscription billing and analytics."]}],
        }, main.RESUME_SCHEMA)

    def test_follows_the_job_titles_spelling(self):
        fixed, _ = review.fix_resume(self.resume, [], {}, job_title="Senior Full Stack Developer")
        self.assertTrue(fixed["headline"].startswith("Senior Full Stack Developer"))
        fixed, _ = review.fix_resume(self.resume, [], {}, job_title="Full-Stack Engineer")
        self.assertIn("Full-Stack", fixed["headline"])

    def test_practices_are_not_tools(self):
        fixed, _ = review.fix_resume(self.resume, [], {})
        self.assertIn("Core Competencies: Backend API development, Performance optimization", fixed["skills"])

    def test_project_bullet_copying_a_job_bullet(self):
        self.assertTrue(quality.duplicate_bullets(self.resume))


class TestPromptsAndRequests(unittest.TestCase):
    def test_mode_selects_prompt(self):
        system, content = main.tailor_request("profile", "job", "Accountant", {"content_mode": "Strict",
                                                                              "page_limit": 1})
        self.assertIn("STRICT EVIDENCE", system)
        self.assertIn("page_limit: 1", content)
        self.assertIn("use only what the profile shows", content)
        system, content = main.tailor_request("profile", "job", "", {"content_mode": "Flexible"})
        self.assertIn("FLEXIBLE KEYWORD INJECTION", system)
        self.assertIn("inject every job skill", content)
        self.assertIn("STRICT", main.tailor_request("p", "j")[0])  # default is Strict
        self.assertIn("only where the evidence rules allow", main.revision_request("p", "j", {}, "r")[0])

    def test_prompts_are_profession_neutral(self):
        for text in (main.ANALYSIS_PROMPT, main.tailor_prompt("Strict"), main.RECRUITER_PROMPT):
            low = text.lower()
            self.assertTrue("account" in low or "finance" in low)
            self.assertIn("market", low)
            self.assertIn("support", low)
            self.assertIn("profession", low)

    def test_clean_title(self):
        self.assertEqual(main.clean_title("Buchhalter (m/w/d)"), "Buchhalter")
        self.assertEqual(main.clean_title("Nurse (all genders)"), "Nurse")

    def test_upload_validation(self):
        with self.assertRaises(main.TailorError):
            main.read_upload("cv.exe", b"MZ")
        with self.assertRaises(main.TailorError):
            main.read_upload("cv.pdf", b"not a pdf")
        with self.assertRaises(main.TailorError):
            main.read_upload("cv.txt", b"x" * (main.MAX_UPLOAD_BYTES + 1))
        with self.assertRaises(main.TailorError):
            main.read_upload("cv.json", b"{bad json")
        self.assertEqual(main.read_upload("cv.txt", "﻿Jane Doe".encode("utf-8")), "Jane Doe")

    def test_recruiter_parse(self):
        r = main.parse_recruiter('{"first_impression": "Clear", "fit": "strong", "improvements": ["x"]}')
        self.assertEqual(r["fit"], "strong")
        self.assertEqual(r["ratings"], [])


class TestStore(unittest.TestCase):
    def test_roundtrip_and_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            app_id = store.save_application(base, {"company": "Acme", "job_title": "Accountant",
                                                   "master": "PROFILE v1", "resume": {"name": "A"}},
                                            pdf=b"%PDF-1.4 test", docx=b"PK docx")
            first = store.load_application(base, app_id)
            self.assertEqual(first["status"], "Draft")
            store.update_application(base, app_id, status="Applied", notes="Called back")
            again = store.load_application(base, app_id)
            self.assertEqual((again["status"], again["notes"]), ("Applied", "Called back"))
            self.assertEqual(again["created"], first["created"])
            self.assertEqual(again["master"], "PROFILE v1")  # the snapshot stays as it was
            self.assertEqual(store.file_bytes(base, app_id, "resume.pdf"), b"%PDF-1.4 test")
            second = store.save_application(base, {"company": "Beta", "job_title": "Analyst"})
            self.assertEqual({a["id"] for a in store.list_applications(base)}, {app_id, second})
            with self.assertRaises(store.StoreError):
                store.load_application(base, "../../settings")
            with self.assertRaises(store.StoreError):
                store.file_bytes(base, app_id, "../application.json")
            store.delete_application(base, second)
            self.assertEqual(len(store.list_applications(base)), 1)

    def test_profile_history_and_additions(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(store.HISTORY_KEEP + 3):
                store.backup_profile(Path(tmp), f"v{i}")
            self.assertEqual(len(store.profile_history(Path(tmp))), store.HISTORY_KEEP)
        text = "Jane\n\nEXPERIENCE\nAnalyst · Acme · 2020 – Present\n\nEDUCATION\nBSc\n"
        added = store.add_to_profile(text, "Experience", "Intern · Beta · 2019")
        self.assertLess(added.index("Intern · Beta"), added.index("EDUCATION"))
        self.assertIn("Analyst · Acme", added)
        added = store.add_to_profile(text, "Certifications", "CPA, 2023")
        self.assertTrue(added.rstrip().endswith("CERTIFICATIONS\nCPA, 2023"))


if __name__ == "__main__":
    unittest.main()

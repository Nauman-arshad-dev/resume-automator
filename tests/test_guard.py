"""Acceptance tests for the source guard (guard.py): every resume comes only from the master profile,
the saved answers and the confirmed skills.

Run: python -m unittest tests.test_guard
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import guard  # noqa: E402
import main  # noqa: E402
import review  # noqa: E402

# A profile like a real one: it still carries the location line of an earlier tailored resume.
MASTER = """SAM RIVERA
Full Stack Software Engineer | Java, Spring Boot | REST APIs
Lahore, Pakistan | +92 300 0000000 | sam.rivera@example.com | linkedin.com/in/sam-rivera | github.com/sam-rivera | Open to relocation to Auckland/Wellington (AEWV sponsorship)
PROFESSIONAL SUMMARY
Full-stack software engineer with 5 years of experience in Java, Spring Boot, React and Node.js.
TECHNICAL SKILLS
Programming Languages: Java, SQL, JavaScript, TypeScript, Python
Java and Spring: Spring Boot, Hibernate, RESTful APIs, JWT Authentication
Other Frameworks: Angular, React, Next.js, Node.js
Databases: PostgreSQL, MySQL, MongoDB
Cloud and DevOps: Amazon Web Services (AWS): EC2, Lambda, S3; Docker; Kubernetes; Git
AI and LLM: OpenAI API, Claude API, Embeddings, RAG
PROFESSIONAL EXPERIENCE
Solution Architect (Full-Stack)
Syntivis AG | Remote, Germany | Contract | Feb 2024 – Present
Built the frontend in Next.js and React and the backend REST APIs in Node.js, supporting multi-team project and deliverable tracking.
Designed the PostgreSQL database schema and backend architecture.
Wrote unit tests for core backend modules, reviewed pull requests and kept architecture standards consistent across the team.
Freelance Full Stack Developer
Fiverr and Upwork | Remote | Aug 2021 – Present
Built Tracio, Social Cleaner Pro and UnfollowScout, SaaS apps for real-time monitoring and analytics.
Integrated OpenAI and Claude APIs into client applications for chatbots and data extraction.
Full Stack Developer (Java / Spring Boot)
ConvSync | Remote, Pakistan | Apr 2023 – Jan 2024
Engineered RESTful APIs in Java Spring Boot with JWT authentication, cutting API latency by 40%.
Optimized SQL queries for reporting dashboards.
EDUCATION
BS Software Engineering, Riphah International University, 2017 – 2022
"""
ANSWERS = [
    {"question": "At Syntivis AG, how big was the team?", "answer": "At Syntivis AG I worked solo, directly with the CEO"},
    {"question": "Which of your SaaS products had the most users?",
     "answer": "I built Tracio, Social Cleaner Pro, UnfollowScout for clients and don't know their user numbers"},
]
REMOTE = {"job_type": "Remote", "timezone": "UTC+5"}
NZ = {"job_type": "Relocation", "location": "Auckland, New Zealand", "visa": ""}
G2I_JOB = ("Remote Full Stack Developer at G2i Talent Pool. Our community works on real projects, from evals and "
           "RL environments to training data for frontier models. Software, data, AI/ML, infrastructure, security, "
           "design, QA. You'll complete a technical assessment.")
G2I_ANALYSIS = {"job_title": "Remote Full Stack Developer", "job_location": "", "skills": [
    {"skill": s, "group": g, "category": "essential"} for s, g in
    [("Full stack development", "A"), ("Evals", "unconfirmed"), ("RL environments", "unconfirmed"),
     ("Training data for frontier models", "B"), ("Technical assessment", "A"), ("QA", "A"), ("Security", "A"),
     ("React", "A"), ("Node.js", "A")]]}


def claude_like(**changes) -> dict:
    """What a model returned in real tests: leaked location, job-ad skills, filler numbers, team claims."""
    resume = main.normalize({
        "name": "SAM RIVERA",
        "headline": "Full-Stack Software Engineer (F/M/D) | AI/ML & LLM Integration | React, Node.js, Java, Spring "
                    "Boot, PostgreSQL, Docker",
        "contact": ["sam.rivera@example.com", "+92 300 0000000", "Lahore, Pakistan", "linkedin.com/in/sam-rivera",
                    "Open to relocation to Auckland/Wellington (AEWV sponsorship)"],
        "availability": "Open to relocation to Auckland/Wellington (AEWV sponsorship)",
        "summary": "Remote Full Stack Developer with 5+ years building React and Java Spring Boot platforms, and has "
                   "launched production SaaS products. Open to relocation to Auckland.",
        "skills": ["Engineering: Full stack development, QA, Security, Infrastructure",
                   "AI/ML: Evals, RL environments, Training data for frontier models, LLM Integration",
                   "Languages & Frameworks: Java, Spring Boot, TypeScript, React, Node.js, Python",
                   "Cloud: AWS, Docker, Vue.js (via Angular and React experience)",
                   "Technical assessment"],
        "experience": [
            {"title": "Solution Architect (Full-Stack)", "company": "Syntivis AG", "location": "Remote, Germany",
             "dates": "Feb 2024 – Present", "bullets": [
                 "Built the frontend in Next.js and React and the backend REST APIs in Node.js, supporting multi-team "
                 "project and deliverable tracking.",
                 "Designed the PostgreSQL schema and ingested 1 large dataset into the database.",
                 "Wrote unit tests (QA) for core backend modules, reviewed pull requests and kept architecture "
                 "standards consistent across the team."]},
            {"title": "Freelance Full Stack Developer", "company": "Fiverr and Upwork", "location": "Remote",
             "dates": "Aug 2021 – Present", "bullets": [
                 "Launched 3 production SaaS applications (Tracio, Social Cleaner Pro, UnfollowScout) with 5,000 "
                 "users covering real-time monitoring and analytics.",
                 "Integrated 2 LLM APIs (OpenAI, Claude) into client applications for chatbots."]},
            {"title": "Full Stack Developer (Java / Spring Boot)", "company": "ConvSync", "location": "Remote",
             "dates": "Apr 2023 – Jan 2024", "bullets": [
                 "Engineered RESTful APIs in Java Spring Boot with JWT authentication (application security), "
                 "cutting API latency by 40%, across 4 AWS services.",
                 "Optimized SQL queries for reporting dashboards."]}],
        "education": [{"degree": "BS Software Engineering", "school": "Riphah International University",
                       "dates": "2017 – 2022", "details": []}],
    }, main.RESUME_SCHEMA)
    resume.update(changes)
    return resume


def run(resume=None, settings=REMOTE, answers=ANSWERS, confirmations=None, analysis=G2I_ANALYSIS, job=G2I_JOB):
    analysis = guard.split_requirements(analysis) if analysis else None
    out, notes = guard.enforce(resume or claude_like(), MASTER, answers, confirmations or {}, settings, analysis, job)
    return out, notes


def section(resume: dict, company: str) -> str:
    job = next(j for j in resume["experience"] if j["company"] == company)
    return " ".join([job["title"], job["location"]] + job["bullets"]).lower()


class TestAcceptance(unittest.TestCase):
    def test_1_location_line_comes_from_this_job_only(self):
        remote1, _ = run(settings=REMOTE)
        nz, _ = run(settings=NZ)
        remote2, _ = run(settings=REMOTE)
        self.assertEqual(nz["availability"], "Open to relocation to Auckland, New Zealand (AEWV sponsorship)")
        for r in (remote1, remote2):
            self.assertEqual(r["availability"], "Open to remote work (UTC+5)")
            text = str(r)
            for word in ("AEWV", "Auckland", "Wellington"):
                self.assertNotIn(word, text)
        local, _ = run(settings={"job_type": "Local / Onsite"})
        self.assertEqual(local["availability"], "")
        # The profile sent to Claude never carries the old line
        cleaned, removed = guard.clean_master(MASTER)
        self.assertNotIn("AEWV", cleaned)
        self.assertIn("github.com/sam-rivera", cleaned)
        self.assertEqual(removed, ["Open to relocation to Auckland/Wellington (AEWV sponsorship)"])

    def test_2_job_ad_text_never_becomes_skills_and_real_skills_stay(self):
        r, _ = run()
        skills = " | ".join(r["skills"])
        for bad in ("Evals", "RL environments", "Training data for frontier models", "Technical assessment",
                    "Full stack development", "Infrastructure", "QA", "Security", "Vue.js"):
            self.assertNotIn(bad.lower(), skills.lower().replace("full stack developer", ""), bad)
        for good in ("SQL", "PostgreSQL", "Next.js", "OpenAI API", "Java", "React"):
            self.assertIn(good, skills, good)
        analysis = guard.split_requirements(G2I_ANALYSIS)
        self.assertEqual({s["skill"] for s in analysis["skills"]}, {"React", "Node.js"})
        self.assertIn("Evals", analysis["not_skills"])

    def test_3_solo_answer_removes_team_claims_at_that_employer(self):
        r, _ = run()
        text = section(r, "Syntivis AG")
        for word in ("team", "multi-team", "pull request"):
            self.assertNotIn(word, text)
        self.assertIn("unit tests for core backend modules", text, "the real part of the bullet stays")

    def test_4_client_products_are_built_for_clients_never_launched(self):
        r, _ = run()
        text = section(r, "Fiverr and Upwork")
        self.assertNotIn("launched", text)
        self.assertRegex(text, r"built .*tracio.*for clients")
        self.assertNotIn("5,000 users", text, "you said you don't know the user numbers")
        self.assertNotIn("launched", r["summary"].lower())

    def test_5_no_number_that_is_not_in_the_sources(self):
        r, _ = run()
        sources = guard.sources_text(guard.clean_master(MASTER)[0], ANSWERS)
        self.assertEqual(guard.unbacked_numbers(r, sources, review.experience_years(r["experience"])), [])
        text = str(r)
        for filler in ("1 large dataset", "2 LLM APIs", "3 production", "4 AWS services"):
            self.assertNotIn(filler, text)
        self.assertIn("a large dataset", text)
        self.assertIn("40%", text, "a real number from the profile stays")
        # An invented metric (not a small count) is flagged for review, not silently kept
        invented = claude_like()
        invented["experience"][2]["bullets"][1] = "Optimized SQL queries for reporting dashboards, saving 35% of time."
        r, _ = run(invented)
        items = guard.checks(r, MASTER, ANSWERS, {}, REMOTE, None, [])
        self.assertTrue(any(i.check == 5 and "35%" in i.message for i in items))

    def test_6_gender_tag_never_in_the_headline(self):
        r, _ = run()
        self.assertNotIn("(F/M/D)", r["headline"])
        self.assertNotIn("AI/ML", r["headline"], "not in the profile")
        self.assertLessEqual(len(r["headline"]), guard.HEADLINE_MAX)
        self.assertTrue(r["headline"].startswith("Full-Stack Software Engineer |"), r["headline"])
        self.assertEqual(main.clean_title("Engineer (F/M/D)"), "Engineer")
        self.assertNotIn("(F/M/D)", run(claude_like(headline="Engineer (F/M/D) | Java"))[0]["headline"])

    def test_7_every_profile_link_is_in_the_contact_line(self):
        r, _ = run()
        self.assertIn("github.com/sam-rivera", r["contact"])
        self.assertIn("linkedin.com/in/sam-rivera", r["contact"])
        self.assertFalse(any("AEWV" in c for c in r["contact"]))

    def test_brackets_and_basic_answers(self):
        r, _ = run(confirmations={"kubernetes": {"skill": "Kubernetes", "level": "Strong"},
                                  "graphql": {"skill": "GraphQL", "level": "Basic"}},
                   resume=claude_like(skills=["Backend: Java, GraphQL, Kubernetes"]))
        text = str(r)
        for bracket in ("(QA)", "(application security)", "(via Angular"):
            self.assertNotIn(bracket, text)
        self.assertIn("(OpenAI, Claude)", text, "a bracket listing profile items stays")
        self.assertEqual(r["skills"][-1], "Working knowledge: GraphQL", "Basic answers go under Working knowledge")

    def test_a_clean_resume_passes_every_check(self):
        r, _ = run()
        serious = [i for i in guard.checks(r, MASTER, ANSWERS, {}, REMOTE, guard.split_requirements(G2I_ANALYSIS),
                                           ["React", "Node.js"]) if i.severity == guard.SERIOUS]
        self.assertEqual(serious, [], [(i.check, i.message, i.text) for i in serious])

    def test_checks_catch_the_raw_output_and_fixes_remove_it(self):
        raw = claude_like()
        items = guard.checks(raw, MASTER, ANSWERS, {}, REMOTE, None, ["React"])
        found = {i.check for i in items if i.severity == guard.SERIOUS}
        self.assertTrue({1, 2, 4, 5, 7} <= found, found)
        skill_item = next(i for i in items if i.check == 2 and i.value == "Evals")
        self.assertNotIn("Evals", " ".join(guard.apply_fix(raw, skill_item)["skills"]))
        number_item = next(i for i in items if i.check == 5 and i.value == "2")
        fixed = guard.apply_fix(raw, number_item)
        self.assertIn("Integrated LLM APIs (OpenAI, Claude)", str(fixed))

    def test_new_words_marks_only_what_is_not_in_the_sources(self):
        marks = dict(guard.new_words("Designed the PostgreSQL schema using GraphQL", MASTER))
        self.assertFalse(marks["PostgreSQL"])
        self.assertTrue(marks["GraphQL"])


if __name__ == "__main__":
    unittest.main()

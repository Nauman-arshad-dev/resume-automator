"""Ten candidate/job scenarios across professions and levels, for deterministic tests (no Claude calls)."""

from __future__ import annotations

import main

# name, job title, seniority, the candidate's real roles, skills they HAVE (in the profile),
# skills the job asks for that they DON'T have, a synonym pair (job term, profile term)
SCENARIOS = [
    ("Java Backend Engineer", "Senior", [("Backend Engineer", "Finexa", "Jan 2019 – Present",
     ["Built Java 17 and Spring Boot payment services handling 2M transactions a day",
      "Designed PostgreSQL schemas and Kafka consumers for settlement reporting",
      "Cut API latency by 40% by adding Redis caching"])],
     ["Java", "Spring Boot", "PostgreSQL", "Kafka", "Redis"], ["Kubernetes", "gRPC"], ("Postgres", "PostgreSQL")),
    ("React Frontend Developer", "Mid-level", [("Frontend Developer", "Shoply", "Mar 2021 – Present",
     ["Built React and TypeScript storefront pages used by 300k monthly visitors",
      "Wrote Jest and Testing Library tests, raising coverage to 85%",
      "Improved Lighthouse performance score from 62 to 91"])],
     ["React", "TypeScript", "Jest"], ["Next.js", "GraphQL"], ("JS", "JavaScript")),
    ("Data Analyst", "Mid-level", [("Data Analyst", "RetailCo", "Jun 2020 – Present",
     ["Built Power BI dashboards for 12 regional managers",
      "Wrote SQL queries in Snowflake to analyse weekly sales",
      "Automated Excel reporting with Python and pandas, saving 6 hours a week"])],
     ["SQL", "Power BI", "Python", "Excel"], ["Tableau", "dbt"], ("Microsoft Excel", "Excel")),
    ("DevOps Engineer", "Senior", [("DevOps Engineer", "CloudNine", "Feb 2018 – Present",
     ["Managed Kubernetes clusters on AWS for 40 microservices",
      "Wrote Terraform modules for VPCs, EKS and RDS",
      "Built GitHub Actions CI/CD pipelines that cut release time from 2 days to 2 hours"])],
     ["Kubernetes", "AWS", "Terraform", "CI/CD"], ["Azure", "Ansible"], ("Amazon Web Services", "AWS")),
    ("Digital Marketing Manager", "Lead / Manager", [("Marketing Lead", "GrowthLab", "Apr 2017 – Present",
     ["Managed a £40k monthly Google Ads budget at a 4.2x ROAS",
      "Grew organic traffic 120% through SEO content and technical fixes",
      "Led a team of 4 marketers and ran HubSpot email campaigns"])],
     ["Google Ads", "SEO", "HubSpot"], ["Marketo", "TikTok Ads"], ("Search engine optimization", "SEO")),
    ("Accountant", "Mid-level", [("Accountant", "Baker & Co", "Sep 2019 – Present",
     ["Prepared month-end close and journal entries for 3 entities in Xero",
      "Reconciled accounts payable and bank accounts every week",
      "Prepared VAT returns and supported the annual audit"])],
     ["Xero", "month-end close", "accounts payable", "VAT returns"], ["SAP", "IFRS 16"], ("AP", "accounts payable")),
    ("Project Manager", "Senior", [("Project Manager", "BuildRight", "Jan 2016 – Present",
     ["Delivered 14 construction IT projects on time using Agile and PRINCE2",
      "Managed budgets up to $2M and reported to the steering committee",
      "Ran risk registers and weekly stakeholder updates in Jira"])],
     ["PRINCE2", "Agile", "Jira", "budget management"], ["PMP", "MS Project"], ("Jira Software", "Jira")),
    ("Junior Software Developer", "Entry-level", [("Software Intern", "AppWorks", "Jun 2024 – Aug 2024",
     ["Built a Python Flask REST API for an internal booking tool",
      "Fixed 25 bugs in the Django admin and wrote unit tests"])],
     ["Python", "Flask", "Django", "Git"], ["Docker", "AWS"], ("RESTful APIs", "REST API")),
    ("Senior Engineering Manager", "Executive", [("Engineering Manager", "ScaleUp", "Mar 2015 – Present",
     ["Led 5 teams and 32 engineers building a B2B SaaS platform",
      "Hired 18 engineers and introduced quarterly OKRs",
      "Owned a $4M engineering budget and the platform roadmap"])],
     ["OKRs", "hiring", "B2B SaaS"], ["SOC 2", "Golang"], ("Software as a Service", "SaaS")),
    ("Customer Support Specialist", "Junior", [("Support Agent", "HelpDesk Inc", "Jan 2023 – Present",
     ["Resolved 60 customer tickets a day in Zendesk with 96% CSAT",
      "Wrote 30 help-centre articles that reduced repeat tickets",
      "Handled live chat and phone support for billing questions"])],
     ["Zendesk", "live chat", "CSAT"], ["Salesforce Service Cloud", "Intercom"], ("customer satisfaction", "CSAT")),
]


def master_text(sc) -> str:
    title, level, roles, have, _, _ = sc
    lines = ["Alex Morgan", "alex.morgan@example.com | +44 7700 900123 | London, UK", "", "EXPERIENCE"]
    for role, company, dates, bullets in roles:
        lines += [f"{role} · {company} · {dates}"] + [f"- {b}" for b in bullets]
    lines += ["", "SKILLS", ", ".join(have), "", "EDUCATION", "BSc Business, University of Leeds, 2012 – 2015",
              "", "LANGUAGES", "English (native)"]
    return "\n".join(lines)


def analysis(sc) -> dict:
    title, level, roles, have, missing, (syn_job, _) = sc
    skills = [{"skill": s, "category": "essential", "importance": "required", "weight": 5 if i == 0 else 3,
               "mentions": 2, "group": "A", "evidence": roles[0][1], "meaning": "", "synonyms": []}
              for i, s in enumerate(have)]
    skills += [{"skill": s, "category": "preferred", "importance": "nice to have", "weight": 2, "mentions": 1,
                "group": "unconfirmed", "evidence": "", "meaning": "", "synonyms": []} for s in missing]
    data = main.normalize({"job_title": title, "seniority_level": level, "skills": skills,
                           "title_variants": [], "company": "Acme"}, main.ANALYSIS_SCHEMA)
    return data


def claude_like_resume(sc, fabricate: bool = True) -> dict:
    """What a model might return: the real roles, plus (optionally) every missing job skill claimed."""
    title, level, roles, have, missing, _ = sc
    skills = list(have) + (list(missing) if fabricate else [])
    experience = [{"title": r, "company": c, "location": "London, UK", "dates": d, "bullets": list(b)}
                  for r, c, d, b in roles]
    if fabricate:
        experience[0]["bullets"].append(f"Used {missing[0]} daily to deliver team goals")
    return main.normalize({
        "name": "Alex Morgan", "headline": f"{title} | {have[0]} | {have[1]}",
        "contact": ["alex.morgan@example.com", "+44 7700 900123", "London, UK"],
        "summary": f"{title} with strong experience in {have[0]} and {have[1]}.",
        "skills": ["Core: " + ", ".join(skills)], "experience": experience,
        "education": [{"degree": "BSc Business", "school": "University of Leeds", "dates": "2012 – 2015",
                       "details": []}],
        "languages": ["English (native)"],
    }, main.RESUME_SCHEMA)

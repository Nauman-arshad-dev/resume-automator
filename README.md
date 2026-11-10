# Resume Automator — AI Resume Tailor & ATS Score Optimizer (Claude AI, Python)

**Build the strongest truthful resume for any job.** Resume Automator is a free, open-source Python app that uses Claude AI (Anthropic) to tailor your resume to a specific job description. It works for any profession and level, from a junior developer or customer support specialist to an accountant, marketing manager, nurse or engineering director. It analyses the job, compares it with your profile, writes a tailored resume and checks every claim against your profile. Then it reviews the resume the way an ATS and a recruiter would, and saves each application with the exact resume you sent.

Runs locally on your laptop. **No API key needed:** it uses your existing Claude subscription through Claude Code, with no copy-pasting. An Anthropic API key and a manual claude.ai mode are also supported.

> Scores in the app are its own estimates. Employers' ATS products score differently, and no tool can guarantee an interview. The app optimises for accuracy, relevance and readability first.

---

## Features

**Your profile, entered once**
- 📄 Import your resume from **PDF, Word, JSON, Markdown or text** (several files at once, e.g. your resume plus your LinkedIn PDF). It is **saved straight away** and becomes your master profile, the source of truth for every resume.
- 🪄 **Build up your profile automatically**: Claude merges your resume, LinkedIn export, older resumes and notes into one organised profile. It keeps every real fact and number, invents nothing, and asks what's missing (e.g. user counts, which CI/CD tool). Numbers it added or dropped are flagged, and nothing is saved until you review it.
- ✏️ Edit it any time, or **➕ add** a new role, project, certification or achievement under its section without retyping anything. Every change keeps the previous version in a **history** you can restore.
- A job-specific resume **never changes your profile**.

**Job analysis for any profession**
- 🔍 Claude reads the job and extracts its title, company, industry, profession, location, employment type and seniority level. It also pulls out every requirement: hard skills, tools, regulations, certifications, domain knowledge, soft skills, and the education and experience asked for.
- Each requirement gets a **category** (essential / preferred / domain / additional), a **1-5 weight** taken from the posting, and **synonyms**. Each one is also compared with your profile: ✅ shown, 🔁 equivalent, or ❓ needs your confirmation.

**Evidence-based tailoring**
- 🛡️ **Strict mode (default):** every statement must trace to your profile or to your own confirmation. **Strong** and **Basic** answers are included (Basic in Skills only, never as experience). **Learning**, **No** and unanswered requirements stay out and are reported as gaps.
- ⚡ **Flexible mode (opt-in):** every job keyword is listed except the ones you mark **No**, each linked to your closest real experience, for maximum keyword coverage. The quality gate warns about every claim without backing.
- The strategy adapts to the profession and seniority: section order, what the summary leads with, and 1 or 2 pages (Auto: 1 page for entry-level and junior jobs).
- 🧹 Automatic clean-up after every Claude answer:
  - skills your profile doesn't support are removed (Strict);
  - bracket hedges and the job ad's "(m/f/d)" are removed;
  - years of experience are capped at your real dated span;
  - duplicates and catch-all skill lines are removed;
  - dates are normalised;
  - bullets are trimmed to the page limit.

**Review before you send it**
- 📈 **Built-in ATS check** that reads the PDF text layer, as an ATS does: sections, bullets, contact details, length, pronouns, buzzwords, job title and keywords. 🚀 **Auto-improve** revises until your target score.
- 🎯 **Job match estimate**: essential and preferred requirements covered, weighted by importance and matched with synonyms ("JS" counts for "JavaScript", "SEO" for "search engine optimisation"). Exact-wording coverage is shown too.
- 🧾 **Evidence view**: every skill marked ✅ in profile / ☑️ you confirmed / ❓ not backed. Every bullet is shown next to the profile line it comes from, with a support percentage.
- 👔 **Recruiter view**: a simulated recruiter and hiring manager give:
  - first impression, positioning and a fit rating;
  - ratings on eight criteria;
  - selling points and weak areas;
  - missing evidence and possible rejection reasons;
  - truthful improvements, and a button to apply them;
  - interview questions and language issues.
- 🛑 **Quality gate**:
  - **Serious** issues block downloads until you review them: unsupported skills or bullet claims, skills you said No to, numbers not in your profile, dates that end before they start or start in the future, a missing email.
  - **Warnings** are advice: duplicate bullets, keyword stuffing, buzzwords, inconsistent punctuation, repeated words, too many pages.

**Applications**
- 💾 **Save application**: company, title, job description, a profile snapshot, every version, the reports, status, date, notes and the **exact PDF and Word files**.
- 💼 **My applications** lists them by status (Draft, Applied, Interviewing, Offer, Rejected, Withdrawn). You can re-download exactly what you sent, update status and notes, or reopen an application in the editor.

**Design and extras**
- 8 templates (4 ✅ ATS-friendly one-column, 4 🎨 design-focused), 10 ATS-safe fonts, Small / Normal / Large text, A4 / US Letter, PDF and Word downloads named Firstname-Lastname-Role.
- ✉️ A cover letter for the same job, written from the tailored resume.
- Job-type-aware header: Remote, Relocation with the usual visa for the country, or Local.
- Versions and undo. Paste any external checker's report (Jobscan, Resume Worded) to improve against it.
- 🔒 Private: runs on your machine, and nothing is uploaded except the text sent to Claude.

## How it works

1. **Profile**: upload your resume once (PDF, DOCX, JSON, MD or TXT; LinkedIn PDF too). It's saved at once; open 🪄 to let Claude organise it and answer its questions.
2. **The job**: paste the job description and title, and pick the job type (Remote / Relocation / Local). Optionally set the length and must-include / must-exclude items.
3. **🔍 Analyse the job**: see the requirements by importance, then answer Strong / Basic / Learning / No for the ones your profile doesn't show. Answers are remembered.
4. **✨ Build**: Claude tailors the resume from your profile. The clean-up and quality gate run on the result, and auto-improve sends any serious issue back to Claude first.
5. **Review**: the 📈 ATS check, 🧾 Review & evidence, and 👔 Recruiter view tabs. Resolve the quality gate.
6. **Export**: pick a design, download the PDF or Word file, and **💾 Save the application**. For the next job, click **🆕 New job**.

## Quick start

Requires **Python 3.10+** and one of:
- **[Claude Code](https://claude.com/claude-code)** signed in with your Claude Pro/Max plan (recommended; the VS Code extension's bundled copy is detected automatically),
- an [Anthropic API key](https://console.anthropic.com/settings/keys) with credits, or
- a [claude.ai](https://claude.ai) account for manual copy-paste.

```bash
git clone https://github.com/Nauman-arshad-dev/resume-automator.git
cd resume-automator

python -m venv .venv
# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
streamlit run app.py
```

Your browser opens at `http://localhost:8501`. Choose how to connect to Claude in the sidebar. **Claude Code** is selected automatically when it's installed.

### Command line (optional)

```bash
python main.py --resume my_resume.pdf --job job.txt --title "Senior Accountant" --job-type Remote --timezone UTC+5
python main.py --resume my_resume.docx --job job.txt --mode Flexible --pages 1
python main.py --help
```

The CLI uses Claude Code when it's installed (`--engine claude-code`), or `ANTHROPIC_API_KEY` (`--engine api`). It runs the same automatic clean-up (Strict mode by default) and writes `output/Firstname-Lastname-Role.pdf`, `.docx` and `.json`.

## Resume templates

| Template | Layout | Best for |
|---|---|---|
| **Classic** | Serif, centered header, ruled sections | Traditional industries, ✅ ATS-friendly |
| **Modern** | Bold name, accent headings, skills up top | Tech and most roles, ✅ ATS-friendly |
| **Executive** | Full-width dark header band | Senior and leadership roles, ✅ ATS-friendly |
| **Compact** | Dense, two-column skills and projects | Fitting a long career on one page, ✅ ATS-friendly |
| **Minimal** | Section labels in the left margin, airy | Design-conscious roles 🎨 |
| **Sidebar** | Colored sidebar with contact, skill tags and education | Emailing recruiters directly 🎨 |
| **Timeline** | Career history on a vertical timeline | Showing career progression 🎨 |
| **Bold** | Large name, colored skill tags, tinted side panel | Startups and creative roles 🎨 |

All templates use standard section headings, real text bullets and no letter-spacing, so ATS parsers read the PDF correctly. Designs are HTML/CSS files in [`templates/`](templates/). To add your own, copy one and register it in `TEMPLATES` in `render.py`.

PDFs are printed by a headless browser via Playwright. On Windows the built-in **Microsoft Edge** is used automatically. On macOS and Linux, Chrome is used if installed; otherwise run `python -m playwright install chromium` once.

## Project structure

```
resume-automator/
├── app.py               # Streamlit web UI (workflow, tabs, applications page)
├── prompts.py           # Every Claude prompt and JSON schema: analysis, tailoring (Strict/Flexible),
│                        #   revision, recruiter simulation, cover letter
├── main.py              # Request builders, reply parsing, uploads (PDF/DOCX/...), CLI
├── engines.py           # Calls Claude: Claude Code CLI (subscription) or Anthropic API, with timeouts
├── evidence.py          # Evidence checks: synonym-aware matching, claim status, bullet tracing
├── match.py             # Weighted job-match estimate (essential / preferred / domain / additional)
├── review.py            # Automatic clean-up and review checks
├── quality.py           # Quality gate before export (serious issues and warnings)
├── ats.py               # Built-in ATS checker (reads the PDF text layer)
├── store.py             # Saved applications and profile history (local files under data/)
├── render.py            # Templates → PDF (headless browser), Word (.docx), previews
├── templates/           # HTML/CSS resume designs (Jinja2)
├── tests/               # Unit tests (10 profession scenarios) and an end-to-end app test
├── master_resume.json   # Sample master resume
└── requirements.txt
```

## FAQ

**Is it free?**
Yes. The app is open source, and with Claude Code it runs on your existing Claude Pro/Max subscription (it counts toward your plan's usage limits) with no API credits. API mode costs a few cents per resume.

**Will it make up skills or experience?**
Not in Strict mode (the default). Every skill and bullet is checked against your profile and your own confirmations. Unsupported skills are removed automatically. Anything else, such as a bullet that names a tool you don't have or a number that isn't in your profile, blocks the download until you review it. Requirements you don't have are reported as gaps with a plan to close them. Flexible mode lists every job keyword (except No answers) for maximum keyword coverage, and the quality gate warns about each one.

**My score stopped going up. Why?**
What remains is usually requirements your profile doesn't show (Strict mode keeps them out on purpose; the report lists them as gaps), or checks only real facts can fix, like too few bullets with numbers. Add real experience and numbers to your profile with ➕ Add, or answer Strong / Basic for skills you really have.

**Where is my data stored?**
In this app's folder only: `settings.json` and `data/` (profile, its history, skill answers, saved applications and the job in progress, so a browser refresh or restart picks up where you left off), all git-ignored. Set the `RESUME_TAILOR_HOME` environment variable to keep them somewhere else. Nothing is sent anywhere except the text sent to Claude. The app is meant to run locally for one person; it has no user accounts, so don't host it publicly.

**How do I run the tests?**
`python -m unittest discover -s tests`. This runs the business-logic tests across 10 professions and the end-to-end app test with Claude mocked, all in a temporary folder.

**Does the built-in score match Jobscan / other checkers?**
It checks the same things most checkers do, but every checker weighs them differently. Use it to improve quickly, then confirm with your favorite checker. You can paste that checker's report into the Improve tab.

**Can I use it with Google Docs?**
Yes. Upload the `.docx` to Google Drive and open it with Google Docs, or set the save-to folder to your Google Drive for desktop folder.

## Tech stack

Python · [Anthropic Claude](https://docs.anthropic.com) (Claude Code headless mode / Messages API with structured JSON output) · [Streamlit](https://streamlit.io) · HTML/CSS + [Playwright](https://playwright.dev/python/) (PDF) · [python-docx](https://python-docx.readthedocs.io) (Word) · [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) (preview and ATS text extraction)

## Contributing

Issues and pull requests are welcome. Ideas: more templates, batch tailoring for several job postings, multiple candidate profiles.

## License

[MIT](LICENSE): free for personal and commercial use.

---

*Keywords: AI resume tailor, AI cover letter generator, ATS resume optimizer, ATS score checker, improve ATS score, free AI resume builder, professional resume templates, two-column resume template, resume to PDF, resume keyword optimizer, tailor resume to job description, ATS-friendly resume generator, Claude AI resume, Claude Code, Anthropic API Python, resume automation, job application automation, CV optimizer, Jobscan alternative, open source resume tool.*

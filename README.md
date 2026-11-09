# Resume Automator — AI Resume Tailor & ATS Score Optimizer (Claude AI, Python)

**Paste a job description, click once, get a tailored, ATS-optimized resume.** Resume Automator is a free, open-source Python app that uses Claude AI (Anthropic) to tailor your resume to any job. It checks the result with a built-in ATS checker and automatically improves it until it hits your target score, **without inventing fake experience**. Then you pick one of 8 professional templates and download a PDF or Word resume.

Runs locally on your laptop. **No API key needed:** it uses your existing Claude subscription through Claude Code, with no copy-pasting. An Anthropic API key and a manual claude.ai mode are also supported.

---

## Features

- ✅ **Job analysis first**: Claude reads the job and ranks every hard skill (required or nice to have, how often it's mentioned, what it means at this company), then sorts each one against your resume: ✅ in your resume, 🔁 transferable (e.g. GCP via your AWS), or ❓ not shown yet.
- ✅ **Flexible keyword injection**: every hard requirement and bonus skill from the job goes into Skills as a clean token (target 90%+ keyword coverage), and Claude rewrites your closest real bullet to surface the adjacent experience (Google Maps API -> geospatial data, legacy C -> C/C++). Mark **No** for any skill you never want listed; answers are remembered across jobs.
- ✅ **Automatic clean-up of fabrication tells** after every Claude answer: bracket hedges like "Vue.js (via Angular experience)" removed, the job ad's gender marker "(m/f/d)" kept out of your headline, years of experience capped at your real dated span, duplicate skills and catch-all "Working knowledge" lines removed, skills capped at about 20, "Full-Stack" and "Mon YYYY – Mon YYYY" dates normalised, and bullets trimmed until the PDF fits 2 pages.
- ✅ **Job-type aware header**: choose Remote (adds "Open to remote work (UTC+X)"), Relocation (adds "Open to relocation to <city> (<visa> sponsorship)") or Local. Picked fresh for every job, so a visa line never leaks into the next application.
- ✅ **Always built from your master resume**: every job starts from the master resume, never from the last tailored version. 🆕 New job keeps your resume and answers; ♻️ Reset forgets everything.
- ✅ **Built-in ATS checker**: reads the PDF text like an applicant tracking system and scores it on sections, bullets, length, pronouns, buzzwords, job title and job-keyword coverage, with a keyword-match meter (master resume → tailored).
- ✅ **🚀 Auto-improve**: check → revise → re-check, round after round, until your target score. Rounds that lower the score are discarded automatically.
- ✅ **ATS report (stays in the app)**: skill groups A-D, keywords covered and where, gaps and quick wins, interview prep talking points and a cover-letter angle. Only the resume is exported.
- ✅ **Review before export**: flags a skill you said No to, a duplicate employer, a dropped Education/Certifications/Languages section, inflated years, duplicate skills, a headline that wraps, a location line that doesn't match the job type, and numbers that aren't in your master resume.
- ✅ **Works with external checkers**: paste a Jobscan / Resume Worded report; missing keywords you have are added, the rest are asked about or listed as gaps.
- ✅ **8 professional resume templates**: Classic, Modern, Executive, Minimal, Compact, Sidebar, Timeline and Bold, each a different layout. You see all of them with your own resume.
- ✅ **Built on proven resume rules**: one-column text PDF, standard headings, contact details in the body, exact job-post spelling, acronym plus full term, top skills up front, Action verb + what + measurable result bullets, 3-6 bullets per role. Each one is a scored check, and the app has a 📋 tips panel.
- ✅ **Fonts and text size**: 10 ATS-safe fonts (Arial, Calibri, Segoe UI, Georgia, Cambria, Garamond, Palatino, Times New Roman and more) with Small / Normal / Large text. Small helps fit one page.
- ✅ **✉️ Cover letter generator**: one click writes a cover letter for the same job from your tailored resume (facts only, 220-330 words). Edit it in the app and download it as PDF or Word in the same design.
- ✅ **PDF and Word download**: named Firstname-Lastname-Role.pdf by default; choose the file name, accent color and A4 / US Letter page size.
- ✅ **Versions and undo**: every revision is a version with its score, and you can step back any time.
- ✅ **Remembers your resume**: upload it once and it's reused for every job. Stored locally only.
- ✅ **Works with any ATS checker**: optionally paste a Jobscan or Resume Worded report into the improve step.
- ✅ **Private**: runs on your machine, and nothing is uploaded except the text sent to Claude.

## How it works

1. **Your master resume**: upload it once (`.docx`, `.json`, `.md` or `.txt`): all roles, projects and skills. It's remembered.
2. **The job**: paste the job description, the exact job title, and pick the job type (Remote / Relocation / Local).
3. Click **🔍 Analyse the job**, then (optional) mark **No** for any job skill you never want listed.
4. Click **✨ Build my resume**. Claude writes it from your master resume, then auto-improves it to the target score.
5. Check the **📋 ATS report** tab (review items, gaps, interview prep), pick a **design** and click **⬇️ Download PDF**. Next application: **🆕 New job**.

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
python main.py --resume my_resume.docx --job job.txt --title "Senior Backend Engineer" --job-type Remote --timezone UTC+5
python main.py --help
```

The CLI uses Claude Code when it's installed (`--engine claude-code`), or `ANTHROPIC_API_KEY` (`--engine api`). It writes `output/<Name>_<date>.pdf`, `.docx` and `.json`.

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
├── app.py               # Streamlit web UI
├── main.py              # Prompts, resume schema, reply parsing, CLI
├── engines.py           # Calls Claude: Claude Code CLI (subscription) or Anthropic API
├── ats.py               # Built-in ATS checker (reads the PDF text layer)
├── review.py            # Review-before-export checks (unconfirmed skills, numbers, location line)
├── render.py            # Templates → PDF (headless browser), Word (.docx), previews
├── templates/           # HTML/CSS resume designs (Jinja2)
├── master_resume.json   # Sample master resume
└── requirements.txt
```

## FAQ

**Is it free?**
Yes. The app is open source, and with Claude Code it runs on your existing Claude Pro/Max subscription (it counts toward your plan's usage limits) with no API credits. API mode costs a few cents per resume.

**Will it make up skills or experience?**
It never invents employers, titles, dates, degrees, certifications or numbers, and the clean-up removes the tells reviewers spot (bracket hedges, inflated years, duplicates). It does list every job skill in Skills unless you mark it **No**, and links it to your closest real experience, so check the ATS report: skills in group C have no real anchor yet, and it gives a quick plan to back each one up before interviews.

**My ATS score stopped going up. Why?**
What remains is usually skills you answered Learning or No (left out on purpose; the ATS report gives a quick plan for each) or checks only real facts can fix, like too few bullets with numbers. Add real numbers to your master resume, or change an answer to Basic if you do know the skill a little.

**Does the built-in score match Jobscan / other checkers?**
It checks the same things most checkers do, but every checker weighs them differently. Use it to improve quickly, then confirm with your favorite checker. You can paste that checker's report into the Improve tab.

**Can I use it with Google Docs?**
Yes. Upload the `.docx` to Google Drive and open it with Google Docs, or set the save-to folder to your Google Drive for desktop folder.

## Tech stack

Python · [Anthropic Claude](https://docs.anthropic.com) (Claude Code headless mode / Messages API with structured JSON output) · [Streamlit](https://streamlit.io) · HTML/CSS + [Playwright](https://playwright.dev/python/) (PDF) · [python-docx](https://python-docx.readthedocs.io) (Word) · [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) (preview and ATS text extraction)

## Contributing

Issues and pull requests are welcome. Ideas: cover letter generation, more templates, batch tailoring for several job postings.

## License

[MIT](LICENSE): free for personal and commercial use.

---

*Keywords: AI resume tailor, AI cover letter generator, ATS resume optimizer, ATS score checker, improve ATS score, free AI resume builder, professional resume templates, two-column resume template, resume to PDF, resume keyword optimizer, tailor resume to job description, ATS-friendly resume generator, Claude AI resume, Claude Code, Anthropic API Python, resume automation, job application automation, CV optimizer, Jobscan alternative, open source resume tool.*

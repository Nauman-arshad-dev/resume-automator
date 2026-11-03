# Resume Automator — AI Resume Tailor & ATS Score Optimizer (Claude AI, Python)

**Paste a job description, click once, get a tailored, ATS-optimized resume.** Resume Automator is a free, open-source Python app that uses Claude AI (Anthropic) to tailor your resume to any job. It checks the result with a built-in ATS checker and automatically improves it until it hits your target score, **without inventing fake experience**. Then you pick one of 8 professional templates and download a PDF or Word resume.

Runs locally on your laptop. **No API key needed:** it uses your existing Claude subscription through Claude Code, with no copy-pasting. An Anthropic API key and a manual claude.ai mode are also supported.

---

## Features

- ✅ **One-click tailoring**: rewrites your summary, skills and bullets for the job, using only facts from your resume.
- ✅ **Built-in ATS checker**: reads the PDF text like an applicant tracking system and scores it on sections, bullets, length, pronouns, buzzwords, job title and job-keyword coverage.
- ✅ **🚀 Auto-improve**: check → revise → re-check, round after round, until your target score. Rounds that lower the score are discarded automatically.
- ✅ **🎯 Max ATS match** (on by default): puts every job keyword into the resume, in Skills and in a fitting bullet, repeating the ones the posting stresses, aiming for a 100% keyword match. Employers, titles, dates, degrees, certifications and numbers are never invented. Turn it off for strict mode.
- ✅ **Matches external checkers**: paste a Jobscan / Resume Worded report and every keyword it lists as missing is added, then tracked by the built-in score.
- ✅ **One-click missing keywords** (strict mode): for each job keyword your resume doesn't prove, choose *I know the basics* or *I've done this at <job>* and it's added in the next round. Nothing is added unless you confirm it's true.
- ✅ **8 professional resume templates**: Classic, Modern, Executive, Minimal, Compact, Sidebar, Timeline and Bold, each a different layout. You see all of them with your own resume.
- ✅ **PDF and Word download**: choose the file name, accent color and A4 / US Letter page size.
- ✅ **Versions and undo**: every revision is a version with its score, and you can step back any time.
- ✅ **Remembers your resume**: upload it once and it's reused for every job. Stored locally only.
- ✅ **Works with any ATS checker**: optionally paste a Jobscan or Resume Worded report into the improve step.
- ✅ **Private**: runs on your machine, and nothing is uploaded except the text sent to Claude.

## How it works

1. **Your resume**: upload it once (`.docx`, `.json`, `.md` or `.txt`). It's remembered.
2. **The job**: paste the job description, plus the exact job title and company.
3. Click **✨ Tailor my resume**. You get a built-in ATS score right away.
4. Click **🚀 Auto-improve**. Optionally, first click any missing keywords that are true for you.
5. Pick a **design** and click **⬇️ Download PDF**.

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
python main.py --resume my_resume.docx --job job.txt --title "Senior Backend Engineer" --template modern --max-match
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
├── render.py            # Templates → PDF (headless browser), Word (.docx), previews
├── templates/           # HTML/CSS resume designs (Jinja2)
├── master_resume.json   # Sample master resume
└── requirements.txt
```

## FAQ

**Is it free?**
Yes. The app is open source, and with Claude Code it runs on your existing Claude Pro/Max subscription (it counts toward your plan's usage limits) with no API credits. API mode costs a few cents per resume.

**Will it make up skills or experience?**
In strict mode, no: only facts from your resume are used. With 🎯 Max ATS match on, it adds every job keyword as a skill you vouch for, but never invents employers, titles, dates, degrees, certifications or numbers. Be ready to discuss every skill listed.

**My ATS score stopped going up. Why?**
With Max ATS match on, keywords are covered; what remains is usually checks only real facts can fix, such as too few bullets with numbers or a missing certification. Add real numbers to your master resume. In strict mode, open *Missing keywords you might have* and click the ones that are true.

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

*Keywords: AI resume tailor, ATS resume optimizer, ATS score checker, improve ATS score, free AI resume builder, professional resume templates, two-column resume template, resume to PDF, resume keyword optimizer, tailor resume to job description, ATS-friendly resume generator, Claude AI resume, Claude Code, Anthropic API Python, resume automation, job application automation, CV optimizer, Jobscan alternative, open source resume tool.*

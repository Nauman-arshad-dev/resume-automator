# Resume Automator — AI Resume Tailor & ATS Keyword Optimizer (Claude AI, Python)

**Tailor your resume to any job description in minutes.** Resume Automator is a free, open-source Python app that uses Claude AI (Anthropic) to add missing ATS keywords to your resume's existing bullet points **without inventing fake experience**. Pick a resume template, preview it live, and download an ATS-friendly **PDF** or **Word (`.docx`)** resume.

Runs locally on your laptop, with a simple web UI (Streamlit) or a command-line interface (CLI). **No API key needed.** Use it for free with your existing Claude.ai subscription (copy-paste mode), or connect an Anthropic API key for one-click automation.

---

## Why use it?

Applicant Tracking Systems (ATS) filter out resumes that don't match the job description's keywords. Rewriting your CV for every application takes a long time. Resume Automator does it in a single AI call:

- ✅ **ATS keyword optimization**: paste a job description or your ATS checker feedback (Jobscan, Resume Worded, etc.) and the missing keywords are worked into your real experience.
- ✅ **No hallucinated experience**: keywords are added only where your existing experience supports them. Anything that can't be added honestly is listed separately, so you decide.
- ✅ **Free with Claude.ai Pro/Max**: copy-paste mode needs no API key or API credits.
- ✅ **Upload your existing resume**: `.docx` (Word), `.json`, `.md` or `.txt`.
- ✅ **8 professional resume templates**: each one is a fully different layout. Classic, Modern, Executive, Minimal, Compact, Sidebar, Timeline and Bold. You can see all of them with **your own resume** in a visual gallery, then customize the accent color and page size (A4 / US Letter).
- ✅ **Download as PDF or Word**: rename the file before you download it.
- ✅ **ATS-friendly output**: single column, standard fonts, no tables or graphics.
- ✅ **Raise your ATS score round by round**: paste the report from Jobscan, Resume Worded or any ATS checker, and Claude creates the next version, fixing length, bullets, pronouns, buzzwords, job title and missing keywords. Enter each round's score to get a progress table and chart. The app warns you if a round made the score worse, and Undo takes you back to any earlier version.
- ✅ **Edit before download**: fix any line in the app and the preview updates instantly.
- ✅ **Google Drive / Google Docs sync**: save straight into your Google Drive for desktop folder.
- ✅ **Low token cost**: one request, compact input, adjustable model and effort.
- ✅ **Private**: runs on your machine. Your API key stays local.

## How it works

1. Upload your **master resume** (the full version with everything you've done).
2. Paste the **job description** and/or the **ATS report's missing keywords**.
3. Let Claude tailor it. In **free mode**, copy the generated prompt into [claude.ai](https://claude.ai/new) and paste the reply back. In **API mode**, just click **Tailor resume**. Either way, Claude rewrites the summary, reorders the skills and rephrases bullets to match the role, using only facts from your resume.
4. Pick a **design** from the gallery, check the **live preview**, review the keywords that were added or skipped, and download a **PDF** or **Word** resume.
5. Run the PDF through an **ATS checker**, enter the score, paste its report, and let Claude create the next version. Download it, check it again, and repeat. The app tracks each version's score so you can see it climb.

## Quick start

Requires **Python 3.10+** and either a [Claude.ai](https://claude.ai) subscription (free mode) or an [Anthropic API key](https://console.anthropic.com/settings/keys) with credits (API mode).

```bash
git clone https://github.com/Nauman-arshad-dev/resume-automator.git
cd resume-automator

python -m venv .venv
# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Option 1: Web UI (recommended)

```bash
streamlit run app.py
```

Your browser opens at `http://localhost:8501`.

**Free mode (Claude.ai subscription):** upload your resume, paste the job description, click the 📋 copy icon on the generated prompt, and paste it into a [new Claude chat](https://claude.ai/new). Copy Claude's whole reply, paste it back into the app, and click **✨ Create resume from reply**.

**API mode:** choose *Anthropic API key* in the sidebar, enter your key, and click **✨ Tailor resume**. The Anthropic API is billed separately from Claude.ai subscriptions.

Then pick a design from the gallery (each thumbnail shows your own resume), choose an accent color and page size, rename the file, and download the **PDF** or **Word** file.

Sidebar settings:
- **How to run Claude**: free copy-paste mode or API mode.
- **Model / Effort** (API mode): Claude Sonnet (balanced), Haiku (cheapest) or Opus (best quality). Lower effort uses fewer tokens.
- **Save-to folder**: e.g. `G:\My Drive\Resumes` to sync to Google Drive automatically.
- **Remember settings**: saved locally to `settings.json` (git-ignored, never uploaded).

### Option 2: Command line (CLI)

```bash
# Windows PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
# macOS / Linux
export ANTHROPIC_API_KEY="sk-ant-..."

python main.py --resume master_resume.json --job job_feedback.txt --label "Acme Data Engineer"
python main.py --resume my_resume.docx --template sidebar --page "US Letter" --out-dir "G:\My Drive\Resumes"
python main.py --help
```

The CLI uses API mode. Output: `output/<Name>_<Label>_<date>.pdf`, `.docx` and a `.json` copy. The JSON copy uses the same format as `master_resume.json`, so you can reuse it as a starting point for similar roles.

Optional environment variables: `RESUME_MODEL` (default `claude-sonnet-5-5`) and `RESUME_EFFORT` (`low` / `medium` / `high`).

## Resume templates

| Template | Layout | Best for |
|---|---|---|
| **Classic** | Serif, centered header, ruled sections | Traditional industries, ✅ ATS-friendly |
| **Modern** | Bold name, accent headings, skills up top | Tech and most roles, ✅ ATS-friendly |
| **Executive** | Full-width dark header band, small-caps sections | Senior and leadership roles, ✅ ATS-friendly |
| **Minimal** | Section labels in the left margin, lots of whitespace | Design-conscious roles 🎨 |
| **Compact** | Dense, two-column skills and projects | Fitting a long career on one page, ✅ ATS-friendly |
| **Sidebar** | Colored sidebar with contact, skill tags and education | Emailing directly to recruiters 🎨 |
| **Timeline** | Career history on a vertical timeline | Showing career progression 🎨 |
| **Bold** | Large name, colored skill tags, tinted side panel | Startups and creative roles 🎨 |

All templates use standard section headings (Summary, Skills, Experience, Projects, Education, Certifications), plain-text bullet characters and no letter-spacing, so ATS parsers can read the PDF text correctly.

Designs are HTML/CSS files in [`templates/`](templates/), so adding your own is easy: copy one, change the layout, and register it in `TEMPLATES` in `render.py`.

PDFs are printed by a headless browser. On Windows the built-in **Microsoft Edge** is used automatically. On macOS and Linux, Google Chrome is used if installed; otherwise run `python -m playwright install chromium` once.

## Project structure

```
resume-automator/
├── app.py               # Streamlit web UI (free + API modes, templates, preview)
├── main.py              # Core logic + CLI (prompt, Claude API call, reply parsing)
├── render.py            # Renders templates → PDF (headless browser), Word (.docx), previews
├── templates/           # HTML/CSS resume designs (Jinja2), one file per template
├── master_resume.json   # Sample master resume (replace with yours)
├── job_feedback.txt     # Sample job description / ATS feedback
└── requirements.txt     # anthropic, streamlit, playwright, jinja2, python-docx, pypdfium2
```

## FAQ

**Is this an AI resume builder?**
It's an AI **resume tailoring** tool. You start from your own resume and it adapts it to each job posting. It doesn't generate a resume from nothing.

**Will it make up skills or experience?**
No. The prompt forbids inventing employers, dates, metrics, tools or responsibilities. Keywords without supporting experience are reported under "Not added" instead of being inserted.

**My ATS score stopped going up. Why?**
The checker is probably asking for keywords your resume doesn't mention, so Claude won't add them. In step 6, open *Unlock missing keywords*, tick the ones that are genuinely true for you, and note where you used them. The next version adds them to Skills and the right job's bullets.

**Is it free?**
Yes. The app is open source, and the copy-paste mode uses your existing Claude.ai subscription (Pro/Max) at no extra cost. API mode costs a few cents per resume with Claude Sonnet, and less with Haiku.

**Can I download my resume as a PDF?**
Yes. Choose a template and download a PDF or Word file with any file name you like.

**Can I use it with Google Docs?**
Yes. Upload the `.docx` to Google Drive and choose *Open with → Google Docs*, or set the output folder to your Google Drive for desktop folder.

**Does it work with Jobscan / ATS checker results?**
Yes. After you create a resume, paste the full ATS checker report into the *Improve with ATS checker feedback* section, and Claude revises the resume to fix the issues it raises, without inventing experience.

## Tech stack

Python · [Anthropic Claude API](https://docs.anthropic.com) (structured JSON output) · [Streamlit](https://streamlit.io) · HTML/CSS + [Playwright](https://playwright.dev/python/) (PDF) · [python-docx](https://python-docx.readthedocs.io) (Word) · [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) (preview)

## Contributing

Issues and pull requests are welcome. Ideas: cover letter generation, more resume templates, batch tailoring for several job postings.

## License

[MIT](LICENSE): free for personal and commercial use.

---

*Keywords: AI resume tailor, ATS checker feedback, improve ATS score, free AI resume builder, professional resume templates, two-column resume template, resume to PDF, ATS resume optimizer, resume keyword optimizer, tailor resume to job description, ATS-friendly resume generator, Claude AI resume, Anthropic API Python, resume automation, job application automation, CV optimizer, Jobscan alternative, open source resume tool.*

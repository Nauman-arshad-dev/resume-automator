# Resume Automator — AI Resume Tailor & ATS Keyword Optimizer (Claude AI, Python)

**Tailor your resume to any job description in one click.** Resume Automator is a free, open-source Python app that uses Claude AI (Anthropic) to add missing ATS keywords to your resume's existing bullet points **without inventing fake experience**. It then exports a clean, ATS-friendly Word (`.docx`) resume you can upload or open in Google Docs.

Runs locally on your laptop, with a simple web UI (Streamlit) or a command-line interface (CLI). Bring your own Anthropic API key, and pay only a few cents per tailored resume.

---

## Why use it?

Applicant Tracking Systems (ATS) filter out resumes that don't match the job description's keywords. Rewriting your CV for every application takes a long time. Resume Automator does it in a single AI call:

- ✅ **ATS keyword optimization**: paste a job description or your ATS checker feedback (Jobscan, Resume Worded, etc.) and the missing keywords are worked into your real experience.
- ✅ **No hallucinated experience**: keywords are added only where your existing experience supports them. Anything that can't be added honestly is listed separately, so you decide.
- ✅ **Upload your existing resume**: `.docx` (Word), `.json`, `.md` or `.txt`.
- ✅ **ATS-friendly Word output**: single column, standard fonts, no tables or graphics.
- ✅ **Google Drive / Google Docs sync**: save straight into your Google Drive for desktop folder.
- ✅ **Low token cost**: one request, compact input, adjustable model and effort.
- ✅ **Private**: runs on your machine. Your API key stays local.

## How it works

1. Upload your **master resume** (the full version with everything you've done).
2. Paste the **job description** and/or the **ATS report's missing keywords**.
3. Click **Tailor resume**. Claude rewrites the summary, reorders the skills and rephrases bullets to match the role, using only facts from your resume.
4. Download the tailored **`.docx`** resume and review the keywords that were added or skipped.

## Quick start

Requires **Python 3.10+** and an [Anthropic API key](https://console.anthropic.com/settings/keys).

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

Your browser opens at `http://localhost:8501`. Enter your API key in the sidebar, upload your resume, paste the job description, and click **✨ Tailor resume**.

Sidebar settings:
- **Remember settings**: saves your key locally to `settings.json` (git-ignored, never uploaded).
- **Model**: Claude Sonnet (balanced), Haiku (cheapest) or Opus (best quality).
- **Effort**: lower uses fewer tokens.
- **Also save to folder**: e.g. `G:\My Drive\Resumes` to sync to Google Drive automatically.

### Option 2: Command line (CLI)

```bash
# Windows PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
# macOS / Linux
export ANTHROPIC_API_KEY="sk-ant-..."

python main.py --resume master_resume.json --job job_feedback.txt --label "Acme Data Engineer"
python main.py --resume my_resume.docx --out-dir "G:\My Drive\Resumes"
python main.py --help
```

Output: `output/<Name>_<Label>_<date>.docx` and a `.json` copy. The JSON copy uses the same format as `master_resume.json`, so you can reuse it as a starting point for similar roles.

Optional environment variables: `RESUME_MODEL` (default `claude-sonnet-5-5`) and `RESUME_EFFORT` (`low` / `medium` / `high`).

## Project structure

```
resume-automator/
├── app.py               # Streamlit web UI
├── main.py              # Core logic + CLI (Claude call, .docx export)
├── master_resume.json   # Sample master resume (replace with yours)
├── job_feedback.txt     # Sample job description / ATS feedback
└── requirements.txt     # anthropic, python-docx, streamlit
```

## FAQ

**Is this an AI resume builder?**
It's an AI **resume tailoring** tool. You start from your own resume and it adapts it to each job posting. It doesn't generate a resume from nothing.

**Will it make up skills or experience?**
No. The prompt forbids inventing employers, dates, metrics, tools or responsibilities. Keywords without supporting experience are reported under "Not added" instead of being inserted.

**How much does it cost?**
A typical resume plus job description is a few thousand tokens. That's usually a few cents per run with Claude Sonnet, and less with Haiku.

**Can I use it with Google Docs?**
Yes. Upload the `.docx` to Google Drive and choose *Open with → Google Docs*, or set the output folder to your Google Drive for desktop folder.

**Does it work with Jobscan / ATS checker results?**
Yes. Paste the missing-keywords list from any ATS resume checker into the job description box.

## Tech stack

Python · [Anthropic Claude API](https://docs.anthropic.com) (structured JSON output) · [python-docx](https://python-docx.readthedocs.io) · [Streamlit](https://streamlit.io)

## Contributing

Issues and pull requests are welcome. Ideas: PDF export, cover letter generation, multiple resume templates, batch tailoring for several job postings.

## License

[MIT](LICENSE): free for personal and commercial use.

---

*Keywords: AI resume tailor, ATS resume optimizer, resume keyword optimizer, tailor resume to job description, ATS-friendly resume generator, Claude AI resume, Anthropic API Python, resume automation, job application automation, CV optimizer, Jobscan alternative, open source resume tool.*

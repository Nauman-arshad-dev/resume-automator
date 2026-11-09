r"""
Resume Tailor — local web UI.

Run from the VS Code terminal:
    .\.venv\Scripts\Activate.ps1
    streamlit run app.py

Flow: 1 your profile (master resume) → 2 the job → 3 analyse the job and confirm the
requirements your profile doesn't show → 4 build (evidence-checked, auto-improved) →
review the ATS check, job match, evidence and simulated recruiter view → resolve the
quality gate → design and download → save the application with its exact resume.
Claude runs through Claude Code (your Claude subscription), the Anthropic API, or
copy-paste with claude.ai. Every resume is built from the master profile, which a
job-specific resume never changes.
"""

import io
import json
import os
import re
import time
from datetime import date
from pathlib import Path

import streamlit as st

import ats
import engines
import evidence
import main
import match
import quality
import render
import review
import store

APP_DIR = Path(__file__).parent
# Where settings and data live (git-ignored). RESUME_TAILOR_HOME moves them, e.g. for tests.
HOME_DIR = Path(os.environ.get("RESUME_TAILOR_HOME") or APP_DIR)
SETTINGS_FILE = HOME_DIR / "settings.json"
DATA_DIR = HOME_DIR / "data"
MASTER_FILE = DATA_DIR / "master_resume.txt"
CONFIRM_FILE = DATA_DIR / "skill_confirmations.json"
VIEW_TAILOR, VIEW_APPS = "✨ Tailor a resume", "💼 My applications"
MODE_LABELS = {  # content mode -> sidebar label
    "Strict": "Strict — only what my profile shows (recommended)",
    "Flexible": "Flexible — every job keyword except No answers",
}
PAGE_OPTIONS = ("Auto", "1 page", "2 pages")
PROFILE_SECTIONS = ("Experience", "Projects", "Skills", "Achievements", "Education", "Certifications",
                    "Languages", "Publications", "Portfolio & links", "Volunteering")

ENGINE_CODE = "Claude Code — your Claude plan (automatic)"
ENGINE_API = "Anthropic API key (pay per use)"
ENGINE_MANUAL = "Copy-paste with claude.ai"
MODELS = {  # label -> (API model id, Claude Code alias)
    "Sonnet 5.5 · balanced": ("claude-sonnet-5-5", "sonnet"),
    "Opus 5.5 · best quality": ("claude-opus-5-5", "opus"),
    "Haiku 5.5 · fastest": ("claude-haiku-5-5", "haiku"),
}
EFFORTS = ["low", "medium", "high"]
LEVEL_HELP = {  # Strict: Strong/Basic are included, Learning/No stay out. Flexible: only No stays out.
    "Strong": "Used it for real work",
    "Basic": "Know it a little",
    "Learning": "Learning it now",
    "No": "Leave it out",
}
# Per-job state: cleared by "New job" (the master resume and skill answers stay)
JOB_KEYS = {"versions", "job_keywords", "analysis", "analysis_for", "cover_letter", "filename", "notice",
            "job_text", "job_title", "job_type", "location", "visa", "must_include", "must_exclude",
            "analysis_reply", "tailor_reply", "cover_reply", "auto_build", "recruiter", "recruiter_reply",
            "application_id", "app_company", "app_status", "app_notes", "app_date", "page_option"}
JOB_KEY_PREFIXES = ("confirm_", "ext_report_", "revision_reply_", "edit_", "cover_edit_", "kw_", "gate_ack_")
# Shown in the Improve tab; the built-in check and Claude's rules enforce each point.
TIPS = """**Make it readable by software first.** A person only sees your resume if the software parsed it.
- One column, standard headings: Summary, Skills, Experience, Projects, Education.
- A text-based PDF or DOCX, never a screenshot, scan or photo.
- Contact details in the body, not in the page header or footer.
- No tables, text boxes, icons, skill bars or graphics.
- File name: Firstname-Lastname-Role.pdf.

**Mirror the job post honestly.** Recruiters search for the exact words from the job post.
- Same spelling for tools and titles, e.g. "JavaScript" and "customer support".
- Acronym and full term once: "search engine optimisation (SEO)".
- Most relevant skills near the top: in the summary and the first bullets.
- A skill only where it's true: interviewers ask about every skill listed.

**Write bullets that prove results.** Duties say what the job was; bullets show what you achieved.
- Action verb + what you did + technology + result: "Reduced page load time by 35% by splitting bundles \
and lazy loading images."
- Cut "responsible for" and "worked on". Start with Built, Led, Automated, Launched.
- Real numbers only. No real number? Describe the impact in words.
- 3 to 6 bullets per role, most relevant first."""


# --------------------------------------------------------------------------- #
# Persistence (all local, in this app's folder)
# --------------------------------------------------------------------------- #

def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_master() -> str:
    try:
        return MASTER_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


def save_master(text: str) -> None:
    """Save the profile, keeping the previous version in data/profile_history/."""
    MASTER_FILE.parent.mkdir(exist_ok=True)
    old = load_master()
    if old and old != text:
        store.backup_profile(DATA_DIR, old)
    MASTER_FILE.write_text(text, encoding="utf-8")


def save_confirmations(confirmations: dict) -> None:
    CONFIRM_FILE.parent.mkdir(exist_ok=True)
    CONFIRM_FILE.write_text(json.dumps(confirmations, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Cached rendering and scoring
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner=False, max_entries=64)
def make_files(resume_json: str, template: str, accent: str, page: str, font: str = "",
               size: str = "Normal") -> tuple[bytes, bytes, list[bytes]]:
    resume = json.loads(resume_json)
    pdf = render.build_pdf(resume, template, accent, page, font, size)
    docx = io.BytesIO()
    render.build_docx(resume, docx, template, accent, page, font, size)
    return pdf, docx.getvalue(), render.pdf_page_images(pdf)


@st.cache_data(show_spinner=False, max_entries=16)
def make_cover(resume_json: str, letter_json: str, template: str, accent: str, page: str, font: str,
               size: str) -> tuple[bytes, bytes, list[bytes]]:
    resume, letter = json.loads(resume_json), json.loads(letter_json)
    pdf = render.build_cover_pdf(resume, letter, accent, page, font, size)
    docx = io.BytesIO()
    render.build_cover_docx(resume, letter, docx, template, accent, page, font, size)
    return pdf, docx.getvalue(), render.pdf_page_images(pdf, scale=1.2)


@st.cache_data(show_spinner=False, max_entries=8)
def make_gallery(resume_json: str, page: str) -> dict[str, tuple[bytes, int]]:
    """First-page thumbnail and page count of every design."""
    pdfs = render.build_all_pdfs(json.loads(resume_json), page)
    return {t: (render.pdf_page_images(pdf, scale=0.6, max_pages=1)[0], render.pdf_page_count(pdf))
            for t, pdf in pdfs.items()}


@st.cache_data(show_spinner=False, max_entries=64)
def ats_check(resume_json: str, template: str, accent: str, page: str, font: str, size: str,
              keywords_json: str, job_title: str, job_text: str = "", page_limit: int = 2):
    pdf, _, _ = make_files(resume_json, template, accent, page, font, size)
    return ats.check(json.loads(resume_json), pdf, json.loads(keywords_json), job_title, job_text,
                     one_column=render.TEMPLATES[template]["ats"], page_limit=page_limit)


def as_json(resume: dict) -> str:
    return json.dumps(resume, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# Versions: every build / revision / edit is a new version; Undo pops one
# --------------------------------------------------------------------------- #

def versions() -> list[dict]:
    return st.session_state.setdefault("versions", [])


def start_versions(result: dict, note: str, role: str, keywords: list[str]) -> None:
    st.session_state["versions"] = [{"result": result, "note": note, "score": None}]
    st.session_state.pop("cover_letter", None)  # it belonged to the previous build
    st.session_state.pop("recruiter", None)
    # Keep the job's keywords fixed across versions so scores stay comparable
    st.session_state["job_keywords"] = keywords or result["job_keywords"]
    st.session_state["filename"] = main.output_stem(result["resume"], role)  # Firstname-Lastname-Role


def add_version(result: dict, note: str, score: int | None = None) -> None:
    versions().append({"result": result, "note": note, "score": score})


def undo() -> None:
    if len(versions()) > 1:
        versions().pop()


def pick_template(template_id: str) -> None:
    st.session_state["template"] = template_id


def new_job() -> None:
    """Keep the master resume and skill answers; clear everything about the previous job."""
    for key in list(st.session_state):
        if key in JOB_KEYS or key.startswith(JOB_KEY_PREFIXES):
            del st.session_state[key]
    st.session_state["notice_top"] = "Ready for a new job. Your resume and skill answers are kept."


def reset_everything() -> None:
    """Forget everything: resume, job, answers, results, job settings. Claude connection settings and
    saved applications stay."""
    st.session_state.clear()
    st.cache_data.clear()
    MASTER_FILE.unlink(missing_ok=True)
    CONFIRM_FILE.unlink(missing_ok=True)
    for old in store.profile_history(DATA_DIR):
        old.unlink(missing_ok=True)
    kept = {k: v for k, v in _read_json(SETTINGS_FILE).items() if k not in ("timezone",)}
    if kept:
        SETTINGS_FILE.write_text(json.dumps(kept, indent=2), encoding="utf-8")
    st.session_state["notice_top"] = ("Reset complete. Everything has been cleared. Please add (1) your master "
                                      "resume, (2) the job description, (3) the job type.")


# --------------------------------------------------------------------------- #
# Claude calls
# --------------------------------------------------------------------------- #

def engine_config() -> dict:
    api_id, cli_alias = MODELS[model_label]
    return {"engine": "code" if engine == ENGINE_CODE else "api", "model_cli": cli_alias, "model_api": api_id,
            "effort": effort, "api_key": api_key, "cli_path": cli_path}


def run_claude(label: str, system: str, content: str, schema: dict | None = None):
    started = time.time()
    with st.status(label, expanded=False) as status:
        try:
            out = engines.run(engine_config(), system, content, schema)
        except engines.EngineError as e:
            status.update(label=f"❌ {e}", state="error", expanded=True)
            return None
        status.update(label=f"✅ Done in {time.time() - started:.0f}s", state="complete")
    return out


def polish(result: dict, ctx: dict) -> dict:
    """Safety nets after every Claude reply: evidence-aware clean-up, word limit, and the page limit."""
    resume, fixes = review.fix_resume(result["resume"], ctx["job_skills"], ctx["confirmations"], ctx["master"],
                                      main.content_mode(ctx["settings"]))
    keywords = st.session_state.get("job_keywords", [])
    limit = ctx["page_limit"]
    resume, removed = ats.trim_to_length(resume, keywords, ats.words_for_pages(limit)[1] - 40)
    view = (ctx["template"], ctx["accent"], ctx["page"], ctx["font"], ctx["size"])
    for _ in range(6):  # a last page with only Education on it: cut the least relevant bullets until it fits
        pages = render.pdf_page_count(make_files(as_json(resume), *view)[0])
        if pages <= limit:
            break
        resume, cut = ats.trim_to_length(resume, keywords, ats.count_words(ats.resume_text(resume)) - 50)
        if not cut:
            break
        removed += cut
    if removed:
        fixes.append(f"Length: removed {removed} of the least relevant bullet{'s' if removed != 1 else ''} "
                     f"to stay within {limit} page{'s' if limit != 1 else ''}.")
    return {**result, "resume": resume, "fixes": fixes}


def gate_for(resume: dict, ctx: dict, pages: int | None = None) -> list:
    return quality.gate(resume, ctx["master"], ctx["confirmations"], ctx["analysis"], ctx["settings"],
                        ctx["job_skills"], pages, ctx["page_limit"])


def build_resume(ctx: dict) -> None:
    """Step 2: tailor from the master resume, then auto-improve to the target."""
    out = run_claude("✍️ Claude is writing your tailored resume — usually 1-2 minutes…",
                     *main.tailor_request(ctx["master"], ctx["job_text"], ctx["job_title"], ctx["settings"],
                                          ctx["analysis"], ctx["confirmations"]))
    if out:
        start_versions(polish(out[0], ctx), f"Built · {out[1]}", ctx["job_title"], ctx["keywords"])
        remember_master()
        auto_improve(ctx["target"], ctx["max_rounds"], "", ctx)  # reruns the page when done


def auto_improve(target: int, max_rounds: int, ext_report: str, ctx: dict) -> None:
    """Check → revise → re-check until the target score, no further gain, or max_rounds."""
    view = (ctx["template"], ctx["accent"], ctx["page"], ctx["font"], ctx["size"])
    with st.status("🚀 Improving your resume…", expanded=True) as status:
        for n in range(1, max_rounds + 1):
            keywords_json = json.dumps(st.session_state.get("job_keywords", []))
            resume = versions()[-1]["result"]["resume"]
            before = ats_check(as_json(resume), *view, keywords_json, ctx["job_title"], ctx["job_text"],
                               ctx["page_limit"])
            serious = [i for i in gate_for(resume, ctx) if i.severity == quality.SERIOUS]
            first_with_input = n == 1 and bool(ext_report or ctx.get("new_answers"))
            if before.score >= target and not first_with_input and not serious:
                status.write(f"Score {before.score} already meets your target of {target}.")
                break
            status.write(f"**Round {n}** — score {before.score}. Claude is revising…")
            report_text = before.as_text()
            if serious:
                report_text += "\n\nQuality review (fix these first):\n" + "\n".join(f"- {i.message}" for i in serious)
            if ext_report and n == 1:
                report_text += f"\n\nReport from an external ATS checker or reviewer:\n{ext_report}"
            progress = ", ".join(f"v{i + 1}: {v['score']}" for i, v in enumerate(versions()) if v["score"] is not None)
            try:
                new, usage = engines.run(engine_config(), *main.revision_request(
                    ctx["master"], ctx["job_text"], resume, report_text, ctx["job_title"], ctx["settings"],
                    ctx["analysis"], ctx["confirmations"], progress))
            except engines.EngineError as e:
                status.update(label=f"❌ {e}", state="error")
                return
            if ext_report and n == 1:  # track the external checker's keywords from now on
                merged = list(dict.fromkeys(st.session_state.get("job_keywords", []) + new["job_keywords"]))
                st.session_state["job_keywords"] = merged
                keywords_json = json.dumps(merged)
                before = ats_check(as_json(resume), *view, keywords_json, ctx["job_title"], ctx["job_text"],
                                   ctx["page_limit"])
            new = polish(new, ctx)
            after = ats_check(as_json(new["resume"]), *view, keywords_json, ctx["job_title"], ctx["job_text"],
                              ctx["page_limit"])
            fixed_serious = len(serious) > len([i for i in gate_for(new["resume"], ctx)
                                                if i.severity == quality.SERIOUS])
            if after.score < before.score and not first_with_input and not fixed_serious:
                status.write(f"Round {n} would lower the score ({before.score} → {after.score}), so it was discarded.")
                break
            add_version(new, f"Auto-improve round {n} · {usage}", after.score)
            status.write(f"✅ Version {len(versions())}: {before.score} → **{after.score}**")
            if after.score >= target:
                break
            if after.score == before.score and not fixed_serious:
                status.write("No further gain — the rest is usually requirements your profile doesn't show "
                             "(they stay out in Strict mode) or skills you marked No.")
                break
        final = versions()[-1]["score"]
        status.update(label=f"Done — ATS score {final}/100 (version {len(versions())})", state="complete",
                      expanded=False)
    st.session_state["notice"] = f"Your resume is ready: ATS score {final}/100 (version {len(versions())})."
    st.rerun()


# --------------------------------------------------------------------------- #
# Skill confirmations
# --------------------------------------------------------------------------- #

def skill_key(skill: str) -> str:
    return skill.strip().lower()


def confirm_widgets(skills: list[dict], saved_answers: dict) -> None:
    """One Strong / Basic / Learning / No row per skill; answers live in st.session_state['confirm_<key>']."""
    for s in skills:
        key = f"confirm_{skill_key(s['skill'])}"
        if key not in st.session_state and skill_key(s["skill"]) in saved_answers:
            st.session_state[key] = saved_answers[skill_key(s["skill"])]["level"]
        meta_bits = [b for b in (match.CATEGORY_LABEL[match.category_of(s)] if "group" in s else "",
                                 f"{s['mentions']}× in the job" if s.get("mentions") else "",
                                 f"🔁 you have {s['evidence']}" if s.get("group") == "B" and s.get("evidence") else "",
                                 s.get("meaning")) if b]
        label = f"**{s['skill']}**" + (f" · <span style='color:gray'>{' · '.join(meta_bits)}</span>"
                                       if meta_bits else "")
        st.markdown(label, unsafe_allow_html=True)
        st.radio(s["skill"], main.CONFIRM_LEVELS, index=None, key=key, horizontal=True,
                 label_visibility="collapsed", captions=[LEVEL_HELP[x] for x in main.CONFIRM_LEVELS])


def answer_unanswered(skills: list[str], level: str) -> None:
    for skill in skills:
        st.session_state.setdefault(f"confirm_{skill_key(skill)}", level)
        if st.session_state[f"confirm_{skill_key(skill)}"] is None:
            st.session_state[f"confirm_{skill_key(skill)}"] = level


def collect_answers(saved_answers: dict) -> dict:
    """Saved answers updated with every answer given on this page."""
    answers = dict(saved_answers)
    for key, level in st.session_state.items():
        if key.startswith("confirm_") and level in main.CONFIRM_LEVELS:
            skill = key[len("confirm_"):]
            answers[skill] = {"skill": answers.get(skill, {}).get("skill") or skill_names.get(skill, skill),
                              "level": level}
    return answers


def manual_step(prompt: str, reply_key: str, button: str):
    """Copy-paste mode: show a prompt, take Claude's reply. Returns the reply text when the button is clicked."""
    st.markdown("**a.** Copy the prompt (📋 top-right of the box) → **b.** paste it into a "
                "[new Claude chat](https://claude.ai/new) → **c.** paste Claude's whole reply below.")
    st.code(prompt, language=None, height=140)
    reply = st.text_area("Claude's reply", height=100, key=reply_key)
    return reply if st.button(button, type="primary", width="stretch") else None


# --------------------------------------------------------------------------- #
# Saved applications
# --------------------------------------------------------------------------- #

def open_application(app: dict) -> None:
    """Load a saved application back into the editor (callback: runs before the widgets exist)."""
    for key in list(st.session_state):
        if key in JOB_KEYS or key.startswith(JOB_KEY_PREFIXES):
            del st.session_state[key]
    s = app.get("settings") or {}
    st.session_state.update({
        "view": VIEW_TAILOR, "job_text": app.get("job_text", ""), "job_title": app.get("job_title", ""),
        "versions": app.get("versions") or [], "job_keywords": app.get("job_keywords") or [],
        "filename": app.get("filename", ""), "application_id": app["id"],
        "app_company": app.get("company", ""), "app_status": app.get("status", "Draft"),
        "app_notes": app.get("notes", ""), "location": s.get("location", ""), "visa": s.get("visa", ""),
        "must_include": s.get("must_include", ""), "must_exclude": s.get("must_exclude", ""),
    })
    if s.get("job_type") in main.JOB_TYPES:
        st.session_state["job_type"] = s["job_type"]
    look_saved = app.get("look") or {}
    if look_saved.get("template") in render.TEMPLATES:
        st.session_state["template"] = look_saved["template"]
    if app.get("analysis"):  # valid only while the profile is the one the application was built from
        st.session_state["analysis"] = app["analysis"]
        st.session_state["analysis_for"] = hash((load_master(), app.get("job_text", ""), app.get("job_title", "")))
    if app.get("recruiter"):
        st.session_state["recruiter"] = {"version": len(app.get("versions") or []), "review": app["recruiter"]}
    if app.get("cover_letter"):
        st.session_state["cover_letter"] = app["cover_letter"]
    st.session_state["notice_top"] = f"Opened your application for {app.get('job_title') or 'this job'}."


def applications_page() -> None:
    st.title("💼 My applications")
    st.caption("Every saved application keeps its own job description, profile snapshot, versions, reports and "
               "the exact PDF/Word files you saved. Your master profile is never changed by them.")
    apps = store.list_applications(DATA_DIR)
    if not apps:
        st.info("No saved applications yet. Build a resume, then use the **💾 Save application** tab.")
        return
    counts = {s: sum(a["status"] == s for a in apps) for s in store.STATUSES}
    st.markdown(" · ".join(f"**{s}** {n}" for s, n in counts.items() if n))
    st.dataframe([{"Date": a["applied_date"] or a["created"][:10], "Company": a["company"], "Job": a["job_title"],
                   "Status": a["status"], "ATS": a["ats_score"], "Match": a["match_score"]} for a in apps],
                 hide_index=True, width="stretch")
    labels = {a["id"]: f"{a['company'] or '—'} · {a['job_title'] or '—'} · {a['status']} · "
                       f"{(a['applied_date'] or a['created'])[:10]}" for a in apps}
    pick = st.selectbox("Open an application", list(labels), format_func=labels.get)
    try:
        app = store.load_application(DATA_DIR, pick)
    except store.StoreError as e:
        st.error(str(e))
        return
    with st.container(border=True):
        st.subheader(f"{app.get('job_title') or 'Job'} · {app.get('company') or '—'}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("ATS score", app.get("ats_score", "—"))
        c2.metric("Job match", app.get("match_score", "—"))
        c3.metric("Versions", len(app.get("versions") or []))
        c4.metric("Saved", (app.get("updated") or "")[:10])
        s1, s2 = st.columns([1, 2])
        status = s1.selectbox("Status", store.STATUSES, index=store.STATUSES.index(app.get("status", "Draft"))
                              if app.get("status") in store.STATUSES else 0, key=f"apps_status_{pick}")
        notes = s2.text_area("Notes", app.get("notes", ""), height=80, key=f"apps_notes_{pick}")
        if st.button("💾 Save status and notes", disabled=(status, notes) == (app.get("status"), app.get("notes"))):
            store.update_application(DATA_DIR, pick, status=status, notes=notes)
            st.rerun()
        pdf, docx = store.file_bytes(DATA_DIR, pick, "resume.pdf"), store.file_bytes(DATA_DIR, pick, "resume.docx")
        name = main.safe_filename(app.get("filename", "")) or "resume"
        d1, d2, d3 = st.columns(3)
        if pdf:
            d1.download_button("⬇️ The PDF you saved", pdf, f"{name}.pdf", mime="application/pdf", width="stretch")
        if docx:
            d2.download_button("⬇️ The Word file you saved", docx, f"{name}.docx", width="stretch",
                               mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        d3.button("✏️ Open in the editor", on_click=open_application, args=(app,), width="stretch",
                  help="Load the job, versions and reports to keep working on it.")
        with st.expander("Job description"):
            st.text(app.get("job_text", ""))
        if app.get("match_missing"):
            with st.expander("Requirements not shown in the saved resume"):
                st.markdown("\n".join(f"- {m}" for m in app["match_missing"]))
        if app.get("quality"):
            with st.expander(f"Quality gate when saved ({len(app['quality'])})"):
                st.markdown("\n".join(f"- {'🛑' if q['severity'] == quality.SERIOUS else '⚠️'} {q['message']}"
                                      for q in app["quality"]))
        if app.get("versions"):
            with st.expander("Version history"):
                st.dataframe([{"Version": f"v{i + 1}", "Score": v.get("score"), "What changed": v.get("note")}
                              for i, v in enumerate(app["versions"])], hide_index=True, width="stretch")
        with st.expander("Profile used for this application"):
            st.text(app.get("master", ""))
        if pdf:
            with st.expander("Preview of the saved resume"):
                for png in render.pdf_page_images(pdf, scale=1.2):
                    st.image(png, width="stretch")
        with st.popover("🗑️ Delete this application"):
            st.markdown("This permanently deletes the saved job, versions and files.")
            if st.button("Yes, delete it", type="primary", key=f"del_{pick}"):
                store.delete_application(DATA_DIR, pick)
                st.rerun()


# --------------------------------------------------------------------------- #
# Sidebar: settings
# --------------------------------------------------------------------------- #
st.set_page_config(page_title="Resume Tailor", page_icon="📄", layout="wide")
saved = _read_json(SETTINGS_FILE)
cli_path = engines.find_claude_cli()

with st.sidebar:
    n_apps = len(store.list_applications(DATA_DIR))
    view = st.radio("View", [VIEW_TAILOR, VIEW_APPS], key="view", label_visibility="collapsed",
                    format_func=lambda v: f"{v} ({n_apps})" if v == VIEW_APPS else v)
    st.divider()
    st.header("⚙️ Settings")
    mode_default = saved.get("content_mode") if saved.get("content_mode") in MODE_LABELS else "Strict"
    content_mode = st.radio("Content mode", list(MODE_LABELS), index=list(MODE_LABELS).index(mode_default),
                            format_func=MODE_LABELS.get,
                            help="Strict: every claim must be in your profile or confirmed by you; requirements "
                                 "you don't have are reported as gaps. Flexible: every job keyword is listed "
                                 "(except No answers) and linked to your closest experience, for maximum "
                                 "keyword coverage; the quality gate warns about each unbacked one.")
    engine_options = [ENGINE_CODE, ENGINE_API, ENGINE_MANUAL]
    default_engine = saved.get("engine") if saved.get("engine") in engine_options else (
        ENGINE_CODE if cli_path else ENGINE_MANUAL)
    engine = st.radio("Connect to Claude", engine_options, index=engine_options.index(default_engine))
    if engine == ENGINE_CODE:
        if cli_path:
            st.caption("✅ Claude Code found. Runs on your Claude Pro/Max plan — no API key, no copy-paste.")
        else:
            st.error("Claude Code not found. Install it from claude.com/claude-code and sign in once, "
                     "or pick another option.")

    api_key = saved.get("api_key", "")
    if engine == ENGINE_API:
        api_key = st.text_input("Anthropic API key", value=api_key, type="password", placeholder="sk-ant-...")
    model_label = saved.get("model_label") if saved.get("model_label") in MODELS else next(iter(MODELS))
    effort = saved.get("effort") if saved.get("effort") in EFFORTS else "medium"
    if engine != ENGINE_MANUAL:
        model_label = st.selectbox("Model", list(MODELS), index=list(MODELS).index(model_label))
        effort = st.select_slider("Effort", EFFORTS, value=effort, help="Higher = more careful, slower.")

    out_dir = st.text_input("Save-to folder (optional)", value=saved.get("out_dir", ""),
                            placeholder=r"G:\My Drive\Resumes", help="E.g. your Google Drive folder.")
    remember = st.checkbox("Remember my resume, skill answers and settings on this laptop",
                           value=saved.get("remember", True),
                           help="Stored in this app's folder only (settings.json, data/). Never uploaded.")
    settings = {"engine": engine, "api_key": api_key, "model_label": model_label, "effort": effort,
                "out_dir": out_dir, "remember": remember, "content_mode": content_mode,
                "font": st.session_state.get("font", saved.get("font", "")),
                "timezone": st.session_state.get("timezone", saved.get("timezone", ""))}
    if remember and settings != saved:
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    elif not remember:
        SETTINGS_FILE.unlink(missing_ok=True)
        MASTER_FILE.unlink(missing_ok=True)
        CONFIRM_FILE.unlink(missing_ok=True)

saved_answers = _read_json(CONFIRM_FILE) if remember else {}
if view == VIEW_APPS:
    applications_page()
    st.stop()

# --------------------------------------------------------------------------- #
# Header: new job / reset
# --------------------------------------------------------------------------- #
title_col, new_col, reset_col = st.columns([4, 1, 1], vertical_alignment="bottom")
title_col.title("📄 Resume Tailor")
new_col.button("🆕 New job", on_click=new_job, width="stretch",
               help="Start the next application. Keeps your master resume and skill answers.")
with reset_col.popover("♻️ Reset", width="stretch"):
    st.markdown("Forget **everything**: master resume, its history, job, skill answers and results. "
                "Saved applications are kept (delete them under 💼 My applications).")
    st.button("Yes, reset everything", on_click=reset_everything, type="primary")
st.caption("Built fresh from your master profile for each job, for any profession and level. "
           + ("Strict mode: every claim is backed by your profile or your own confirmation; gaps are reported, "
              "never invented." if content_mode == "Strict" else
              "Flexible mode: every job keyword is listed and linked to your closest real experience, with no "
              "invented employers, dates or numbers."))
if notice := st.session_state.pop("notice_top", None):
    st.info(notice)

# --------------------------------------------------------------------------- #
# 1-2. Resume and job
# --------------------------------------------------------------------------- #
col_resume, col_job = st.columns(2, gap="large")
with col_resume:
    st.subheader("1 · Your profile (master resume)")
    saved_master = load_master() if remember else ""
    master = ""
    if saved_master and not st.session_state.get("replace_master"):
        st.success(f"Using your saved profile ({len(saved_master.split())} words). It is the source of truth "
                   "for every resume and is never changed by tailoring.")
        with st.expander("✏️ View, correct or add to your profile"):
            edited_master = st.text_area("Profile text", saved_master, height=260, key=f"profile_{hash(saved_master)}",
                                         help="Everything you have done: all roles, dates, projects, skills, "
                                              "results with real numbers, education, certifications, languages.")
            if st.button("💾 Save profile", disabled=edited_master.strip() == saved_master.strip()):
                save_master(edited_master.strip())
                st.session_state["notice_top"] = "Profile saved. The previous version is kept in its history."
                st.rerun()
            st.markdown("**➕ Add something new** (appended under its section, nothing else changes)")
            add_section = st.selectbox("Section", PROFILE_SECTIONS, key="add_section")
            add_text = st.text_area("Entry", height=90, key="add_entry", placeholder=(
                "e.g. Senior Accountant · Acme Ltd · London · Jan 2024 – Present\n"
                "- Led the month-end close for 3 entities in Xero, cutting close time from 8 to 5 days"))
            if st.button("Add to profile", disabled=not add_text.strip()):
                save_master(store.add_to_profile(saved_master, add_section, add_text))
                st.session_state.pop("add_entry", None)
                st.session_state["notice_top"] = f"Added to {add_section} in your profile."
                st.rerun()
            history = store.profile_history(DATA_DIR)
            if history:
                pick = st.selectbox("Earlier versions", history, format_func=lambda f: f.stem[:15].replace("-", " "),
                                    key="history_pick")
                if st.button("↩️ Restore this version"):
                    save_master(pick.read_text(encoding="utf-8"))
                    st.session_state["notice_top"] = "Earlier profile version restored."
                    st.rerun()
        if st.button("Use a different resume"):
            st.session_state["replace_master"] = True
            st.rerun()
        master = saved_master
    else:
        uploaded = st.file_uploader("Upload your full resume (all roles, projects, skills)",
                                    type=["pdf", "docx", "json", "md", "txt"], key="upload_master",
                                    help="Text-based PDF or Word file, up to 5 MB. Scans can't be read.")
        if uploaded is not None:
            try:
                extracted = main.read_upload(uploaded.name, uploaded.getvalue())
            except main.TailorError as e:
                st.error(f"Could not read the resume file: {e}")
            else:
                st.caption("Check the extracted text and correct anything that was read wrongly:")
                master = st.text_area("Extracted profile", extracted, height=200,
                                      key=f"extracted_{hash(extracted)}").strip()
        else:
            master = st.text_area("…or paste it", height=110, key="paste_master").strip()
    if saved_answers:
        st.caption(f"🧠 {len(saved_answers)} skill answers remembered from earlier jobs.")

with col_job:
    st.subheader("2 · The job")
    job_text = st.text_area("Job description", height=150, placeholder="Paste the full job posting",
                            key="job_text").strip()
    job_title = st.text_input("Exact job title", placeholder="Senior Backend Engineer (or leave empty: "
                              "taken from the posting)", key="job_title",
                              help="ATS checkers look for the exact title. Also names the file "
                                   "Firstname-Lastname-Role.pdf.").strip()
    job_type = st.radio("Job type", main.JOB_TYPES, index=None, horizontal=True, key="job_type",
                        help="Sets the line under your contact details. Picked fresh for every job.")
    if job_type == "Remote":
        st.session_state.setdefault("timezone", saved.get("timezone", ""))
        st.text_input("Your time zone", placeholder="UTC+5", key="timezone",
                      help="Shown as: Open to remote work (UTC+5)")
    elif job_type == "Relocation":
        loc_col, visa_col = st.columns(2)
        loc_col.text_input("Relocate to (city, country)", placeholder="Berlin, Germany", key="location")
        visa_col.text_input("Visa (optional)", placeholder="auto: e.g. EU Blue Card", key="visa")
    with st.expander("Length, must include / must exclude (optional)"):
        st.radio("Resume length", PAGE_OPTIONS, horizontal=True, key="page_option",
                 help="Auto: 1 page for entry-level and junior jobs, 2 pages otherwise.")
        st.text_input("Must include", placeholder="e.g. my open-source project, German B1", key="must_include")
        st.text_input("Must exclude", placeholder="e.g. my freelance work, PHP", key="must_exclude")

target_settings = {"job_type": job_type or "", "timezone": st.session_state.get("timezone", ""),
                   "location": st.session_state.get("location", ""), "visa": st.session_state.get("visa", ""),
                   "must_include": st.session_state.get("must_include", ""),
                   "must_exclude": st.session_state.get("must_exclude", ""), "content_mode": content_mode}


def remember_master() -> None:
    if remember and master and master != saved_master:
        save_master(master)
    st.session_state["replace_master"] = False


# --------------------------------------------------------------------------- #
# 3. Analyse the job and confirm skills
# --------------------------------------------------------------------------- #
st.divider()
st.subheader("3 · Skills the job asks for")
fingerprint = hash((master, job_text, job_title))
analysis = st.session_state.get("analysis") if st.session_state.get("analysis_for") == fingerprint else None
claude_ready = engine != ENGINE_CODE or bool(cli_path)


def store_analysis(result: dict) -> None:
    st.session_state["analysis"] = result
    st.session_state["analysis_for"] = fingerprint
    st.session_state["auto_build"] = True  # build straight away if every skill is already answered
    remember_master()


if not (master and job_text):
    st.info("Add your master resume and the job description, then analyse the job.")
elif engine == ENGINE_MANUAL and not analysis:
    with st.expander("🔍 Analyse the job with claude.ai (copy-paste)", expanded=True):
        reply = manual_step(main.manual_prompt(*main.analysis_request(master, job_text, job_title),
                                               main.ANALYSIS_SCHEMA), "analysis_reply", "🔍 Read the analysis")
        if reply is not None:
            try:
                store_analysis(main.parse_analysis(reply))
                st.rerun()
            except main.TailorError as e:
                st.error(str(e))
elif engine != ENGINE_MANUAL:
    if st.button("🔁 Analyse again" if analysis else "🔍 Analyse the job", disabled=not claude_ready,
                 type="secondary" if analysis else "primary", width="content" if analysis else "stretch"):
        out = run_claude("🔍 Claude is reading the job and comparing it with your resume…",
                         *main.analysis_request(master, job_text, job_title), main.ANALYSIS_SCHEMA)
        if out:
            store_analysis(out[0])
            st.rerun()

skill_names: dict[str, str] = {}
confirmations: dict = {}
unanswered: list[str] = []
if analysis:
    title = main.clean_title(job_title or analysis["job_title"])
    info = [f"**{title}**"] + [x for x in (analysis["company"], analysis.get("industry"), analysis["job_location"],
                                           analysis.get("employment_type"), analysis.get("seniority_level")) if x]
    st.markdown(" · ".join(info))
    if analysis["seniority"]:
        st.caption(analysis["seniority"])
    skills = analysis["skills"]
    with st.expander(f"📊 Requirements by importance ({len(skills)})"):
        rank = {"essential": 0, "domain": 1, "preferred": 2, "additional": 3}
        st.dataframe([{"Requirement": s["skill"], "Category": match.CATEGORY_LABEL[match.category_of(s)],
                       "Weight": "●" * max(1, min(5, s.get("weight") or 3)),
                       "Your profile": {"A": "✅ shown", "B": f"🔁 equivalent: {s['evidence']}"}.get(
                           s["group"], "❓ not shown")}
                      for s in sorted(skills, key=lambda s: (rank[match.category_of(s)], -(s.get("weight") or 3)))],
                     hide_index=True, width="stretch")
        extra_info = [("Soft skills", analysis.get("soft_skills")),
                      ("Education", analysis.get("education_requirements")),
                      ("Experience", analysis.get("experience_requirements")),
                      ("Main responsibilities", analysis.get("responsibilities"))]
        for label, values in extra_info:
            if values:
                st.markdown(f"**{label}:** " + "; ".join(values))
    skill_names = {skill_key(s["skill"]): s["skill"] for s in skills}
    strong = [s for s in skills if s["group"] == "A"]
    transferable = [s for s in skills if s["group"] == "B"]
    to_confirm = [s for s in skills if s["group"] not in ("A", "B")]
    if strong:
        st.markdown("✅ **In your resume:** " + " ".join(f":green-background[{s['skill']}]" for s in strong))
    strict = content_mode == "Strict"
    if strict:  # an equivalent is shown as itself; the job's term needs the candidate's confirmation
        to_confirm = transferable + to_confirm
    elif transferable:
        st.markdown("🔁 **Transferable:** " + " ".join(f":blue-background[{s['skill']} ← {s['evidence']}]"
                                                      for s in transferable))
    if to_confirm:
        if strict:
            st.markdown(f"❓ **Needs your confirmation** ({len(to_confirm)}) — not shown in your profile. "
                        "**Strong** or **Basic** includes it (Basic: Skills only, never as experience); "
                        "**Learning**, **No** or no answer keeps it out and lists it as a gap.")
            st.caption("🔁 marks requirements where you have an equivalent: that equivalent is shown either way. "
                       "Answers are remembered for future jobs.")
        else:
            st.markdown(f"❓ **Not in your resume yet** ({len(to_confirm)}) — all of them are added to Skills and "
                        "linked to your closest real experience. Optional: mark **No** for any you never want listed.")
            st.caption("Interviewers may ask about every listed skill: the report gives a quick plan for each one "
                       "without a real anchor. Answers are remembered for future jobs.")
        b1, b2, _ = st.columns([1, 1, 2])
        names = [s["skill"] for s in to_confirm]
        b1.button("Mark unanswered as Basic", on_click=answer_unanswered, args=(names, "Basic"), width="stretch")
        b2.button("Mark unanswered as No", on_click=answer_unanswered, args=(names, "No"), width="stretch")
        confirm_widgets(to_confirm, saved_answers)
    else:
        st.success("Every skill in this job is already shown in your resume.")

    answers = collect_answers(saved_answers)
    if remember and answers != saved_answers:
        save_confirmations(answers)
    # This job's skills, plus keywords added later (e.g. from an external checker's report)
    job_keys = {skill_key(s["skill"]) for s in skills} | {skill_key(k) for k in st.session_state.get("job_keywords", [])}
    confirmations = {k: v for k, v in answers.items() if k in job_keys}
    unanswered = [s["skill"] for s in to_confirm if skill_key(s["skill"]) not in confirmations]
else:
    title = main.clean_title(job_title)

# --------------------------------------------------------------------------- #
# Shared context for building and improving
# --------------------------------------------------------------------------- #
if st.session_state.get("template") not in render.TEMPLATES:
    st.session_state["template"] = next(iter(render.TEMPLATES))
template = st.session_state["template"]
meta = render.TEMPLATES[template]
accent = st.session_state.get(f"accent_{template}") or meta["accent"]
page = st.session_state.get("page", "A4")
font = st.session_state.get("font") or saved.get("font", "")
font = font if font in render.FONTS else next(iter(render.FONTS))
size = st.session_state.get("text_size", "Normal")
look = (template, accent, page, font, size)
target = st.session_state.get("target", 95)
max_rounds = st.session_state.get("max_rounds", 3)
job_skills = [s["skill"] for s in analysis["skills"]] if analysis else []
job_skills += [k for k in st.session_state.get("job_keywords", []) if k not in job_skills and k != title]
page_option = st.session_state.get("page_option") or "Auto"
if page_option == "Auto":
    entry = analysis and analysis.get("seniority_level") in ("Entry-level", "Junior")
    page_limit = 1 if entry else 2
else:
    page_limit = int(page_option[0])
target_settings["page_limit"] = page_limit
ctx = {"page_limit": page_limit,"template": template, "accent": accent, "page": page, "font": font, "size": size, "job_title": title,
       "master": master, "job_text": job_text, "settings": target_settings, "analysis": analysis,
       "confirmations": confirmations, "job_skills": job_skills,
       "keywords": list(dict.fromkeys(job_skills + ([title] if title else []))),
       "target": target, "max_rounds": max_rounds}

# --------------------------------------------------------------------------- #
# 4. Build
# --------------------------------------------------------------------------- #
auto_build = st.session_state.pop("auto_build", False)  # set once, right after an analysis
if analysis:
    missing_bits = ["the job type"] if not job_type else []  # skill answers are optional
    can_build = not missing_bits and claude_ready
    if missing_bits:
        st.warning("To build your resume, choose " + " and ".join(missing_bits) + ".")
    if engine == ENGINE_MANUAL:
        if can_build:
            with st.expander("✨ Build with claude.ai (copy-paste)", expanded=not versions()):
                reply = manual_step(main.manual_prompt(*main.tailor_request(
                    master, job_text, title, target_settings, analysis, confirmations)),
                    "tailor_reply", "✨ Create my resume")
                if reply is not None:
                    try:
                        result = main.parse_claude_reply(reply)
                        start_versions(polish(result, ctx), "Built with claude.ai", title, ctx["keywords"])
                        remember_master()
                        st.rerun()
                    except main.TailorError as e:
                        st.error(str(e))
    else:
        clicked = st.button(f"✨ Build my resume and optimize to {target}%", type="primary", width="stretch",
                            disabled=not can_build)
        if clicked or (auto_build and can_build and not unanswered):  # new skills: let them be seen first
            build_resume(ctx)

if not versions():
    st.stop()

# --------------------------------------------------------------------------- #
# Result: score bar
# --------------------------------------------------------------------------- #
vs = versions()
current = vs[-1]
result = current["result"]
resume = result["resume"]
resume_json = as_json(resume)
job_keywords = st.session_state.get("job_keywords", [])
filename = main.safe_filename(st.session_state.get("filename", "")) or "resume"

try:
    with st.spinner("Rendering and checking…"):
        pdf_bytes, docx_bytes, pages = make_files(resume_json, *look)
        report = ats_check(resume_json, *look, json.dumps(job_keywords), title, job_text, page_limit)
except render.RenderError as e:
    st.error(str(e))
    st.stop()
current["score"] = report.score
previous = vs[-2]["score"] if len(vs) > 1 else None
match_before = ats.keyword_coverage(master, job_keywords)
match_after = round(100 * len(report.matched) / len(job_keywords)) if job_keywords else 0
fit = match.job_match(resume, analysis, title, st.session_state.get("job_keywords", []), master, confirmations)
fit_master = match.job_match({**main.normalize({}, main.RESUME_SCHEMA), "summary": master}, analysis, title,
                             st.session_state.get("job_keywords", []))
issues = gate_for(resume, ctx, len(pages))
serious = [i for i in issues if i.severity == quality.SERIOUS]
gate_ok = not serious or st.session_state.get(f"gate_ack_{len(vs)}", False)

st.divider()
if notice := st.session_state.pop("notice", None):
    st.success(notice)
score_col, match_col, words_col, actions_col = st.columns([1, 1, 1, 1.6])
score_col.metric("ATS score (built-in)", f"{report.score}/100",
                 delta=None if previous is None else report.score - previous, help=match.DISCLAIMER)
match_col.metric("Job match (estimate)", f"{fit.overall}/100", delta=f"{fit.overall - fit_master.overall:+d} vs profile",
                 help="Requirements covered, weighted by importance (essential counts most), with synonyms. "
                      f"Exact-wording keyword match: {match_after}% (profile: {match_before}%). " + match.DISCLAIMER)
words_col.metric("Words · version", f"{report.words} · v{len(vs)}", help=current["note"])
with actions_col:
    st.download_button(f"⬇️ Download PDF · {meta['label']}", pdf_bytes, f"{filename}.pdf",
                       mime="application/pdf", type="primary", width="stretch", disabled=not gate_ok)
    if len(vs) > 1:
        st.button(f"↩️ Undo — back to v{len(vs) - 1}", on_click=undo, width="stretch")
st.caption("Scores are this app's own estimates, not an employer's ATS result. Accuracy comes first: "
           "the quality gate checks every claim against your profile.")
if serious:
    with st.container(border=True):
        st.error(f"🛑 Quality gate: {len(serious)} serious issue{'s' if len(serious) != 1 else ''} to review "
                 "before export (details in the **Review & evidence** tab):")
        for i in serious[:6]:
            st.markdown(f"- **{i.area}** — {i.message}")
        st.checkbox("I've reviewed these and the resume is accurate: allow download", key=f"gate_ack_{len(vs)}")
        st.caption("Or fix them: 🚀 Auto-improve sends these issues to Claude first, or edit in ✏️ Edit text.")
elif issues:
    st.warning(f"🔎 {len(issues)} suggestion{'s' if len(issues) != 1 else ''} to review before you send it — "
               "see the **Review & evidence** tab.")

tab_improve, tab_report, tab_recruiter, tab_design, tab_cover, tab_edit, tab_save = st.tabs(
    ["📈 Improve ATS score", "🧾 Review & evidence", "👔 Recruiter view", "🎨 Design & download",
     "✉️ Cover letter", "✏️ Edit text", "💾 Save application"])

# --------------------------------------------------------------------------- #
# Tab: Improve
# --------------------------------------------------------------------------- #
with tab_improve:
    checklist_col, keywords_col = st.columns([1.1, 1], gap="large")
    with checklist_col:
        for group in ("Structure", "Content", "Job match"):
            items = [c for c in report.checks if c.group == group]
            if not items:
                continue
            st.markdown(f"**{group}**")
            for c in items:
                if c.ok:
                    st.markdown(f"✅ {c.name}")
                else:
                    st.markdown(f"❌ **{c.name}** — {c.detail}  \n"
                                f"<span style='color:gray;font-size:0.9em'>Fix: {c.fix}</span>",
                                unsafe_allow_html=True)
        with st.expander("📋 Resume tips"):
            st.markdown(TIPS)
        if not meta["ats"]:
            st.warning(f"**{meta['label']}** is a design-focused template; for ATS checks switch to an "
                       "✅ ATS-friendly one in *Design & download*.")

    with keywords_col:
        st.markdown("**Job keywords**")
        if job_keywords:
            tag = lambda k: k.replace("[", "(").replace("]", ")")  # noqa: E731 — keep markdown intact
            if report.matched:
                st.markdown(" ".join(f":green-background[{tag(k)}]" for k in report.matched))
            if report.missing:
                st.markdown(" ".join(f":red-background[{tag(k)}]" for k in report.missing))
            st.caption(f"{len(report.matched)} of {len(job_keywords)} found in the exact wording. Red ones are "
                       "usually requirements your profile doesn't show (Strict mode keeps them out) or skills you "
                       "marked No. The 🧾 Review & evidence tab also counts synonyms.")
        # Keywords added later (e.g. from an external report) that you haven't answered yet
        extra = [{"skill": k} for k in report.missing if skill_key(k) not in skill_names
                 and skill_key(k) not in saved_answers and skill_key(k) != skill_key(title)]
        if extra:
            with st.expander(f"❓ New keywords to answer ({len(extra)})"):
                confirm_widgets(extra, saved_answers)
                for e in extra:
                    skill_names.setdefault(skill_key(e["skill"]), e["skill"])
                answers = collect_answers(saved_answers)
                if remember and answers != saved_answers:
                    save_confirmations(answers)
                confirmations.update({skill_key(e["skill"]): answers[skill_key(e["skill"])] for e in extra
                                      if skill_key(e["skill"]) in answers})
                ctx["new_answers"] = any(skill_key(e["skill"]) in answers for e in extra)

    st.divider()
    st.markdown("**Your ATS checker scored it lower?** Paste its report (select all on its results page, copy, "
                "paste). Missing keywords you have are added; the rest stay listed as gaps.")
    ext_report = st.text_area("External ATS checker report (optional)", height=100, key=f"ext_report_{len(vs)}",
                              label_visibility="collapsed",
                              placeholder="Paste the Jobscan / Resume Worded / Enhancv report here").strip()

    if engine == ENGINE_MANUAL:
        st.markdown("**Improve with claude.ai**")
        report_text = report.as_text() + (f"\n\nReport from an external ATS checker:\n{ext_report}" if ext_report else "")
        progress = ", ".join(f"v{i + 1}: {v['score']}" for i, v in enumerate(vs) if v["score"] is not None)
        reply = manual_step(main.manual_prompt(*main.revision_request(
            master, job_text, resume, report_text, title, target_settings, analysis, confirmations, progress)),
            f"revision_reply_{len(vs)}", f"🔁 Create version {len(vs) + 1}")
        if reply is not None:
            try:
                new = main.parse_claude_reply(reply)
                if ext_report:
                    st.session_state["job_keywords"] = list(dict.fromkeys(job_keywords + new["job_keywords"]))
                add_version(polish(new, ctx), "Revised with claude.ai")
                st.rerun()
            except main.TailorError as e:
                st.error(str(e))
    else:
        go_col, target_col, rounds_col = st.columns([1.4, 1, 1])
        target = target_col.select_slider("Target score", [70, 75, 80, 85, 90, 95, 100], value=target, key="target")
        max_rounds = rounds_col.select_slider("Max rounds", [1, 2, 3, 4, 5], value=max_rounds, key="max_rounds")
        go_col.markdown("<div style='height:1.7rem'></div>", unsafe_allow_html=True)
        if go_col.button("🚀 Auto-improve", type="primary", width="stretch",
                         help="Check → Claude revises → re-check, until the target or no further gain."):
            auto_improve(target, max_rounds, ext_report, ctx)

    if len(vs) > 1:
        st.markdown("**History**")
        scores = {f"v{i + 1}": v["score"] for i, v in enumerate(vs) if v["score"] is not None}
        if len(scores) > 1:
            st.line_chart({"ATS score": scores}, height=160)
        st.dataframe([{"Version": f"v{i + 1}", "Score": v["score"], "What changed": v["note"]}
                      for i, v in enumerate(vs)], hide_index=True, width="stretch")

# --------------------------------------------------------------------------- #
# Tab: ATS report (Part B — stays in the app, never exported)
# --------------------------------------------------------------------------- #
with tab_report:
    part_b = result["report"]
    st.caption("This report stays in the app. Only the resume itself is exported. " + match.DISCLAIMER)
    st.markdown("**🔎 Quality gate**")
    if issues:
        for i in issues:
            st.markdown(f"{'🛑' if i.severity == quality.SERIOUS else '⚠️'} **{i.area}** — {i.message}")
    else:
        st.markdown("✅ Nothing to review: every skill and bullet traces to your profile, dates are consistent, no "
                    "duplicate bullets, skills or employers, no inflated years or invented numbers, no keyword "
                    "stuffing or buzzwords, contact details present, all sections kept, and it fits "
                    f"{page_limit} page{'s' if page_limit != 1 else ''}.")
    if result.get("fixes"):
        with st.expander(f"🧹 Fixed automatically in this version ({len(result['fixes'])})"):
            for note in result["fixes"]:
                st.markdown(f"- {note}")

    st.markdown("**🎯 Job match**")
    cols = st.columns(len(fit.coverage) + 1)
    cols[0].metric("Overall estimate", f"{fit.overall}/100", help="Weighted by importance; title counts 15%.")
    for col, (cat, pct) in zip(cols[1:], fit.coverage.items()):
        found, total = fit.counts[cat]
        col.metric(f"{match.CATEGORY_LABEL[cat]} requirements", f"{found}/{total}", help=f"{pct}% by weight")
    st.caption(f"Job title in headline/summary: {'✅' if fit.title_match else '❌'} · requirements found under any "
               f"name: {fit.synonym_pct}% · in the posting's exact wording: {fit.exact_pct}% (some ATS only match "
               f"exact words) · job type & location line: {job_type or 'not set'} — "
               f"{resume.get('availability') or part_b['location_line_used'] or 'none (local job)'}")
    if fit.missing:
        status_text = {"verified": "in your profile — not used yet", "reworded": "in your profile in other words",
                       "confirmed": "you confirmed it — not used yet",
                       "needs confirmation": "not in your profile — confirm it in step 3 if true",
                       "unsupported": "you're learning it — reported as a gap", "excluded": "you said No"}
        st.markdown("**Not shown in this resume:**")
        for r in sorted(fit.missing, key=lambda r: (r.category != "essential", -r.weight)):
            st.markdown(f"- **{r.skill}** · {match.CATEGORY_LABEL[r.category]} · "
                        f"{status_text.get(r.status, r.status)}")

    st.markdown("**🧾 Evidence: where each claim comes from**")
    skill_rows = evidence.skill_evidence(review.skill_items(resume), master, confirmations)
    icons = {"verified": "✅ in profile", "reworded": "🔁 in profile, other words", "confirmed": "☑️ you confirmed",
             "needs confirmation": "❓ not backed",
             "unsupported": "⚠️ learning only", "excluded": "🛑 you said No"}
    with st.expander(f"Skills ({sum(r['status'] in evidence.BACKED for r in skill_rows)} of "
                     f"{len(skill_rows)} backed)"):
        st.dataframe([{"Skill": r["skill"], "Category": r["category"], "Evidence": icons[r["status"]]}
                      for r in skill_rows], hide_index=True, width="stretch")
    trace = evidence.trace_bullets(resume, master, job_skills, confirmations)
    traced = sum(r["status"] == "verified" for r in trace)
    with st.expander(f"Summary and bullets ({traced} of {len(trace)} closely match a line in your profile)"):
        st.caption("Support = share of the bullet's words found in the closest profile line. Rewording is "
                   "normal; ❓ rows have little in common with your profile, so check they're accurate.")
        st.dataframe([{"": {"verified": "✅", "reworded": "🔁", "not found": "❓"}[r["status"]]
                       + (" ⚠️" if r["unsupported_terms"] else ""),
                       "Where": r["where"], "Resume text": r["text"], "Support": f"{r['support']:.0%}",
                       "Closest profile line": r["source"]} for r in trace], hide_index=True, width="stretch")

    groups = {g: [s for s in part_b["skill_groups"] if s["group"].strip().upper()[:1] == g] for g in "ABCD"}
    st.markdown("**Claude's report**")
    names = {"A": "A · In your profile", "B": "B · Equivalent shown", "C": "C · Listed, no anchor",
             "D": "D · Left out (gap)"}
    for col, g in zip(st.columns(4), "ABCD"):
        col.markdown(f"**{names[g]}** ({len(groups[g])})")
        for s in groups[g]:
            col.markdown(f"- {s['skill']}" + (f" <span style='color:gray'>— {s['note']}</span>" if s["note"] else ""),
                         unsafe_allow_html=True)

    if part_b["keywords_covered"]:
        with st.expander(f"Keywords covered ({len(part_b['keywords_covered'])}) and where they appear"):
            st.dataframe(part_b["keywords_covered"], hide_index=True, width="stretch")
    if part_b["gaps"]:
        st.markdown("**Gaps & quick wins**")
        for g in part_b["gaps"]:
            st.markdown(f"- **{g['skill']}** — {g['plan']}")
    if part_b["interview_prep"]:
        st.markdown("**Interview prep**")
        for q in part_b["interview_prep"]:
            st.markdown(f"- **{q['skill']}** — {q['talking_point']}")
    if part_b["cover_letter_angle"]:
        st.markdown(f"**Cover letter angle:** {part_b['cover_letter_angle']}")

# --------------------------------------------------------------------------- #
# Tab: Recruiter view (simulated recruiter + hiring manager)
# --------------------------------------------------------------------------- #
with tab_recruiter:
    st.caption("A simulated recruiter and hiring manager read this version for this job. It's a second opinion "
               "to improve the resume, not an employer's decision.")
    rec = st.session_state.get("recruiter")
    if engine == ENGINE_MANUAL:
        reply = manual_step(main.manual_prompt(*main.recruiter_request(resume, job_text, title, analysis),
                                               main.RECRUITER_SCHEMA), "recruiter_reply", "👔 Read the review")
        if reply is not None:
            try:
                st.session_state["recruiter"] = {"version": len(vs), "review": main.parse_recruiter(reply)}
                st.rerun()
            except main.TailorError as e:
                st.error(str(e))
    elif st.button("👔 Review as a recruiter" if not rec else "🔁 Review this version again", type="primary",
                   width="stretch", disabled=not claude_ready):
        out = run_claude("👔 A recruiter and hiring manager are reading your resume…",
                         *main.recruiter_request(resume, job_text, title, analysis), main.RECRUITER_SCHEMA)
        if out:
            st.session_state["recruiter"] = {"version": len(vs), "review": out[0]}
            st.rerun()
    if rec:
        r = rec["review"]
        if rec["version"] != len(vs):
            st.info(f"This review is of version {rec['version']}; you're on version {len(vs)}.")
        fit_icon = {"strong": "🟢", "promising": "🟡", "borderline": "🟠", "weak": "🔴"}.get(r["fit"].lower(), "⚪")
        st.markdown(f"**Fit for this job (simulated):** {fit_icon} {r['fit'].title() or '—'}")
        st.markdown(f"**First impression:** {r['first_impression']}")
        st.markdown(f"**Positioning:** {r['positioning']}")
        if r["ratings"]:
            st.dataframe([{"Criterion": x["criterion"], "Rating": "★" * max(0, min(5, x["rating"])) +
                           "☆" * (5 - max(0, min(5, x["rating"]))), "Comment": x["comment"]} for x in r["ratings"]],
                         hide_index=True, width="stretch")
        left, right = st.columns(2, gap="large")
        for col, label, key in ((left, "💪 Strongest selling points", "selling_points"),
                                (right, "🪫 Weakest areas", "weak_areas"),
                                (left, "🔍 Missing evidence", "missing_evidence"),
                                (right, "🚩 Possible rejection reasons", "rejection_risks"),
                                (left, "🛠️ Recommended improvements", "improvements"),
                                (right, "❔ Interview questions to expect", "interview_questions"),
                                (left, "✍️ Language issues", "language_issues")):
            if r[key]:
                col.markdown(f"**{label}**\n" + "\n".join(f"- {x}" for x in r[key]))
        if r["hiring_manager_view"]:
            st.markdown(f"**Hiring manager's view:** {r['hiring_manager_view']}")
        if r["improvements"] and engine != ENGINE_MANUAL and rec["version"] == len(vs):
            if st.button("🛠️ Apply these improvements (truthfully)", width="stretch",
                         help="Claude revises the resume with these notes; anything that would need an invented "
                              "fact is skipped and listed as a gap."):
                notes = "Recruiter review notes:\n" + "\n".join(f"- {x}" for x in r["improvements"] +
                                                                  r["language_issues"])
                auto_improve(100, 1, notes, ctx)

# --------------------------------------------------------------------------- #
# Tab: Design & download
# --------------------------------------------------------------------------- #
with tab_design:
    try:
        with st.spinner("Rendering all designs…"):
            gallery = make_gallery(as_json(vs[0]["result"]["resume"]), page)  # once per build
    except render.RenderError as e:
        st.error(str(e))
        gallery = {}
    st.caption("Thumbnails show your first draft · the preview on the right is always the current version.")
    items = list(render.TEMPLATES.items())
    for row in range(0, len(items), 4):
        for col, (tid, info) in zip(st.columns(4), items[row:row + 4]):
            with col, st.container(border=True):
                if tid in gallery:
                    st.image(gallery[tid][0], width="stretch")
                st.button(("✓ " if tid == template else "") + info["label"], key=f"pick_{tid}",
                          type="primary" if tid == template else "secondary", width="stretch",
                          on_click=pick_template, args=(tid,))
                st.caption(f"{info['about']} · " + ("✅ ATS-friendly" if info["ats"] else "🎨 design-focused"))

    st.divider()
    controls, preview = st.columns([1, 1.5], gap="large")
    with controls:
        st.markdown(f"**{meta['label']}**")
        st.color_picker("Accent color", meta["accent"], key=f"accent_{template}")
        font_col, size_col = st.columns(2)
        font_col.selectbox("Font", list(render.FONTS), index=list(render.FONTS).index(font), key="font",
                           help="All of these are standard fonts that ATS parsers and Word read reliably.")
        size_col.radio("Text size", list(render.TEXT_SIZES), index=list(render.TEXT_SIZES).index(size),
                       horizontal=True, key="text_size", help="Small helps fit one page.")
        st.radio("Page size", list(render.PAGE_SIZES), horizontal=True, key="page")
        st.text_input("File name", key="filename")
        if not gate_ok:
            st.warning("Review the quality gate's serious issues (above) to enable downloads.")
        st.download_button("⬇️ Download PDF", pdf_bytes, f"{filename}.pdf", mime="application/pdf",
                           type="primary", width="stretch", key="dl_pdf", disabled=not gate_ok)
        st.download_button("⬇️ Download Word (.docx)", docx_bytes, f"{filename}.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           width="stretch", disabled=not gate_ok)
        st.download_button("⬇️ Download .json", resume_json, f"{filename}.json",
                           mime="application/json", width="stretch", disabled=not gate_ok)
        st.caption("The Word file is a simplified single-column version of the design, for easy editing.")
        if out_dir and st.button(f"💾 Save PDF + Word to {out_dir}", width="stretch", disabled=not gate_ok):
            try:
                folder = Path(out_dir)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{filename}.pdf").write_bytes(pdf_bytes)
                (folder / f"{filename}.docx").write_bytes(docx_bytes)
                st.success(f"Saved to {folder}")
            except OSError as e:
                st.warning(f"Could not save to {out_dir}: {e}")
    with preview:
        st.caption(f"Preview — {len(pages)} page{'s' if len(pages) != 1 else ''}")
        for png in pages:
            st.image(png, width="stretch")

# --------------------------------------------------------------------------- #
# Tab: Cover letter
# --------------------------------------------------------------------------- #
with tab_cover:
    st.caption("A cover letter for this job, written from your current resume version, in the same design.")
    letter = st.session_state.get("cover_letter")
    angle = result["report"]["cover_letter_angle"]
    if engine == ENGINE_MANUAL:
        reply = manual_step(main.build_cover_letter_prompt(resume, job_text, title, angle), "cover_reply",
                            "✉️ Create cover letter")
        if reply is not None:
            try:
                st.session_state["cover_letter"] = main.parse_cover_letter(reply)
                st.rerun()
            except main.TailorError as e:
                st.error(str(e))
    elif st.button("✉️ Write my cover letter" if not letter else "🔁 Write a new cover letter",
                   type="primary", width="stretch", disabled=not job_text):
        out = run_claude("✉️ Claude is writing your cover letter…",
                         *main.cover_letter_request(resume, job_text, title, angle), main.COVER_LETTER_SCHEMA)
        if out:
            st.session_state["cover_letter"] = out[0]
            st.rerun()

    if letter:
        body = "\n\n".join(x for x in [letter["greeting"], *letter["paragraphs"]] if x)
        edited = st.text_area("Edit the letter (blank line between paragraphs)", body, height=320,
                              key=f"cover_edit_{hash(body)}")
        parts = [x.strip() for x in re.split(r"\n\s*\n", edited) if x.strip()]
        if parts and parts[0].lower().startswith("dear "):
            letter = {**letter, "greeting": parts[0], "paragraphs": parts[1:]}
        else:
            letter = {**letter, "greeting": "", "paragraphs": parts}
        st.caption(f"{sum(len(p.split()) for p in letter['paragraphs'])} words · most recruiters like 200-350.")
        try:
            cover_pdf, cover_docx, cover_pages = make_cover(resume_json, json.dumps(letter), *look)
        except render.RenderError as e:
            st.error(str(e))
        else:
            pdf_col, docx_col = st.columns(2)
            pdf_col.download_button("⬇️ Download cover letter PDF", cover_pdf, f"{filename}-Cover-Letter.pdf",
                                    mime="application/pdf", type="primary", width="stretch")
            docx_col.download_button("⬇️ Download cover letter Word", cover_docx,
                                     f"{filename}-Cover-Letter.docx", width="stretch",
                                     mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            for png in cover_pages:
                st.image(png, width="stretch")

# --------------------------------------------------------------------------- #
# Tab: Edit
# --------------------------------------------------------------------------- #
with tab_edit:
    st.caption("Change any wording, then Apply. Your edit becomes a new version; Undo restores the previous one.")
    edited = st.text_area("Resume (JSON)", resume_json, height=420, key=f"edit_{len(vs)}",
                          label_visibility="collapsed")
    if st.button("Apply edits", width="stretch"):
        try:
            add_version({**result, "resume": main.parse_claude_reply(edited)["resume"]}, "Edited by hand")
            st.rerun()
        except main.TailorError as e:
            st.error(str(e))

# --------------------------------------------------------------------------- #
# Tab: Save application
# --------------------------------------------------------------------------- #
with tab_save:
    st.caption("Saves this job with the exact resume version you'll send (PDF + Word), the job description, a "
               "snapshot of your profile, every version and the reports. Your master profile is not changed. "
               "Find it later under 💼 My applications.")
    app_id = st.session_state.get("application_id")
    st.session_state.setdefault("app_company", analysis["company"] if analysis else "")
    st.session_state.setdefault("app_date", date.today())
    c1, c2, c3 = st.columns([2, 1, 1])
    c1.text_input("Company", key="app_company")
    c2.selectbox("Status", store.STATUSES, key="app_status")
    c3.date_input("Application date", key="app_date")
    st.text_area("Notes", key="app_notes", height=80, placeholder="Recruiter name, referral, follow-up date…")
    if serious and not gate_ok:
        st.warning("The quality gate still has serious issues; the saved record lists them.")
    if st.button("💾 Update this application" if app_id else "💾 Save application", type="primary",
                 width="stretch"):
        rec = st.session_state.get("recruiter")
        record = {
            "id": app_id, "company": st.session_state["app_company"].strip(), "job_title": title,
            "status": st.session_state.get("app_status", "Draft"),
            "applied_date": str(st.session_state.get("app_date") or ""),
            "notes": st.session_state.get("app_notes", ""), "job_text": job_text, "master": master,
            "analysis": analysis, "settings": target_settings, "confirmations": confirmations,
            "job_keywords": st.session_state.get("job_keywords", []), "versions": vs,
            "submitted_version": len(vs), "resume": resume, "filename": filename,
            "look": {"template": template, "accent": accent, "page": page, "font": font, "size": size},
            "ats_score": report.score, "match_score": fit.overall,
            "match_missing": [f"{r.skill} ({match.CATEGORY_LABEL[r.category]})" for r in fit.missing],
            "quality": [{"severity": i.severity, "area": i.area, "message": i.message} for i in issues],
            "recruiter": rec["review"] if rec and rec["version"] == len(vs) else None,
            "cover_letter": st.session_state.get("cover_letter"),
        }
        try:
            st.session_state["application_id"] = store.save_application(DATA_DIR, record, pdf_bytes, docx_bytes)
        except (OSError, store.StoreError) as e:
            st.error(f"Could not save the application: {e}")
        else:
            st.success("Saved. Open it any time under 💼 My applications.")

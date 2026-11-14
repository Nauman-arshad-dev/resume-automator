r"""
Resume Tailor — local web UI.

Run from the VS Code terminal:
    .\.venv\Scripts\Activate.ps1
    streamlit run app.py

Flow: 1 your profile (once) → 2 the job → Create → 3 questions (only if needed) → Build →
4 check & download: the source guard (guard.py) fixes and checks every line against the
profile and saved answers; flagged items are removed or kept before the download unlocks.
Then the recruiter view, cover letter and saving the application with its exact resume.
Claude runs through Claude Code (your Claude subscription), the Anthropic API, or
copy-paste with claude.ai. Every resume is built from the master profile, which a
job-specific resume never changes.
"""

import hashlib
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
import guard
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
            "application_id", "app_company", "app_status", "app_notes", "app_date", "page_option",
            "job_draft", "job_details_saved", "versions_job", "approved"}
# Kept in data/workspace.json so a browser refresh or app restart keeps the job in progress.
WORKSPACE_KEYS = {"job_text", "job_title", "job_type", "location", "visa", "must_include", "must_exclude",
                  "page_option", "analysis", "analysis_for", "versions", "job_keywords", "cover_letter",
                  "recruiter", "filename", "application_id", "app_company", "app_status", "app_notes",
                  "versions_job", "approved"}
JOB_KEY_PREFIXES = ("pq_", "confirm_", "ext_report_", "revision_reply_", "edit_", "cover_edit_", "kw_", "gate_ack_")
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


def job_fingerprint(master: str, job_text: str, job_title: str) -> str:
    """Stable across app restarts (unlike hash()), so a restored analysis still matches its job."""
    return hashlib.sha256("|".join((master, job_text, job_title)).encode("utf-8")).hexdigest()[:20]


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
    st.session_state["versions_job"] = job_fingerprint("", st.session_state.get("job_text", "").strip(),
                                                       st.session_state.get("job_title", "").strip())
    st.session_state.pop("approved", None)
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
    store.clear_workspace(DATA_DIR)
    st.session_state["notice_top"] = ("Ready for a new job. Your profile, saved answers and skill answers are "
                                      "kept.")


def reset_everything() -> None:
    """Forget everything: resume, job, answers, results, job settings. Claude connection settings and
    saved applications stay."""
    st.session_state.clear()
    st.session_state["workspace_restored"] = True  # nothing to restore: the workspace is deleted below
    st.cache_data.clear()
    store.clear_workspace(DATA_DIR)
    MASTER_FILE.unlink(missing_ok=True)
    CONFIRM_FILE.unlink(missing_ok=True)
    store.clear_answers(DATA_DIR)
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
                                      main.content_mode(ctx["settings"]), ctx["job_title"])
    resume, guarded = guard.enforce(resume, ctx["master"], ctx["answers"], ctx["confirmations"], ctx["settings"],
                                    ctx["analysis"], ctx["job_text"])
    fixes += guarded
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


def checks_for(resume: dict, ctx: dict, pages: int | None = None) -> list[guard.Item]:
    """Checks 1-7 (guard) plus the other quality checks (dates, contact, duplicates, length)."""
    items = guard.checks(resume, ctx["master"], ctx["answers"], ctx["confirmations"], ctx["settings"],
                         ctx["analysis"], ctx["job_skills"])
    covered = ("Evidence",)  # the guard's checks 2, 5 and 6 cover these
    for i in quality.gate(resume, ctx["master"], ctx["confirmations"], ctx["analysis"], ctx["settings"],
                          ctx["job_skills"], pages, ctx["page_limit"]):
        if i.area in covered or i.message.startswith("Numbers not found") or "location line" in i.message:
            continue
        items.append(guard.Item(0, guard.SERIOUS if i.severity == quality.SERIOUS else guard.WARNING,
                                f"{i.area}: {i.message}"))
    return sorted(items, key=lambda i: (i.severity != guard.SERIOUS, i.check))


def blocking(resume: dict, ctx: dict) -> list[guard.Item]:
    approved = set(st.session_state.get("approved", []))
    return [i for i in checks_for(resume, ctx) if i.severity == guard.SERIOUS and i.id not in approved]


def build_resume(ctx: dict) -> None:
    """Step 2: tailor from the master resume, then auto-improve to the target."""
    out = run_claude("✍️ Claude is writing your tailored resume — usually 1-2 minutes…",
                     *main.tailor_request(ctx["master"], ctx["job_text"], ctx["job_title"], ctx["settings"],
                                          ctx["analysis"], ctx["confirmations"], ctx["answers"]))
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
            serious = blocking(resume, ctx)
            thin = [i for i in checks_for(resume, ctx) if quality.SKILLS_ONLY in i.message]
            first_with_input = n == 1 and bool(ext_report or ctx.get("new_answers"))
            if before.score >= target and not first_with_input and not serious:
                status.write(f"Score {before.score} already meets your target of {target}.")
                break
            status.write(f"**Round {n}** — score {before.score}. Claude is revising…")
            report_text = before.as_text()
            if serious:
                report_text += "\n\nReview findings (fix these first; never by inventing anything):\n" + "\n".join(
                    f"- {i.message} {i.text}".strip() for i in serious)
            if thin:
                report_text += "\n\nAlso: " + thin[0].message + " Use only how the profile describes them."
            if ext_report and n == 1:
                report_text += f"\n\nReport from an external ATS checker or reviewer:\n{ext_report}"
            progress = ", ".join(f"v{i + 1}: {v['score']}" for i, v in enumerate(versions()) if v["score"] is not None)
            try:
                new, usage = engines.run(engine_config(), *main.revision_request(
                    ctx["master"], ctx["job_text"], resume, report_text, ctx["job_title"], ctx["settings"],
                    ctx["analysis"], ctx["confirmations"], progress, ctx["answers"]))
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
            fixed_serious = len(serious) > len(blocking(new["resume"], ctx))
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
        "versions_job": job_fingerprint("", app.get("job_text", "").strip(), app.get("job_title", "").strip()),
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
        st.session_state["analysis_for"] = job_fingerprint(load_master(), app.get("job_text", ""),
                                                           app.get("job_title", ""))
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
    content_mode = "Strict"  # every claim comes from the profile, the saved answers or a confirmation
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


def persist() -> None:
    """Save the job in progress (job, analysis, every version, reports) for the next visit."""
    if not remember:
        return
    state = {k: v for k, v in st.session_state.items() if k in WORKSPACE_KEYS}
    try:
        if state.get("job_text") or state.get("versions"):
            store.save_workspace(DATA_DIR, state)
        else:
            store.clear_workspace(DATA_DIR)
    except OSError:
        pass


if not st.session_state.get("workspace_restored"):  # first run of this browser session
    st.session_state["workspace_restored"] = True
    restored = {k: v for k, v in store.load_workspace(DATA_DIR).items() if k in WORKSPACE_KEYS} if remember else {}
    if restored.get("job_text"):
        for key, value in restored.items():
            st.session_state.setdefault(key, value)
        st.session_state.setdefault("notice_top", "Welcome back: your last job and its resume versions are restored. "
                                                  "Click 🆕 New job to start another.")
if view == VIEW_APPS:
    applications_page()
    st.stop()

# --------------------------------------------------------------------------- #
# Header: new job / reset, and the step tracker
# --------------------------------------------------------------------------- #
title_col, new_col, reset_col = st.columns([4, 1, 1], vertical_alignment="bottom")
title_col.title("📄 Resume Tailor")
new_col.button("🆕 New job", on_click=new_job, width="stretch",
               help="Start the next application. Keeps your profile, your saved answers and your skill answers.")
with reset_col.popover("♻️ Reset", width="stretch"):
    st.markdown("Forget **everything**: your profile and its history, your saved answers, your skill answers, the "
                "job and its results. Saved applications are kept (delete them under 💼 My applications).")
    st.button("Yes, reset everything", on_click=reset_everything, type="primary")
st.caption("Paste a job and click **Create**. Your resume is written only from your profile and your own answers, "
           "then checked line by line before you can download it.")
tracker = st.empty()  # filled once every step's state is known
if notice := st.session_state.pop("notice_top", None):
    st.info(notice)

STEPS = ("Your profile", "The job", "Questions", "Check & download")


def show_tracker(done: list[bool], hints: list[str]) -> None:
    current = next((i for i, d in enumerate(done) if not d), None)
    with tracker.container(border=True):
        for i, (col, name) in enumerate(zip(st.columns(len(STEPS)), STEPS)):
            icon = "✅" if done[i] else "👉" if i == current else "⚪"
            col.markdown(f"{icon} **{i + 1} · {name}**" if i == current else f"{icon} {i + 1} · {name}")
        st.caption("**Next:** " + (hints[current] if current is not None else
                                    "All done. Find this application any time under 💼 My applications."))


def step(number: int, title: str, caption: str = ""):
    """One bordered box per step, so the process reads top to bottom."""
    box = st.container(border=True)
    box.subheader(f"{number} · {title}")
    if caption:
        box.caption(caption)
    return box


UPLOAD_TYPES = ["pdf", "docx", "json", "md", "txt"]
NUMBER = re.compile(r"\d[\d,.]*\d%?|\d%?")


def upload_round() -> int:
    """Changing an uploader's key is the only way to clear it after use."""
    return st.session_state.get("upload_round", 0)


def read_sources(files) -> list[tuple[str, str]]:
    sources = []
    for f in files or []:
        try:
            sources.append((f.name, main.read_upload(f.name, f.getvalue())))
        except main.TailorError as e:
            st.error(f"Could not read {f.name}: {e}")
    return sources


def profile_checks(draft: str, inputs: str) -> list[str]:
    """Numbers Claude added or dropped while merging: the profile must stay exactly as true as its sources."""
    have, got = set(NUMBER.findall(inputs)), set(NUMBER.findall(draft))
    notes = []
    if added := sorted(got - have):
        notes.append("⚠️ Numbers that are **not in your files or answers**: " + ", ".join(added)
                     + ". Remove them unless they are true.")
    if dropped := sorted(have - got):
        notes.append("Numbers from your profile or answers that the new version no longer has: "
                     + ", ".join(dropped[:15]) + ". Add them back if they matter.")
    if len(draft.split()) < 0.6 * len(inputs.split()):
        notes.append("The new profile is much shorter than before; check nothing important was left out.")
    return notes


def merge_profile(current: str, sources: list[tuple[str, str]], notes: str) -> None:
    """Ask Claude to merge files and notes into one profile; a draft until the candidate saves it."""
    system, content = main.profile_request(current, sources, notes)
    inputs = "\n".join([current, notes] + [t for _, t in sources])
    if engine == ENGINE_MANUAL:
        reply = manual_step(main.manual_prompt(system, content, main.PROFILE_SCHEMA), "profile_reply",
                            "🪄 Read the merged profile")
        if reply is None:
            return
        try:
            draft = main.parse_profile(reply)
        except main.TailorError as e:
            st.error(str(e))
            return
    else:
        out = run_claude("🪄 Claude is merging this into your profile…", system, content, main.PROFILE_SCHEMA)
        if not out:
            return
        draft = main.normalize(out[0], main.PROFILE_SCHEMA)
        draft["profile"] = main.clean_profile(draft["profile"])
    draft["checks"] = profile_checks(draft["profile"], inputs)
    st.session_state["profile_draft"] = draft
    st.session_state["upload_round"] = upload_round() + 1
    st.rerun()


def profile_builder(current: str) -> None:
    """Merge more files or notes into the profile with Claude, with a preview before saving."""
    draft = st.session_state.get("profile_draft")
    if draft:
        st.markdown("**Claude's merged profile.** Check it, then save. Nothing changes until you do.")
        for line in draft.get("checks", []):
            st.markdown(line)
        for line in draft["changes"]:
            st.caption("• " + line)
        text = st.text_area("Merged profile", draft["profile"], height=320,
                            key=f"draft_{hashlib.sha256(draft['profile'].encode()).hexdigest()[:12]}")
        save_col, discard_col = st.columns(2)
        if save_col.button("💾 Save this profile", type="primary", width="stretch"):
            save_master(text.strip())
            st.session_state.pop("profile_draft", None)
            st.session_state["notice_top"] = "Profile saved. The previous version is kept in its history."
            st.rerun()
        if discard_col.button("Discard", width="stretch"):
            st.session_state.pop("profile_draft", None)
            st.rerun()
        return
    st.caption("Add your LinkedIn profile (saved as PDF), older resumes or project write-ups. Claude merges them "
               "into one organised profile, keeps every real fact and number and invents nothing. You check the "
               "result before it's saved.")
    files = st.file_uploader("Files", type=UPLOAD_TYPES, accept_multiple_files=True,
                             key=f"more_sources_{upload_round()}")
    notes = st.text_area("New facts (optional)", height=80, key=f"profile_notes_{upload_round()}",
                         placeholder="e.g. Since March 2025 I lead 2 developers at Tracio.")
    if engine == ENGINE_MANUAL or st.button("🪄 Merge into my profile", disabled=engine == ENGINE_CODE and not cli_path):
        merge_profile(current, read_sources(files), notes)


def add_link_to_profile(text: str, link: str) -> str:
    """Put a link on the profile's contact line (the line with the email address)."""
    lines = text.splitlines()
    idx = next((i for i, ln in enumerate(lines[:8]) if "@" in ln), None)
    if idx is None:
        return f"{link}\n{text}"
    lines[idx] = f"{lines[idx].rstrip()} | {link}"
    return "\n".join(lines)


def delete_answer(index: int) -> None:
    answers = store.load_answers(DATA_DIR)
    if 0 <= index < len(answers):
        answers.pop(index)
        store.save_answers(DATA_DIR, answers)


# --------------------------------------------------------------------------- #
# Step 1. Profile
# --------------------------------------------------------------------------- #
saved_profile_answers = store.load_answers(DATA_DIR) if remember else []
with step(1, "Your profile", "Entered once and saved on this laptop. Every resume is built only from it and "
                             "your saved answers; tailoring never changes it."):
    saved_master = load_master() if remember else ""
    master = ""
    if saved_master and not st.session_state.get("replace_master"):
        _, stale_lines = guard.clean_master(saved_master)
        links = guard.master_links(saved_master)
        st.success(f"✅ Profile ready · {len(saved_master.split())} words · {len(saved_profile_answers)} saved "
                   f"answers · {len(saved_answers)} skill answers · links: "
                   + (", ".join(links) if links else "none"))
        if stale_lines:
            st.warning(f"Your profile contains a line from an earlier tailored resume: **\"{stale_lines[0]}\"**. "
                       "The app never uses it, but it's cleaner to remove it.")
            if st.button("🧹 Remove it from my profile"):
                save_master(guard.clean_master(saved_master)[0])
                st.session_state["notice_top"] = "Removed. Every resume gets its location line from the job type."
                st.rerun()
        if not any(re.search(r"github\.com|gitlab\.com|https?://|www\.", ln, re.I) and "linkedin" not in ln.lower()
                   for ln in links):
            link_col, add_col = st.columns([3, 1], vertical_alignment="bottom")
            new_link = link_col.text_input("Your profile has no GitHub or portfolio link. Add one so every resume "
                                           "shows it:", placeholder="github.com/your-name", key="new_link").strip()
            if add_col.button("➕ Add link", disabled=not new_link, width="stretch"):
                save_master(add_link_to_profile(saved_master, new_link))
                st.session_state.pop("new_link", None)
                st.session_state["notice_top"] = f"Added {new_link} to your profile's contact line."
                st.rerun()
        with st.expander("✏️ Manage your profile: edit it, your saved answers, add files, history"):
            t_edit, t_answers, t_files, t_history = st.tabs(["Edit", f"Saved answers ({len(saved_profile_answers)})",
                                                             "🪄 Add from files", "History"])
            with t_edit:
                edited_master = st.text_area("Profile text", saved_master, height=260,
                                             key=f"profile_{hash(saved_master)}",
                                             help="Everything you have done: roles, dates, projects, skills, results "
                                                  "with real numbers, education, certifications, languages, links.")
                if st.button("💾 Save profile", disabled=edited_master.strip() == saved_master.strip()):
                    save_master(edited_master.strip())
                    st.session_state["notice_top"] = "Profile saved. The previous version is kept in its history."
                    st.rerun()
                if st.button("Use a different resume"):
                    st.session_state["replace_master"] = True
                    st.rerun()
            with t_answers:
                st.caption("Facts you gave about your own experience. They are sent with every build and override "
                           "your profile where they disagree, e.g. \"At Syntivis AG I worked solo, directly with "
                           "the CEO\".")
                for i, a in enumerate(saved_profile_answers):
                    q_col, del_col = st.columns([6, 1], vertical_alignment="center")
                    q_col.markdown(f"**{a.get('question') or 'Fact'}**  \n{a['answer']}")
                    del_col.button("🗑️", key=f"del_answer_{i}", on_click=delete_answer, args=(i,),
                                   help="Delete this answer")
                fact = st.text_input("Add a fact or correction", key="new_fact",
                                     placeholder="e.g. I built Tracio for clients and don't know its user numbers")
                if st.button("➕ Save fact", disabled=not fact.strip()):
                    store.add_answers(DATA_DIR, [(fact.strip()[:80], fact)])
                    st.session_state.pop("new_fact", None)
                    st.rerun()
            with t_files:
                profile_builder(saved_master)
            with t_history:
                history = store.profile_history(DATA_DIR)
                if not history:
                    st.caption("No earlier versions yet.")
                else:
                    pick = st.selectbox("Earlier versions", history,
                                        format_func=lambda f: f.stem[:15].replace("-", " "), key="history_pick")
                    if st.button("↩️ Restore this version"):
                        save_master(pick.read_text(encoding="utf-8"))
                        st.session_state["notice_top"] = "Earlier profile version restored."
                        st.rerun()
        master = saved_master
    else:
        uploaded = st.file_uploader("Upload your resume (add your LinkedIn PDF or older resumes too)",
                                    type=UPLOAD_TYPES, accept_multiple_files=True,
                                    key=f"upload_master_{upload_round()}",
                                    help="Text-based PDF or Word files, up to 5 MB each. Scans can't be read.")
        pasted = st.text_area("…or paste it", height=110, key="paste_master").strip()
        sources = read_sources(uploaded)
        master = "\n\n".join([text for _, text in sources] + ([pasted] if pasted else [])).strip()
        if master and remember:  # saved straight away, so a refresh never loses it
            save_master(master)
            st.session_state.pop("paste_master", None)
            st.session_state.update(replace_master=False, upload_round=upload_round() + 1)
            st.session_state["notice_top"] = "Profile saved on this laptop: it stays after a refresh."
            st.rerun()

# What Claude and the checks see: the profile without availability lines from earlier resumes
master_clean = guard.clean_master(master)[0] if master else ""

# --------------------------------------------------------------------------- #
# Step 2. The job
# --------------------------------------------------------------------------- #
claude_ready = engine != ENGINE_CODE or bool(cli_path)
with step(2, "The job", "Paste the whole posting, choose where you'd work, then click Create."):
    job_text = st.text_area("Job description", height=170, placeholder="Paste the full job posting",
                            key="job_text").strip()
    job_type = st.radio("Where would you work?", main.JOB_TYPES, index=None, horizontal=True, key="job_type",
                        help="Sets the line under your contact details for this job only.")
    if job_type == "Remote":
        st.session_state.setdefault("timezone", saved.get("timezone", ""))
        st.text_input("Your time zone", placeholder="UTC+5", key="timezone",
                      help="Shown as: Open to remote work (UTC+5)")
    elif job_type == "Relocation":
        loc_col, visa_col = st.columns(2)
        loc_col.text_input("Relocate to (city, country)", placeholder="the job's location", key="location")
        visa_col.text_input("Visa (optional)", placeholder="the country's usual work visa", key="visa")
    with st.expander("More options: exact job title, length, must include / exclude"):
        job_title = st.text_input("Exact job title", placeholder="Leave empty: taken from the posting",
                                  key="job_title").strip()
        st.radio("Resume length", PAGE_OPTIONS, horizontal=True, key="page_option",
                 help="Auto: 1 page for entry-level and junior jobs, 2 pages otherwise.")
        st.text_input("Must include", placeholder="e.g. my open-source project, German B1", key="must_include")
        st.text_input("Must exclude", placeholder="e.g. my freelance work, PHP", key="must_exclude")
    ready = bool(master and job_text and job_type)
    if not ready:
        st.caption("Needs: " + ", ".join(x for x, ok in (("your profile (step 1)", master),
                                                           ("the job description", job_text),
                                                           ("where you'd work", job_type)) if not ok) + ".")
    create_clicked = engine != ENGINE_MANUAL and st.button(
        "✨ Create my resume", type="primary", width="stretch", disabled=not (ready and claude_ready))

# A resume belongs to one job: when the job changes, the old one is cleared (it is never reused)
job_id = job_fingerprint("", job_text, job_title)
if versions() and st.session_state.get("versions_job") not in (None, job_id):
    for key in ("versions", "recruiter", "cover_letter", "approved", "versions_job"):
        st.session_state.pop(key, None)
    st.session_state["notice_top"] = "The job changed, so the resume for the previous job was cleared."

target_settings = {"job_type": job_type or "", "timezone": st.session_state.get("timezone", ""),
                   "location": st.session_state.get("location", ""), "visa": st.session_state.get("visa", ""),
                   "must_include": st.session_state.get("must_include", ""),
                   "must_exclude": st.session_state.get("must_exclude", ""), "content_mode": content_mode}


def remember_master() -> None:
    if remember and master and master != saved_master:
        save_master(master)
    st.session_state["replace_master"] = False


fingerprint = job_fingerprint(master, job_text, job_title)
analysis = st.session_state.get("analysis") if st.session_state.get("analysis_for") == fingerprint else None


def store_analysis(result: dict) -> None:
    st.session_state["analysis"] = guard.split_requirements(main.normalize(result, main.ANALYSIS_SCHEMA))
    st.session_state["analysis_for"] = fingerprint
    st.session_state["auto_build"] = True  # build straight away if there is nothing to ask
    remember_master()


if create_clicked:
    out = run_claude("🔍 Claude is reading the job and comparing it with your profile…",
                     *main.analysis_request(master_clean, job_text, job_title), main.ANALYSIS_SCHEMA)
    if out:
        store_analysis(out[0])
        st.rerun()
elif engine == ENGINE_MANUAL and ready and not analysis:
    with st.expander("🔍 Analyse the job with claude.ai (copy-paste)", expanded=True):
        reply = manual_step(main.manual_prompt(*main.analysis_request(master_clean, job_text, job_title),
                                               main.ANALYSIS_SCHEMA), "analysis_reply", "🔍 Read the analysis")
        if reply is not None:
            try:
                store_analysis(main.parse_analysis(reply))
                st.rerun()
            except main.TailorError as e:
                st.error(str(e))

title = main.clean_title(job_title or (analysis or {}).get("job_title", ""))


# --------------------------------------------------------------------------- #
# Shared context for building and improving
# --------------------------------------------------------------------------- #
def make_ctx(confirmations: dict) -> dict:
    job_skills = [s["skill"] for s in analysis["skills"]] if analysis else []
    job_skills += [k for k in st.session_state.get("job_keywords", []) if k not in job_skills and k != title
                   and guard.is_skill(k)]
    page_option = st.session_state.get("page_option") or "Auto"
    if page_option == "Auto":
        entry = analysis and analysis.get("seniority_level") in ("Entry-level", "Junior")
        page_limit = 1 if entry else 2
    else:
        page_limit = int(page_option[0])
    settings_now = {**target_settings, "page_limit": page_limit,
                    "availability_line": guard.availability_line(target_settings, analysis)}
    return {"page_limit": page_limit, "template": template, "accent": accent, "page": page, "font": font,
            "size": size, "job_title": title, "master": master_clean, "job_text": job_text,
            "settings": settings_now, "analysis": analysis, "confirmations": confirmations,
            "job_skills": job_skills, "keywords": list(dict.fromkeys(job_skills + ([title] if title else []))),
            "target": st.session_state.get("target", 95), "max_rounds": st.session_state.get("max_rounds", 3),
            "answers": store.load_answers(DATA_DIR) if remember else st.session_state.get("answers_mem", [])}


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


# --------------------------------------------------------------------------- #
# Step 3. Questions: only what this job needs and the profile doesn't say
# --------------------------------------------------------------------------- #
def save_job_answers(questions: list[dict]) -> None:
    pairs = [(q["question"], st.session_state.get(f"pq_{i}") or "") for i, q in enumerate(questions)]
    if not any(a.strip() for _, a in pairs):
        return
    if remember:
        store.add_answers(DATA_DIR, pairs)
    else:
        mem = st.session_state.setdefault("answers_mem", [])
        mem += [{"question": q, "answer": a.strip()} for q, a in pairs if a.strip()]


skill_names: dict[str, str] = {}
confirmations: dict = {}
unanswered: list[str] = []
questions: list[dict] = []
build_clicked = False
answered_before = {a.get("question", "").strip().lower(): a["answer"] for a in saved_profile_answers}
with step(3, "Questions", "Only what this job needs and your profile doesn't say. Skip anything you like: "
                          "skipped skills stay out of the resume."):
    if not analysis:
        st.caption("They appear here after you click ✨ Create my resume.")
    else:
        info = [f"**{title}**"] + [x for x in (analysis["company"], analysis["job_location"],
                                               analysis.get("seniority_level")) if x]
        st.markdown(" · ".join(info))
        skills = analysis["skills"]
        skill_names = {skill_key(s["skill"]): s["skill"] for s in skills}
        shown = [s for s in skills if s["group"] == "A"]
        to_confirm = [s for s in skills if s["group"] != "A"]
        if shown:
            st.markdown(f"✅ **Your profile shows {len(shown)} of {len(skills)} requirements:** "
                        + " ".join(f":green-background[{s['skill']}]" for s in shown))
        if analysis.get("not_skills"):
            st.caption("Not treated as skills (the job ad's work areas or hiring steps): "
                       + ", ".join(analysis["not_skills"]) + ".")
        if to_confirm:
            st.markdown(f"**Do you have these?** ({len(to_confirm)})")
            st.caption("**Strong** = used it for real work · **Basic** = know it a little (listed under *Working "
                       "knowledge*, never as experience) · **Learning** / **No** / no answer = left out. "
                       "Remembered for future jobs.")
            confirm_widgets(to_confirm, saved_answers)
        answers = collect_answers(saved_answers)
        if remember and answers != saved_answers:
            save_confirmations(answers)
        job_keys = {skill_key(s["skill"]) for s in skills} | {skill_key(k) for k in
                                                                st.session_state.get("job_keywords", [])}
        confirmations = {k: v for k, v in answers.items() if k in job_keys}
        unanswered = [s["skill"] for s in to_confirm if skill_key(s["skill"]) not in confirmations]

        questions = analysis.get("profile_questions") or []
        if questions:
            st.markdown("**Details that make your real experience count** (optional)")
            st.caption("Real facts only: numbers, scope, what you did yourself. Saved with your profile, sent with "
                       "every build, and they override your profile where they disagree.")
            for i, q in enumerate(questions):
                st.session_state.setdefault(f"pq_{i}", answered_before.get(q["question"].strip().lower(), ""))
                st.text_input(q["question"], key=f"pq_{i}",
                              placeholder=f"e.g. {q['example']}" if q.get("example") else "Your answer (optional)",
                              help=f"Supports: {q['requirement']}" if q.get("requirement") else None)
        if not to_confirm and not questions:
            st.success("✅ Nothing to ask: your profile covers this job.")
        if engine != ENGINE_MANUAL:
            build_clicked = st.button("🔁 Build again with my answers" if versions() else "✨ Build my resume",
                                      type="secondary" if versions() else "primary", width="stretch",
                                      disabled=not (job_type and claude_ready))

ctx = make_ctx(confirmations)
nothing_to_ask = bool(analysis) and not unanswered and all(
    q["question"].strip().lower() in answered_before for q in questions)
if analysis and engine == ENGINE_MANUAL and job_type:
    with st.expander("✨ Build with claude.ai (copy-paste)", expanded=not versions()):
        save_job_answers(questions)
        ctx = make_ctx(confirmations)
        reply = manual_step(main.manual_prompt(*main.tailor_request(
            master_clean, job_text, title, ctx["settings"], analysis, confirmations, ctx["answers"])),
            "tailor_reply", "✨ Create my resume")
        if reply is not None:
            try:
                start_versions(polish(main.parse_claude_reply(reply), ctx), "Built with claude.ai", title,
                               ctx["keywords"])
                remember_master()
                st.rerun()
            except main.TailorError as e:
                st.error(str(e))
auto_build = st.session_state.pop("auto_build", False)  # set once, right after an analysis
if build_clicked or (auto_build and nothing_to_ask and job_type and claude_ready and engine != ENGINE_MANUAL):
    save_job_answers(questions)
    build_resume(make_ctx(confirmations))

done = [bool(master), bool(analysis), bool(analysis) and (bool(versions()) or nothing_to_ask), bool(versions())
        and bool(st.session_state.get("application_id"))]
hints = ["upload your resume (PDF or Word) or paste it in step 1.",
         "paste the job, choose where you'd work and click ✨ Create my resume in step 2.",
         "answer what you can in step 3 (all optional), then click ✨ Build my resume.",
         "fix or keep each flagged item in step 4, download your PDF, then 💾 save the application."]
show_tracker(done, hints)

persist()
if not versions():
    with step(4, "Check & download", "Your resume appears here, checked line by line against your profile."):
        pass
    st.stop()

# --------------------------------------------------------------------------- #
# Step 4. Check & download
# --------------------------------------------------------------------------- #
vs = versions()
current = vs[-1]
result = current["result"]
# The location line always follows the current job type, even if it changed after the build
resume = {**result["resume"], "availability": guard.availability_line(target_settings, analysis)}
resume_json = as_json(resume)
job_keywords = st.session_state.get("job_keywords", [])
filename = main.safe_filename(st.session_state.get("filename", "")) or "resume"
page_limit = ctx["page_limit"]
job_skills = ctx["job_skills"]

try:
    with st.spinner("Rendering and checking…"):
        pdf_bytes, docx_bytes, pages = make_files(resume_json, *look)
        report = ats_check(resume_json, *look, json.dumps(job_keywords), title, job_text, page_limit)
except render.RenderError as e:
    st.error(str(e))
    st.stop()
current["score"] = report.score
previous = vs[-2]["score"] if len(vs) > 1 else None
match_before = ats.keyword_coverage(master_clean, job_keywords)
match_after = round(100 * len(report.matched) / len(job_keywords)) if job_keywords else 0
fit = match.job_match(resume, analysis, title, job_keywords, master_clean, confirmations)
fit_master = match.job_match({**main.normalize({}, main.RESUME_SCHEMA), "summary": master_clean}, analysis, title,
                             job_keywords)
issues = checks_for(resume, ctx, len(pages))
approved = set(st.session_state.get("approved", []))
unresolved = [i for i in issues if i.severity == guard.SERIOUS and i.id not in approved]
serious = [i for i in issues if i.severity == guard.SERIOUS]
gate_ok = not unresolved


def approve(item_id: str) -> None:
    st.session_state["approved"] = list(dict.fromkeys(st.session_state.get("approved", []) + [item_id]))


def remove_item(item: guard.Item) -> None:
    add_version({**result, "resume": guard.apply_fix(resume, item)},
                f"Removed: {item.value[:60]}")


def md(text: str) -> str:
    """Plain text safe inside Streamlit markdown."""
    return re.sub(r"([\\`*_{}\[\]<>#|~$])", r"\\\1", text)


with step(4, "Check & download", "Every line is checked against your profile and your saved answers "
                                 "(7 checks). Fix or keep anything flagged, then download."):
    if notice := st.session_state.pop("notice", None):
        st.success(notice)
    if unresolved:
        st.error(f"**{len(unresolved)} thing{'s' if len(unresolved) != 1 else ''} to check before you download.** "
                 "Remove it, or keep it if it's true.")
        for item in unresolved:
            with st.container(border=True):
                st.markdown(f"**Check {item.check} · {guard.CHECK_NAMES.get(item.check, 'Other')}**  \n"
                            f"{md(item.message)}" + (f"  \n*{md(item.where)}:* " if item.where else "  \n")
                            + (f"\"{md(item.text)}\"" if item.text and item.text != item.value or item.fix ==
                               "bullet" else ""))
                fix_col, keep_col, _ = st.columns([1, 1, 2])
                if item.fix:
                    fix_col.button("🗑️ Remove it", key=f"fix_{item.id}", on_click=remove_item, args=(item,),
                                   width="stretch")
                keep_col.button("✅ It's true, keep it", key=f"keep_{item.id}", on_click=approve, args=(item.id,),
                                width="stretch")
        if engine != ENGINE_MANUAL and st.button("🤖 Ask Claude to fix them all", width="stretch"):
            auto_improve(100, 1, "Fix these review findings:\n" + "\n".join(
                f"- {i.message} {i.text}" for i in unresolved), ctx)
    else:
        passed = sorted({i.check for i in serious})
        st.success("✅ **Ready to send.** " + " · ".join(
            f"{'☑️' if n in passed else '✅'} {name}" for n, name in guard.CHECK_NAMES.items()))
        if passed:
            st.caption("☑️ = you reviewed and kept the flagged items.")

    dl_col, word_col, undo_col = st.columns([2, 1, 1])
    dl_col.download_button(f"⬇️ Download PDF · {filename}.pdf", pdf_bytes, f"{filename}.pdf",
                           mime="application/pdf", type="primary", width="stretch", disabled=not gate_ok)
    word_col.download_button("⬇️ Word (.docx)", docx_bytes, f"{filename}.docx", width="stretch",
                             mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                             disabled=not gate_ok)
    if len(vs) > 1:
        undo_col.button(f"↩️ Undo (back to v{len(vs) - 1})", on_click=undo, width="stretch")
    st.caption(f"ATS score {report.score}/100 · job match {fit.overall}/100 · {report.words} words · "
               f"{len(pages)} page{'s' if len(pages) != 1 else ''} · version {len(vs)}. Scores are this app's own "
               "estimates, not an employer's result.")

tab_diff, tab_design, tab_report, tab_improve, tab_recruiter, tab_cover, tab_edit, tab_save = st.tabs(
    ["🔍 New vs your profile", "🎨 Preview & design", "🧾 Evidence", "📈 Improve", "👔 Recruiter view",
     "✉️ Cover letter", "✏️ Edit text", "💾 Save application"])

# --------------------------------------------------------------------------- #
# Tab: New vs your profile (the diff)
# --------------------------------------------------------------------------- #
sources = guard.sources_text(master_clean, ctx["answers"])


def highlight(text: str) -> str:
    return " ".join(f":red-background[{md(w)}]" if new else md(w) for w, new in guard.new_words(text, sources))


with tab_diff:
    st.caption("Everything in :red-background[red] is **not** in your profile or your saved answers: usually "
               "rewording, but check each one. Remove what isn't true with the buttons above or in ✏️ Edit text.")
    st.markdown(f"**Headline:** {highlight(resume['headline'])}")
    st.markdown(f"**Location line:** {md(resume.get('availability') or '(none: local job)')} "
                f"· *from your job type*")
    contact_new = [c for c in resume["contact"] if not evidence.mentions(master_clean, c)]
    st.markdown("**Contact:** " + " · ".join(f":red-background[{md(c)}]" if c in contact_new else md(c)
                                             for c in resume["contact"]))
    st.markdown(f"**Summary:** {highlight(resume['summary'])}")
    st.markdown("**Skills**")
    master_norm = evidence.normalise(master_clean)
    for g in render._skill_groups(resume["skills"]):
        chips = []
        for item in g["items"]:
            st_item = evidence.status(item, master_clean, confirmations, master_norm)
            chips.append(md(item) if st_item in ("verified", "reworded") else
                         f":orange-background[{md(item)} · you confirmed]" if st_item == "confirmed" else
                         f":red-background[{md(item)}]")
        st.markdown(f"- {md(g['label']) + ': ' if g['label'] else ''}" + ", ".join(chips))
    for j in resume["experience"]:
        st.markdown(f"**{md(j['title'])} · {md(j['company'])}** · {md(j['dates'])}")
        st.markdown("\n".join(f"- {highlight(b)}" for b in j["bullets"]))
    for p in resume["projects"]:
        st.markdown(f"**{md(p['name'])}**")
        st.markdown("\n".join(f"- {highlight(b)}" for b in p["bullets"]))
    if result.get("fixes"):
        with st.expander(f"🧹 Fixed automatically in this version ({len(result['fixes'])})"):
            st.markdown("\n".join(f"- {md(note)}" for note in result["fixes"]))

# --------------------------------------------------------------------------- #
# Tab: Evidence (job match, where each claim comes from, Claude's report)
# --------------------------------------------------------------------------- #
with tab_report:
    part_b = result["report"]
    st.caption("This report stays in the app. Only the resume itself is exported. " + match.DISCLAIMER)
    warnings = [i for i in issues if i.severity != guard.SERIOUS]
    if warnings:
        st.markdown("**Suggestions**")
        st.markdown("\n".join(f"- ⚠️ {md(i.message)}" for i in warnings))
    st.markdown("**🎯 Job match**")
    cols = st.columns(len(fit.coverage) + 1)
    cols[0].metric("Overall estimate", f"{fit.overall}/100", delta=f"{fit.overall - fit_master.overall:+d} vs profile",
                   help="Weighted by importance; title counts 15%. " + f"Exact-wording keyword match: {match_after}% "
                        f"(profile: {match_before}%).")
    for col, (cat, pct) in zip(cols[1:], fit.coverage.items()):
        found, total = fit.counts[cat]
        col.metric(f"{match.CATEGORY_LABEL[cat]} requirements", f"{found}/{total}", help=f"{pct}% by weight")
    if fit.missing:
        status_text = {"verified": "in your profile, not used yet", "reworded": "in your profile in other words",
                       "confirmed": "you confirmed it, not used yet",
                       "needs confirmation": "not in your profile: answer it in step 3 if true",
                       "unsupported": "you're learning it: reported as a gap", "excluded": "you said No"}
        st.markdown("**Not shown in this resume:**")
        for r in sorted(fit.missing, key=lambda r: (r.category != "essential", -r.weight)):
            st.markdown(f"- **{md(r.skill)}** · {match.CATEGORY_LABEL[r.category]} · "
                        f"{status_text.get(r.status, r.status)}")
    trace = evidence.trace_bullets(resume, sources, job_skills, confirmations)
    with st.expander(f"Where each bullet comes from ({sum(r['status'] == 'verified' for r in trace)} of "
                     f"{len(trace)} closely match your profile or answers)"):
        st.dataframe([{"": {"verified": "✅", "reworded": "🔁", "not found": "❓"}[r["status"]],
                       "Where": r["where"], "Resume text": r["text"], "Support": f"{r['support']:.0%}",
                       "Closest profile line": r["source"]} for r in trace], hide_index=True, width="stretch")
    if part_b["gaps"]:
        st.markdown("**Gaps & quick wins**")
        for g in part_b["gaps"]:
            st.markdown(f"- **{md(g['skill'])}** — {md(g['plan'])}")
    if part_b["interview_prep"]:
        st.markdown("**Interview prep**")
        for q in part_b["interview_prep"]:
            st.markdown(f"- **{md(q['skill'])}** — {md(q['talking_point'])}")

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
                       "✅ ATS-friendly one in *Download & design*.")

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
            master_clean, job_text, resume, report_text, title, ctx["settings"], analysis, confirmations, progress,
            ctx["answers"])),
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
        target = target_col.select_slider("Target score", [70, 75, 80, 85, 90, 95, 100], value=ctx["target"], key="target")
        max_rounds = rounds_col.select_slider("Max rounds", [1, 2, 3, 4, 5], value=ctx["max_rounds"], key="max_rounds")
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
            st.warning("Fix or keep the flagged items in step 4 to enable downloads.")
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
    if not gate_ok:
        st.warning("Some flagged items aren't reviewed yet; the saved record lists them.")
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
            "quality": [{"severity": i.severity, "area": guard.CHECK_NAMES.get(i.check, "Other"),
                         "message": f"{i.message} {i.text}".strip(), "approved": i.id in approved} for i in issues],
            "recruiter": rec["review"] if rec and rec["version"] == len(vs) else None,
            "cover_letter": st.session_state.get("cover_letter"),
        }
        try:
            st.session_state["application_id"] = store.save_application(DATA_DIR, record, pdf_bytes, docx_bytes)
        except (OSError, store.StoreError) as e:
            st.error(f"Could not save the application: {e}")
        else:
            st.success("Saved. Open it any time under 💼 My applications.")

persist()

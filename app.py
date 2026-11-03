r"""
Resume Tailor — local web UI.

Run from the VS Code terminal:
    .\.venv\Scripts\Activate.ps1
    streamlit run app.py

Flow: your resume + the job → ✨ Tailor → built-in ATS score → 🚀 Auto-improve →
pick a design → download. Claude runs through Claude Code (your Claude
subscription, no copy-paste), the Anthropic API, or copy-paste with claude.ai.
"""

import io
import json
import re
import time
from pathlib import Path

import streamlit as st

import ats
import engines
import main
import render

APP_DIR = Path(__file__).parent
SETTINGS_FILE = APP_DIR / "settings.json"
MASTER_FILE = APP_DIR / "data" / "master_resume.txt"  # git-ignored

ENGINE_CODE = "Claude Code — your Claude plan (automatic)"
ENGINE_API = "Anthropic API key (pay per use)"
ENGINE_MANUAL = "Copy-paste with claude.ai"
MODELS = {  # label -> (API model id, Claude Code alias)
    "Sonnet 5.5 · balanced": ("claude-sonnet-5-5", "sonnet"),
    "Opus 5.5 · best quality": ("claude-opus-5-5", "opus"),
    "Haiku 5.5 · fastest": ("claude-haiku-5-5", "haiku"),
}
EFFORTS = ["low", "medium", "high"]
# Shown in the Improve tab; the built-in check and Claude's rules enforce each point.
TIPS = """**Make it readable by software first.** A person only sees your resume if the software parsed it.
- One column, standard headings: Summary, Skills, Experience, Projects, Education.
- A text-based PDF or DOCX, never a screenshot, scan or photo.
- Contact details in the body, not in the page header or footer.
- No tables, text boxes, icons, skill bars or graphics.
- File name: Firstname-Lastname-Role.pdf.

**Mirror the job post.** Recruiters search for the exact words from the job post.
- Same spelling for tools and titles, e.g. "JavaScript" and "customer support".
- Acronym and full term once: "search engine optimisation (SEO)".
- Most relevant skills near the top: in the summary and the first bullets.
- One tailored version per application, made from a longer master resume.

**Write bullets that prove results.** Duties say what the job was; bullets show what you achieved.
- Action verb + what you did + measurable result: "Reduced page load time by 35% by splitting bundles \
and lazy loading images."
- Cut "responsible for" and "worked on". Start with Built, Led, Automated, Launched.
- Real numbers: users, revenue, time saved, tickets, accuracy.
- 3 to 6 bullets per role, most relevant first."""


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #

def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_master() -> str:
    try:
        return MASTER_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


def save_master(text: str) -> None:
    MASTER_FILE.parent.mkdir(exist_ok=True)
    MASTER_FILE.write_text(text, encoding="utf-8")


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
              keywords_json: str, job_title: str, job_text: str = ""):
    pdf, _, _ = make_files(resume_json, template, accent, page, font, size)
    return ats.check(json.loads(resume_json), pdf, json.loads(keywords_json), job_title, job_text,
                     one_column=render.TEMPLATES[template]["ats"])


def as_json(resume: dict) -> str:
    return json.dumps(resume, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# Versions: every tailoring / revision / edit is a new version; Undo pops one
# --------------------------------------------------------------------------- #

def versions() -> list[dict]:
    return st.session_state.setdefault("versions", [])


def start_versions(result: dict, note: str, role: str) -> None:
    st.session_state["versions"] = [{"result": result, "note": note, "score": None}]
    st.session_state.pop("cover_letter", None)  # it belonged to the previous job
    # Keep the job's keywords fixed across versions so scores stay comparable
    st.session_state["job_keywords"] = result["job_keywords"]
    st.session_state["filename"] = main.output_stem(result["resume"], role)  # Firstname-Lastname-Role


def add_version(result: dict, note: str, score: int | None = None) -> None:
    if not st.session_state.get("job_keywords"):
        st.session_state["job_keywords"] = result["job_keywords"]
    versions().append({"result": result, "note": note, "score": score})


def undo() -> None:
    if len(versions()) > 1:
        versions().pop()


def pick_template(template_id: str) -> None:
    st.session_state["template"] = template_id


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


def polish(result: dict, max_match: bool) -> dict:
    """Safety nets after every Claude reply: all job keywords in (Max ATS match), length within limits."""
    keywords = st.session_state.get("job_keywords", [])
    resume, added = ats.ensure_keywords(result["resume"], keywords) if max_match else (result["resume"], [])
    resume, _ = ats.trim_to_length(resume, keywords)
    return {**result, "resume": resume,
            "keywords_added": result["keywords_added"] + [{"keyword": k, "where": "Skills"} for k in added]}


def auto_improve(target: int, max_rounds: int, confirmed: list[dict], ext_report: str, ctx: dict) -> None:
    """Check → revise → re-check until the target score, no further gain, or max_rounds."""
    view = (ctx["template"], ctx["accent"], ctx["page"], ctx["font"], ctx["size"])
    max_match = ctx["max_match"]
    with st.status("🚀 Improving your resume…", expanded=True) as status:
        for n in range(1, max_rounds + 1):
            keywords_json = json.dumps(st.session_state.get("job_keywords", []))
            resume = versions()[-1]["result"]["resume"]
            before = ats_check(as_json(resume), *view, keywords_json, ctx["job_title"], ctx["job_text"])
            first_with_input = n == 1 and bool(confirmed or ext_report)
            if before.score >= target and not first_with_input:
                status.write(f"Score {before.score} already meets your target of {target}.")
                break
            status.write(f"**Round {n}** — score {before.score}. Claude is revising…")
            report_text = before.as_text()
            if ext_report and n == 1:
                report_text += f"\n\nReport from an external ATS checker:\n{ext_report}"
            progress = ", ".join(f"v{i + 1}: {v['score']}" for i, v in enumerate(versions()) if v["score"] is not None)
            try:
                new, usage = engines.run(engine_config(), *main.revision_request(
                    ctx["master"], ctx["job_text"], resume, report_text, ctx["job_title"], progress,
                    confirmed if n == 1 else None, max_match))
            except engines.EngineError as e:
                status.update(label=f"❌ {e}", state="error")
                return
            if ext_report and n == 1:  # track the external checker's keywords from now on
                merged = list(dict.fromkeys(st.session_state.get("job_keywords", []) + new["job_keywords"]))
                st.session_state["job_keywords"] = merged
                keywords_json = json.dumps(merged)
                before = ats_check(as_json(resume), *view, keywords_json, ctx["job_title"], ctx["job_text"])
            new = polish(new, max_match)
            after = ats_check(as_json(new["resume"]), *view, keywords_json, ctx["job_title"], ctx["job_text"])
            if after.score < before.score and not first_with_input:
                status.write(f"Round {n} would lower the score ({before.score} → {after.score}), so it was discarded.")
                break
            add_version(new, f"Auto-improve round {n} · {usage}", after.score)
            status.write(f"✅ Version {len(versions())}: {before.score} → **{after.score}**")
            if after.score >= target:
                break
            if after.score == before.score:
                status.write("No further gain from rewording alone — check the missing keywords and checklist.")
                break
        final = versions()[-1]["score"]
        status.update(label=f"Done — ATS score {final}/100 (version {len(versions())})", state="complete",
                      expanded=False)
    st.session_state["notice"] = f"Auto-improve finished: ATS score {final}/100 on version {len(versions())}."
    st.rerun()


# --------------------------------------------------------------------------- #
# Sidebar: settings
# --------------------------------------------------------------------------- #
st.set_page_config(page_title="Resume Tailor", page_icon="📄", layout="wide")
saved = load_settings()
cli_path = engines.find_claude_cli()

with st.sidebar:
    st.header("⚙️ Settings")
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
    remember = st.checkbox("Remember settings and my resume on this laptop", value=saved.get("remember", True),
                           help="Stored in this app's folder only (settings.json, data/). Never uploaded.")
    settings = {"engine": engine, "api_key": api_key, "model_label": model_label, "effort": effort,
                "out_dir": out_dir, "remember": remember,
                "max_match": st.session_state.get("max_match", saved.get("max_match", True)),
                "font": st.session_state.get("font", saved.get("font", ""))}
    if remember and settings != saved:
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    elif not remember:
        SETTINGS_FILE.unlink(missing_ok=True)
        MASTER_FILE.unlink(missing_ok=True)

# --------------------------------------------------------------------------- #
# 1-2. Resume and job
# --------------------------------------------------------------------------- #
st.title("📄 Resume Tailor")
st.caption("Your resume + a job → tailored, ATS-checked, nicely designed. Claude never invents experience.")

col_resume, col_job = st.columns(2, gap="large")
with col_resume:
    st.subheader("1 · Your resume")
    saved_master = load_master() if remember else ""
    master = ""
    if saved_master and not st.session_state.get("replace_master"):
        st.success(f"Using your saved resume ({len(saved_master.split())} words).")
        with st.expander("Show saved resume"):
            st.text(saved_master[:4000])
        if st.button("Use a different resume"):
            st.session_state["replace_master"] = True
            st.rerun()
        master = saved_master
    else:
        uploaded = st.file_uploader("Upload your full resume", type=["docx", "json", "md", "txt"])
        pasted = st.text_area("…or paste it", height=110)
        try:
            if uploaded is not None:
                suffix = Path(uploaded.name).suffix
                if suffix.lower() == ".docx":
                    master = main.docx_to_text(io.BytesIO(uploaded.getvalue()))
                else:
                    master = main.prepare_master(uploaded.getvalue().decode("utf-8"), suffix)
            else:
                master = pasted.strip()
        except Exception as e:  # malformed upload: bad JSON, not UTF-8, corrupt .docx
            st.error(f"Could not read the resume file: {e}")

with col_job:
    st.subheader("2 · The job")
    job_text = st.text_area("Job description", height=150, placeholder="Paste the full job posting").strip()
    job_title = st.text_input("Exact job title", placeholder="Senior Backend Engineer",
                              help="ATS checkers look for the exact title. Also names the file "
                                   "Firstname-Lastname-Role.pdf.").strip()
    max_match = st.toggle(
        "🎯 Max ATS match: add every job keyword", value=saved.get("max_match", True), key="max_match",
        help="On: every keyword from the job goes into your resume (Skills plus a fitting bullet), aiming for "
             "100%. Employers, titles, dates, degrees, certifications and numbers are never invented. "
             "Off: only keywords your resume already proves.")
    if max_match:
        st.caption("Adds skills your resume doesn't show yet, so be ready to talk about each one in an interview.")

ready = bool(master and job_text)

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
ctx = {"template": template, "accent": accent, "page": page, "font": font, "size": size, "job_title": job_title,
       "master": master, "job_text": job_text, "max_match": max_match}
target = st.session_state.get("target", 100 if max_match else 90)
max_rounds = st.session_state.get("max_rounds", 3)


def remember_master() -> None:
    if remember and master and master != saved_master:
        save_master(master)
    st.session_state["replace_master"] = False


# --------------------------------------------------------------------------- #
# 3. Tailor
# --------------------------------------------------------------------------- #
if engine == ENGINE_MANUAL:
    with st.expander("✨ Tailor with claude.ai (copy-paste)", expanded=not versions()):
        if ready:
            st.markdown("**a.** Copy the prompt (📋 top-right of the box) → **b.** paste it into a "
                        "[new Claude chat](https://claude.ai/new) → **c.** paste Claude's whole reply below.")
            st.code(main.build_manual_prompt(master, job_text, job_title, max_match), language=None, height=150)
        else:
            st.info("Add your resume and the job description first.")
        reply = st.text_area("Claude's reply", height=110, key="tailor_reply")
        if st.button("✨ Create my resume", type="primary", width="stretch"):
            try:
                start_versions(main.parse_claude_reply(reply), "Tailored with claude.ai", job_title)
                versions()[0]["result"] = polish(versions()[0]["result"], max_match)
                remember_master()
                st.rerun()
            except main.TailorError as e:
                st.error(str(e))
else:
    if st.button(f"✨ Tailor my resume and optimize to {target}%", type="primary", width="stretch",
                 disabled=not ready or (engine == ENGINE_CODE and not cli_path),
                 help="Tailors, then runs Auto-improve rounds until the target score." if ready
                 else "Add your resume and the job description first."):
        out = run_claude("✍️ Claude is tailoring your resume — usually under a minute…",
                         *main.tailor_request(master, job_text, job_title, max_match))
        if out:
            start_versions(out[0], f"Tailored · {out[1]}", job_title)
            versions()[0]["result"] = polish(out[0], max_match)
            remember_master()
            auto_improve(target, max_rounds, [], "", ctx)  # reruns the page when done

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
        report = ats_check(resume_json, *look, json.dumps(job_keywords), job_title, job_text)
except render.RenderError as e:
    st.error(str(e))
    st.stop()
current["score"] = report.score
previous = vs[-2]["score"] if len(vs) > 1 else None

st.divider()
if notice := st.session_state.pop("notice", None):
    st.success(notice)
score_col, words_col, version_col, actions_col = st.columns([1, 1, 1, 1.6])
score_col.metric("ATS score (built-in)", f"{report.score}/100",
                 delta=None if previous is None else report.score - previous)
words_col.metric("Words", report.words, help=f"Most checkers want {ats.WORDS_MIN}-{ats.WORDS_MAX}.")
version_col.metric("Version", f"v{len(vs)}", help=current["note"])
with actions_col:
    st.download_button(f"⬇️ Download PDF · {meta['label']}", pdf_bytes, f"{filename}.pdf",
                       mime="application/pdf", type="primary", width="stretch")
    if len(vs) > 1:
        st.button(f"↩️ Undo — back to v{len(vs) - 1}", on_click=undo, width="stretch")

tab_improve, tab_design, tab_cover, tab_edit = st.tabs(
    ["📈 Improve ATS score", "🎨 Design & download", "✉️ Cover letter", "✏️ Edit text"])

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
            st.caption(f"{len(report.matched)} of {len(job_keywords)} found.")
        else:
            st.caption("Keyword matching appears after tailoring with a job description.")

        # One-click unlock for keywords Claude couldn't prove from the resume
        candidates = list(result["keywords_not_added"])
        known = {k["keyword"].lower() for k in candidates}
        candidates += [{"keyword": k, "reason": "Not in your resume yet.", "suggestions": []}
                       for k in report.missing if k.lower() not in known]
        confirmed = []
        roles = [j["company"] or j["title"] for j in resume["experience"]] + [p["name"] for p in resume["projects"]]
        if max_match:  # every keyword is added automatically; only missing credentials remain
            if result["keywords_not_added"]:
                st.info("Not added (needs a real credential): "
                        + ", ".join(k["keyword"] for k in result["keywords_not_added"]))
        elif candidates:
            with st.expander(f"🔓 Missing keywords you might have ({len(candidates)}) — click the true ones"):
                st.caption("Claude only adds what your resume proves. If you have done one of these, pick an "
                           "option and the next round adds it. Leave the rest on Skip.")
                for i, k in enumerate(candidates):
                    options = [("skip", None, None)]
                    options += [(s["level"].lower(), s["where"], s["text"])
                                for s in k.get("suggestions", []) if s["text"]]
                    if "basic" not in {o[0] for o in options}:
                        options.append(("basic", "Skills", k["keyword"]))
                    if roles:
                        options.append(("work", None, None))

                    def label(o):
                        level, where, text = o
                        return {"skip": "Skip — not true for me",
                                "basic": f"I know the basics → add “{text}” to Skills",
                                "work": "I used it at work → pick where"}.get(
                            level, f"I've done this at {where} → add: “{text}”")

                    st.markdown(f"**{k['keyword']}** <span style='color:gray'>— {k['reason']}</span>",
                                unsafe_allow_html=True)
                    level, where, text = st.radio(k["keyword"], options, format_func=label,
                                                  key=f"kw_{len(vs)}_{i}", label_visibility="collapsed")
                    if level == "basic":
                        confirmed.append({"keyword": k["keyword"], "note": f"Skills only (basic level), as: {text}"})
                    elif level == "work":
                        role = st.selectbox("Where?", roles, key=f"kw_role_{len(vs)}_{i}")
                        confirmed.append({"keyword": k["keyword"],
                                          "note": f"used it at {role}; add it to Skills and one bullet there, kept general"})
                    elif level != "skip":
                        confirmed.append({"keyword": k["keyword"], "note": f"used it at {where}; suggested bullet: {text}"})
                if confirmed:
                    st.success("Next round adds: " + ", ".join(c["keyword"] for c in confirmed))

    st.divider()
    st.markdown("**Your ATS checker scored it lower?** Paste its report (select all on its results page, copy, "
                "paste). The next round adds every keyword it lists as missing, and the built-in score tracks "
                "them from then on.")
    ext_report = st.text_area("External ATS checker report (optional)", height=100, key=f"ext_report_{len(vs)}",
                              label_visibility="collapsed",
                              placeholder="Paste the Jobscan / Resume Worded / Enhancv report here").strip()

    if engine == ENGINE_MANUAL:
        st.markdown("**Improve with claude.ai** — copy the prompt → paste into Claude → paste the reply below.")
        report_text = report.as_text() + (f"\n\nReport from an external ATS checker:\n{ext_report}" if ext_report else "")
        progress = ", ".join(f"v{i + 1}: {v['score']}" for i, v in enumerate(vs) if v["score"] is not None)
        st.code(main.build_revision_prompt(master, job_text, resume, report_text, job_title, progress, confirmed,
                                           max_match), language=None, height=130)
        reply = st.text_area("Claude's reply", height=100, key=f"revision_reply_{len(vs)}")
        if st.button(f"🔁 Create version {len(vs) + 1}", type="primary", width="stretch"):
            try:
                new = main.parse_claude_reply(reply)
                if ext_report:
                    st.session_state["job_keywords"] = list(dict.fromkeys(job_keywords + new["job_keywords"]))
                add_version(polish(new, max_match), "Revised with claude.ai")
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
            auto_improve(target, max_rounds, confirmed, ext_report, ctx)

    if len(vs) > 1:
        st.markdown("**History**")
        scores = {f"v{i + 1}": v["score"] for i, v in enumerate(vs) if v["score"] is not None}
        if len(scores) > 1:
            st.line_chart({"ATS score": scores}, height=160)
        st.dataframe([{"Version": f"v{i + 1}", "Score": v["score"], "What changed": v["note"]}
                      for i, v in enumerate(vs)], hide_index=True, width="stretch")

# --------------------------------------------------------------------------- #
# Tab: Design & download
# --------------------------------------------------------------------------- #
with tab_design:
    try:
        with st.spinner("Rendering all designs…"):
            gallery = make_gallery(as_json(vs[0]["result"]["resume"]), page)  # once per tailoring
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
        st.download_button("⬇️ Download PDF", pdf_bytes, f"{filename}.pdf", mime="application/pdf",
                           type="primary", width="stretch", key="dl_pdf")
        st.download_button("⬇️ Download Word (.docx)", docx_bytes, f"{filename}.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           width="stretch")
        st.download_button("⬇️ Download .json (reuse as master resume)", resume_json, f"{filename}.json",
                           mime="application/json", width="stretch")
        st.caption("The Word file is a simplified single-column version of the design, for easy editing.")
        if out_dir and st.button(f"💾 Save PDF + Word to {out_dir}", width="stretch"):
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
# Tab: Cover letter
# --------------------------------------------------------------------------- #
with tab_cover:
    st.caption("A cover letter for this job, written from your current resume version, in the same design.")
    letter = st.session_state.get("cover_letter")
    if engine == ENGINE_MANUAL:
        st.markdown("Copy the prompt → paste into a [new Claude chat](https://claude.ai/new) → paste the reply below.")
        st.code(main.build_cover_letter_prompt(resume, job_text, job_title), language=None, height=120)
        reply = st.text_area("Claude's reply", height=100, key="cover_reply")
        if st.button("✉️ Create cover letter", type="primary", width="stretch"):
            try:
                st.session_state["cover_letter"] = main.parse_cover_letter(reply)
                st.rerun()
            except main.TailorError as e:
                st.error(str(e))
    elif st.button("✉️ Write my cover letter" if not letter else "🔁 Write a new cover letter",
                   type="primary", width="stretch", disabled=not job_text):
        out = run_claude("✉️ Claude is writing your cover letter…",
                         *main.cover_letter_request(resume, job_text, job_title), main.COVER_LETTER_SCHEMA)
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

r"""
Resume Tailor — local web UI.

Run from the VS Code terminal:
    .\.venv\Scripts\Activate.ps1
    streamlit run app.py
Your browser opens at http://localhost:8501. Everything runs on this laptop.

Two ways to run Claude:
  * Claude.ai subscription (free): the app builds a prompt, you paste it into
    claude.ai and paste the reply back. Uses your Pro/Max plan, no API credits.
  * Anthropic API key (automatic): one click, billed per use from API credits.
"""

import io
import json
import re
from pathlib import Path

import anthropic
import streamlit as st

import main
import render

SETTINGS_FILE = Path(__file__).with_name("settings.json")
MODE_FREE = "Claude.ai subscription (free, copy-paste)"
MODE_API = "Anthropic API key (automatic, paid)"
MODELS = {
    "Claude Sonnet 5.5 (balanced)": "claude-sonnet-5-5",
    "Claude Haiku 5.5 (cheapest)": "claude-haiku-5-5",
    "Claude Opus 5.5 (best quality)": "claude-opus-5-5",
}
EFFORTS = ["low", "medium", "high"]


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


@st.cache_data(show_spinner=False)
def make_files(resume_json: str, template: str, accent: str, page: str) -> tuple[bytes, bytes, list[bytes]]:
    resume = json.loads(resume_json)
    pdf = render.build_pdf(resume, template, accent, page)
    docx = io.BytesIO()
    render.build_docx(resume, docx, template, accent, page)
    return pdf, docx.getvalue(), render.pdf_page_images(pdf)


@st.cache_data(show_spinner=False)
def make_gallery(resume_json: str, page: str) -> dict[str, tuple[bytes, int]]:
    """First-page thumbnail and page count of every design, using the user's resume."""
    pdfs = render.build_all_pdfs(json.loads(resume_json), page)
    return {t: (render.pdf_page_images(pdf, scale=0.6, max_pages=1)[0], render.pdf_page_count(pdf))
            for t, pdf in pdfs.items()}


def pick_template(template_id: str) -> None:
    st.session_state["template"] = template_id


def set_result(result: dict, usage: str, label: str) -> None:
    st.session_state["result"] = result
    st.session_state["usage"] = usage
    st.session_state["filename"] = main.output_stem(result["resume"], label)
    st.session_state["history"] = []
    st.session_state["rounds"] = []


def apply_revision(result: dict, usage: str, score: int | None) -> None:
    """Make a revised resume the new version, keeping the old one (and its ATS score) for Undo."""
    history = st.session_state.setdefault("history", [])
    current = st.session_state["result"]
    st.session_state.setdefault("rounds", []).append({
        "Version": len(history) + 1,
        "ATS score": score,
        "Words": main.word_count(current["resume"]),
    })
    history.append((current, st.session_state["usage"]))
    st.session_state["result"] = result
    st.session_state["usage"] = usage
    st.session_state["clear_revision_inputs"] = True
    st.session_state["revision_notice"] = (
        f"Version {len(history) + 1} is ready. Download it, check it in your ATS checker, and start the next round."
    )


def undo_revision() -> None:
    st.session_state["result"], st.session_state["usage"] = st.session_state["history"].pop()
    if st.session_state.get("rounds"):
        st.session_state["rounds"].pop()


def call_api(fn, *args, **kwargs):
    """Run a Claude API call; returns (value, None) or (None, friendly error message)."""
    try:
        return fn(*args, **kwargs), None
    except main.TailorError as e:
        return None, str(e)
    except anthropic.AuthenticationError:
        return None, "Invalid API key. Check the key in the sidebar."
    except anthropic.RateLimitError:
        return None, "Rate limited by the API. Wait a minute and try again."
    except anthropic.APIStatusError as e:
        if "credit balance" in str(e.message).lower():
            return None, ("Your Anthropic API account is out of credits. Add credits at "
                          "https://console.anthropic.com/settings/billing — or switch to the free "
                          "Claude.ai subscription mode in the sidebar.")
        return None, f"API error {e.status_code}: {e.message}"
    except anthropic.APIConnectionError:
        return None, "Could not reach the Anthropic API. Check your internet connection."


st.set_page_config(page_title="Resume Tailor", page_icon="📄", layout="wide")
saved = load_settings()

# --------------------------------------------------------------------------- #
# Sidebar: settings
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("Settings")
    mode = st.radio(
        "How to run Claude", [MODE_FREE, MODE_API],
        index=1 if saved.get("mode") == MODE_API else 0,
        help="Free mode uses your Claude.ai Pro/Max plan via copy-paste. "
             "API mode is one click but needs API credits (billed separately from Claude.ai).",
    )

    api_key = saved.get("api_key", "")
    model_id = saved.get("model", MODELS[next(iter(MODELS))])
    effort = saved.get("effort", "medium")
    if mode == MODE_API:
        api_key = st.text_input(
            "Anthropic API key", value=api_key, type="password", placeholder="sk-ant-...",
            help="Create one at console.anthropic.com/settings/keys. Needs credits: console.anthropic.com/settings/billing",
        )
        model_names = list(MODELS)
        current = next((n for n, m in MODELS.items() if m == model_id), model_names[0])
        model_id = MODELS[st.selectbox("Model", model_names, index=model_names.index(current))]
        effort = st.select_slider("Effort", EFFORTS, value=effort,
                                  help="Lower effort uses fewer tokens. 'medium' is a good default.")
    else:
        st.caption("Uses your Claude.ai plan — no API key or credits needed.")

    out_dir = st.text_input(
        "Save-to folder (optional)", value=saved.get("out_dir", ""), placeholder=r"G:\My Drive\Resumes",
        help="E.g. your Google Drive for desktop folder, so saved files sync automatically.",
    )
    remember = st.checkbox(
        "Remember settings on this laptop", value=bool(saved),
        help=f"Saves to {SETTINGS_FILE.name} in the app folder (plain text, never uploaded).",
    )
    settings = {"mode": mode, "api_key": api_key, "model": model_id, "effort": effort, "out_dir": out_dir}
    if remember and settings != saved:
        save_settings(settings)
    elif not remember and SETTINGS_FILE.exists():
        SETTINGS_FILE.unlink()

# --------------------------------------------------------------------------- #
# 1-2. Inputs
# --------------------------------------------------------------------------- #
st.title("📄 Resume Tailor")
st.caption("Tailor your resume to any job description with Claude — no invented details.")

col_resume, col_job = st.columns(2)
with col_resume:
    st.subheader("1. Master resume")
    upload_tab, paste_tab = st.tabs(["Upload file", "Paste text"])
    with upload_tab:
        uploaded = st.file_uploader("Resume file", type=["docx", "json", "md", "txt"], label_visibility="collapsed")
    with paste_tab:
        pasted = st.text_area("Resume text", height=250, label_visibility="collapsed",
                              placeholder="Paste your full resume here (used if no file is uploaded)")
with col_job:
    st.subheader("2. Job description / ATS feedback")
    job_text = st.text_area(
        "Job description", height=250, label_visibility="collapsed",
        placeholder="Paste the job posting and/or ATS feedback, e.g.\n\nMissing keywords: ETL, CI/CD, Airflow",
    ).strip()
    title_col, label_col = st.columns(2)
    job_title = title_col.text_input(
        "Exact job title from the posting", placeholder="Senior Backend Engineer",
        help="ATS checkers look for the exact title. Claude uses it in your headline if it truthfully fits.",
    ).strip()
    label = label_col.text_input("Label for the file name", placeholder="Northwind")

master, read_error = "", None
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
    read_error = f"Could not read the resume file: {e}"

# --------------------------------------------------------------------------- #
# 3. Tailor
# --------------------------------------------------------------------------- #
st.subheader("3. Tailor with Claude")
if read_error:
    st.error(read_error)

if mode == MODE_FREE:
    if master and job_text:
        st.markdown(
            "**a.** Copy the prompt (📋 icon at the top-right of the box) → "
            "**b.** paste it into a [new Claude chat](https://claude.ai/new) → "
            "**c.** copy Claude's whole reply and paste it below."
        )
        st.code(main.build_manual_prompt(master, job_text, job_title), language=None, height=180)
    else:
        st.info("Add your resume (step 1) and the job description (step 2) to generate the prompt for Claude.ai.")
    reply = st.text_area("Paste Claude's reply here", height=150, placeholder='{"resume": {"name": ...}, ...}')
    if st.button("✨ Create resume from reply", type="primary", width="stretch"):
        try:
            set_result(main.parse_claude_reply(reply), "Created from your Claude.ai reply", label)
        except main.TailorError as e:
            st.error(str(e))
else:
    if st.button("✨ Tailor resume", type="primary", width="stretch"):
        if not api_key:
            st.error("Add your Anthropic API key in the sidebar.")
        elif not master:
            st.error("Upload or paste your master resume.")
        elif not job_text:
            st.error("Paste the job description or ATS feedback.")
        else:
            with st.spinner("Claude is tailoring your resume..."):
                out, error = call_api(main.tailor_resume, master, job_text, api_key=api_key,
                                      model=model_id, effort=effort, job_title=job_title)
            if error:
                st.error(error)
            else:
                set_result(*out, label)

# --------------------------------------------------------------------------- #
# 4. Design gallery
# --------------------------------------------------------------------------- #
result = st.session_state.get("result")
if result:
    resume = result["resume"]
    resume_json = json.dumps(resume, indent=2, ensure_ascii=False)
    if st.session_state.get("template") not in render.TEMPLATES:
        st.session_state["template"] = next(iter(render.TEMPLATES))
    template = st.session_state["template"]
    meta = render.TEMPLATES[template]

    st.divider()
    st.subheader("4. Choose a design")
    st.caption(st.session_state["usage"])
    page = st.radio("Page size", list(render.PAGE_SIZES), horizontal=True, key="page")

    try:
        with st.spinner("Rendering all designs with your resume..."):
            gallery = make_gallery(resume_json, page)
    except render.RenderError as e:
        st.error(str(e))
        st.stop()

    for row_start in range(0, len(render.TEMPLATES), 4):
        cols = st.columns(4)
        for col, (tid, info) in zip(cols, list(render.TEMPLATES.items())[row_start:row_start + 4]):
            thumb, n_pages = gallery[tid]
            with col, st.container(border=True):
                st.image(thumb, width="stretch")
                st.button(("✓ " if tid == template else "") + info["label"], key=f"pick_{tid}",
                          type="primary" if tid == template else "secondary", width="stretch",
                          on_click=pick_template, args=(tid,))
                st.caption(f"{info['about']} · {n_pages} page{'s' if n_pages != 1 else ''} · "
                           + ("✅ ATS-friendly" if info["ats"] else "🎨 design-focused"))

    # ----------------------------------------------------------------------- #
    # 5. Customize, preview, download
    # ----------------------------------------------------------------------- #
    st.divider()
    st.subheader(f"5. Customize & download — {meta['label']}")
    controls, preview = st.columns([1, 1.5], gap="large")
    with controls:
        accent = st.color_picker("Accent color", meta["accent"], key=f"accent_{template}")
        filename = main.safe_filename(st.text_input("File name", key="filename")) or "resume"

        try:
            with st.spinner("Rendering..."):
                pdf_bytes, docx_bytes, pages = make_files(resume_json, template, accent, page)
        except render.RenderError as e:
            st.error(str(e))
            st.stop()

        st.download_button("⬇️ Download PDF", pdf_bytes, f"{filename}.pdf", mime="application/pdf",
                           type="primary", width="stretch")
        st.download_button("⬇️ Download Word (.docx)", docx_bytes, f"{filename}.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           width="stretch")
        st.download_button("⬇️ Download .json (reuse as master resume)", resume_json, f"{filename}.json",
                           mime="application/json", width="stretch")
        st.caption("The Word file is a simplified single-column version of this design, for easy editing.")
        if not meta["ats"]:
            st.info("Multi-column designs look great to recruiters, but some applicant tracking systems "
                    "read them less reliably. For online application portals, an ✅ ATS-friendly design is safer.")
        if out_dir and st.button(f"💾 Save PDF + Word to {out_dir}", width="stretch"):
            try:
                folder = Path(out_dir)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{filename}.pdf").write_bytes(pdf_bytes)
                (folder / f"{filename}.docx").write_bytes(docx_bytes)
                st.success(f"Saved {filename}.pdf and .docx to {folder}")
            except OSError as e:
                st.warning(f"Could not save to {out_dir}: {e}")

        st.markdown("**✅ Keywords added**")
        if result["keywords_added"]:
            st.dataframe(result["keywords_added"], width="stretch", hide_index=True)
        else:
            st.caption("None")
        st.markdown("**⚠️ Not added** — no supporting experience; add yourself only if true")
        if result["keywords_not_added"]:
            st.dataframe(result["keywords_not_added"], width="stretch", hide_index=True)
        else:
            st.caption("None")

        with st.expander("✏️ Edit resume text"):
            edited = st.text_area("Resume JSON", resume_json, height=320, label_visibility="collapsed")
            if st.button("Apply edits", width="stretch"):
                try:
                    st.session_state["result"]["resume"] = main.parse_claude_reply(edited)["resume"]
                    st.rerun()
                except main.TailorError as e:
                    st.error(str(e))

    with preview:
        st.caption(f"Preview — {len(pages)} page{'s' if len(pages) != 1 else ''}")
        for png in pages:
            st.image(png, width="stretch")

    # ----------------------------------------------------------------------- #
    # 6. Improve with ATS checker feedback — repeatable rounds
    # ----------------------------------------------------------------------- #
    st.divider()
    st.subheader("6. Raise your ATS score — round by round")
    history = st.session_state.get("history", [])
    rounds = st.session_state.get("rounds", [])
    version = len(history) + 1
    words = main.word_count(resume)

    # Clear last round's inputs (must happen before the widgets are created)
    if st.session_state.pop("clear_revision_inputs", False):
        for key in ("ats_report", "revision_reply", "ats_score"):
            st.session_state.pop(key, None)
    if notice := st.session_state.pop("revision_notice", None):
        st.success(notice)

    # Score progress
    if rounds:
        progress = rounds + [{"Version": version, "ATS score": None, "Words": words}]
        table_col, chart_col = st.columns([1, 1.4])
        table_col.dataframe(
            [{**r, "ATS score": f"{r['ATS score']}%" if r["ATS score"] is not None else "not checked yet"}
             for r in progress],
            hide_index=True, width="stretch",
        )
        scored = [r for r in rounds if r["ATS score"] is not None]
        if len(scored) >= 2:
            chart_col.line_chart({"ATS score": {f"v{r['Version']}": r["ATS score"] for r in scored}}, height=180)
        best = max(scored, key=lambda r: r["ATS score"], default=None)
        if best and best["Version"] != version - 1 and best["ATS score"] > scored[-1]["ATS score"]:
            st.warning(f"Version {best['Version']} scored higher ({best['ATS score']}%) than the latest checked "
                       f"version. If the last round made things worse, click ↩️ Undo until you're back on "
                       f"version {best['Version']}.")

    st.markdown(
        f"**Round {version}** — current resume: **version {version}**, {words} words\n\n"
        f"1. Download version {version} below (use an ✅ ATS-friendly design for ATS checks)\n"
        f"2. Upload it to your ATS checker\n"
        f"3. Enter the score and paste the full report here, then let Claude create version {version + 1}"
    )
    if not meta["ats"]:
        st.warning(f"**{meta['label']}** is a design-focused template, so some checkers will flag its layout no "
                   "matter what the content says. For ATS rounds, pick an ✅ ATS-friendly design in step 4.")

    d1, d2 = st.columns(2)
    d1.download_button(f"⬇️ Download version {version} (PDF)", pdf_bytes, f"{filename}.pdf",
                       mime="application/pdf", width="stretch", key="round_download")
    if history:
        d2.button(f"↩️ Undo — back to version {version - 1}", on_click=undo_revision, width="stretch")

    score_col, _ = st.columns([1, 3])
    ats_score = score_col.number_input(f"ATS score for version {version} (%)", min_value=0, max_value=100,
                                       value=None, step=1, key="ats_score", placeholder="e.g. 72")
    if ats_score == 100:
        st.balloons()
        st.success("100% — this version is ready to send. 🎉")
    ats_report = st.text_area(
        f"ATS checker report for version {version}", height=200, key="ats_report",
        placeholder="Paste everything the checker shows, e.g.\n\nMissing from your resume: gcp, networking, client libraries\n"
                    "Length (1002 words): aim for 300 to 800 words\nWritten without \"I\" and \"my\": found 6 first-person pronouns",
    ).strip()
    # Keywords Claude couldn't prove from the resume: let the candidate confirm the true ones
    not_added = result["keywords_not_added"]
    confirmed = []
    with st.expander(f"🔓 Unlock missing keywords — confirm the ones that are true for you "
                     f"({len(not_added)} not added yet)", expanded=bool(not_added)):
        st.caption("Claude only adds keywords your resume proves, so the score stalls if the checker wants words "
                   "your resume doesn't use yet. If you **have** done it, tick it and say briefly where — Claude adds "
                   "it to Skills and that job's bullets in the next version. Leave skills you don't have unticked: "
                   "recruiters ask about them in interviews.")
        for i, k in enumerate(not_added):
            tick_col, note_col = st.columns([1, 2])
            if tick_col.checkbox(k["keyword"], key=f"kw_ok_{version}_{i}"):
                note = note_col.text_input(
                    f"Where/how you used {k['keyword']}", key=f"kw_note_{version}_{i}", label_visibility="collapsed",
                    placeholder="Where/how, e.g. 'built S3 file uploads at Syntivis'",
                )
                confirmed.append({"keyword": k["keyword"], "note": note.strip()})
            else:
                note_col.caption(k["reason"])
        extra = st.text_area(
            "Other missing keywords from the report that are true for you — one per line: keyword — where/how",
            key=f"kw_extra_{version}", height=90,
            placeholder="uploading — file uploads to S3 in Tracio\nnetworking — VPC and security groups on AWS (SkipQ)",
        )
        for line in extra.splitlines():
            parts = re.split(r"\s+[—–-]\s+|:\s+", line.strip(), maxsplit=1)  # "keyword — note" or "keyword: note"
            if parts[0]:
                confirmed.append({"keyword": parts[0], "note": parts[1].strip() if len(parts) > 1 else ""})
        if confirmed:
            st.success(f"{len(confirmed)} keyword(s) will be added in version {version + 1}: "
                       + ", ".join(c["keyword"] for c in confirmed))

    progress_note = ", ".join(f"v{r['Version']}: {r['ATS score']}%" for r in rounds if r["ATS score"] is not None)
    if ats_score is not None:
        progress_note = ", ".join(x for x in (progress_note, f"v{version} (this report): {ats_score}%") if x)

    if mode == MODE_FREE:
        if ats_report:
            st.markdown("**a.** Copy this prompt → **b.** paste it into Claude (a [new chat](https://claude.ai/new) "
                        "is fine) → **c.** paste Claude's whole reply below.")
            st.code(main.build_revision_prompt(master, job_text, resume, ats_report, job_title, progress_note,
                                               confirmed), language=None, height=160)
        revision_reply = st.text_area("Paste Claude's revised reply here", height=130, key="revision_reply",
                                      placeholder='{"resume": {"name": ...}, ...}')
        if st.button(f"🔁 Create version {version + 1} from reply", type="primary", width="stretch"):
            try:
                apply_revision(main.parse_claude_reply(revision_reply),
                               f"Version {version + 1}: revised from ATS feedback (Claude.ai)", ats_score)
                st.rerun()
            except main.TailorError as e:
                st.error(str(e))
    elif st.button(f"🔁 Revise with Claude → version {version + 1}", type="primary", width="stretch",
                   disabled=not ats_report):
        if not api_key:
            st.error("Add your Anthropic API key in the sidebar.")
        else:
            with st.spinner("Claude is revising your resume..."):
                out, error = call_api(main.revise_resume, master, job_text, resume, ats_report, api_key=api_key,
                                      model=model_id, effort=effort, job_title=job_title, progress=progress_note,
                                      confirmed=confirmed)
            if error:
                st.error(error)
            else:
                result_v, usage_v = out
                apply_revision(result_v, f"Version {version + 1}: {usage_v}", ats_score)
                st.rerun()

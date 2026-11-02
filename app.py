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


def set_result(result: dict, usage: str, label: str) -> None:
    st.session_state["result"] = result
    st.session_state["usage"] = usage
    st.session_state["filename"] = main.output_stem(result["resume"], label)


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
    label = st.text_input("Company / role label (for the file name)", placeholder="Northwind Data Engineer")

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
        st.code(main.build_manual_prompt(master, job_text), language=None, height=180)
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
                try:
                    result, usage = main.tailor_resume(master, job_text, api_key=api_key, model=model_id, effort=effort)
                    set_result(result, usage, label)
                except main.TailorError as e:
                    st.error(str(e))
                except anthropic.AuthenticationError:
                    st.error("Invalid API key. Check the key in the sidebar.")
                except anthropic.RateLimitError:
                    st.error("Rate limited by the API. Wait a minute and try again.")
                except anthropic.APIStatusError as e:
                    if "credit balance" in str(e.message).lower():
                        st.error("Your Anthropic API account is out of credits. Add credits at "
                                 "https://console.anthropic.com/settings/billing — or switch to the free "
                                 "Claude.ai subscription mode in the sidebar.")
                    else:
                        st.error(f"API error {e.status_code}: {e.message}")
                except anthropic.APIConnectionError:
                    st.error("Could not reach the Anthropic API. Check your internet connection.")

# --------------------------------------------------------------------------- #
# 4. Result: template, preview, downloads
# --------------------------------------------------------------------------- #
result = st.session_state.get("result")
if result:
    resume = result["resume"]
    resume_json = json.dumps(resume, indent=2, ensure_ascii=False)

    st.divider()
    st.subheader("4. Your tailored resume")
    st.caption(st.session_state["usage"])

    controls, preview = st.columns([1, 1.5], gap="large")
    with controls:
        template = st.selectbox("Template", list(render.TEMPLATES), key="template")
        accent = st.color_picker("Accent color", render.TEMPLATES[template]["accent"], key=f"accent_{template}")
        page = st.radio("Page size", list(render.PAGE_SIZES), horizontal=True, key="page")
        filename = main.safe_filename(st.text_input("File name", key="filename")) or "resume"

        pdf_bytes, docx_bytes, pages = make_files(resume_json, template, accent, page)

        st.download_button("⬇️ Download PDF", pdf_bytes, f"{filename}.pdf", mime="application/pdf",
                           type="primary", width="stretch")
        st.download_button("⬇️ Download Word (.docx)", docx_bytes, f"{filename}.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           width="stretch")
        st.download_button("⬇️ Download .json (reuse as master resume)", resume_json, f"{filename}.json",
                           mime="application/json", width="stretch")
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

r"""
Resume Tailor — local web UI.

Run from the VS Code terminal:
    .\.venv\Scripts\Activate.ps1
    streamlit run app.py
Your browser opens at http://localhost:8501. Everything runs on this laptop;
only the resume and job text are sent to the Anthropic API.
"""

import io
import json
from pathlib import Path

import anthropic
import streamlit as st

import main

SETTINGS_FILE = Path(__file__).with_name("settings.json")
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


st.set_page_config(page_title="Resume Tailor", page_icon="📄", layout="wide")
saved = load_settings()

# --------------------------------------------------------------------------- #
# Sidebar: settings
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("Settings")
    api_key = st.text_input(
        "Anthropic API key",
        value=saved.get("api_key", ""),
        type="password",
        placeholder="sk-ant-...",
        help="Get one at console.anthropic.com/settings/keys",
    )
    remember = st.checkbox(
        "Remember settings on this laptop",
        value=bool(saved),
        help=f"Saves to {SETTINGS_FILE.name} in the app folder (plain text, never uploaded).",
    )

    model_names = list(MODELS)
    saved_model = next((n for n, m in MODELS.items() if m == saved.get("model")), model_names[0])
    model_label = st.selectbox("Model", model_names, index=model_names.index(saved_model))
    effort = st.select_slider(
        "Effort", EFFORTS, value=saved.get("effort", "medium"),
        help="Lower effort uses fewer tokens. 'medium' is a good default.",
    )
    out_dir = st.text_input(
        "Also save to folder (optional)",
        value=saved.get("out_dir", ""),
        placeholder=r"G:\My Drive\Resumes",
        help="E.g. your Google Drive for desktop folder, so the file syncs automatically.",
    )

    settings = {"api_key": api_key, "model": MODELS[model_label], "effort": effort, "out_dir": out_dir}
    if remember and settings != saved:
        save_settings(settings)
    elif not remember and SETTINGS_FILE.exists():
        SETTINGS_FILE.unlink()

# --------------------------------------------------------------------------- #
# Main: inputs
# --------------------------------------------------------------------------- #
st.title("📄 Resume Tailor")
st.caption("Tailor your master resume to a job description in one Claude call — no invented details.")

col_resume, col_job = st.columns(2)

with col_resume:
    st.subheader("1. Master resume")
    upload_tab, paste_tab = st.tabs(["Upload file", "Paste text"])
    with upload_tab:
        uploaded = st.file_uploader("Resume file", type=["docx", "json", "md", "txt"], label_visibility="collapsed")
    with paste_tab:
        pasted = st.text_area("Resume text", height=300, label_visibility="collapsed",
                              placeholder="Paste your full resume here (used if no file is uploaded)")

with col_job:
    st.subheader("2. Job description / ATS feedback")
    job_text = st.text_area(
        "Job description", height=300, label_visibility="collapsed",
        placeholder="Paste the job posting and/or ATS feedback, e.g.\n\nMissing keywords: ETL, CI/CD, Airflow",
    )
    label = st.text_input("Company / role label (for the file name)", placeholder="Northwind Data Engineer")


def read_master() -> str:
    if uploaded is not None:
        suffix = Path(uploaded.name).suffix
        if suffix.lower() == ".docx":
            return main.docx_to_text(io.BytesIO(uploaded.getvalue()))
        return main.prepare_master(uploaded.getvalue().decode("utf-8"), suffix)
    return pasted.strip()


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
if st.button("✨ Tailor resume", type="primary", width="stretch"):
    try:
        master = read_master()
    except (ValueError, UnicodeDecodeError) as e:
        st.error(f"Could not read the resume file: {e}")
        st.stop()

    if not api_key:
        st.error("Add your Anthropic API key in the sidebar.")
    elif not master:
        st.error("Upload or paste your master resume.")
    elif not job_text.strip():
        st.error("Paste the job description or ATS feedback.")
    else:
        with st.spinner("Claude is tailoring your resume..."):
            try:
                result, usage = main.tailor_resume(
                    master, job_text.strip(), api_key=api_key, model=MODELS[model_label], effort=effort
                )
                st.session_state["result"] = result
                st.session_state["usage"] = usage
                st.session_state["label"] = label
            except main.TailorError as e:
                st.error(str(e))
            except anthropic.AuthenticationError:
                st.error("Invalid API key. Check the key in the sidebar.")
            except anthropic.RateLimitError:
                st.error("Rate limited by the API. Wait a minute and try again.")
            except anthropic.APIStatusError as e:
                st.error(f"API error {e.status_code}: {e.message}")
            except anthropic.APIConnectionError:
                st.error("Could not reach the Anthropic API. Check your internet connection.")

# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
result = st.session_state.get("result")
if result:
    resume = result["resume"]
    stem = main.output_stem(resume, st.session_state.get("label", ""))

    docx_buf = io.BytesIO()
    main.build_docx(resume, docx_buf)
    json_text = json.dumps(resume, indent=2, ensure_ascii=False)

    st.divider()
    st.subheader("3. Result")
    st.caption(st.session_state["usage"])

    d1, d2 = st.columns(2)
    d1.download_button("⬇️ Download .docx", docx_buf.getvalue(), f"{stem}.docx",
                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                       type="primary", width="stretch")
    d2.download_button("⬇️ Download .json (reuse as master)", json_text, f"{stem}.json",
                       mime="application/json", width="stretch")

    if out_dir:
        try:
            folder = Path(out_dir)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{stem}.docx").write_bytes(docx_buf.getvalue())
            (folder / f"{stem}.json").write_text(json_text, encoding="utf-8")
            st.success(f"Saved to {folder / (stem + '.docx')}")
        except OSError as e:
            st.warning(f"Could not save to {out_dir}: {e}")

    k1, k2 = st.columns(2)
    with k1:
        st.markdown("**✅ Keywords added**")
        if result["keywords_added"]:
            st.dataframe(result["keywords_added"], width="stretch", hide_index=True)
        else:
            st.write("None")
    with k2:
        st.markdown("**⚠️ Not added** — no supporting experience; add yourself only if true")
        if result["keywords_not_added"]:
            st.dataframe(result["keywords_not_added"], width="stretch", hide_index=True)
        else:
            st.write("None")

    with st.expander("Preview tailored resume", expanded=True):
        st.markdown(f"### {resume['name']}")
        st.write(" | ".join(resume["contact"]))
        if resume["summary"]:
            st.markdown("**Summary**")
            st.write(resume["summary"])
        if resume["skills"]:
            st.markdown("**Skills**")
            st.write(", ".join(resume["skills"]))
        for job in resume["experience"]:
            st.markdown(f"**{job['title']}** — {job['company']}  ·  _{job['dates']}_")
            st.markdown("\n".join(f"- {b}" for b in job["bullets"]))
        for proj in resume["projects"]:
            st.markdown(f"**{proj['name']}**")
            st.markdown("\n".join(f"- {b}" for b in proj["bullets"]))

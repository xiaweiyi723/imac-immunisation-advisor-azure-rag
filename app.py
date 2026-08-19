"""
Azure AI Foundry Agent - Streamlit frontend.
English interface for an immunisation guidance Q&A workflow.
"""

import logging
from datetime import datetime
from html import escape

import streamlit as st

from backend import AzureAgentClient


st.set_page_config(
    page_title="Immunisation Guidance Assistant",
    page_icon="V",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
    <style>
    .block-container {
        padding-top: 2rem;
        max-width: 1180px;
    }

    .app-title {
        font-size: 2.2rem;
        font-weight: 700;
        color: #17324d;
        margin-bottom: 0.2rem;
    }

    .app-subtitle {
        color: #536476;
        font-size: 1rem;
        margin-bottom: 1.2rem;
    }

    .status-ok {
        background: #e8f5ee;
        border: 1px solid #b9dec9;
        color: #20583b;
        padding: 0.85rem 1rem;
        border-radius: 6px;
        margin-bottom: 1rem;
    }

    .status-error {
        background: #fbebeb;
        border: 1px solid #e7b8b8;
        color: #7c2525;
        padding: 0.85rem 1rem;
        border-radius: 6px;
        margin-bottom: 1rem;
    }

    .answer-panel {
        background: #ffffff;
        border: 1px solid #d8dee6;
        border-radius: 6px;
        padding: 1.2rem 1.3rem;
        margin-bottom: 1rem;
    }

    .evidence-panel {
        background: #f8fafc;
        border: 1px solid #d8dee6;
        border-radius: 6px;
        padding: 1rem;
        margin-bottom: 0.75rem;
    }

    .evidence-title {
        color: #17324d;
        font-weight: 700;
        margin-bottom: 0.3rem;
    }

    .evidence-meta {
        color: #64748b;
        font-size: 0.82rem;
        margin-bottom: 0.55rem;
    }

    .snippet {
        border-left: 3px solid #2f6f9f;
        color: #314256;
        padding-left: 0.7rem;
        margin-top: 0.45rem;
        font-size: 0.9rem;
        line-height: 1.45;
    }

    .history-entry {
        border-bottom: 1px solid #e2e8f0;
        padding: 0.65rem 0;
    }

    .disclaimer {
        color: #64748b;
        font-size: 0.82rem;
        margin-top: 1rem;
    }
    </style>
""", unsafe_allow_html=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


if "client" not in st.session_state:
    try:
        st.session_state.client = AzureAgentClient()
        st.session_state.initialized = True
    except Exception as e:
        st.session_state.initialized = False
        st.session_state.error = str(e)

if "conversation_history" not in st.session_state:
    st.session_state.conversation_history = []

if "last_result" not in st.session_state:
    st.session_state.last_result = None

if "last_question" not in st.session_state:
    st.session_state.last_question = None


def normalize_snippet(text: str, limit: int = 430) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


def display_error(message: str) -> None:
    st.markdown(f"""
        <div class="status-error">
            <strong>Error:</strong> {escape(str(message))}
        </div>
    """, unsafe_allow_html=True)


def grouped_sources(sources):
    groups = []
    index_by_key = {}

    for source in sources or []:
        key = source.get("file_id") or source.get("file_name") or "Unknown"
        if key not in index_by_key:
            index_by_key[key] = len(groups)
            groups.append({
                "file_name": source.get("file_name") or "Unknown source",
                "file_id": source.get("file_id") or "",
                "file_path": source.get("file_path") or "",
                "score": source.get("score") or "",
                "snippets": [],
            })

        snippet = normalize_snippet(source.get("quote"))
        if snippet and snippet not in groups[index_by_key[key]]["snippets"]:
            groups[index_by_key[key]]["snippets"].append(snippet)

    return groups


def display_sources(sources) -> None:
    groups = grouped_sources(sources)
    if not groups:
        st.info("No structured source was returned for this answer. Check that File Search is enabled and the agent cited an uploaded guideline.")
        return

    st.markdown("#### Evidence")
    for index, group in enumerate(groups, start=1):
        file_name = escape(str(group["file_name"]))
        file_id = escape(str(group["file_id"]))
        file_path = escape(str(group["file_path"]))
        score = escape(str(group["score"]))

        meta_parts = []
        if file_id:
            meta_parts.append(f"File ID: {file_id}")
        if score:
            meta_parts.append(f"Score: {score}")
        if file_path:
            meta_parts.append(f"Path/link: {file_path}")

        snippets_html = ""
        for snippet in group["snippets"][:3]:
            snippets_html += f"<div class='snippet'>{escape(snippet)}</div>"

        st.markdown(f"""
            <div class="evidence-panel">
                <div class="evidence-title">Source {index}: {file_name}</div>
                <div class="evidence-meta">{' | '.join(meta_parts)}</div>
                {snippets_html}
            </div>
        """, unsafe_allow_html=True)


def display_chat_history() -> None:
    if not st.session_state.conversation_history:
        return

    with st.expander("Conversation history", expanded=False):
        for msg in st.session_state.conversation_history:
            role = escape(str(msg.get("role", "")))
            content = escape(str(msg.get("content", "")))
            st.markdown(f"""
                <div class="history-entry">
                    <strong>{role}</strong><br>
                    {content}
                </div>
            """, unsafe_allow_html=True)


def display_result(result) -> None:
    if not result:
        return

    st.success("Answer generated.")

    answer_col, evidence_col = st.columns([1.35, 1], gap="large")
    with answer_col:
        st.markdown("#### Answer")
        st.markdown(f"""
            <div class="answer-panel">
                {escape(result["response"]).replace(chr(10), "<br>")}
            </div>
        """, unsafe_allow_html=True)

    with evidence_col:
        display_sources(result.get("sources", []))


def sidebar() -> None:
    with st.sidebar:
        st.header("System")

        if not st.session_state.initialized:
            display_error(st.session_state.get("error", "Unknown error"))
            st.stop()

        st.markdown("""
            <div class="status-ok">
                <strong>Connected</strong><br>
                Azure AI Foundry Agent is ready.
            </div>
        """, unsafe_allow_html=True)

        if st.button("New consultation", use_container_width=True):
            st.session_state.conversation_history = []
            st.session_state.last_result = None
            st.session_state.last_question = None
            st.rerun()

        st.divider()
        st.subheader("Workflow")
        st.markdown("""
        1. Ask a vaccination or immunisation guidance question.
        2. The agent searches uploaded guideline files.
        3. The answer is shown separately from the evidence.
        4. Evidence is limited to the most relevant retrieved passages.
        """)

        st.divider()
        st.caption(f"Local time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")


def main() -> None:
    sidebar()

    st.markdown("""
        <div class="app-title">Immunisation Guidance Assistant</div>
        <div class="app-subtitle">
            Evidence-backed vaccine and immunisation answers powered by Azure AI Foundry Agent.
        </div>
    """, unsafe_allow_html=True)

    st.markdown("### Ask a clinical guidance question")

    with st.form("question_form", clear_on_submit=True):
        user_question = st.text_area(
            "Question",
            height=125,
            placeholder="Example: Can infants under 6 months receive influenza vaccine?",
        )
        submitted = st.form_submit_button("Submit question", use_container_width=True)

    if submitted:
        if not user_question.strip():
            display_error("Please enter a question.")
        else:
            with st.spinner("Searching guidance sources and preparing the answer..."):
                try:
                    response_data = st.session_state.client.call_agent(
                        user_message=user_question,
                        conversation_history=st.session_state.conversation_history,
                    )

                    if response_data.get("success"):
                        st.session_state.last_question = user_question
                        st.session_state.last_result = response_data

                        st.session_state.conversation_history.append({
                            "role": "User",
                            "content": user_question,
                        })
                        st.session_state.conversation_history.append({
                            "role": "Assistant",
                            "content": response_data["response"],
                            "sources": response_data.get("sources", []),
                        })
                    else:
                        display_error(response_data.get("error", "Unknown error"))

                except Exception as e:
                    display_error(f"Agent call failed: {str(e)}")
                    logger.error(str(e), exc_info=True)

    display_result(st.session_state.last_result)

    display_chat_history()

    st.markdown("""
        <div class="disclaimer">
            This tool supports clinical consultation and guideline lookup. It does not replace professional clinical judgement.
        </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()

"""Local Streamlit entry point."""
from pathlib import Path
import json
import streamlit as st

from graphic_culture_atlas.config import settings
from graphic_culture_atlas.demo import DemoProvider, image_bytes
from graphic_culture_atlas.records import Repository
from graphic_culture_atlas.workflow import OpenAIProvider, execute, prepare, token

st.set_page_config(page_title="Visual Culture Cartographer", page_icon="◧", layout="wide")
config = settings(Path(__file__).parent)
repo = Repository(config["data_dir"])
st.markdown("""
<style>
.block-container {max-width: 1240px; padding-top: 2.5rem;}
h1 {letter-spacing: -.04em;}
</style>
""", unsafe_allow_html=True)
st.caption("GRAPHIC CULTURE ATLAS  /  LOCAL RESEARCH STUDIO")
st.title("Visual Culture Cartographer")
st.write("Read the image. Trace the interpretation. Keep the evidence.")
st.caption("Prototype · Model interpretations are hypotheses. Human reviews are saved separately.")

with st.sidebar:
    st.subheader("Workspace")
    page = st.radio("Navigate", ["Read an artwork", "Import artwork", "Case library"])
    st.divider()
    st.caption("Local data directory")
    st.code(str(repo.root), language=None)
    st.caption("Originals and records stay out of Git. Live reading sends the selected image and previewed text to the model API.")
    st.caption("API key configured" if config["api_key_available"] else "No API key configured · offline example available")
    if st.button("Load offline example", use_container_width=True):
        matches = [a for a in repo.artworks() if a["kind"] == "synthetic_demo"]
        artwork = matches[0] if matches else repo.import_artwork(
            image_bytes(), title="Synthetic study — interrupted repetition",
            source="Built-in hand-authored software fixture.",
            context="This synthetic software fixture demonstrates a gap in a sequence; it is not a historical design.",
            kind="synthetic_demo")
        st.session_state["selected_artwork"] = artwork["id"]
        st.session_state.pop("preview", None)
        st.success("Example loaded. Open Read an artwork to try it.")

if page == "Import artwork":
    st.header("Import an artwork")
    st.write("Original bytes and a normalized reading image are both retained locally.")
    with st.form("import"):
        upload = st.file_uploader("Image", type=["png", "jpg", "jpeg", "webp"])
        title = st.text_input("Title or local label")
        kind = st.selectbox("Origin", ["unknown", "existing_work", "generated"])
        source = st.text_area("Source / attribution (optional)", help="Stored verbatim. URLs are not fetched.")
        context = st.text_area("Background or author statement (optional)", help="Only supplied in the contextual stage.")
        submitted = st.form_submit_button("Save to local corpus", type="primary")
    if submitted:
        if upload is None:
            st.error("Choose an image first.")
        else:
            try:
                artwork = repo.import_artwork(upload.getvalue(), title=title, source=source, context=context, kind=kind)
                st.session_state["selected_artwork"] = artwork["id"]
                st.success("Artwork saved. Open Read an artwork to continue.")
            except Exception as exc:
                st.error(f"Import failed: {exc}")
    st.stop()

artworks = repo.artworks()
if page == "Case library":
    st.header("Case library")
    st.caption("Local keyword filtering across metadata, readings and human reviews. This is not semantic RAG.")
    query = st.text_input("Search cases").casefold().strip()
    for asset in artworks:
        readings = repo.readings(asset["id"])
        haystack = json.dumps(asset, ensure_ascii=False)
        for reading in readings:
            if reading["status"] == "completed":
                haystack += json.dumps(repo.result(reading["id"]), ensure_ascii=False)
                haystack += json.dumps(repo.reviews(reading["id"]), ensure_ascii=False)
        if query and query not in haystack.casefold():
            continue
        with st.container(border=True):
            left, right = st.columns([1, 3])
            left.image(str(repo.image(asset["id"])), width=160)
            right.subheader(asset["title"])
            right.caption(f'{asset["kind"]} · {len(readings)} readings')
            if right.button("Select artwork", key=f'choose_{asset["id"]}'):
                st.session_state["selected_artwork"] = asset["id"]
                st.session_state.pop("preview", None)
                st.success("Selected. Open Read an artwork.")
    if not artworks:
        st.info("Your corpus is empty. Import an artwork or load the offline example.")
    st.stop()

if not artworks:
    st.info("Start by importing an artwork, or load the offline example from the sidebar.")
    st.write("01 · Import a source image → 02 · Preview a reading → 03 · Review and retain")
    st.stop()

by_id = {a["id"]: a for a in artworks}
selected = st.session_state.get("selected_artwork")
asset_id = st.selectbox("Artwork", list(by_id),
    index=list(by_id).index(selected) if selected in by_id else 0,
    format_func=lambda key: by_id[key]["title"])
st.session_state["selected_artwork"] = asset_id
asset = by_id[asset_id]
left, right = st.columns([0.9, 1.15], gap="large")
with left:
    st.image(str(repo.image(asset_id)), use_container_width=True)
    st.caption(f'{asset["width"]} × {asset["height"]} · {asset["kind"]}')
    with st.expander("Source and background — excluded from blind reading"):
        st.text(asset["source"] or "No source supplied.")
        st.text(asset["context"] or "No background supplied.")
with right:
    st.subheader("Prepare a reading")
    mode_label = st.radio("Execution", ["Live model", "Offline fixture"], horizontal=True,
                          index=1 if asset["kind"] == "synthetic_demo" else 0)
    mode = "demo" if mode_label == "Offline fixture" else "live"
    if mode == "demo":
        st.info("Fixed, hand-authored example response. No model or network call; only the built-in image is supported.")
        model = "offline-fixture"
    else:
        model = st.text_input("Model ID", value=config["model"], placeholder="Your image-capable model ID")
    stage_label = st.radio("Reading stage", ["Blind reading", "With context"], horizontal=True)
    stage = "blind" if stage_label == "Blind reading" else "contextual"
    history = repo.readings(asset_id)
    parent = None
    if stage == "contextual":
        parents = {r["id"]: r for r in history if r["stage"] == "blind" and r["status"] == "completed" and r["mode"] == mode}
        if parents:
            parent = st.selectbox("Blind reading to compare", list(parents),
                                  format_func=lambda key: parents[key]["created_at"][:19] + " · " + key[-8:])
        else:
            st.info("Complete a blind reading in this execution mode first.")
    current = None
    try:
        current = prepare(repo, asset_id, model=model, stage=stage, parent_reading_id=parent, mode=mode)
    except ValueError as exc:
        st.caption(str(exc))
    if st.button("Preview request", disabled=current is None, use_container_width=True):
        st.session_state["preview"] = current
    preview = st.session_state.get("preview")
    if preview is not None and current is not None and token(preview) == token(current):
        st.caption("The image at left and the following instructions, text and schema form this request.")
        with st.expander("Read exact request", expanded=True):
            with st.container(height=360, border=False):
                st.text(preview["instructions"])
                st.text(preview["input_text"])
            st.caption(f'Model: {model} · Image detail: high · Output limit: {preview["max_output_tokens"]} tokens')
            st.caption("Remote response storage disabled; one call, no automatic retries." if mode == "live" else "Local fixture only.")
            st.json(preview["response_schema"], expanded=False)
        ready = mode == "demo" or config["api_key_available"]
        label = "Run offline fixture" if mode == "demo" else "Send one model request"
        if not ready:
            st.warning("Set OPENAI_API_KEY in local .env, then restart the app.")
        if st.button(label, type="primary", disabled=not ready, use_container_width=True):
            request = st.session_state.pop("preview")
            try:
                with st.spinner("Reading and saving the record…"):
                    record = execute(repo, request, approved_token=token(request),
                                     provider=DemoProvider() if mode == "demo" else OpenAIProvider())
                st.session_state["latest_reading"] = record["id"]
                st.rerun()
            except Exception as exc:
                st.error(f"Reading did not complete ({type(exc).__name__}). The attempt is retained. No retry was sent.")
    elif preview is not None:
        st.caption("Selection or inputs changed. Preview again.")

st.divider()
st.subheader("Reading history")
history = repo.readings(asset_id)
if not history:
    st.caption("No readings yet.")
else:
    ids = [r["id"] for r in history]
    latest = st.session_state.get("latest_reading")
    reading_id = st.selectbox("Saved reading", ids, index=ids.index(latest) if latest in ids else 0,
        format_func=lambda key: next(f'{r["stage"]} · {r["mode"]} · {r["status"]} · {r["created_at"][:19]} · {key[-6:]}' for r in history if r["id"] == key))
    record = repo.reading(reading_id)
    if record["status"] != "completed":
        st.warning(f'Attempt status: {record["status"]}. A requested/interrupted attempt may have an unknown remote outcome. Nothing is retried automatically.')
    else:
        result = repo.result(reading_id)
        if record["mode"] == "demo":
            st.info("OFFLINE FIXTURE — hand-authored test content, not model analysis.")
        st.write(result["summary"])
        observations, interpretations, comparison, reviews = st.tabs(
            ["Visible observations", "Interpretations", "Context comparison", "Human review & export"])
        with observations:
            for observation in result["observations"]:
                with st.container(border=True):
                    st.markdown(f'**{observation["id"]} · {observation["location"]}**')
                    st.write(observation["description"])
                    if observation["visible_text"]:
                        st.caption("Visible text — verbatim")
                        st.text(observation["visible_text"])
                    st.caption(f'Approximate bounds: {observation["bbox"]}')
                    if observation["uncertainty"]:
                        st.write(observation["uncertainty"])
        with interpretations:
            for relation in result["sign_relations"]:
                with st.container(border=True):
                    st.markdown(f'**{relation["sign_vehicle"]}**')
                    st.caption("Evidence: " + ", ".join(relation["observation_ids"]) + " · " + ", ".join(relation["relation_types"]))
                    st.write("Proposed object: " + relation["proposed_object"])
                    st.write("Possible reading: " + relation["proposed_interpretant"])
                    st.write("Grounds: " + relation["grounds"])
                    st.write("Alternatives: " + "; ".join(relation["alternative_readings"]))
                    st.write("Limits: " + relation["limits"])
            st.markdown("**Candidate insights — provisional**")
            for insight in result["candidate_insights"]:
                st.write(insight)
            st.write({"uncertainties": result["uncertainties"], "tags": result["tags"]})
        with comparison:
            if record["stage"] == "blind":
                st.info("No external context was sent for this reading.")
            else:
                first, second = st.columns(2)
                with first:
                    st.markdown("**Original blind reading**")
                    st.write(repo.result(record["parent_reading_id"])["summary"])
                    with st.expander("Complete blind record"):
                        st.json(repo.result(record["parent_reading_id"]))
                with second:
                    st.markdown("**Contextual updates**")
                    for update in result["contextual_updates"]:
                        st.write(update)
        with reviews:
            st.caption("Corrections are appended as human records. Original model output remains unchanged.")
            with st.form("review_" + reading_id):
                verdict = st.selectbox("Assessment", ["needs_revision", "useful_with_limits", "rejected"])
                notes = st.text_area("Notes, corrections, and evidence")
                save = st.form_submit_button("Save human review")
            if save:
                try:
                    repo.save_review(reading_id, verdict=verdict, notes=notes)
                    st.success("Review saved locally.")
                except ValueError as exc:
                    st.error(str(exc))
            for review in repo.reviews(reading_id):
                st.caption(review["created_at"] + " · " + review["verdict"])
                st.text(review["notes"])
            if st.button("Export local reference package"):
                folder = repo.export(reading_id)
                st.success("Reference package saved locally.")
                st.code(str(folder), language=None)
            st.download_button("Download reading JSON", data=json.dumps(result, ensure_ascii=False, indent=2),
                               file_name=reading_id + ".json", mime="application/json")

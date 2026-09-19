"""Knowledge-stage orchestration for evidence-linked visual interpretations."""
import base64
from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from .. import __version__
from ..information.reading_encoding import validate_observations
from .interpretation import validate_interpretations
from .records import digest, identifier, now, write_json

RESOURCES = Path(__file__).parent / "resources"


def token(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode())


def validate_result(value, schema, stage):
    Draft202012Validator(schema).validate(value)
    observation_ids = validate_observations(value["observations"])
    validate_interpretations(value, observation_ids, stage)
    return value


def prepare(repo, artwork_id, *, model, stage="blind", parent_reading_id=None, mode="live"):
    if stage not in {"blind", "contextual"} or mode not in {"live", "demo"}:
        raise ValueError("Invalid reading stage or mode")
    if not model.strip():
        raise ValueError("Enter a model ID.")
    artwork = repo.artwork(artwork_id)
    raw = repo.image(artwork_id).read_bytes()
    if digest(raw) != artwork["input_sha256"]:
        raise ValueError("Stored image changed; import it as a new artwork.")
    instructions = (RESOURCES / "prompts" / f"{stage}.md").read_text(encoding="utf-8")
    schema = json.loads((RESOURCES / "schemas" / "reading.schema.json").read_text(encoding="utf-8"))
    payload = "Read the attached image. No external context is provided."
    parent_hash = None
    if stage == "contextual":
        if not parent_reading_id:
            raise ValueError("Choose a completed blind reading.")
        parent = repo.reading(parent_reading_id)
        if (parent["artwork_id"] != artwork_id or parent["stage"] != "blind"
                or parent["status"] != "completed" or parent["mode"] != mode):
            raise ValueError("Parent must be a completed blind reading of this artwork in the same mode.")
        if not artwork["context"].strip() and not artwork["source"].strip():
            raise ValueError("Import the artwork with source or background text for this stage.")
        blind = repo.result(parent_reading_id)
        parent_hash = token(blind)
        payload = json.dumps({
            "blind_reading": blind,
            "unverified_source_material": {"source": artwork["source"], "context": artwork["context"]},
        }, ensure_ascii=False, indent=2)
    elif parent_reading_id:
        raise ValueError("A blind reading has no parent.")
    if mode == "demo":
        from .demo import image_bytes
        if artwork["kind"] != "synthetic_demo" or artwork["original_sha256"] != digest(image_bytes()):
            raise ValueError("Offline fixture mode only supports the built-in synthetic artwork.")
    return {
        "format_version": 1, "app_version": __version__, "artwork_id": artwork_id,
        "stage": stage, "mode": mode, "model": model.strip(),
        "instructions": instructions, "input_text": payload, "response_schema": schema,
        "image_sha256": digest(raw), "image_file": "input.png",
        "parent_reading_id": parent_reading_id, "parent_result_sha256": parent_hash,
        "store": False, "image_detail": "high", "max_output_tokens": 6000,
    }


def execute(repo, request, *, approved_token, provider):
    request = deepcopy(request)
    if approved_token != token(request):
        raise ValueError("Request changed. Preview it again.")
    current = prepare(repo, request["artwork_id"], model=request["model"], stage=request["stage"],
                      parent_reading_id=request["parent_reading_id"], mode=request["mode"])
    if token(current) != approved_token:
        raise ValueError("Image, prompts or context changed. Preview again.")
    raw = repo.image(request["artwork_id"]).read_bytes()
    if digest(raw) != request["image_sha256"]:
        raise ValueError("Image changed after preview.")
    reading_id = identifier("reading")
    folder = repo.path("readings", reading_id)
    folder.mkdir()
    (folder / "input.png").write_bytes(raw)
    write_json(folder / "request.json", request)
    record = {"id": reading_id, "format_version": 1, "artwork_id": request["artwork_id"],
              "stage": request["stage"], "mode": request["mode"], "model": request["model"],
              "parent_reading_id": request["parent_reading_id"], "created_at": now(),
              "request_sha256": approved_token, "status": "requested",
              "epistemic_status": "synthetic_fixture" if request["mode"] == "demo" else "unreviewed_model_hypotheses"}
    write_json(folder / "manifest.json", record)
    try:
        response = provider(request, raw)
        write_json(folder / "response.json", response)
        if response["status"] != "completed" or not response.get("output_text"):
            raise ValueError("Model did not return a completed structured reading.")
        result = validate_result(json.loads(response["output_text"]), request["response_schema"], request["stage"])
        write_json(folder / "result.json", result)
        record.update(status="completed", completed_at=now())
    except BaseException as exc:
        record.update(status="failed" if isinstance(exc, Exception) else "interrupted",
                      error_type=type(exc).__name__, completed_at=now())
        write_json(folder / "manifest.json", record)
        raise
    write_json(folder / "manifest.json", record)
    return record


class OpenAIProvider:
    """One Responses call. No retries and no credential serialization."""
    def __call__(self, request, image):
        from openai import OpenAI
        with OpenAI(max_retries=0, timeout=180.0) as client:
            result = client.responses.create(
                model=request["model"], store=False, instructions=request["instructions"],
                max_output_tokens=request["max_output_tokens"],
                input=[{"role": "user", "content": [
                    {"type": "input_text", "text": request["input_text"]},
                    {"type": "input_image", "detail": request["image_detail"],
                     "image_url": "data:image/png;base64," + base64.b64encode(image).decode("ascii")},
                ]}],
                text={"format": {"type": "json_schema", "name": "visual_reading",
                                  "strict": True, "schema": request["response_schema"]}},
            )
            return {"status": result.status, "output_text": result.output_text,
                    "response_id": result.id, "request_id": getattr(result, "_request_id", None),
                    "usage": result.usage.model_dump(mode="json") if result.usage else None}

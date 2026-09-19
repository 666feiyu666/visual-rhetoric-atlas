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


def prepare(repo, object_id, *, model, stage="blind", parent_reading_id=None):
    if stage not in {"blind", "contextual"}:
        raise ValueError("Invalid reading stage")
    if not model.strip():
        raise ValueError("Enter a model ID.")
    object_record = repo.object(object_id)
    asset = repo.canonical_asset(object_id)
    image_path = repo.image(object_id)
    raw = image_path.read_bytes()
    if asset.get("sha256") and digest(raw) != asset["sha256"]:
        raise ValueError("Canonical corpus image changed and no longer matches its Information record.")
    instructions = (RESOURCES / "prompts" / f"{stage}.md").read_text(encoding="utf-8")
    schema = json.loads((RESOURCES / "schemas" / "reading.schema.json").read_text(encoding="utf-8"))
    payload = "Read the attached image. No external context is provided."
    parent_hash = None
    if stage == "contextual":
        if not parent_reading_id:
            raise ValueError("Choose a completed blind reading.")
        parent = repo.reading(parent_reading_id)
        if (parent["object_id"] != object_id or parent["stage"] != "blind"
                or parent["status"] != "completed"):
            raise ValueError("Parent must be a completed blind reading of this corpus object.")
        blind = repo.result(parent_reading_id)
        parent_hash = token(blind)
        payload = json.dumps({
            "blind_reading": blind,
            "unverified_source_material": repo.source_context(object_id),
        }, ensure_ascii=False, indent=2)
    elif parent_reading_id:
        raise ValueError("A blind reading has no parent.")
    return {
        "format_version": 1, "app_version": __version__, "object_id": object_id,
        "canonical_asset_id": object_record["canonical_asset_id"],
        "stage": stage, "model": model.strip(),
        "instructions": instructions, "input_text": payload, "response_schema": schema,
        "image_sha256": digest(raw), "image_mime": asset.get("mime") or "application/octet-stream",
        "image_file": "input" + image_path.suffix.lower(),
        "parent_reading_id": parent_reading_id, "parent_result_sha256": parent_hash,
        "store": False, "image_detail": "high", "max_output_tokens": 6000,
    }


def execute(repo, request, *, approved_token, provider):
    request = deepcopy(request)
    if approved_token != token(request):
        raise ValueError("Request changed. Preview it again.")
    current = prepare(repo, request["object_id"], model=request["model"], stage=request["stage"],
                      parent_reading_id=request["parent_reading_id"])
    if token(current) != approved_token:
        raise ValueError("Image, prompts or context changed. Preview again.")
    raw = repo.image(request["object_id"]).read_bytes()
    if digest(raw) != request["image_sha256"]:
        raise ValueError("Image changed after preview.")
    reading_id = identifier("reading")
    folder = repo.path("readings", reading_id)
    folder.mkdir()
    (folder / request["image_file"]).write_bytes(raw)
    write_json(folder / "request.json", request)
    record = {"id": reading_id, "format_version": 1, "object_id": request["object_id"],
              "canonical_asset_id": request["canonical_asset_id"], "image_file": request["image_file"],
              "stage": request["stage"], "model": request["model"],
              "parent_reading_id": request["parent_reading_id"], "created_at": now(),
              "request_sha256": approved_token, "status": "requested",
              "epistemic_status": "unreviewed_model_hypotheses"}
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
                     "image_url": f"data:{request['image_mime']};base64," + base64.b64encode(image).decode("ascii")},
                ]}],
                text={"format": {"type": "json_schema", "name": "visual_reading",
                                  "strict": True, "schema": request["response_schema"]}},
            )
            return {"status": result.status, "output_text": result.output_text,
                    "response_id": result.id, "request_id": getattr(result, "_request_id", None),
                    "usage": result.usage.model_dump(mode="json") if result.usage else None}

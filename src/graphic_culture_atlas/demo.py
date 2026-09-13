"""Hand-authored synthetic fixture, never presented as a model analysis."""
import io
import json
from PIL import Image, ImageDraw


def image_bytes():
    image = Image.new("RGB", (600, 800), "#faf8f3")
    draw = ImageDraw.Draw(image)
    draw.text((60, 180), "A GAP IN THE SEQUENCE", fill="#252a2b", font_size=27)
    for i in range(6):
        if i != 3:
            draw.rectangle((60+i*80, 350, 112+i*80, 445), fill="#252a2b")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def result(stage):
    value = {
        "summary": "OFFLINE FIXTURE: a synthetic arrangement of text and interrupted repetition.",
        "observations": [
            {"id": "o1", "description": "A short line of dark text above the shapes.",
             "location": "Upper-left area", "bbox": [0.08, 0.20, 0.95, 0.28],
             "visible_text": "A GAP IN THE SEQUENCE", "uncertainty": ""},
            {"id": "o2", "description": "Five filled rectangles occupy six equally spaced positions; the fourth position is empty.",
             "location": "Middle horizontal band", "bbox": [0.10, 0.43, 0.86, 0.56],
             "visible_text": "", "uncertainty": "The grouping is a visual description, not an intended message."}
        ],
        "sign_relations": [{
            "observation_ids": ["o1", "o2"], "sign_vehicle": "Repetition with one omitted position",
            "proposed_object": "An interruption in a sequence",
            "proposed_interpretant": "A viewer might notice an absence or a pause.",
            "relation_types": ["iconic", "symbolic"],
            "grounds": "A schematic sequence establishes expectation; the wording anchors one possible reading.",
            "alternative_readings": ["A missing or unfinished item"],
            "limits": "No audience reception or author intent has been established."
        }],
        "candidate_insights": [{
            "observation_ids": ["o2"], "claim": "An omission can become perceptible against a regular sequence.",
            "applicability": "Layouts with a clear repeated unit and enough repetitions to establish expectation.",
            "limits": "This does not establish what the omission means."
        }],
        "contextual_updates": [], "uncertainties": ["This is a hand-authored software fixture, not a model result."],
        "tags": ["synthetic-demo", "repetition", "negative-space"]
    }
    if stage == "contextual":
        value["contextual_updates"] = [{
            "earlier_claim": "The gap may be read as an interruption.",
            "assessment": "supported",
            "context_evidence": "The built-in source describes a synthetic software fixture.",
            "explanation": "The source explains its origin but does not establish an audience reading."
        }]
    return value


class DemoProvider:
    def __call__(self, request, image):
        if request["mode"] != "demo":
            raise ValueError("Fixture provider cannot execute live readings.")
        return {"status": "completed", "output_text": json.dumps(result(request["stage"])),
                "response_id": None, "usage": None, "fixture": True}

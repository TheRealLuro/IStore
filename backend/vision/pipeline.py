"""Pass-A vision pipeline.

One CLIP image embedding feeds four zero-shot queries:
content type, scene category, face likelihood, and tag generation.
Returns a VisionResult that the upload orchestrator persists into the
images row plus the image_tags table.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image as PILImage

from backend.vision.runtime import get_clip


CONTENT_TYPE_LABELS: tuple[tuple[str, str], ...] = (
    ("photo", "a photograph"),
    ("screenshot", "a screenshot of a software interface or webpage"),
    ("document", "a scanned document or page of printed text"),
    ("illustration", "a digital illustration or drawing or painting"),
    ("icon", "a logo or icon on a plain background"),
)

FACE_LIKELIHOOD_LABELS: tuple[tuple[str, str], ...] = (
    ("face", "a photograph showing one or more people"),
    ("noface", "a photograph with no people in it"),
)

INDOOR_OUTDOOR_LABELS: tuple[tuple[str, str], ...] = (
    ("indoor", "a photograph taken indoors"),
    ("outdoor", "a photograph taken outdoors"),
)

SCENE_LABELS: tuple[tuple[str, str], ...] = (
    ("kitchen", "a photo of a kitchen"),
    ("bathroom", "a photo of a bathroom"),
    ("bedroom", "a photo of a bedroom"),
    ("living_room", "a photo of a living room"),
    ("office", "a photo of an office or workplace"),
    ("restaurant", "a photo of a restaurant or cafe interior"),
    ("store", "a photo inside a store or shop"),
    ("classroom", "a photo of a classroom"),
    ("gym", "a photo of a gym or fitness studio"),
    ("park", "a photo of a park or garden"),
    ("beach", "a photo of a beach"),
    ("mountain", "a photo of mountains"),
    ("forest", "a photo of a forest"),
    ("desert", "a photo of a desert"),
    ("ocean", "a photo of the ocean or sea"),
    ("river", "a photo of a river or lake"),
    ("snow", "a photo of a snowy landscape"),
    ("city_street", "a photo of a city street"),
    ("highway", "a photo of a highway or road"),
    ("airport", "a photo of an airport"),
    ("stadium", "a photo of a stadium or sports field"),
    ("concert", "a photo of a concert or live music venue"),
    ("museum", "a photo inside a museum or gallery"),
    ("vehicle_interior", "a photo of the inside of a vehicle"),
    ("food", "a close-up photo of food or a meal"),
    ("portrait", "a portrait photo of a person"),
    ("group_photo", "a group photo of several people"),
    ("pet", "a photo of a pet animal"),
    ("wildlife", "a photo of wild animals"),
    ("event", "a photo of an event or celebration"),
)

TAG_VOCABULARY: tuple[str, ...] = (
    "dog", "cat", "bird", "horse", "fish", "wildlife",
    "tree", "flower", "plant", "mountain", "ocean", "river", "lake",
    "forest", "snow", "beach", "sunset", "sunrise", "sky", "clouds",
    "city", "street", "building", "bridge", "vehicle", "car", "bicycle", "boat",
    "airplane", "train",
    "food", "drink", "coffee", "dessert", "fruit", "vegetable",
    "kitchen", "restaurant", "bedroom", "bathroom",
    "wedding", "party", "concert", "sports", "fitness", "yoga",
    "child", "baby", "family", "selfie", "group_photo",
    "art", "music", "book", "computer", "phone",
    "indoors", "outdoors", "night", "day",
    "beach_holiday", "hiking", "cooking",
)

# Threshold below which we don't trust the classification.
MIN_CONTENT_CONFIDENCE = 0.55
FACE_THRESHOLD = 0.50
TAG_THRESHOLD = 0.20


@dataclass
class VisionResult:
    clip_embedding: list[float]              # length 768
    content_type: str
    content_confidence: float
    scene_label: str
    scene_confidence: float
    face_likelihood: float
    indoor_outdoor: str                       # 'indoor' | 'outdoor' | 'unknown'
    tags: list[tuple[str, float]]             # [(label, confidence), ...]


def _open_for_clip(raw: bytes, preprocess):
    with PILImage.open(BytesIO(raw)) as im:
        im = im.convert("RGB")
    return preprocess(im).unsqueeze(0)


def _zero_shot_softmax(image_features, prompts, model, tokenizer, device):
    """Return a numpy array of softmax probabilities over the prompt list."""
    import numpy as np
    import torch

    texts = [p for _, p in prompts]
    tokens = tokenizer(texts).to(device)
    with torch.no_grad():
        text_feats = model.encode_text(tokens)
        text_feats = text_feats / text_feats.norm(dim=-1, keepdim=True)
        # CLIP uses logit_scale; for our purposes plain cosine softmax is fine.
        logits = (image_features @ text_feats.T) * 100.0
        probs = logits.softmax(dim=-1)[0]
    return probs.float().cpu().numpy()


def process(raw_bytes: bytes, top_k_tags: int = 8) -> VisionResult:
    """Run the full Pass-A pipeline on a single image."""
    import torch

    model, preprocess, tokenizer, device = get_clip()

    img_tensor = _open_for_clip(raw_bytes, preprocess).to(device)
    if device == "cuda":
        img_tensor = img_tensor.half()

    with torch.no_grad():
        image_feats = model.encode_image(img_tensor)
        image_feats = image_feats / image_feats.norm(dim=-1, keepdim=True)

    embedding_768 = image_feats[0].float().cpu().numpy().tolist()

    ct_probs = _zero_shot_softmax(image_feats, CONTENT_TYPE_LABELS, model, tokenizer, device)
    ct_idx = int(ct_probs.argmax())
    content_type = CONTENT_TYPE_LABELS[ct_idx][0]
    content_confidence = float(ct_probs[ct_idx])

    scene_probs = _zero_shot_softmax(image_feats, SCENE_LABELS, model, tokenizer, device)
    s_idx = int(scene_probs.argmax())
    scene_label = SCENE_LABELS[s_idx][0]
    scene_confidence = float(scene_probs[s_idx])

    face_probs = _zero_shot_softmax(image_feats, FACE_LIKELIHOOD_LABELS, model, tokenizer, device)
    face_likelihood = float(face_probs[0])

    io_probs = _zero_shot_softmax(image_feats, INDOOR_OUTDOOR_LABELS, model, tokenizer, device)
    io_idx = int(io_probs.argmax())
    indoor_outdoor = INDOOR_OUTDOOR_LABELS[io_idx][0] if io_probs[io_idx] > 0.6 else "unknown"

    tag_prompts = tuple((t, f"a photo of {t.replace('_', ' ')}") for t in TAG_VOCABULARY)
    tag_probs = _zero_shot_softmax(image_feats, tag_prompts, model, tokenizer, device)
    # Independent threshold filtering rather than softmax-only ranking.
    import numpy as np

    order = np.argsort(-tag_probs)
    tags: list[tuple[str, float]] = []
    for idx in order[:top_k_tags]:
        score = float(tag_probs[idx])
        if score < TAG_THRESHOLD:
            break
        tags.append((TAG_VOCABULARY[idx], score))

    return VisionResult(
        clip_embedding=embedding_768,
        content_type=content_type,
        content_confidence=content_confidence,
        scene_label=scene_label,
        scene_confidence=scene_confidence,
        face_likelihood=face_likelihood,
        indoor_outdoor=indoor_outdoor,
        tags=tags,
    )

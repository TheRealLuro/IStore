"""Phase 11 v2 — AI Vision content summary.

Runs after upload as a BackgroundTask (mirroring the Pass B face scan).
Per-category dispatch:

    image    → Florence-2 detailed caption (+ <OCR> for whiteboard /
               document / screenshot scenes) → Qwen2.5-Instruct rewriter
               composes one natural search-friendly sentence using the
               caption, named people, OCR text, and scene metadata.
    video    → ffmpeg single keyframe → image branch.
    document → server-side text extraction (pypdf / python-docx / openpyxl /
               plaintext) → BART abstractive (sumy LSA fallback).

All three paths populate `images.summary`, `summary_topic`,
`summary_points`, `summary_generated_at`, and flip `pending_summary =
false` in one update.

Each helper is best-effort: optional deps that fail to import (Florence-2
without `[ml]` extras, Qwen2.5 weights uncached, sumy without nltk data)
fall through to a regex-based deterministic path so a torch-less test
environment still produces a usable summary.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.image import fetch_original
from backend.models import Face, FaceDetection, Image, Person

logger = logging.getLogger(__name__)


# --- public dataclass ------------------------------------------------------


@dataclass
class SummaryResult:
    topic: str
    summary: str
    points: list[str]


# --- public entry point ----------------------------------------------------


async def summarize_image_id(session: AsyncSession, image_id: UUID) -> None:
    """Load the image row, run the right strategy, write the summary back.

    Always overwrites the existing summary — switching captioning models or
    upgrading the doc extractor needs the row to be regenerable. Callers
    that don't want to redo work should check `pending_summary` themselves
    before invoking. The upload path only invokes this once per upload.
    """
    if not settings.summarize_enabled:
        return

    image = await session.get(Image, image_id)
    if image is None or image.deleted_at is not None:
        return

    try:
        raw_bytes, _mime = await fetch_original(image)
    except Exception:
        logger.exception(
            "summarize: failed to fetch original for %s", image_id
        )
        await _mark_done(session, image, None)
        return

    # Look up named people while we're still in the async event loop —
    # `_dispatch` runs in a thread and can't issue DB queries against the
    # async engine. Empty list when no consent / no faces yet / no
    # named persons in this image.
    named_people: list[str] = []
    if image.category == "image":
        named_people = await _load_named_people(
            session, image.id, image.user_id
        )

    try:
        result = await asyncio.to_thread(
            _dispatch, image, raw_bytes, named_people
        )
    except Exception:
        logger.exception("summarize: dispatch failed for %s", image_id)
        result = None

    await _mark_done(session, image, result)


async def _load_named_people(
    session: AsyncSession, image_id, user_id
) -> list[str]:
    """Distinct, ordered names of identified people in this image.

    Pass B (faces) populates `face_detections.face_id → faces.person_id →
    persons.display_name`. Anonymous detections (face exists but no person
    named yet) are filtered out — generic captions are better than ones
    that say "and another person" with no anchor.
    """
    # Min(bbox_x) lets us order persons left-to-right even with DISTINCT —
    # plain ORDER BY bbox_x violates the SELECT-list rule on Postgres.
    rows = (
        await session.execute(
            select(Person.display_name, func.min(FaceDetection.bbox_x).label("x"))
            .join(Face, Face.person_id == Person.id)
            .join(FaceDetection, FaceDetection.face_id == Face.id)
            .where(
                FaceDetection.image_id == image_id,
                FaceDetection.user_id == user_id,
                Person.display_name.is_not(None),
                Person.display_name != "",
            )
            .group_by(Person.display_name)
            .order_by("x")
        )
    ).all()
    return [r[0] for r in rows if r[0]]


async def _mark_done(
    session: AsyncSession, image: Image, result: Optional[SummaryResult]
) -> None:
    if result is not None:
        image.summary = result.summary
        image.summary_topic = result.topic
        image.summary_points = result.points
    image.pending_summary = False
    image.summary_generated_at = datetime.now(timezone.utc)
    await session.commit()


def _dispatch(
    image: Image, raw_bytes: bytes, named_people: list[str]
) -> Optional[SummaryResult]:
    if image.category == "image":
        return _summarize_image(image, raw_bytes, named_people)
    if image.category == "video":
        return _summarize_video(image, raw_bytes)
    if image.category == "document":
        return _summarize_document(image, raw_bytes)
    return None


# --- image -----------------------------------------------------------------


# Scenes / content types where reading visible text materially improves
# the summary. We don't OCR every cat photo — Florence-2 OCR is ~1-2 s on
# GPU, would dominate latency on bulk re-summarize.
_OCR_SCENES = frozenset({
    "classroom", "lecture_room", "conference_room", "computer_room",
    "library", "office", "bookstore", "whiteboard",
})

_OCR_CONTENT_TYPES = frozenset({"screenshot", "document"})


def _summarize_image(
    image: Image, raw_bytes: bytes, named_people: list[str]
) -> SummaryResult:
    """Florence-2 detailed caption + scene-gated OCR + LLM rewrite.

    Flow:
      1. Florence-2 <MORE_DETAILED_CAPTION>  — dense scene description.
      2. Florence-2 <OCR>                    — only for whiteboard /
                                               classroom / screenshot etc.
      3. Qwen2.5-Instruct rewrite            — one natural sentence
                                               combining all signals.
      4. Regex fallback (clean+splice)       — when the LLM is unavailable.

    The fallback chain means the function still returns a usable summary
    when only some of the models are loadable, instead of crashing or
    emitting a generic placeholder.
    """
    raw_caption = (_caption_image(raw_bytes) or "").strip()

    needs_ocr = (
        (image.content_type or "") in _OCR_CONTENT_TYPES
        or (image.scene_label or "") in _OCR_SCENES
    )
    ocr_text = _ocr_image(raw_bytes) if needs_ocr else None

    summary = _llm_rewrite_summary(
        caption=raw_caption,
        names=named_people,
        ocr_text=ocr_text,
        scene=image.scene_label,
        setting=image.indoor_outdoor,
        content_type=image.content_type,
    )

    if not summary:
        # Deterministic fallback — same path as v1.
        cleaned = _clean_caption(raw_caption)
        spliced = _splice_names(cleaned, named_people)
        summary = spliced or _fallback_image_summary(image)
        if ocr_text and "text" not in summary.lower():
            excerpt = ocr_text[:120].replace("\n", " ").strip()
            if excerpt:
                summary = f"{summary.rstrip('.')}. Visible text: {excerpt}."

    topic = _compose_topic(image, summary, named_people)

    points: list[str] = []
    if named_people:
        # Lead with names — the most useful search anchor.
        points.append("People: " + ", ".join(named_people))
    elif image.face_likelihood and image.face_likelihood > 0.5:
        points.append("Likely contains people")
    if image.content_type and image.content_type != "photo":
        points.append(f"Type: {image.content_type}")
    if image.scene_confidence and image.scene_label:
        points.append(
            f"Scene: {image.scene_label.replace('_', ' ')} "
            f"({int(image.scene_confidence * 100)}%)"
        )
    if image.indoor_outdoor and image.indoor_outdoor != "unknown":
        points.append(f"Setting: {image.indoor_outdoor}")
    if ocr_text:
        excerpt = ocr_text[:80].replace("\n", " ").strip()
        if excerpt:
            ellipsis = "…" if len(ocr_text) > 80 else ""
            points.append(f"Text: {excerpt}{ellipsis}")
    # Top tags from image_tags relationship if loaded; otherwise skip silently.
    tag_labels = []
    try:
        for it in image.tags or []:
            if it.tag is not None and it.tag.label:
                tag_labels.append(it.tag.label)
        if tag_labels:
            points.append("Tags: " + ", ".join(tag_labels[:5]))
    except Exception:
        pass

    return SummaryResult(topic=topic, summary=summary, points=points[:5])


# Filler phrases BLIP frequently leads with — "This is a photo of a man"
# is filler around the actual content "a man". Stripping them keeps the
# searchable terms (subject, scene, objects) and drops the framing.
# Order matters: longer, more specific prefixes must come before shorter
# subsets so we don't half-strip them.
_FILLER_PREFIXES = (
    r"^the (?:image|photo|picture) shows\s+",
    r"^in the (?:image|photo|picture),?\s+",
    r"^in this (?:image|photo|picture),?\s+",
    r"^this is a (?:photo|picture|photograph|image) of\s+",
    r"^this is an? (?:photo|picture|photograph|image)\s+",
    r"^an? (?:image|picture|photo|photograph) of\s+",
    r"^this is\s+",
    r"^there are\s+",
    r"^there is\s+",
)


# Patterns BLIP uses for generic person references, in order of priority
# (most specific first). Each gets exactly one substitution per caption.
_PERSON_PATTERNS = (
    r"\btwo people\b",
    r"\bthree people\b",
    r"\bseveral people\b",
    r"\ba group of people\b",
    r"\bgroup of people\b",
    r"\bpeople\b",
    r"\ba young man\b",
    r"\ba young woman\b",
    r"\ba man\b",
    r"\ba woman\b",
    r"\ba boy\b",
    r"\ba girl\b",
    r"\ba person\b",
    r"\bthe man\b",
    r"\bthe woman\b",
)


def _clean_caption(caption: str) -> str:
    """Strip BLIP filler and normalize awkward phrasings before name splicing.

    BLIP often pads captions with "This is a..." / "There is a..." which
    adds no semantic value and dilutes search relevance — the user is
    searching for "whiteboard math classroom", not for "this is a photo".
    Removing the filler also lets `_splice_names` produce natural sentences:
    "There is a man writing" → "There is Mr Koler writing" (awkward) becomes
    "A man writing" → "Mr Koler writing" (clean).

    Also rewrites "taking a picture/photo of himself|herself|themselves" to
    "taking a selfie", which is the term people actually search by.
    """
    if not caption:
        return caption

    cleaned = caption
    for pattern in _FILLER_PREFIXES:
        new, n = re.subn(pattern, "", cleaned, count=1, flags=re.IGNORECASE)
        if n > 0:
            cleaned = new
            break  # one prefix at most — chained strips would over-cut

    # Plural first so "pictures of themselves" doesn't get half-matched by
    # the singular pattern.
    cleaned = re.sub(
        r"\btaking (?:pictures|photos) of themselves\b",
        "taking selfies",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\btaking a (?:picture|photo) of (?:himself|herself|themselves)\b",
        "taking a selfie",
        cleaned,
        flags=re.IGNORECASE,
    )

    cleaned = cleaned.strip()
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]
        if cleaned[-1] not in ".!?":
            cleaned += "."
    return cleaned


def _splice_names(caption: str, names: list[str]) -> str:
    """Substitute the first generic person reference with the named people.

    "A black and white photo of a man looking out a car window."
        + names=["Me"]
    → "A black and white photo of Me looking out a car window."

    Returns the caption unchanged when nothing matches or no names are
    known, so BLIP captions stay grammatical and search-friendly.
    """
    if not caption or not names:
        return caption
    if len(names) == 1:
        replacement = names[0]
    elif len(names) == 2:
        replacement = f"{names[0]} and {names[1]}"
    else:
        replacement = ", ".join(names[:-1]) + f", and {names[-1]}"

    spliced = caption
    matched = False
    for pattern in _PERSON_PATTERNS:
        new_caption, n = re.subn(
            pattern, replacement, spliced, count=1, flags=re.IGNORECASE
        )
        if n > 0:
            spliced = new_caption
            matched = True
            break
    if not matched:
        # No person pattern matched (e.g. landscape) — append parenthetical
        # so names still index for search without mangling the sentence.
        return f"{caption} (with {replacement})"

    return _polish_after_splice(spliced, names)


# Pronoun / grammar cleanup that only makes sense once names are inserted.
# BLIP says "a man that is taking a picture of himself with his phone";
# splicing yields "Me that is taking a picture of himself with his phone";
# this pass rewrites it to "Me taking a selfie with my phone".
_FIRST_PERSON_NAMES = {"me", "i"}


def _polish_after_splice(caption: str, names: list[str]) -> str:
    # 1. "[name] that is V-ing" / "[name] who is V-ing" → "[name] V-ing".
    #    Reads natural in English ("Mr Koler standing" beats "Mr Koler that
    #    is standing") and is what BLIP really meant.
    for name in names:
        esc = re.escape(name)
        caption = re.sub(
            rf"\b{esc}\s+(?:that|who)\s+is\s+",
            f"{name} ",
            caption,
        )

    # 2. First-person pronoun rewrite. When the spliced subject is "Me" or
    #    "I", any third-person pronouns BLIP emitted refer back to that
    #    subject and need to flip to first person — otherwise we get
    #    "Me taking a selfie with his cell phone".
    if any(n.lower() in _FIRST_PERSON_NAMES for n in names):
        caption = re.sub(
            r"\b(?:himself|herself|themselves)\b",
            "myself",
            caption,
            flags=re.IGNORECASE,
        )
        # "his" is unambiguously possessive; "her" is mostly possessive in
        # caption contexts ("her phone", "her face"), so map both to "my".
        caption = re.sub(r"\bhis\b", "my", caption, flags=re.IGNORECASE)
        caption = re.sub(r"\bher\b", "my", caption, flags=re.IGNORECASE)
        # Object pronoun "him" is rare but happens ("a dog next to him").
        caption = re.sub(r"\bhim\b", "me", caption, flags=re.IGNORECASE)

    return caption


def _compose_topic(
    image: Image, caption: str, named_people: list[str]
) -> str:
    """Pick a short, content-rich topic line. Named people lead — Drive's
    "People" facet works the same way."""
    cap_lower = caption.lower()
    is_selfie = "selfie" in cap_lower or "self portrait" in cap_lower
    is_group = (
        "group photo" in cap_lower
        or "group of people" in cap_lower
        or len(named_people) >= 2
    )

    if named_people:
        names = ", ".join(named_people)
        if is_selfie:
            return f"Selfie of {names}"
        if is_group:
            return f"Group photo · {names}"
        return f"Photo of {names}"

    if is_selfie:
        return "Selfie"
    if is_group:
        return "Group photo"
    if image.scene_label == "portrait" or image.scene_label == "group_photo":
        return image.scene_label.replace("_", " ").title()
    if image.face_likelihood and image.face_likelihood > 0.7 and (
        "person" in cap_lower or "man" in cap_lower or "woman" in cap_lower
    ):
        return "Portrait"
    if image.scene_label and image.scene_label not in ("portrait",):
        return image.scene_label.replace("_", " ").title()
    return "Photo"


def _fallback_image_summary(image: Image) -> str:
    bits = []
    if image.scene_label:
        bits.append(image.scene_label.replace("_", " "))
    if image.indoor_outdoor and image.indoor_outdoor != "unknown":
        bits.append(image.indoor_outdoor)
    if not bits:
        return "Image."
    return "A " + " ".join(bits) + " image."


def _caption_image(raw_bytes: bytes) -> Optional[str]:
    """Detailed image caption. Florence-2 primary, BLIP fallback.

    Returns a 1-3 sentence detailed description, or None if both models
    fail to load. The caller folds None into the regex/fallback path.
    """
    cap = _florence_caption(raw_bytes)
    if cap:
        return cap
    return _blip_caption(raw_bytes)


def _florence_caption(raw_bytes: bytes) -> Optional[str]:
    """Florence-2 <MORE_DETAILED_CAPTION>. Returns None on any failure.

    Outputs are dense ("The image shows a man wearing glasses standing in
    front of a large whiteboard covered in handwritten mathematical
    equations and diagrams in blue marker"), much richer than BLIP's
    one-clause captions. Filler ("The image shows...") is stripped by the
    LLM rewriter or by `_clean_caption` in the regex fallback path.
    """
    try:
        from PIL import Image as PILImage
        import torch

        from backend.vision.runtime import get_florence2

        model, processor, device = get_florence2()
        image = PILImage.open(BytesIO(raw_bytes)).convert("RGB")

        prompt = "<MORE_DETAILED_CAPTION>"
        inputs = processor(text=prompt, images=image, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        if device == "cuda" and "pixel_values" in inputs:
            inputs["pixel_values"] = inputs["pixel_values"].half()

        with torch.no_grad():
            ids = model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=256,
                num_beams=3,
                do_sample=False,
            )
        text = processor.batch_decode(ids, skip_special_tokens=False)[0]
        parsed = processor.post_process_generation(
            text, task=prompt, image_size=(image.width, image.height)
        )
        out = (parsed.get(prompt) or "").strip()
        return out or None
    except Exception:
        logger.exception("florence2: caption failed")
        return None


def _ocr_image(raw_bytes: bytes) -> Optional[str]:
    """Florence-2 <OCR>. Returns extracted text or None.

    Reads visible text in the image — whiteboards, document scans,
    screenshots. Empty/whitespace-only output → None so callers don't
    propagate a useless empty string.
    """
    try:
        from PIL import Image as PILImage
        import torch

        from backend.vision.runtime import get_florence2

        model, processor, device = get_florence2()
        image = PILImage.open(BytesIO(raw_bytes)).convert("RGB")

        prompt = "<OCR>"
        inputs = processor(text=prompt, images=image, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        if device == "cuda" and "pixel_values" in inputs:
            inputs["pixel_values"] = inputs["pixel_values"].half()

        with torch.no_grad():
            ids = model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=512,
                num_beams=3,
                do_sample=False,
            )
        text = processor.batch_decode(ids, skip_special_tokens=False)[0]
        parsed = processor.post_process_generation(
            text, task=prompt, image_size=(image.width, image.height)
        )
        ocr = (parsed.get(prompt) or "").strip()
        return ocr or None
    except Exception:
        logger.exception("florence2: ocr failed")
        return None


def _blip_caption(raw_bytes: bytes) -> Optional[str]:
    """BLIP fallback when Florence-2 fails. Same shape as v1.

    BLIP produces short single-clause captions ("a man taking a selfie in
    a kitchen"); kept as a safety net so first-time installs that haven't
    pulled Florence-2 weights still work.
    """
    try:
        from PIL import Image as PILImage
        import torch

        from backend.vision.runtime import get_caption_model

        model, processor, device = get_caption_model()
        image = PILImage.open(BytesIO(raw_bytes)).convert("RGB")

        inputs = processor(images=image, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        if device == "cuda" and "pixel_values" in inputs:
            inputs["pixel_values"] = inputs["pixel_values"].half()

        with torch.no_grad():
            generated_ids = model.generate(
                **inputs,
                max_new_tokens=80,
                num_beams=5,
                do_sample=False,
                repetition_penalty=1.2,
            )
        caption = processor.decode(generated_ids[0], skip_special_tokens=True)
        caption = caption.strip()
        if caption:
            caption = caption[0].upper() + caption[1:]
            if caption[-1] not in ".!?":
                caption += "."
        return caption or None
    except Exception:
        logger.exception("blip: caption failed")
        return None


def _llm_rewrite_summary(
    caption: str,
    names: list[str],
    ocr_text: Optional[str],
    scene: Optional[str],
    setting: Optional[str],
    content_type: Optional[str],
) -> Optional[str]:
    """Qwen2.5-Instruct rewrites raw signals into one natural sentence.

    Replaces regex-based pronoun/grammar fixes with proper coreference:
    given ("a man taking a picture of himself", names=["Me"]) the LLM
    produces "Me taking a selfie with my phone" instead of mechanical
    substitution. When OCR text is provided, the LLM is instructed to
    DESCRIBE what the text is about ("matrix algebra equations") rather
    than copy it verbatim — keeps the summary short and search-friendly.

    Returns None when:
      - rewriter is disabled in settings
      - the model can't load (no weights, no torch, OOM)
      - the model emits an empty / pathological output
    Caller falls back to the deterministic regex pipeline in those cases.
    """
    if not settings.rewriter_enabled:
        return None
    if not caption and not ocr_text:
        return None

    try:
        import torch

        from backend.vision.runtime import get_summary_rewriter

        model, tokenizer, device = get_summary_rewriter()

        ctx_lines: list[str] = []
        if caption:
            ctx_lines.append(f"Caption: {caption}")
        if names:
            ctx_lines.append(f"People in image: {', '.join(names)}")
        if ocr_text:
            ctx_lines.append(f"Visible text in image: {ocr_text[:400]}")
        if scene:
            ctx_lines.append(f"Scene: {scene.replace('_', ' ')}")
        if setting and setting != "unknown":
            ctx_lines.append(f"Setting: {setting}")
        if content_type:
            ctx_lines.append(f"Content type: {content_type}")

        first_person = any(
            (n or "").strip().lower() in {"me", "i"} for n in names
        )

        instructions = (
            "Rewrite the image description as ONE natural, concise English "
            "sentence (under 30 words) that a user would actually write "
            "when searching for this photo. "
            "Use the named people instead of generic terms like 'a man'. "
            "If visible text is provided, describe WHAT the text is about "
            "(e.g. 'matrix algebra equations', 'a chat conversation', "
            "'a recipe') rather than quoting it. "
            "Do NOT start with 'The image shows', 'This is a', or 'There is'. "
            "Output only the rewritten sentence — no preamble, no quotes."
        )
        if first_person:
            instructions += (
                " The named person 'Me' is the photo owner — use first "
                "person ('I', 'my', 'myself')."
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "You rewrite raw image-analysis output into natural, "
                    "search-friendly captions. You are concise, factual, "
                    "and never invent details that aren't in the input."
                ),
            },
            {
                "role": "user",
                "content": instructions + "\n\n" + "\n".join(ctx_lines),
            },
        ]
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(prompt, return_tensors="pt").to(device)
        prompt_len = inputs.input_ids.shape[1]

        with torch.no_grad():
            out_ids = model.generate(
                **inputs,
                max_new_tokens=80,
                do_sample=False,
                num_beams=1,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        new_ids = out_ids[0][prompt_len:]
        reply = tokenizer.decode(new_ids, skip_special_tokens=True).strip()

        # Guard against the LLM going off — multi-line, JSON, etc.
        # Take the first line, strip surrounding quotes.
        reply = reply.split("\n")[0].strip().strip('"').strip("'").strip()
        if not reply or len(reply) > 400:
            return None
        # Ensure terminal punctuation.
        if reply[-1] not in ".!?":
            reply = reply.rstrip(",;:") + "."
        # Capitalize.
        reply = reply[0].upper() + reply[1:]
        return reply
    except Exception:
        logger.exception("rewriter: failed")
        return None


# --- video -----------------------------------------------------------------


def _summarize_video(image: Image, raw_bytes: bytes) -> Optional[SummaryResult]:
    """Extract a single keyframe via ffmpeg and reuse the image pipeline.

    Falls back to a filename-only summary when ffmpeg is unavailable. The
    Image row passed in is the video row, so vision fields are empty —
    Florence-2 carries the load on its own.
    """
    frame = _extract_keyframe(raw_bytes)
    caption = _florence2_caption(frame) if frame else None

    topic = "Video"
    if image.original_filename:
        topic = (
            image.original_filename.rsplit(".", 1)[0]
            .replace("_", " ")
            .replace("-", " ")
            .strip()
            or "Video"
        )

    summary = (caption or "Video file. Preview unavailable.").strip()
    points: list[str] = []
    if image.byte_size_original:
        mb = image.byte_size_original / (1024 * 1024)
        points.append(f"Size: {mb:.1f} MB")

    return SummaryResult(topic=topic, summary=summary, points=points[:5])


def _extract_keyframe(raw_bytes: bytes, seek_seconds: int = 5) -> Optional[bytes]:
    """Pipe video bytes through ffmpeg, capture one PNG frame, return bytes.

    Requires `ffmpeg` on PATH. Returns None on any failure (missing binary,
    unsupported container, etc.) so the caller can degrade gracefully.
    """
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-loglevel", "error",
                "-ss", str(seek_seconds),
                "-i", "pipe:0",
                "-frames:v", "1",
                "-f", "image2pipe",
                "-vcodec", "png",
                "pipe:1",
            ],
            input=raw_bytes,
            capture_output=True,
            timeout=30,
            check=True,
        )
        return proc.stdout
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


# --- document --------------------------------------------------------------


def _summarize_document(
    image: Image, raw_bytes: bytes
) -> Optional[SummaryResult]:
    text, topic = _extract_doc_text(image.original_filename or "", raw_bytes)
    if not text.strip():
        return SummaryResult(
            topic=topic or "Document",
            summary="Document content could not be extracted.",
            points=[],
        )

    truncated = text[: settings.summarize_doc_max_chars]
    summary = _extractive_summary(truncated)
    points = _extract_keypoints(truncated)

    return SummaryResult(
        topic=topic or "Document",
        summary=summary or truncated[:400].strip(),
        points=points[:5],
    )


def _extract_doc_text(
    filename: str, raw: bytes
) -> tuple[str, Optional[str]]:
    """Return (text, topic). Topic falls back to None when nothing usable."""
    name = filename.lower()
    try:
        if name.endswith(".pdf"):
            return _extract_pdf(raw)
        if name.endswith(".docx"):
            return _extract_docx(raw)
        if name.endswith((".xlsx", ".xls")):
            return _extract_xlsx(raw)
        if name.endswith((".txt", ".md", ".csv", ".log", ".json", ".tsv",
                          ".yaml", ".yml", ".toml", ".ini", ".sql", ".html",
                          ".htm", ".xml")):
            return _extract_plaintext(raw, filename)
    except Exception:
        logger.exception("doc extract failed for %s", filename)
    return "", None


def _extract_pdf(raw: bytes) -> tuple[str, Optional[str]]:
    from pypdf import PdfReader  # type: ignore

    reader = PdfReader(BytesIO(raw))
    pages_text = []
    for page in reader.pages[:30]:  # cap at 30 pages
        try:
            pages_text.append(page.extract_text() or "")
        except Exception:
            continue
    text = "\n\n".join(pages_text)

    topic: Optional[str] = None
    md = reader.metadata or {}
    if md and md.title:
        topic = str(md.title).strip() or None
    if not topic:
        # First non-blank line truncated.
        for line in text.splitlines():
            line = line.strip()
            if 4 <= len(line) <= 120:
                topic = line
                break
    return text, topic


def _extract_docx(raw: bytes) -> tuple[str, Optional[str]]:
    import docx  # type: ignore

    doc = docx.Document(BytesIO(raw))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    text = "\n".join(paragraphs)

    topic: Optional[str] = None
    # Heading 1 wins; otherwise first paragraph.
    for p in doc.paragraphs:
        if p.style and p.style.name and p.style.name.startswith("Heading 1"):
            if p.text.strip():
                topic = p.text.strip()
                break
    if not topic and paragraphs:
        topic = paragraphs[0][:120].strip() or None
    return text, topic


def _extract_xlsx(raw: bytes) -> tuple[str, Optional[str]]:
    import openpyxl  # type: ignore

    wb = openpyxl.load_workbook(BytesIO(raw), read_only=True, data_only=True)
    rows_out: list[str] = []
    sheet_names = wb.sheetnames
    for name in sheet_names[:3]:
        ws = wb[name]
        rows_out.append(f"# Sheet: {name}")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= 200:
                break
            cells = [str(c) for c in row if c not in (None, "")]
            if cells:
                rows_out.append(" | ".join(cells))
    text = "\n".join(rows_out)
    topic = sheet_names[0] if sheet_names else None
    return text, topic


def _extract_plaintext(raw: bytes, filename: str) -> tuple[str, Optional[str]]:
    text = raw.decode("utf-8", errors="replace")
    topic: Optional[str] = None
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if 4 <= len(stripped) <= 120:
            topic = stripped
            break
    if not topic:
        topic = filename.rsplit(".", 1)[0]
    return text, topic


# --- extractive summarization ---------------------------------------------


def _extractive_summary(text: str) -> str:
    """Abstractive document summary via DistilBART CNN.

    Tries BART first (real paraphrase, "The report covers Q4 growth and the
    Phase 11 release"). Falls back to sumy LSA, then to leading sentences,
    so summarization keeps working even if the model can't load.
    """
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    bart = _bart_summary(text)
    if bart:
        return bart

    n = settings.summarize_doc_sentence_count
    try:
        from sumy.parsers.plaintext import PlaintextParser  # type: ignore
        from sumy.nlp.tokenizers import Tokenizer  # type: ignore
        from sumy.summarizers.lsa import LsaSummarizer  # type: ignore

        parser = PlaintextParser.from_string(text, Tokenizer("english"))
        summarizer = LsaSummarizer()
        sentences = summarizer(parser.document, n)
        joined = " ".join(str(s) for s in sentences).strip()
        if joined:
            return joined
    except Exception:
        logger.exception("sumy LSA failed; using leading sentences")

    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(parts[:n]).strip()


def _bart_summary(text: str) -> Optional[str]:
    """Run DistilBART abstractive summarization. Returns None on any failure
    (model unavailable, OOM, tokenizer error). Caller falls back to sumy.

    BART has a 1024-token input cap; we truncate to ~3500 chars (≈ 800
    tokens for English) to leave room for the model's encoder context.
    """
    try:
        import torch

        from backend.vision.runtime import get_doc_summarizer

        model, tokenizer, device = get_doc_summarizer()
        truncated = text[:3500]
        inputs = tokenizer(
            truncated,
            return_tensors="pt",
            truncation=True,
            max_length=1024,
        ).to(device)

        with torch.no_grad():
            ids = model.generate(
                **inputs,
                max_length=140,
                min_length=40,
                num_beams=4,
                length_penalty=2.0,
                no_repeat_ngram_size=3,
                early_stopping=True,
            )
        out = tokenizer.decode(ids[0], skip_special_tokens=True).strip()
        return out or None
    except Exception:
        logger.exception("bart: summary failed")
        return None


def _extract_keypoints(text: str) -> list[str]:
    """Cheap keypoint heuristic: list-formatted lines + frequent capitalized
    nouns. Better than nothing without spinning up an LLM."""
    points: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^[\-\*•–—]\s+(.{4,160})$", line)
        if m:
            points.append(m.group(1).strip())
        elif re.match(r"^\d+[\.\)]\s+(.{4,160})$", line):
            points.append(re.sub(r"^\d+[\.\)]\s+", "", line).strip())
        if len(points) >= 5:
            break
    return points

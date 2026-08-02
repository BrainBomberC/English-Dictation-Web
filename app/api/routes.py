import json, re, logging, hashlib, math
from difflib import SequenceMatcher
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from app.config import load_sources, save_sources, PROJECTS_DIR, PROCESSING_SERVER

logger = logging.getLogger(__name__)
router = APIRouter()


_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
_MAX_WORDS_PER_SECOND = 8.0
_MIN_GLOBAL_ALIGNMENT = 0.70
_MIN_SENTENCE_ALIGNMENT = 0.55


def scan_all_sources():
    """扫描所有视频源目录"""
    sources = load_sources()
    videos = []
    seen = set()
    for src_dir in sources:
        source = Path(src_dir)
        if not source.exists():
            continue
        for f in sorted(source.rglob("*.mp4")):
            vid = build_video_info(f)
            if vid and vid["id"] not in seen:
                videos.append(vid)
                seen.add(vid["id"])
    return videos


def build_video_info(mp4_path):
    """根据 mp4 文件构建视频信息"""
    f = Path(mp4_path)
    name = f.stem
    vid_id = hashlib.md5(str(f).encode()).hexdigest()[:12]
    vid = {
        "id": vid_id,
        "name": name,
        "source_dir": str(f.parent),
        "video_path": str(f),
        "has_txt": False, "txt_path": None,
        "has_json": False, "json_path": None,
        "has_srt": False, "srt_path": None,
    }
    for ext, key in [(".txt", "txt"), (".json", "json"), (".srt", "srt")]:
        candidate = f.with_suffix(ext)
        if not candidate.exists():
            candidate = f.parent / f"result{ext}"
        if candidate.exists():
            vid[f"has_{key}"] = True
            vid[f"{key}_path"] = str(candidate)
    return vid


def _tokens(text):
    """Normalize text without losing word order or repeated words."""
    return [m.group(0).replace("’", "'").casefold() for m in _WORD_RE.finditer(text or "")]


def _sentences(text):
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    parts = re.findall(
        r".+?(?:[.!?]+(?:[\"'”’\)\]]*)?(?=\s+|$)|$)",
        text,
    )
    return [part.strip() for part in parts if _tokens(part)]


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _timing_is_plausible(text, start, end, previous_end=None):
    """Reject broken timestamps instead of inventing replacement times."""
    if start is None or end is None or start < 0 or end <= start:
        return False
    if previous_end is not None and start < previous_end - 0.05:
        return False

    word_count = len(_tokens(text))
    duration = end - start
    if duration < 0.05:
        return False
    if word_count >= 5 and word_count / duration > _MAX_WORDS_PER_SECOND:
        return False
    return True


def _processed_segments(data):
    """Validate an already processed segment list; never silently retime it."""
    out = []
    previous_end = None
    for item in data:
        if not isinstance(item, dict):
            continue
        text = re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
        start = _number(item.get("start"))
        end = _number(item.get("end"))
        if not text or not _timing_is_plausible(text, start, end, previous_end):
            logger.warning("Ignoring processed segment with invalid timing: %r", item)
            continue

        segment = dict(item)
        segment["id"] = len(out) + 1
        segment["start"] = round(start, 3)
        segment["end"] = round(end, 3)
        segment["text"] = text
        segment.setdefault("raw_text", text.casefold())
        segment.setdefault("boundary_source", "processed")
        out.append(segment)
        previous_end = end
    return out


def _word_units(words):
    units = []
    for word in words or []:
        if not isinstance(word, dict):
            continue
        text = str(word.get("text", word.get("word", ""))).strip()
        start = _number(word.get("start"))
        end = _number(word.get("end"))
        if text and start is not None and end is not None:
            units.append({"text": text, "start": start, "end": end})
    return units


def _timed_units(data):
    """Return the finest timestamps available in a Whisper JSON document."""
    if not isinstance(data, dict):
        return [], None

    # Whisper/faster-whisper cache format: one timestamp per word.
    units = _word_units(data.get("words"))
    if units:
        return units, "word_timestamps"

    collections = []
    for key in ("segments", "transcription"):
        value = data.get(key)
        if isinstance(value, list):
            collections.append(value)

    # Some Whisper formats nest word timestamps inside segments.
    nested_words = []
    for collection in collections:
        for item in collection:
            if isinstance(item, dict) and isinstance(item.get("words"), list):
                nested_words.extend(item["words"])
    units = _word_units(nested_words)
    if units:
        return units, "word_timestamps"

    # Last resort: retain original Whisper segments as indivisible units.
    units = []
    collection = collections[0] if collections else []
    for item in collection:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        offsets = item.get("offsets") if isinstance(item.get("offsets"), dict) else {}
        if offsets:
            start = _number(offsets.get("from"))
            end = _number(offsets.get("to"))
            start = start / 1000 if start is not None else None
            end = end / 1000 if end is not None else None
        else:
            start = _number(item.get("start"))
            end = _number(item.get("end"))
        if text and start is not None and end is not None:
            units.append({"text": text, "start": start, "end": end})
    return units, "segment_timestamps" if units else None


def _token_stream(units):
    source_tokens = []
    token_units = []
    for unit_id, unit in enumerate(units):
        for token in _tokens(unit["text"]):
            source_tokens.append(token)
            token_units.append(unit_id)
    return source_tokens, token_units


def _alignment_candidates(sentences, source_tokens, token_units):
    target_tokens = []
    sentence_token_ids = [[] for _ in sentences]
    for sentence_id, sentence in enumerate(sentences):
        for token in _tokens(sentence):
            token_id = len(target_tokens)
            target_tokens.append(token)
            sentence_token_ids[sentence_id].append(token_id)

    if not source_tokens or not target_tokens:
        return [], 0.0

    matcher = SequenceMatcher(None, target_tokens, source_tokens, autojunk=False)
    target_to_source = {}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            target_to_source[block.a + offset] = block.b + offset

    global_coverage = len(target_to_source) / len(target_tokens)
    candidates = []
    for sentence_id, target_ids in enumerate(sentence_token_ids):
        matched_source_ids = [target_to_source[i] for i in target_ids if i in target_to_source]
        if not matched_source_ids:
            continue

        target_count = len(target_ids)
        matched_count = len(matched_source_ids)
        coverage = matched_count / target_count
        source_start = min(matched_source_ids)
        source_end = max(matched_source_ids)
        source_span = source_end - source_start + 1
        precision = matched_count / source_span

        # Short sentences need exact anchors. Longer corrected sentences may
        # contain substitutions, but most words still have to align in order.
        minimum = 1.0 if target_count <= 2 else _MIN_SENTENCE_ALIGNMENT
        if coverage < minimum or precision < 0.50:
            continue

        candidates.append({
            "sentence_start": sentence_id,
            "sentence_end": sentence_id,
            "unit_start": token_units[source_start],
            "unit_end": token_units[source_end],
            "source_token_start": source_start,
            "source_token_end": source_end,
            "matched": matched_count,
            "target_count": target_count,
            "confidence": 2 * coverage * precision / (coverage + precision),
        })
    return candidates, global_coverage


def _merge_shared_units(candidates):
    """Merge candidates only when an exact word-timestamp unit is shared."""
    groups = []
    for candidate in candidates:
        if groups and candidate["unit_start"] <= groups[-1]["unit_end"]:
            group = groups[-1]
            group["sentence_end"] = candidate["sentence_end"]
            group["unit_end"] = max(group["unit_end"], candidate["unit_end"])
            group["source_token_end"] = max(
                group["source_token_end"], candidate["source_token_end"]
            )
            group["matched"] += candidate["matched"]
            group["target_count"] += candidate["target_count"]
            group["confidence_total"] += candidate["confidence"]
            group["confidence_count"] += 1
        else:
            group = dict(candidate)
            group["confidence_total"] = candidate["confidence"]
            group["confidence_count"] = 1
            groups.append(group)
    return groups


def _individual_groups(candidates):
    groups = []
    for candidate in candidates:
        group = dict(candidate)
        group["confidence_total"] = candidate["confidence"]
        group["confidence_count"] = 1
        groups.append(group)
    return groups


def _interpolated_token_edges(units, source_tokens, token_units):
    """Estimate token edges inside indivisible Whisper segments.

    Character-weighted interpolation is only used for segment-level JSON. It
    prevents several semantic sentences from being chain-merged when each
    sentence boundary shares a Whisper segment with the next sentence.
    """
    starts = [None] * len(source_tokens)
    ends = [None] * len(source_tokens)
    tokens_by_unit = [[] for _ in units]
    for token_id, unit_id in enumerate(token_units):
        tokens_by_unit[unit_id].append(token_id)

    for unit_id, token_ids in enumerate(tokens_by_unit):
        if not token_ids:
            continue
        unit = units[unit_id]
        duration = unit["end"] - unit["start"]
        weights = [max(len(source_tokens[token_id]), 1) for token_id in token_ids]
        total_weight = sum(weights)
        elapsed_weight = 0
        for token_id, weight in zip(token_ids, weights):
            starts[token_id] = unit["start"] + duration * elapsed_weight / total_weight
            elapsed_weight += weight
            ends[token_id] = unit["start"] + duration * elapsed_weight / total_weight
    return starts, ends


def _align_to_units(units, target_text, boundary_source, allow_source_fallback=True):
    source_tokens, token_units = _token_stream(units)
    source_text = " ".join(unit["text"] for unit in units)
    sentences = _sentences(target_text)
    candidates, global_coverage = _alignment_candidates(
        sentences, source_tokens, token_units
    )

    # An unrelated or stale TXT file must never move audio boundaries. Use the
    # timestamped JSON itself as the source of truth in that case.
    if allow_source_fallback and global_coverage < _MIN_GLOBAL_ALIGNMENT:
        logger.warning(
            "TXT/JSON alignment coverage %.2f is too low; using JSON text",
            global_coverage,
        )
        return _align_to_units(
            units, source_text, boundary_source, allow_source_fallback=False
        )

    interpolate_segments = boundary_source == "segment_timestamps"
    if interpolate_segments:
        groups = _individual_groups(candidates)
        token_starts, token_ends = _interpolated_token_edges(
            units, source_tokens, token_units
        )
    else:
        groups = _merge_shared_units(candidates)
        token_starts, token_ends = None, None

    out = []
    previous_end = None
    for group in groups:
        if interpolate_segments:
            start = token_starts[group["source_token_start"]]
            end = token_ends[group["source_token_end"]]
        else:
            start = units[group["unit_start"]]["start"]
            end = units[group["unit_end"]]["end"]
        text = " ".join(
            sentences[group["sentence_start"]:group["sentence_end"] + 1]
        )
        if interpolate_segments:
            raw_text = " ".join(
                source_tokens[
                    group["source_token_start"]:group["source_token_end"] + 1
                ]
            )
        else:
            raw_text = " ".join(
                unit["text"]
                for unit in units[group["unit_start"]:group["unit_end"] + 1]
            )
        if not _timing_is_plausible(text, start, end, previous_end):
            logger.warning(
                "Ignoring sentence with impossible timing %.3f-%.3f: %s",
                start,
                end,
                text,
            )
            continue

        output_boundary_source = boundary_source
        confidence = group["confidence_total"] / group["confidence_count"]
        if interpolate_segments:
            first_unit = units[group["unit_start"]]
            last_unit = units[group["unit_end"]]
            used_interpolation = (
                abs(start - first_unit["start"]) > 0.001
                or abs(end - last_unit["end"]) > 0.001
            )
            if used_interpolation:
                output_boundary_source = "interpolated_segment"
                confidence *= 0.85

        out.append({
            "id": len(out) + 1,
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "raw_text": re.sub(r"\s+", " ", raw_text).strip(),
            "start_word_id": group["source_token_start"],
            "end_word_id": group["source_token_end"],
            "boundary_source": output_boundary_source,
            "boundary_confidence": round(confidence, 2),
        })
        previous_end = end
    return out


def resegment_sentences(json_path, txt_path=None):
    """Build monotonic sentence intervals from timestamped JSON.

    Word timestamps provide exact source boundaries. With segment-only JSON,
    semantic boundaries inside a Whisper segment are character-weighted
    estimates and explicitly marked as interpolated. Matching remains global
    and ordered; low-confidence input falls back to timestamped JSON text.
    """
    if not json_path or not Path(json_path).exists():
        return []

    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    if isinstance(data, list) and data and "start" in data[0] and "text" in data[0]:
        return _processed_segments(data)

    units, boundary_source = _timed_units(data)
    if not units:
        return []

    txt = ""
    if txt_path and Path(txt_path).exists():
        txt = Path(txt_path).read_text(encoding="utf-8")
    target_text = txt.strip() or " ".join(unit["text"] for unit in units)
    return _align_to_units(units, target_text, boundary_source)


# ====== API Routes ======

@router.get("/videos")
async def list_videos():
    return scan_all_sources()


@router.get("/sources")
async def get_sources():
    return {"sources": load_sources()}


@router.post("/sources")
async def add_source(data: dict):
    sources = load_sources()
    path = data.get("path", "").strip()
    if not path or not Path(path).exists():
        raise HTTPException(400, "目录不存在")
    if path not in sources:
        sources.append(path)
        save_sources(sources)
    return {"sources": sources, "videos": scan_all_sources()}


@router.delete("/sources")
async def remove_source(data: dict):
    sources = load_sources()
    path = data.get("path", "")
    if path in sources:
        sources.remove(path)
        save_sources(sources)
    return {"sources": sources}


@router.get("/videos/{video_id}/segments")
async def get_segments(video_id: str):
    videos = scan_all_sources()
    video = next((v for v in videos if v["id"] == video_id), None)
    if not video:
        raise HTTPException(404, "Video not found")

    json_path = video.get("json_path")
    txt_path = video.get("txt_path")
    if not json_path:
        raise HTTPException(404, "No JSON data for this video")

    segments = resegment_sentences(json_path, txt_path)
    return segments


@router.get("/videos/{video_id}/video")
async def serve_video(video_id: str):
    videos = scan_all_sources()
    video = next((v for v in videos if v["id"] == video_id), None)
    if not video:
        raise HTTPException(404, "Video not found")
    return FileResponse(video["video_path"], media_type="video/mp4")


@router.get("/videos/{video_id}/subtitle")
async def serve_subtitle(video_id: str):
    videos = scan_video_dir()
    video = next((v for v in videos if v["id"] == video_id), None)
    if not video or not video.get("srt_path"):
        raise HTTPException(404, "No subtitle")
    return FileResponse(video["srt_path"])


@router.get("/config")
async def get_config():
    return {
        "processing_server": PROCESSING_SERVER,
        "video_source": str(VIDEO_SOURCE_DIR),
    }

import json, re, logging, hashlib
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from app.config import load_sources, save_sources, PROJECTS_DIR, PROCESSING_SERVER

logger = logging.getLogger(__name__)
router = APIRouter()


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


def resegment_sentences(json_path, txt_path):
    """从 JSON 获取句子 — 支持已分割格式和 Whisper 原始格式"""
    if not Path(json_path).exists():
        return []

    data = json.loads(Path(json_path).read_text(encoding="utf-8"))

    # 如果已是 segments 格式但无 txt，直接返回
    if isinstance(data, list) and len(data) > 0 and "start" in data[0] and "text" in data[0]:
        if not Path(txt_path).exists():
            return data
        # 有 txt 则重新匹配优化时间戳 — 继续走下面逻辑
        return data

    segments = data.get("transcription", [])
    if not segments:
        return []

    # 读取正确的句子文本
    txt = Path(txt_path).read_text(encoding="utf-8") if Path(txt_path).exists() else ""
    raw = re.split(r'(?<=[.!?])\s+', txt)
    targets = [s.strip() for s in raw if len(s.strip()) > 5]

    seg_texts = [s["text"].strip().lower() for s in segments]
    out = []
    pos = 0

    for sent in targets:
        sent_clean = re.sub(r'[^\w\s]', '', sent.lower())
        sent_words = set(sent_clean.split())
        if len(sent_words) < 2:
            continue

        best_s, best_e, best_score = -1, -1, 0
        for i in range(max(0, pos - 1), min(pos + 8, len(segments))):
            acc = ""
            for j in range(i, min(i + 5, len(segments))):
                acc += " " + seg_texts[j]
                acc_words = set(re.sub(r'[^\w\s]', '', acc.lower()).split())
                overlap = len(sent_words & acc_words) / max(len(sent_words), 1)
                if overlap > best_score:
                    best_score = overlap
                    best_s = i
                    best_e = j
            if best_score > 0.6:
                break

        if best_s < 0:
            continue

        start_s = segments[best_s]["offsets"]["from"] / 1000
        end_s = segments[best_e]["offsets"]["to"] / 1000

        if out:
            start_s = max(start_s, out[-1]["end"])

        duration = end_s - start_s
        if duration < 0.3:
            continue
        if duration > 30:
            end_s = start_s + 25

        # 去重
        if out and abs(start_s - out[-1]["start"]) < 0.2 and sent == out[-1]["text"]:
            continue

        out.append({
            "id": len(out) + 1,
            "start": round(start_s, 3),
            "end": round(end_s, 3),
            "text": sent,
            "raw_text": sent.lower(),
            "boundary_source": "merged",
            "boundary_confidence": round(best_score, 2),
        })
        pos = best_e + 1

    # 消除重叠
    for i in range(1, len(out)):
        if out[i]["start"] < out[i - 1]["end"]:
            out[i]["start"] = out[i - 1]["end"]

    return out


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
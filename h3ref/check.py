"""After-checks on a finished clip. H3 burns subtitles into Chinese dialogue now and then (once writing 三千八 as 3000),
and that is the one defect a viewer can't miss, so every clip with lines gets a look from a vision model."""
import base64
import json
import re
import tempfile
from pathlib import Path

from . import config, http, media

ASK = ("These are frames sampled in order from one AI-generated video clip. Did the video model burn text into the "
       "picture: subtitles or captions along the bottom, speech text, titles, or words floating over the scene? Writing "
       "that physically belongs to the set (a shop sign, a label on a prop) does not count. Return JSON "
       '{"burned_text": true/false, "frames": [1-based frame numbers with such text], "text": "what it says, if readable"}.')


def subtitles(video: Path) -> dict:
    """{burned_text, frames, text} from 8 frames of the clip."""
    with tempfile.TemporaryDirectory() as tmp:
        frames = media.sample_frames(video, Path(tmp))
        parts = [{"type": "text", "text": ASK}] + [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()}} for p in frames]
    out = http.request(config.VISION_BASE + "/chat/completions", config.vision_key(),
                       {"model": config.VISION_MODEL, "messages": [{"role": "user", "content": parts}], "max_tokens": 2000, "stream": False})
    text = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    m = re.search(r"\{.*\}", text, re.S)
    try:
        ans = json.loads(m.group(0)) if m else {}
    except ValueError:
        ans = {}
    return {"burned_text": bool(ans.get("burned_text")), "frames": ans.get("frames") or [], "text": ans.get("text") or "",
            "checked": bool(m)}

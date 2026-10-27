"""Storyboard frames: one GPT Image picture for the first instant of a shot, drawn on APIMart.

H3 follows a picture far more closely than words. A frame per shot keeps what the model otherwise "normalizes" — a
miniature prop stays miniature, a prop stays where the script put it — and the first frame of the film goes into every
later frame as a reference, so the actor and the set stay the same from shot to shot.
"""
import json
import tempfile
import time
from pathlib import Path

from . import config, http, media

ORIENT = {"9:16": "竖屏", "16:9": "横屏", "1:1": "方形"}
TAIL = "不增加额外人物。不出现字幕、文字、水印和任何真实品牌的标志。电影级布光。"


def prompt(film: dict, shot: dict, who: list[dict], with_board: bool, with_anchor: bool) -> str:
    """Chinese prompt for one frame. Pictures in order: board (optional), each character in it, the anchor frame."""
    aspect = film["aspect"]
    lines = [f"一张{ORIENT.get(aspect, '竖屏')}电影剧照，画幅 {aspect}，它是一段视频里一个镜头的第一帧。画风：{(film.get('look') or '').strip()}"]
    k = 1
    if with_board:
        lines.append("参考图片1是本片的角色、道具和场景参考总表，只用来统一外观，不要把总表、白底和文字标注画进画面。")
        k = 2
    if (film.get("setting") or "").strip():
        lines.append("场景与关键道具：" + film["setting"].strip())
    cam = (shot.get("camera") or "").strip().strip("【】")
    lines.append("画面：" + (f"【{cam}】" if cam else "") + (shot.get("action") or "").strip().rstrip("。")
                 + "。画的是这个镜头刚开始的一瞬间，动作还没做完。")
    for c in who:
        look = (c.get("look") or "").strip().rstrip("。")
        lines.append(f"参考图片{k}是{c['name']}" + (f"：{look}" if look else "") + "。长相、发型、妆容和服装严格照参考图。")
        k += 1
    if with_anchor:
        lines.append(f"参考图片{k}是同一部片子另一个镜头的画面：人物的长相、妆容、服装和场景的样子与它保持一致，构图按上面的画面描述来。")
    lines.append(TAIL)
    return "\n".join(lines)


def _upload(path: Path) -> str:
    out = http.upload(config.APIMART_BASE + "/uploads/images", config.apimart_key(), path)
    url = out.get("url") or (out.get("data") or {}).get("url")
    if not url:
        raise RuntimeError("APIMart upload failed: " + json.dumps(out, ensure_ascii=False)[:200])
    return url


def submit(text: str, refs: list[Path], aspect: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        urls = [_upload(media.shrink(Path(p), Path(tmp) / f"ref{i}.jpg")) for i, p in enumerate(refs)]
    body = {"model": config.IMAGE_MODEL, "prompt": text, "size": aspect, "resolution": config.IMAGE_RESOLUTION,
            "quality": config.IMAGE_QUALITY, "n": 1}
    if urls:
        body["image_urls"] = urls
    out = http.request(config.APIMART_BASE + "/images/generations", config.apimart_key(), body)
    task = ((out.get("data") or [{}])[0] or {}).get("task_id")
    if not task:
        raise RuntimeError("APIMart gave no task id: " + json.dumps(out, ensure_ascii=False)[:200])
    return task


def poll(task: str) -> dict:
    """{state: running|done|failed, url, cny, error}; APIMart reports cost in US dollars."""
    out = http.request(config.APIMART_BASE + f"/tasks/{task}", config.apimart_key())
    st = out.get("data") or out
    usd = st.get("cost")
    res = {"state": "running", "cny": round(float(usd) * config.USD_CNY, 4) if usd is not None else None}
    if st.get("status") == "completed":
        images = (st.get("result") or {}).get("images") or []
        urls = (images[0].get("url") if images else None) or []
        res.update(state="done", url=urls[0] if isinstance(urls, list) and urls else urls or None)
        if not res["url"]:
            res.update(state="failed", error="completed without an image url")
    elif st.get("status") in ("failed", "cancelled"):
        err = st.get("error") or {}
        res.update(state="failed", error=str(err.get("message") if isinstance(err, dict) else err or st.get("status"))[:300])
    return res


def draw(text: str, refs: list[Path], aspect: str, out: Path, record=lambda **k: None, task: str | None = None) -> float:
    """Draw one frame to `out`; `record(task=…)` is called with the task id before waiting, so a rerun resumes it.
    Returns the cost in CNY."""
    task = task or submit(text, refs, aspect)
    record(task=task)
    while True:
        st = poll(task)
        if st["state"] == "done":
            http.download(st["url"], out)
            return float(st.get("cny") or 0)
        if st["state"] == "failed":
            raise RuntimeError("frame failed: " + str(st.get("error")))
        time.sleep(6)

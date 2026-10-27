"""A storyboard (film.json) in, a finished film out: segments of up to 15 s cut at shot boundaries, a storyboard frame
for every shot, each segment's prompt written the way you'd write it for Seedance and rewritten into H3's official
format, generated on RunningHub, checked for burned subtitles, joined. Every paid step is recorded in state.json before
it is waited on, so `run` again resumes instead of paying twice.

Frame modes (--frames):
  shot   a frame for every shot, each the first frame of its [Shot N] (default; segments are independent and run at once)
  first  one frame per segment for its first shot; later segments also get the previous segment's last frame
  none   no frames: characters and setting from words and pictures alone, segments chained on the last frame
"""
import json
import math
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import check, config, frames, http, media, rewrite, rh

GROUP_WORDS = ("两人", "二人", "三人", "四人", "俩", "大家", "众人", "所有人", "他们", "她们", "全员", "everyone", "both")
LANG = {"zh": "中文", "en": "英文", "ja": "日文", "ko": "韩文", "es": "西班牙文", "pt": "葡萄牙文"}
ORIENT = {"9:16": "竖屏", "16:9": "横屏", "1:1": "方形"}
RULES = ("完整表现动作的原因、接触和结果，动作真实连续，不要静态立绘晃动；角色不互换外观，道具不凭空出现或消失，不增加额外人物。"
         "画风全片统一为上面写的风格；每个角色全片是同一位演员，长相、发型、妆容和服装前后一致；"
         "画面里不出现字幕和文字，包装和道具上不印可读的字，只用图案和颜色区分；不出现任何真实品牌的标志。")


def _num(x) -> str:
    x = float(x)
    return str(int(x)) if x.is_integer() else f"{x:g}"


# ------------------------------------------------------------------ the storyboard

def load(path: Path) -> dict:
    film = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    if film.get("aspect", "9:16") not in config.SIZES:
        raise SystemExit(f"aspect 只支持 {', '.join(config.SIZES)}")
    film.setdefault("aspect", "9:16")
    for c in film.get("cast") or []:
        p = Path(c["picture"]).expanduser()
        c["picture"] = str(p if p.is_absolute() else (base / p).resolve())
        if not Path(c["picture"]).is_file():
            raise SystemExit(f"找不到 {c['name']} 的参考图：{c['picture']}")
    if film.get("board"):
        p = Path(film["board"]).expanduser()
        film["board"] = str(p if p.is_absolute() else (base / p).resolve())
    shots = [s for s in film.get("shots") or [] if (s.get("action") or "").strip()]
    if not shots:
        raise SystemExit("film.json 里没有镜头（shots）")
    t = 0.0
    for s in shots:                                    # times made continuous; a shot without times lasts 2 s
        length = float(s["end"]) - float(s["start"]) if s.get("start") is not None and s.get("end") is not None else 2.0
        s["start"], s["end"] = round(t, 2), round(t + max(0.5, length), 2)
        t = s["end"]
    film["shots"] = shots
    return film


def plan(shots: list[dict], max_len: int = config.MAX_SECONDS) -> list[dict]:
    """Segments of at most `max_len` seconds, cut at shot boundaries, about the same length each."""
    total = float(shots[-1]["end"])
    n = max(1, math.ceil(total / max_len))
    target = total / n
    segs: list[list[dict]] = [[]]
    for s in shots:
        cur = segs[-1]
        if cur:
            start = float(cur[0]["start"])
            if float(s["end"]) - start > max_len or (float(s["start"]) - start >= target - 0.01 and len(segs) < n):
                segs.append([])
        segs[-1].append(s)
    out = []
    for i, group in enumerate(segs):
        a, b = float(group[0]["start"]), float(group[-1]["end"])
        out.append({"idx": i, "start": a, "end": b, "shots": group,
                    "seconds": max(config.MIN_SECONDS, min(max_len, int(math.ceil(b - a - 0.25))))})
    return out


def in_shots(c: dict, shots: list[dict]) -> bool:
    """Does this character appear in these shots? Generous on purpose: an extra picture costs little, a missing one lets
    the model draw the character from words alone."""
    text = " ".join(f"{s.get('action') or ''} {s.get('line') or ''}" for s in shots)
    if any(g in text for g in GROUP_WORDS):
        return True
    return any(n and n in text for n in [c["name"], *(c.get("aliases") or [])])


def refs(film: dict, seg: dict, mode: str) -> list[dict]:
    """The segment's pictures in <Picture N> order: board, each character in it, then the frames or the previous last frame.
    Nine at most: when they run short the board goes first, then the later shots' frames."""
    cast = [c for c in film.get("cast") or [] if in_shots(c, seg["shots"])] or list(film.get("cast") or [])
    board = [{"kind": "board", "path": film["board"]}] if film.get("board") else []
    people = [{"kind": "cast", "path": c["picture"], "cast": c} for c in cast]
    tail = []
    if mode == "shot":
        room = config.MAX_IMAGES - len(people) - len(board)
        if room < len(seg["shots"]) and board:
            board, room = [], room + 1
        tail = [{"kind": "frame", "shot": j} for j in range(min(len(seg["shots"]), max(0, room)))]
    else:
        if mode == "first":
            tail.append({"kind": "frame", "shot": 0})
        if seg["idx"] > 0:
            tail.append({"kind": "prev"})
    out = board + people + tail
    if len(out) > config.MAX_IMAGES:
        raise SystemExit(f"第 {seg['idx'] + 1} 段要 {len(out)} 张参考图，H3 最多 {config.MAX_IMAGES} 张；减少这一段的角色")
    return out


def compile_prompt(film: dict, seg: dict, pics: list[dict], count: int) -> str:
    """The segment's prompt in Chinese, written the way you would for Seedance; rewrite.py turns it into H3's format."""
    aspect = film["aspect"]
    head = (f"根据下面文字分镜生成一段{seg['seconds']}秒{ORIENT[aspect]}视频，{aspect}。"
            + (f"这是一条连续短片的第{seg['idx'] + 1}段。" if count > 1 else "")
            + "没有原视频、截图或原音频，画面从这些文字和参考图来。" + (film.get("look") or "").strip())
    offset = float(seg["start"])
    lines, k = [], 0
    for p in pics:
        k += 1
        if p["kind"] == "board":
            lines.append(f"参考图片{k}是本片的角色、道具和场景参考总表，只用来统一外观，不要把总表、白底、文字标注画进画面。")
        elif p["kind"] == "cast":
            look = (p["cast"].get("look") or "").strip().rstrip("。")
            lines.append(f"参考图片{k}只约束{p['cast']['name']}身份" + (f"：{look}。" if look else "。"))
        elif p["kind"] == "frame":
            s = seg["shots"][p["shot"]]
            span = f"{_num(float(s['start']) - offset)}–{_num(float(s['end']) - offset)}秒"
            lines.append(f"参考图片{k}是第{p['shot'] + 1}个镜头（{span}）开始那一瞬的画面，也就是 [Shot {p['shot'] + 1}] 的首帧："
                         "这个镜头从这张图开始演，人物位置、服装、光线和构图照它，之后按分镜接着演。")
        else:
            first = any(x["kind"] == "frame" for x in pics)
            lines.append(f"参考图片{k}是上一段的最后一个画面：" + ("只用来保持画风、光线、场景和角色状态一致，这一段的第一个镜头从它的首帧开始。"
                                                       if first else "这一段从这个画面直接接着演，画风、光线、场景和角色状态保持一致。"))
    n = sum(1 for p in pics if p["kind"] == "cast")
    if n:
        lines.append((f"只有这{n}名角色" if n > 1 else "只有这一名角色") + "，保持各自发型、五官、服饰纹样与比例。")
    out = [head, "".join(lines)]
    if (film.get("setting") or "").strip():
        out.append("场景与关键道具（全片统一）：" + film["setting"].strip())
    speech = film.get("speech") or ""
    shot_lines = []
    for s in seg["shots"]:
        cam = (s.get("camera") or "").strip().strip("【】")
        text = f"{_num(float(s['start']) - offset)}–{_num(float(s['end']) - offset)}秒：" + (f"【{cam}】" if cam else "") \
            + (s.get("action") or "").strip().rstrip("。") + "。"
        if speech and (s.get("line") or "").strip():
            text += "台词：" + s["line"].strip()
        shot_lines.append(text)
    out.append("分镜：\n" + "\n".join(shot_lines))
    out.append(RULES)
    if speech and any((s.get("line") or "").strip() for s in seg["shots"]):
        out.append(f"声音：角色只说上面写出的{LANG.get(speech, speech)}台词，口型对上；其余只有动作音效和环境声；不要背景音乐、旁白、唱歌、字幕和水印。")
    else:
        out.append("声音：这一段没有台词，角色不说话；只有动作音效和环境声；不要背景音乐、旁白、唱歌、字幕和水印。情绪只用表情和肢体表现，无说话口型。")
    return "\n".join(out)


# ------------------------------------------------------------------ the run

class Job:
    def __init__(self, path: Path):
        self.dir = path.resolve()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "state.json"
        self.state = json.loads(self.file.read_text()) if self.file.exists() else {"frames": {}, "segs": {}}
        self.lock = threading.RLock()

    def save(self):
        with self.lock:
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1))
            tmp.replace(self.file)

    def put(self, kind: str, key: str, **rec):
        with self.lock:
            self.state[kind][key] = {**self.state[kind].get(key, {}), **rec}
            self.save()

    def log(self, *parts):
        line = time.strftime("%H:%M:%S") + " " + " ".join(str(p) for p in parts)
        print(line, flush=True)
        with (self.dir / "run.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _frame_key(i: int, j: int) -> str:
    return f"s{i + 1}-{j + 1}"


def stage_frames(job: Job, film: dict, segs: list[dict], pics: dict):
    """Draw every frame the plan needs; the film's first frame first, then the rest with it as the anchor."""
    (job.dir / "frames").mkdir(exist_ok=True)
    wanted = [(i, p["shot"]) for i, seg in enumerate(segs) for p in pics[i] if p["kind"] == "frame"]
    if not wanted:
        return

    def one(ij, anchor):
        i, j = ij
        key = _frame_key(i, j)
        out = job.dir / "frames" / f"{key}.png"
        rec = job.state["frames"].get(key) or {}
        if out.exists():
            return
        shot = segs[i]["shots"][j]
        who = [c for c in film.get("cast") or [] if in_shots(c, [shot])]
        refs = ([film["board"]] if film.get("board") else []) + [c["picture"] for c in who] + ([anchor] if anchor else [])
        text = frames.prompt(film, shot, who, bool(film.get("board")), bool(anchor))
        for attempt in range(2):
            try:
                cny = frames.draw(text, refs, film["aspect"], out, record=lambda **k: job.put("frames", key, prompt=text, **k),
                                  task=rec.get("task") if attempt == 0 else None)
                job.put("frames", key, file=str(out), cny=cny)
                job.log("frame", key, f"¥{cny:.3f}")
                return
            except RuntimeError as exc:
                job.log("frame failed", key, exc)
        raise SystemExit(f"分镜图 {key} 画了两次都没成")

    first = wanted[0]
    one(first, None)
    anchor = str(job.dir / "frames" / f"{_frame_key(*first)}.png")
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda ij: one(ij, anchor), wanted[1:]))


def stage_prompts(job: Job, film: dict, segs: list[dict], pics: dict):
    (job.dir / "prompts").mkdir(exist_ok=True)

    def one(seg):
        i = seg["idx"]
        zh = compile_prompt(film, seg, pics[i], len(segs))
        (job.dir / "prompts" / f"seg{i + 1}.zh.txt").write_text(zh, encoding="utf-8")
        rec = job.state["segs"].get(str(i)) or {}
        if rec.get("h3") and rec.get("zh") == zh:
            return
        h3 = rewrite.rewrite(zh, len(pics[i]), seg["seconds"], film["aspect"])
        (job.dir / "prompts" / f"seg{i + 1}.h3.txt").write_text(h3, encoding="utf-8")
        job.put("segs", str(i), zh=zh, h3=h3)
        job.log("prompt", f"seg{i + 1}", f"{len(pics[i])} pictures")
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(one, segs))


def _picture_paths(job: Job, seg: dict, pics: list[dict]) -> list[Path]:
    out = []
    for p in pics:
        if p["kind"] in ("board", "cast"):
            out.append(Path(p["path"]))
        elif p["kind"] == "frame":
            out.append(job.dir / "frames" / f"{_frame_key(seg['idx'], p['shot'])}.png")
        else:
            out.append(job.dir / "clips" / f"seg{seg['idx']}-last.jpg")
    return out


def gen_segment(job: Job, film: dict, seg: dict, pics: list[dict]) -> dict:
    i = seg["idx"]
    out = job.dir / "clips" / f"seg{i + 1}.mp4"
    rec = job.state["segs"].get(str(i)) or {}
    if out.exists() and rec.get("cny") is not None:
        return rec
    if not rec.get("task"):
        paths = _picture_paths(job, seg, pics)
        missing = [str(p) for p in paths if not p.exists()]
        if missing:
            raise SystemExit(f"第 {i + 1} 段缺参考图：{missing}")
        names = [rh.upload(p) for p in paths]
        seed = int(rec.get("seed") or random.randrange(10 ** 14, 10 ** 15))
        g = rh.graph(rec["h3"], names, seg["seconds"], film["aspect"], seed)
        while True:
            try:
                task = rh.submit(g)
                break
            except rh.QueueFull:
                time.sleep(60)
        job.put("segs", str(i), task=task, seed=seed, submitted=time.time())
        job.log("submitted", f"seg{i + 1}", f"seed {seed}", f"≈¥{rh.estimate_cny(seg['seconds'])}")
        rec = job.state["segs"][str(i)]
    while True:
        st = rh.poll(rec["task"])
        if st["state"] != "running":
            break
        time.sleep(20)
    if st["state"] == "failed":
        job.put("segs", str(i), task=None, error=st.get("error"))
        raise RuntimeError(f"第 {i + 1} 段生成失败：{st.get('error')}（再跑一次 run 会重新提交）")
    http.download(st["url"], out)
    media.last_frame(out, job.dir / "clips" / f"seg{i + 1}-last.jpg")
    job.put("segs", str(i), file=str(out), cny=st["cny"], coins=st.get("coins"), run_s=st.get("run_seconds"),
            wall_s=round(time.time() - float(rec.get("submitted") or time.time())), error=None)
    job.log("done", f"seg{i + 1}", f"¥{st['cny']:.3f}", f"{st.get('run_seconds')} s on the GPU")
    return job.state["segs"][str(i)]


def stage_generate(job: Job, film: dict, segs: list[dict], pics: dict, mode: str):
    (job.dir / "clips").mkdir(exist_ok=True)
    chained = any(p["kind"] == "prev" for ps in pics.values() for p in ps)
    if chained:                                         # each segment starts from the one before
        for seg in segs:
            gen_segment(job, film, seg, pics[seg["idx"]])
    else:
        with ThreadPoolExecutor(max(1, config.RH_CONCURRENCY)) as pool:
            list(pool.map(lambda s: gen_segment(job, film, s, pics[s["idx"]]), segs))


def stage_check(job: Job, film: dict, segs: list[dict]):
    """Burned-subtitle check on every segment with lines (and every segment when the film has any lines)."""
    if not film.get("speech") or not any((s.get("line") or "").strip() for s in film["shots"]):
        return
    for seg in segs:
        rec = job.state["segs"].get(str(seg["idx"])) or {}
        if "subtitles" in rec or not rec.get("file"):
            continue
        try:
            res = check.subtitles(Path(rec["file"]))
        except Exception as exc:                        # a check that can't run is reported, not fatal
            res = {"checked": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
        job.put("segs", str(seg["idx"]), subtitles=res)
        if res.get("burned_text"):
            job.log("SUBTITLES", f"seg{seg['idx'] + 1}", res.get("text"))


def report(job: Job, film: dict, segs: list[dict], mode: str, final: Path) -> Path:
    fr = sum(float(r.get("cny") or 0) for r in job.state["frames"].values())
    gen = sum(float(r.get("cny") or 0) for r in job.state["segs"].values())
    gpu = sum(float(r.get("run_s") or 0) for r in job.state["segs"].values())
    wall = time.time() - float(job.state.get("t_start") or time.time())
    lines = [f"# {film.get('title') or job.dir.name}", "",
             f"成片 `{final.name}`，{media.duration(final):.1f} 秒，{len(segs)} 段，分镜图模式 {mode}。", "",
             f"花费：分镜图 {len(job.state['frames'])} 张 ¥{fr:.2f}，H3 生成 ¥{gen:.2f}，合计 ¥{fr + gen:.2f}；"
             f"GPU {gpu / 60:.1f} 分钟，这次运行墙钟 {wall / 60:.0f} 分钟（续跑只算这一次）。", ""]
    subs = [s for s in segs if (job.state["segs"].get(str(s["idx"])) or {}).get("subtitles", {}).get("burned_text")]
    if subs:
        lines += ["**烧字幕了**：" + "、".join(f"第 {s['idx'] + 1} 段" for s in subs)
                  + "。用 `--redo N` 换种子重跑这一段，或者删掉那句台词。", ""]
    lines += ["| 段 | 秒 | 参考图 | 费用 | GPU 秒 | 字幕检查 |", "|---|---|---|---|---|---|"]
    for s in segs:
        r = job.state["segs"].get(str(s["idx"])) or {}
        sub = r.get("subtitles") or {}
        lines.append(f"| {s['idx'] + 1} | {_num(s['start'])}–{_num(s['end'])} | {len(r.get('pics') or [])} | ¥{float(r.get('cny') or 0):.3f} | "
                     f"{r.get('run_s') or ''} | {'烧字幕：' + sub.get('text', '') if sub.get('burned_text') else ('干净' if sub.get('checked') else '—')} |")
    p = job.dir / "report.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def run(job_dir: Path, film_path: Path | None, mode: str = "shot", redo: list[int] | None = None) -> Path:
    job = Job(job_dir)
    if film_path:
        job.state["film_path"] = str(Path(film_path).resolve())
        job.state["mode"] = mode
    if not job.state.get("film_path"):
        raise SystemExit("新任务要给 --film")
    mode = job.state.get("mode", mode)
    film = load(Path(job.state["film_path"]))
    segs = plan(film["shots"])
    pics = {s["idx"]: refs(film, s, mode) for s in segs}
    for n in redo or []:                                # a new seed for these segments (and, chained, everything after)
        for k in [k for k in job.state["segs"] if int(k) == n - 1 or (mode != "shot" and int(k) > n - 1)]:
            rec = job.state["segs"][k]
            job.state["segs"][k] = {"zh": rec.get("zh"), "h3": rec.get("h3")}
            (job.dir / "clips" / f"seg{int(k) + 1}.mp4").unlink(missing_ok=True)
    job.state["t_start"] = time.time()
    job.save()
    job.log("plan", f"{len(segs)} segments", " / ".join(f"{s['seconds']}s·{len(s['shots'])} shots·{len(pics[s['idx']])} pictures" for s in segs))
    for s in segs:
        job.put("segs", str(s["idx"]), pics=[p["kind"] for p in pics[s["idx"]]])
    stage_frames(job, film, segs, pics)
    stage_prompts(job, film, segs, pics)
    stage_generate(job, film, segs, pics, mode)
    stage_check(job, film, segs)
    final = job.dir / "film.mp4"
    media.join([Path(job.state["segs"][str(s["idx"])]["file"]) for s in segs], final)
    rep = report(job, film, segs, mode, final)
    job.log("film", final, "report", rep)
    return final

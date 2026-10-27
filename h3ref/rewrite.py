"""Rewrite a Seedance-style prompt (Chinese, storyboard lines, reference pictures named 参考图片N) into MiniMax H3's
official full-reference (Ref2VA) format.

H3 reads Seedance prompts badly: it lays music under most clips, burns subtitles into Chinese dialogue and under-acts,
because the workflow has no negative branch, so "不要背景音乐、字幕" reads as music and subtitles. Rewritten in MiniMax's
own format (English, six sections, <Picture N> by input order, no negative phrasing, music N/A), music dropped from
7 clips in 7 to 1 in 13 and H3 matched Seedance clip for clip in blind pairs. The rewrite is done by a language model
given the official guide (fetched from MiniMax's repository), the rules below and two tested examples.
"""
import glob
import json
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from . import config, http

SECTIONS = ["subject_definitions", "summary", "retention_analysis", "detailed_description", "overall_soundscape", "non_diegetic_music"]
EXAMPLES = config.ROOT / "prompts/h3_examples.md"

RULES = """Rules learned on this workflow (they override nothing in the guide, they make it concrete):
1. Output exactly the six sections, in order, each as `name:` on its own line followed by its text. English only, except
   dialogue inside <d> and visible scene text. No Markdown, no code fences, no comments before or after.
2. <Picture N> is the N-th reference image in input order; the input names each one as 参考图片N. Give every image a role:
   - the reference sheet (参考总表 / 总表) is a design reference: cite it inside the Subject for the sets and props it designs;
   - an image that 只约束某角色身份 defines one <Subject> (carry over every look detail from the input: hair, eyes, outfit, props);
   - a key-frame image (「…」这个画面的样子) is the composition reference for the matching shot when that shot is in this segment,
     otherwise say it maps to no shot in this part;
   - a shot's first frame (第N个镜头…的首帧) is the first frame of that [Shot N]: the shot starts from that picture;
   - the previous segment's last frame (上一段的最后一个画面) is the first frame of [Shot 1] when [Shot 1] continues the same
     place and moment, otherwise a continuity reference for the set, light and characters' state.
3. Keep every storyboard beat, in order, with its camera and action. The first shot has no timestamp; each later beat
   starts `[Shot N] At MM:SS.mmm, the camera cuts to …` at the beat's start second. Add no beats, drop none, stay inside
   the duration.
4. Spoken lines: exactly the lines the input gives, in the language it names, as `<Subject N> (S1) … says …, <d>[Language] …</d>`.
   Speaker ids count in order of first speech. With no lines in a segment, write no <d> at all.
5. Never write a negative instruction (no / not / without / avoid / never / don't / free of …) about music, subtitles,
   captions, text, watermarks, logos, brands, narration or singing. The workflow has no negative prompt, so naming a thing
   brings it in. Drop the input's 不要背景音乐、字幕、水印、品牌 sentences entirely; describe only what is there.
6. non_diegetic_music is always N/A. overall_soundscape lists the concrete diegetic sounds of these actions and places.
7. Acting is written as concrete faces and bodies, never as "exaggerated" or "comic". When the script says 面无表情 /
   一本正经, say plainly that the face stays blank and serious the whole time. Big reactions (shock, pain, laughing) get
   specific details: eyes snapping wide, jaw dropping, wincing with a hand on the cheek, doubled over laughing.
8. Open detailed_description with one or two sentences of style taken from the input's style sentence, including the
   aspect ratio, then [Shot 1]."""

NEGATIVE = re.compile(r"\b(no|not|without|never|avoid|don't|do not|free of)\b[^.\n]{0,40}\b(music|soundtrack|score|subtitles?|captions?|"
                      r"on-screen text|text overlays?|watermarks?|logos?|brands?|narration|voice-?over|singing)\b", re.I)


class RewriteError(RuntimeError):
    pass


def guide() -> str:
    """MiniMax's full-reference guide: a local copy when H3_GUIDE_FILE is set, else fetched once and cached."""
    if config.GUIDE_FILE:
        return Path(config.GUIDE_FILE).expanduser().read_text(encoding="utf-8")
    cache = config.CACHE / "ref-en.txt"
    if not cache.is_file():
        req = urllib.request.Request(config.GUIDE_URL, headers={"User-Agent": config.UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode("utf-8")
        except Exception as exc:
            raise RewriteError(f"取不到 MiniMax 官方提示词指南（{config.GUIDE_URL}）：{type(exc).__name__}。"
                               "下载 ref-en.txt 后用 H3_GUIDE_FILE 指向它") from None
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
    return cache.read_text(encoding="utf-8")


def request(prompt_zh: str, n_images: int, seconds: float, aspect: str, feedback: str = "") -> str:
    return "\n\n".join([
        "You rewrite one video-generation prompt for MiniMax H3 in its full-reference (Ref2VA) mode. "
        "Read the official guide, the rules and the two tested examples, then rewrite the input.",
        "=== OFFICIAL GUIDE ===\n" + guide(),
        "=== RULES ===\n" + RULES,
        "=== EXAMPLES ===\n" + EXAMPLES.read_text(encoding="utf-8"),
        f"=== INPUT ===\nThis segment is {seconds:g} seconds, aspect ratio {aspect}, and the request carries {n_images} reference "
        f"images in this order (Picture 1 … Picture {n_images}).\n\n{prompt_zh}",
        (f"=== FIX ===\nYour previous rewrite had these problems; fix them:\n{feedback}" if feedback else ""),
        "Write the rewrite now: the six sections only."]).strip()


def sections(text: str) -> dict:
    """{section name: body}; raises when a section is missing or out of order."""
    found = [(m.start(), m.group(1)) for m in re.finditer(r"^\s*(" + "|".join(SECTIONS) + r")\s*:", text, re.M)]
    names = [n for _, n in found]
    if names != SECTIONS:
        raise RewriteError("六块不全或顺序不对：" + "、".join(names))
    out = {}
    for k, (pos, name) in enumerate(found):
        end = found[k + 1][0] if k + 1 < len(found) else len(text)
        out[name] = text[pos:end].split(":", 1)[1].strip()
    return out


def problems(text: str, n_images: int) -> list[str]:
    try:
        parts = sections(text)
    except RewriteError as exc:
        return [str(exc)]
    out = []
    for name in ("detailed_description", "overall_soundscape"):
        for m in NEGATIVE.finditer(parts[name]):
            out.append(f"{name} still has a negative instruction: “{m.group(0)}”; remove it and describe only what is there")
    used = {int(n) for n in re.findall(r"<Picture (\d+)>", text)}
    if used and max(used) > n_images:
        out.append(f"there are only {n_images} reference images but the rewrite cites <Picture {max(used)}>")
    if "[Shot 1]" not in parts["detailed_description"]:
        out.append("detailed_description has no [Shot 1]")
    return out


def assemble(parts: dict) -> str:
    parts = {**parts, "non_diegetic_music": "N/A"}
    return "\n\n".join(f"{name}:\n{parts[name]}" for name in SECTIONS)


def claude_bin() -> str | None:
    """CLAUDE_BIN, `claude` on PATH, or the copy bundled with the Claude desktop app on macOS."""
    if config.env("CLAUDE_BIN"):
        return config.env("CLAUDE_BIN")
    if shutil.which("claude"):
        return shutil.which("claude")
    bundles = []
    for path in glob.glob(str(Path.home() / "Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude")):
        version = Path(path).parts[-6]
        if re.fullmatch(r"\d+(\.\d+)*", version):
            bundles.append((tuple(int(x) for x in version.split(".")), path))
    return max(bundles)[1] if bundles else None


def _claude(prompt: str) -> str:
    """The local Claude Code CLI under your own login (or CLAUDE_CODE_OAUTH_TOKEN), headless, all tools denied, run
    from an empty directory so no project files are read."""
    exe = claude_bin()
    if not exe:
        raise RewriteError("没找到 claude 命令行；装上 Claude Code，或者设 H3_REWRITER=llm 用别的模型")
    with tempfile.TemporaryDirectory() as cwd:
        proc = subprocess.run([exe, "-p", "--output-format", "json", "--max-turns", "1", "--model", config.CLAUDE_MODEL,
                               "--disallowedTools", "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Task,Agent"],
                              input=prompt, capture_output=True, text=True, timeout=900, cwd=cwd)
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        raise RewriteError(f"Claude 没有返回结果：{(proc.stderr or proc.stdout)[:300]}") from None
    if out.get("is_error"):
        raise RewriteError(f"Claude 调用失败：{str(out.get('result'))[:300]}")
    return str(out.get("result", ""))


def _llm(prompt: str) -> str:
    out = http.request(config.LLM_BASE + "/chat/completions", config.llm_key(),
                       {"model": config.LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 8000, "stream": False},
                       timeout=600)
    text = ((out.get("choices") or [{}])[0].get("message") or {}).get("content")
    if not text:
        raise RewriteError("改写模型没有返回内容：" + json.dumps(out, ensure_ascii=False)[:300])
    return text


def backend() -> str:
    if config.REWRITER in ("claude", "llm"):
        return config.REWRITER
    return "claude" if claude_bin() else "llm"


def rewrite(prompt_zh: str, n_images: int, seconds: float, aspect: str) -> str:
    """The H3 prompt for one clip. Two tries: the second one is told what was wrong with the first."""
    call = _claude if backend() == "claude" else _llm
    feedback = ""
    for _ in range(2):
        text = call(request(prompt_zh, n_images, seconds, aspect, feedback))
        text = re.sub(r"^```\w*\n|\n```$", "", text.strip())
        issues = problems(text, n_images)
        if not issues:
            return assemble(sections(text))
        feedback = "\n".join(f"- {x}" for x in issues)
    raise RewriteError("改写成 H3 格式两次都没过检查：" + feedback.replace("\n", " ")[:300])


def is_rewritten(text: str) -> bool:
    """A prompt already in the six-section format goes to H3 as it is."""
    try:
        sections(text)
        return True
    except RewriteError:
        return False

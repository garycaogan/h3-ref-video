#!/usr/bin/env python3
"""H3 参考图出片：像给 Seedance 写提示词、给参考图那样，在 RunningHub 上用 MiniMax H3 出视频。

    python h3ref.py one --prompt 提示词.txt --image 角色.jpg [--image 场景.jpg …] --seconds 12 --aspect 9:16 --out jobs/试一条
    python h3ref.py film jobs/<任务名> --film film.json [--frames shot|first|none]
    python h3ref.py film jobs/<任务名>                 # 断了接着跑；--redo 2 给第 2 段换种子重跑
    python h3ref.py rewrite --prompt 提示词.txt --images 3 --seconds 12 --aspect 9:16   # 只改写，不出片
    python h3ref.py account                           # RunningHub 余额和在跑任务

one：一条 5–15 秒的片段。提示词用中文写，参考图按「参考图片1、参考图片2…」的顺序点名，和给 Seedance 的一样；
工具先把它改写成 MiniMax 官方的六段式英文，再提交。已经是六段式的提示词原样提交。
film：一份分镜脚本（格式见 examples/film.example.json）出整条片：切段、每镜分镜图、改写、生成、查烧字幕、拼片。
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from h3ref import check, config, film, http, media, rewrite, rh  # noqa: E402


def cmd_one(a):
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    state_file = out / "state.json"
    st = json.loads(state_file.read_text()) if state_file.exists() else {}
    save = lambda: state_file.write_text(json.dumps(st, ensure_ascii=False, indent=1))
    images = [Path(p).expanduser().resolve() for p in a.image]
    for p in images:
        if not p.is_file():
            raise SystemExit(f"找不到参考图：{p}")
    text = Path(a.prompt).read_text(encoding="utf-8")
    t0 = time.time()
    if not st.get("h3"):
        st["h3"] = text if rewrite.is_rewritten(text) else rewrite.rewrite(text, len(images), a.seconds, a.aspect)
        (out / "prompt.h3.txt").write_text(st["h3"], encoding="utf-8")
        save()
        print("改写好了：", out / "prompt.h3.txt")
    if not st.get("task"):
        names = [rh.upload(p) for p in images]
        st["seed"] = a.seed or random.randrange(10 ** 14, 10 ** 15)
        g = rh.graph(st["h3"], names, a.seconds, a.aspect, st["seed"])
        while True:
            try:
                st["task"] = rh.submit(g)
                break
            except rh.QueueFull:
                print("RunningHub 队列满，一分钟后再交", flush=True)
                time.sleep(60)
        save()
        print(f"已提交，预计 ¥{rh.estimate_cny(a.seconds)}、4–6 分钟", flush=True)
    while True:
        res = rh.poll(st["task"])
        if res["state"] != "running":
            break
        time.sleep(20)
    if res["state"] == "failed":
        st.pop("task")
        save()
        raise SystemExit(f"生成失败：{res.get('error')}（再跑一次会重新提交）")
    clip = out / "clip.mp4"
    http.download(res["url"], clip)
    st.update(cny=res["cny"], run_s=res.get("run_seconds"), wall_s=round(time.time() - t0))
    if any(ord(ch) > 0x2E80 for ch in st["h3"]) or a.check:      # dialogue in Chinese/Japanese/Korean: look for burned subtitles
        st["subtitles"] = check.subtitles(clip)
    save()
    print(f"成片 {clip}，{media.duration(clip):.1f} 秒，¥{res['cny']:.3f}，GPU {res.get('run_seconds')} 秒")
    if (st.get("subtitles") or {}).get("burned_text"):
        print("注意：画面里烧了字幕/文字：", st["subtitles"].get("text"), "—— 换个种子（--seed）重跑，或者删掉那句台词")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("one", help="一条 5–15 秒的片段")
    o.add_argument("--prompt", required=True, help="提示词文件（中文 Seedance 写法，或者已经是六段式）")
    o.add_argument("--image", action="append", default=[], help="参考图，按 参考图片1、2… 的顺序，最多 9 张")
    o.add_argument("--seconds", type=int, default=12)
    o.add_argument("--aspect", default="9:16", choices=list(config.SIZES))
    o.add_argument("--seed", type=int)
    o.add_argument("--check", action="store_true", help="生成后一定查一次烧字幕")
    o.add_argument("--out", required=True, help="输出目录（断了再跑同一个目录会接着查结果）")
    f = sub.add_parser("film", help="一份分镜脚本出整条片")
    f.add_argument("job", type=Path)
    f.add_argument("--film", type=Path)
    f.add_argument("--frames", default="shot", choices=["shot", "first", "none"])
    f.add_argument("--redo", type=int, action="append", default=[], help="给第 N 段换种子重跑（可以写多次）")
    r = sub.add_parser("rewrite", help="只把提示词改写成 H3 官方格式")
    r.add_argument("--prompt", required=True)
    r.add_argument("--images", type=int, required=True)
    r.add_argument("--seconds", type=int, default=12)
    r.add_argument("--aspect", default="9:16", choices=list(config.SIZES))
    sub.add_parser("account", help="RunningHub 余额")
    a = ap.parse_args()
    if a.cmd == "one":
        if not 1 <= len(a.image) <= config.MAX_IMAGES:
            raise SystemExit(f"--image 要 1 到 {config.MAX_IMAGES} 张")
        if not config.MIN_SECONDS <= a.seconds <= config.MAX_SECONDS:
            raise SystemExit("--seconds 只能是 5 到 15")
        cmd_one(a)
    elif a.cmd == "film":
        film.run(a.job, a.film, a.frames, a.redo)
    elif a.cmd == "rewrite":
        print(rewrite.rewrite(Path(a.prompt).read_text(encoding="utf-8"), a.images, a.seconds, a.aspect))
    else:
        acc = rh.account()
        print({k: acc.get(k) for k in ("remainCoins", "remainMoney", "currentTaskCounts") if k in acc})


if __name__ == "__main__":
    main()

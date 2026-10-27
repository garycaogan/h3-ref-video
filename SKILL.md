---
name: h3-ref-video
description: 在 RunningHub 上用 MiniMax H3 按「提示词 + 参考图」出视频，和 Seedance 一样的写法：中文分镜和参考图自动改写成 MiniMax 官方六段式格式，一份分镜脚本能整条出片，每个镜头先用 GPT Image 2.5 画分镜图垫给 H3，自动查烧字幕、拼片、出成本报告。Use for H3 出片、把 Seedance 提示词换到 H3、RunningHub H3 参考生视频、分镜图垫图、低成本 AI 短剧和 cos 短片。Not for swapping people in an existing video (use mv-swap).
---

# h3-ref-video

工具在这个目录里：`h3ref.py` 是入口，`README.md` 讲做法、实测结论和坑。跑之前确认 `.env`（或环境变量）里有 `RUNNINGHUB_API_KEY` 和 `APIMART_API_KEY`，机器上有 Python 3.10+、ffmpeg；提示词改写默认用本机登录过的 Claude Code，没有就用 `H3_REWRITER=llm`。

1. **一条片段**（5–15 秒）：把用户的 Seedance 写法提示词存成文件，参考图按「参考图片1、参考图片2…」的顺序给：
   ```bash
   python h3ref.py one --prompt 提示词.txt --image 1.jpg --image 2.jpg --seconds 12 --aspect 9:16 --out jobs/<名字>
   ```
2. **整条片**：照 `examples/film.example.json` 写 film.json。每个镜头 1–3 秒、只做一件事，写清机位、动作和台词；角色写名字（动作里要用同一个名字，或者在 `aliases` 里列出别名，工具靠它判断每段给谁的图）、参考图和外观；场景和关键道具写进 `setting`，道具的大小、位置写具体（「只到膝盖高的列车」）。然后：
   ```bash
   python h3ref.py film jobs/<名字> --film film.json            # 默认每镜一张分镜图
   ```
   动漫、cos 线 H3 直出就够好，可以用 `--frames none` 省图钱；中文真人对白戏至少 `--frames first`；带货戏我们测下来 H3 还是不如 Seedance，先告诉用户。
3. **看结果**：读 `report.md`。标了「烧字幕」的段落用 `--redo N` 换种子重跑，或者删掉那句台词；有台词的片子交付前自己再抽几帧看一眼。H3 偶尔会垫背景音乐，需要干净人声时用 demucs 之类分离掉。
4. 断了就原样再跑同一个命令，任务号都记在 `state.json` 里，不会重复花钱。提交前告诉用户大概花费：分镜图 1K 每张约 ¥0.17，H3 每 15 秒约 ¥0.13（会员币）。

"""RunningHub: MiniMax H3 through the accelerated reference-to-video workflow (workflows/h3-ref2va-turbo.json).

The whole graph is sent with each task (asking for this workflow by id alone returns 810), with the prompt, reference
images, length, size, steps and seed filled in. H3 is billed by machine time, not by output seconds: a member key pays
RH coins (0.2 a second on the standard machine), an enterprise key pays its wallet. Measured: a 10–15 s clip runs
3½–5½ minutes, about 4 coins (≈ ¥0.009) per output second. Callers record the task id before polling, so a restart
resumes the task instead of paying twice.
"""
import json
from pathlib import Path

from . import config, http

# nodes of the workflow we fill in, and their types (checked when the graph is loaded)
PROMPT, LENGTH, SIZE, STEPS, SEED = "250", "205", "230", "124", "129"
IMAGES = ["231", "232", "233", "234", "235", "236", "237", "238", "239"]   # ref_image_0..8, in prompt order (<Picture N>)
AUDIO = ["247", "248", "249"]
NODE_TYPES = {PROMPT: "CR Prompt Text", LENGTH: "ImpactSwitch", SIZE: "MiniMaxH3ReferenceToVideo", STEPS: "BasicScheduler",
              SEED: "RandomNoise", **{n: "LoadImage" for n in IMAGES}, **{n: "LoadAudio" for n in AUDIO}}
RUN_SECONDS_PER_OUTPUT_SECOND = 22     # measured 198–330 s for 10–15 s clips; the estimate rounds up
COINS_PER_RUN_SECOND = 0.2             # member key, standard machine


class QueueFull(RuntimeError):
    """The key runs as many tasks as it may (421 on a member key, 605 when a wallet runs short); nothing was billed."""


def _post(path: str, body: dict, timeout: float = 60) -> dict:
    key = config.rh_key()
    out = http.request(config.RH_BASE + path, key, {"apiKey": key, **body}, timeout=timeout, tries=1 if path.endswith("/create") else 3)
    if isinstance(out.get("data"), dict):        # connection urls carry temporary credentials
        out["data"].pop("netWssUrl", None)
        out["data"].pop("clientId", None)
    return json.loads(json.dumps(out, ensure_ascii=False).replace(key, "[key]"))


def account() -> dict:
    return _post("/uc/openapi/accountStatus", {"apikey": config.rh_key()}).get("data") or {}


def base_graph() -> dict:
    g = json.loads(config.RH_GRAPH.read_text(encoding="utf-8"))
    bad = [n for n, t in NODE_TYPES.items() if g.get(n, {}).get("class_type") != t]
    if bad:
        raise SystemExit(f"{config.RH_GRAPH.name} 的节点对不上（{', '.join(bad)}），换过工作流就要改 rh.py 里的节点号")
    return g


def upload(path: Path) -> str:
    """The file name RunningHub gives an uploaded input; it goes into a LoadImage node as it is."""
    key = config.rh_key()
    out = http.upload(config.RH_BASE + "/task/openapi/upload", key, path, {"apiKey": key, "fileType": "input"})
    if out.get("code") != 0 or not (out.get("data") or {}).get("fileName"):
        raise RuntimeError("upload to RunningHub failed: " + json.dumps(out, ensure_ascii=False).replace(key, "[key]")[:200])
    return out["data"]["fileName"]


def graph(prompt: str, image_names: list[str], seconds: float, aspect: str, seed: int, steps: int | None = None) -> dict:
    """The workflow with this task's inputs. Length is chosen by second (5–15); the workflow snaps frames to 17k+5."""
    if len(image_names) > len(IMAGES):
        raise ValueError(f"H3 最多 {len(IMAGES)} 张参考图，这一段有 {len(image_names)} 张")
    if aspect not in config.SIZES:
        raise ValueError(f"画幅只支持 {', '.join(config.SIZES)}")
    g = base_graph()
    g[PROMPT]["inputs"]["prompt"] = prompt
    g[LENGTH]["inputs"]["select"] = max(1, min(11, round(float(seconds)) - 4))   # input1..11 = 5..15 s
    g[SIZE]["inputs"]["width"], g[SIZE]["inputs"]["height"] = config.SIZES[aspect]
    g[STEPS]["inputs"]["steps"] = int(steps or config.H3_STEPS)
    g[SEED]["inputs"]["noise_seed"] = int(seed)
    for node, name in zip(IMAGES, image_names + ["None"] * len(IMAGES)):
        g[node]["inputs"]["image"] = name
    for node in AUDIO:
        g[node]["inputs"]["audio"] = "None"
    return g


def estimate_cny(seconds: float) -> float:
    return round(max(5.0, float(seconds)) * RUN_SECONDS_PER_OUTPUT_SECOND * COINS_PER_RUN_SECOND * config.RH_COIN_CNY, 3)


def submit(g: dict) -> str:
    out = _post("/task/openapi/create", {"workflowId": config.RH_WORKFLOW_ID, "workflow": json.dumps(g, ensure_ascii=False), "addMetadata": False})
    task = (out.get("data") or {}).get("taskId")
    if out.get("code") in (421, 605, 415) or any(w in str(out.get("msg") or "") for w in ("QUEUE_MAXED", "NOT_ENOUGH_BALANCE")):
        raise QueueFull(json.dumps(out, ensure_ascii=False)[:200])
    if out.get("code") != 0 or not task:
        raise RuntimeError("RunningHub 没有接下任务：" + json.dumps(out, ensure_ascii=False)[:300])
    return str(task)


def poll(task: str) -> dict:
    """{state: running|done|failed, url, cny, coins, run_seconds, error}"""
    out = _post("/task/openapi/outputs", {"taskId": task})
    code = out.get("code")
    if code in (804, 813):          # running / queued
        return {"state": "running", "queued": code == 813}
    if code == 805:
        reason = (out.get("data") or {}).get("failedReason") or out.get("msg")
        if isinstance(reason, dict):
            reason = f"{reason.get('node_name', '')} {reason.get('exception_type', '')}: {str(reason.get('exception_message', ''))[:200]}"
        return {"state": "failed", "error": str(reason)[:300]}
    if code != 0 or not out.get("data"):
        return {"state": "running", "note": f"{code} {out.get('msg', '')}"[:60]}
    items = out["data"]
    item = next((d for d in items if str(d.get("fileType", "")).lower() in ("mp4", "mov", "webm")), items[0])
    coins = float(item.get("consumeCoins") or 0)
    money = float(item.get("consumeMoney") or 0)
    return {"state": "done", "url": item.get("fileUrl"), "cny": round(money + coins * config.RH_COIN_CNY, 4), "coins": coins,
            "money": money, "run_seconds": float(item.get("taskCostTime") or 0)}

"""Keys, endpoints and defaults. Keys come from the environment or a `.env` file at the repo root (see `.env.example`);
they are read, never written or logged."""
import os
from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent


def _load_env_file(path: Path = ROOT / ".env"):
    """KEY=value lines; values already in the environment win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def need(name: str, what: str) -> str:
    v = env(name)
    if not v:
        raise SystemExit(f"缺少 {name}（{what}）：写进仓库根目录的 .env，或者 export {name}=…；格式见 .env.example")
    return v


# RunningHub: the H3 accelerated workflow (see workflows/README.md), Standard machine, 544 on the short side, 20 steps
RH_BASE = env("RUNNINGHUB_BASE", "https://www.runninghub.cn")
RH_WORKFLOW_ID = "2113525277564424193"
RH_GRAPH = ROOT / "workflows/h3-ref2va-turbo.json"
RH_CONCURRENCY = int(env("RUNNINGHUB_CONCURRENCY", "5"))          # a member key runs 5 at once; enterprise keys more
RH_COIN_CNY = float(env("RUNNINGHUB_COIN_CNY", str(399 / 180000)))  # what one RH coin cost you (Max monthly: ¥399 for 180k)
H3_STEPS = int(env("H3_STEPS", "20"))
SIZES = {"9:16": (544, 960), "16:9": (960, 544), "1:1": (736, 736)}
MAX_IMAGES = 9                                                     # the workflow's nine LoadImage inputs
MIN_SECONDS, MAX_SECONDS = 5, 15

# APIMart: GPT Image for storyboard frames, and by default the vision check and the prompt rewriter too
APIMART_BASE = env("APIMART_BASE_URL", "https://api.apimart.ai/v1").rstrip("/")
IMAGE_MODEL = env("IMAGE_MODEL", "gpt-image-2.5-sunburst")
IMAGE_RESOLUTION = env("IMAGE_RESOLUTION", "1k")                  # 1K looked as good as 2K once H3 redraws it at 544p
IMAGE_QUALITY = env("IMAGE_QUALITY", "medium")
USD_CNY = float(env("USD_CNY", "7.2"))

# the prompt rewrite: "claude" = the local Claude Code CLI, "llm" = any OpenAI-compatible endpoint, "auto" = claude if installed
REWRITER = env("H3_REWRITER", "auto")
CLAUDE_MODEL = env("CLAUDE_MODEL", "opus")
LLM_BASE = env("LLM_BASE_URL", APIMART_BASE).rstrip("/")
LLM_MODEL = env("LLM_MODEL", "deepseek-v4-pro")
VISION_BASE = env("VISION_BASE_URL", APIMART_BASE).rstrip("/")
VISION_MODEL = env("VISION_MODEL", "gemini-3.8-flash")

# the official prompt guide is MiniMax's; it is read from their repository, not copied here (see README)
GUIDE_URL = env("H3_GUIDE_URL", "https://raw.githubusercontent.com/MiniMax-AI/MiniMax-H3/main/skills/h3-prompt-writing/references/ref-en.txt")
GUIDE_FILE = env("H3_GUIDE_FILE")                                  # or a local copy of ref-en.txt
CACHE = Path(env("H3REF_CACHE", str(Path.home() / ".cache/h3-ref-video")))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36"


def rh_key() -> str:
    return need("RUNNINGHUB_API_KEY", "RunningHub 的 API key，控制台「API 调用」里拿")


def apimart_key() -> str:
    return need("APIMART_API_KEY", "APIMart 的 key，画分镜图要用")


def llm_key() -> str:
    return env("LLM_API_KEY") or need("APIMART_API_KEY", "改写提示词用的模型 key；不用 Claude Code 时必填，也可以单独配 LLM_API_KEY")


def vision_key() -> str:
    return env("VISION_API_KEY") or need("APIMART_API_KEY", "查烧字幕用的视觉模型 key，也可以单独配 VISION_API_KEY")

"""ffmpeg helpers: picture shrinking, last frames, sample frames, joining clips."""
import subprocess
from pathlib import Path


def ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True, timeout=900)


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def shrink(src: Path, out: Path, longest: int = 1024) -> Path:
    """Image models bill reference pictures by their pixels (a 5 MB picture pushed a 1K frame from $0.015 to $0.023);
    1024 px on the long side looks the same to them."""
    from PIL import Image
    with Image.open(src) as im:
        im = im.convert("RGB")
        im.thumbnail((longest, longest), Image.LANCZOS)
        im.save(out, "JPEG", quality=90)
    return out


def last_frame(video: Path, out: Path) -> Path:
    ff("-sseof", "-0.15", "-i", str(video), "-frames:v", "1", "-update", "1", "-q:v", "2", str(out))
    return out


def sample_frames(video: Path, out_dir: Path, n: int = 8, width: int = 480) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = duration(video)
    paths = []
    for k in range(n):
        p = out_dir / f"f{k}.jpg"
        ff("-ss", f"{d * (k + 0.5) / n:.2f}", "-i", str(video), "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(p))
        if p.exists():
            paths.append(p)
    return paths


def has_audio(path: Path) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return bool(out)


def join(paths: list[Path], out: Path):
    """Clips one after another at the first clip's size, 24 fps, stereo 48 kHz (silence for a clip without sound)."""
    wh = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                         str(paths[0])], capture_output=True, text=True).stdout.strip().split(",")
    w, h = int(wh[0]), int(wh[1])
    args, chains = [], []
    for p in paths:
        args += ["-i", str(p)]
    for k, p in enumerate(paths):
        chains.append(f"[{k}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v{k}]")
        if has_audio(p):
            chains.append(f"[{k}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{k}]")
        else:
            chains.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{duration(p):.3f}[a{k}]")
    chains.append("".join(f"[v{k}][a{k}]" for k in range(len(paths))) + f"concat=n={len(paths)}:v=1:a=1[v][a]")
    ff(*args, "-filter_complex", ";".join(chains), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "18", "-preset", "fast",
       "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out))

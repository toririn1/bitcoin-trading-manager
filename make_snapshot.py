from pathlib import Path
import subprocess

ROOT = Path.cwd()
OUT = ROOT / "analyzer_snapshot.txt"

EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".cache", "cache", "logs", "log",
    "data", "datasets", "downloads", "models", "checkpoints",
}

EXCLUDE_FILES = {
    ".env", ".env.local", ".env.production", "secrets.json",
    "credentials.json", "token.json",
}

TEXT_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".toml", ".yaml", ".yml",
    ".md", ".txt", ".sh", ".bat", ".ps1", ".sql", ".ini", ".cfg",
}

MAX_FILE_CHARS = 12000
MAX_TOTAL_CHARS = 350000

def run(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT, timeout=20)
    except Exception as e:
        return f"[command failed] {cmd}\n{e}\n"

def skip_path(p: Path):
    parts = set(p.relative_to(ROOT).parts)
    if parts & EXCLUDE_DIRS:
        return True
    if p.name in EXCLUDE_FILES:
        return True
    if p.suffix.lower() in {".csv", ".parquet", ".db", ".sqlite", ".pkl", ".pt", ".pth", ".onnx", ".zip", ".tar", ".gz", ".mp4", ".png", ".jpg"}:
        return True
    return False

def tree_lines():
    lines = []
    for p in sorted(ROOT.rglob("*")):
        if skip_path(p):
            continue
        rel = p.relative_to(ROOT)
        depth = len(rel.parts)
        if depth > 4:
            continue
        indent = "  " * (depth - 1)
        mark = "/" if p.is_dir() else ""
        size = ""
        if p.is_file():
            try:
                size = f" ({p.stat().st_size} bytes)"
            except:
                pass
        lines.append(f"{indent}{p.name}{mark}{size}")
    return "\n".join(lines)

chunks = []

chunks.append("# ANALYZER SNAPSHOT\n")
chunks.append(f"ROOT: {ROOT}\n\n")

chunks.append("## git status\n")
chunks.append(run("git status --short || true"))
chunks.append("\n## git branch / recent commits\n")
chunks.append(run("git branch --show-current || true"))
chunks.append(run("git log --oneline -5 || true"))

chunks.append("\n## top-level sizes\n")
chunks.append(run("du -h --max-depth=2 . 2>/dev/null | sort -h | tail -50"))

chunks.append("\n## project tree, filtered\n")
chunks.append(tree_lines())

chunks.append("\n## selected text files\n")

total = sum(len(c) for c in chunks)

for p in sorted(ROOT.rglob("*")):
    if total > MAX_TOTAL_CHARS:
        chunks.append("\n[SNAPSHOT TRUNCATED: total size limit reached]\n")
        break
    if not p.is_file() or skip_path(p):
        continue
    if p.suffix.lower() not in TEXT_EXTS:
        continue
    try:
        size = p.stat().st_size
    except:
        continue
    if size > 80000:
        continue

    rel = p.relative_to(ROOT)
    try:
        text = p.read_text(errors="replace")
    except:
        continue

    if len(text) > MAX_FILE_CHARS:
        text = text[:MAX_FILE_CHARS] + "\n...[truncated]\n"

    block = f"\n\n--- FILE: {rel} ---\n```{p.suffix.lstrip('.')}\n{text}\n```\n"
    chunks.append(block)
    total += len(block)

OUT.write_text("".join(chunks), encoding="utf-8")
print(f"Wrote {OUT}")
print(f"Size: {OUT.stat().st_size / 1024:.1f} KB")

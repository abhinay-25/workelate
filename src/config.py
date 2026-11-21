"""Configuration, `.env` loading, and path resolution for the assistant CLI."""

import os
import re

_PLACEHOLDER_RE = re.compile(
    r"^(?:your_[a-z0-9_]*_here|placeholder.*|replace_[a-z0-9_]*|changeme.*|todo.*|<[^>]+>)$",
    re.IGNORECASE,
)
_DEFAULT_DOTENV_LOADED = False


def is_placeholder_value(val: str | None) -> bool:
    """Return True if val is empty or an unconfigured `.env` template placeholder."""
    cleaned = (val or "").strip()
    if not cleaned:
        return True
    return bool(_PLACEHOLDER_RE.match(cleaned))


def load_dotenv(dotenv_path: str | None = None) -> dict[str, str]:
    """Load key=value pairs from `.env` using stdlib only, skipping placeholder values."""
    global _DEFAULT_DOTENV_LOADED
    if dotenv_path is None:
        if _DEFAULT_DOTENV_LOADED:
            return {}
        repo_root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        target = os.path.join(repo_root, ".env")
        _DEFAULT_DOTENV_LOADED = True
    else:
        target = dotenv_path

    if not os.path.isfile(target):
        return {}

    loaded: dict[str, str] = {}
    with open(target, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if (len(val) >= 2) and ((val[0] == val[-1] == '"') or (val[0] == val[-1] == "'")):
                val = val[1:-1].strip()
            if not key or is_placeholder_value(val):
                continue
            loaded[key] = val
            if key not in os.environ or is_placeholder_value(os.environ.get(key)):
                os.environ[key] = val
    return loaded


load_dotenv()


def pack_root() -> str:
    """Resolve the data pack root via $R3_PACK, CWD, repo root, or nested pack dir."""
    env = os.environ.get("R3_PACK")
    if env and os.path.isdir(os.path.join(env, "data")):
        return os.path.abspath(env)

    cwd = os.getcwd()
    if os.path.isdir(os.path.join(cwd, "data")):
        return os.path.abspath(cwd)

    repo_root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    if os.path.isdir(os.path.join(repo_root, "data")):
        return repo_root

    nested = os.path.join(repo_root, "BuildSprint-R3-candidate")
    if os.path.isdir(os.path.join(nested, "data")):
        return nested

    return repo_root


def db_path() -> str:
    """Resolve the local SQLite database path."""
    raw = os.environ.get("R3_DB_PATH") or os.environ.get("R3_STORE") or os.path.join(os.getcwd(), ".r3_assist.db")
    return os.path.abspath(raw)

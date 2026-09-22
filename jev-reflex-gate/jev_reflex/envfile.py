"""读 .env（不依赖 python-dotenv，保持这个包零依赖）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional, Union


def load_env_file(path: Union[str, Path]) -> bool:
    target = Path(path)
    if not target.exists():
        return False
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)
    return True


def load_env(candidates: Iterable[Union[str, Path]]) -> Optional[str]:
    """按顺序尝试，读到的第一个返回其路径。已存在的环境变量优先，不会被覆盖。"""

    for candidate in candidates:
        if load_env_file(candidate):
            return str(candidate)
    return None

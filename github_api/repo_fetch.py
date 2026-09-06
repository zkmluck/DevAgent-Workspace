"""GitHub 仓库下载与文件读取。

下载策略：
1. 优先请求 GitHub API 的 tarball（api.github.com 在国内通常可达），再跟随跳转下载压缩包；
2. 若 tarball 域名不通，则退化为 Tree + Contents API，逐个下载文本文件；
3. 仓库会缓存在项目根目录 .repos/ 下，重复分析默认直接复用缓存。
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import requests

from app.config import (
    GITHUB_API,
    GITHUB_TOKEN,
    IGNORED_DIR_PARTS,
    MAX_REPO_FILES,
    REPO_DIR,
)

TEXT_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".java", ".kt", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cs",
    ".php", ".rb", ".swift", ".scala", ".sh", ".ps1", ".sql",
    ".html", ".htm", ".css", ".scss", ".vue", ".svelte",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".md", ".rst", ".txt", ".xml",
}

# 下载文件数量有限时，优先保留这些“代码类”扩展名
CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs",
    ".c", ".h", ".cpp", ".cs", ".php", ".rb", ".swift", ".sh", ".sql",
    ".html", ".css", ".vue", ".svelte",
}

_HYPHEN_LIKE = "\u2010\u2011\u2012\u2013\u2014\u2212"


class GitHubError(Exception):
    """GitHub 请求相关的可读错误。"""


def normalize_repo_name(name: str) -> str:
    """把文档复制造成的不可见连字符归一化为 ASCII 连字符。"""
    for ch in _HYPHEN_LIKE:
        name = name.replace(ch, "-")
    return name.strip("/")


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    """解析仓库地址，兼容 https、git@、以及 owner/repo 简写。"""
    url = normalize_repo_name((repo_url or "").strip())
    if not url:
        raise ValueError("仓库地址不能为空")

    match = re.search(r"github\.com[:/]([^/\s]+)/([^/\s#?]+)", url, re.IGNORECASE)
    if match:
        owner, repo = match.group(1), match.group(2)
    else:
        parts = [p for p in url.replace("\\", "/").split("/") if p and p not in (".git",)]
        if len(parts) < 2:
            raise ValueError(f"无法识别的仓库地址: {repo_url}，示例: https://github.com/owner/repo")
        owner, repo = parts[0], parts[1]

    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    owner, repo = owner.strip(), repo.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        raise ValueError(f"仓库名格式不正确: {owner}/{repo}")
    return owner, repo


def _headers(accept: str = "application/vnd.github+json") -> dict:
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


@dataclass
class RepoSnapshot:
    """一次仓库抓取的结果快照。"""

    owner: str
    repo: str
    branch: str
    root: Path
    file_count: int = 0

    @property
    def repo_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    def write_meta(self) -> None:
        meta = {
            "owner": self.owner,
            "repo": self.repo,
            "branch": self.branch,
            "file_count": self.file_count,
        }
        (self.root / ".devagent_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def from_dir(cls, repo_dir: Path):
        meta_path = repo_dir / ".devagent_meta.json"
        if not meta_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return cls(
            owner=meta["owner"],
            repo=meta["repo"],
            branch=meta["branch"],
            root=repo_dir,
            file_count=meta.get("file_count", 0),
        )


def _repo_dir(owner: str, repo: str) -> Path:
    return REPO_DIR / f"{owner}__{repo}"


def get_default_branch(owner: str, repo: str) -> str:
    url = f"{GITHUB_API}/repos/{owner}/{repo}"
    resp = requests.get(url, headers=_headers(), timeout=20)
    if resp.status_code == 200:
        return resp.json().get("default_branch", "main")
    if resp.status_code == 404:
        raise GitHubError(f"仓库不存在或没有访问权限: {owner}/{repo}")
    if resp.status_code == 403:
        raise GitHubError("GitHub API 请求被限流，请在 .env 中配置 GITHUB_TOKEN")
    raise GitHubError(f"获取仓库信息失败: HTTP {resp.status_code}")


def _tree_paths(owner: str, repo: str, branch: str) -> list[str]:
    """通过 Git Trees API 列出仓库内全部文本文件路径。"""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{quote(branch)}?recursive=1"
    resp = requests.get(url, headers=_headers(), timeout=30)
    if resp.status_code == 404:
        raise GitHubError(f"仓库或分支不存在: {owner}/{repo}@{branch}")
    if resp.status_code == 403:
        raise GitHubError("GitHub API 请求被限流，请配置 GITHUB_TOKEN 后重试")
    resp.raise_for_status()
    paths: list[str] = []
    for item in resp.json().get("tree", []):
        path = item.get("path", "")
        pure = PurePosixPath(path)
        suffix = pure.suffix.lower()
        excluded = any(part.lower() in IGNORED_DIR_PARTS for part in pure.parts[:-1])
        if item.get("type") == "blob" and suffix in TEXT_EXTENSIONS and not excluded:
            paths.append(path)
    return paths


def _download_via_contents(
    owner: str, repo: str, branch: str, dest: Path, max_files: int
) -> int:
    """Tree + Contents API 逐文件下载（不需要访问 raw/codeload 域名）。"""
    paths = _tree_paths(owner, repo, branch)
    code_first = sorted(paths, key=lambda p: (PurePosixPath(p).suffix.lower() not in CODE_EXTENSIONS, p.lower()))
    selected = code_first[:max_files]
    raw_headers = _headers(accept="application/vnd.github.raw+json")
    downloaded = 0

    for path in selected:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{quote(path, safe='/')}"
        try:
            resp = requests.get(url, headers=raw_headers, params={"ref": branch}, timeout=20)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        target = (dest / Path(path)).resolve()
        if not target.is_relative_to(dest.resolve()):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(resp.content)
        downloaded += 1
    return downloaded


def _extract_tar_gz(data: bytes, dest: Path) -> int:
    """解压 GitHub tarball，去掉第一层 {owner}-{repo}-{sha} 目录。"""
    dest = dest.resolve()
    count = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            parts = PurePosixPath(member.name).parts
            rel_parts = parts[1:] if len(parts) > 1 else ()
            if not rel_parts:
                continue
            target = dest.joinpath(*rel_parts).resolve()
            if not target.is_relative_to(dest):
                continue
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                source = tar.extractfile(member)
                if source is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read())
                count += 1
    return count


def _prune_ignored_dirs(root: Path) -> None:
    """解压完整 tarball 后，删除依赖/构建目录，避免后续分析扫描到 node_modules。"""
    root = root.resolve()
    for dirpath, dirnames, _ in os.walk(root):
        keep = []
        for name in dirnames:
            if name.lower() in IGNORED_DIR_PARTS:
                target = (Path(dirpath) / name).resolve()
                if target.is_relative_to(root):
                    shutil.rmtree(target, ignore_errors=True)
            else:
                keep.append(name)
        dirnames[:] = keep


def _count_repo_text_files(root: Path) -> int:
    """统计仓库内实际可分析的文本文件数（跳过忽略目录与 meta 文件）。"""
    root = root.resolve()
    count = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.name.startswith(".devagent"):
            continue
        rel_parts = path.relative_to(root).parts[:-1]
        if any(part.lower() in IGNORED_DIR_PARTS for part in rel_parts):
            continue
        if path.suffix.lower() in TEXT_EXTENSIONS:
            count += 1
    return count


def _download_tarball(owner: str, repo: str, branch: str, dest: Path) -> int:
    """尝试通过 API tarball 下载完整仓库。"""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/tarball/{quote(branch)}"
    try:
        resp = requests.get(url, headers=_headers(accept="application/vnd.github+json"), allow_redirects=False, timeout=25)
        if resp.status_code in (302, 301, 307, 308):
            location = resp.headers.get("Location")
            if not location:
                return 0
            resp = requests.get(location, timeout=90)
        if resp.status_code != 200:
            return 0
        count = _extract_tar_gz(resp.content, dest)
        _prune_ignored_dirs(dest)
        return count
    except (requests.RequestException, tarfile.TarError):
        return 0


def fetch_repo(repo_url: str, branch: str = "", force: bool = False) -> RepoSnapshot:
    """下载（或复用缓存中的）GitHub 仓库，返回 RepoSnapshot。"""
    owner, repo = parse_repo_url(repo_url)
    target_dir = _repo_dir(owner, repo)

    cached = RepoSnapshot.from_dir(target_dir) if target_dir.exists() else None
    if cached and not force:
        return cached

    if force and target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    resolved_branch = branch.strip() or get_default_branch(owner, repo)

    # 方案一：tarball
    count = _download_tarball(owner, repo, resolved_branch, target_dir)

    # 方案二：Contents API 逐个下载
    if count == 0:
        max_files = MAX_REPO_FILES if GITHUB_TOKEN else min(MAX_REPO_FILES, 60)
        count = _download_via_contents(owner, repo, resolved_branch, target_dir, max_files)

    count = _count_repo_text_files(target_dir) if count else 0

    if count == 0:
        raise GitHubError(
            f"仓库下载失败: {owner}/{repo}@{resolved_branch}。"
            "若网络无法访问 GitHub，请配置代理；若 API 限流，请配置 GITHUB_TOKEN。"
        )

    snapshot = RepoSnapshot(owner=owner, repo=repo, branch=resolved_branch, root=target_dir, file_count=count)
    snapshot.write_meta()
    return snapshot


def read_repo_file(repo_url: str, file_path: str, branch: str = "") -> str:
    """优先读本地缓存，其次读 GitHub 远程文件。"""
    owner, repo = parse_repo_url(repo_url)
    rel = PurePosixPath(file_path.lstrip("/"))
    if str(rel) in ("", ".", "/"):
        raise ValueError("请填写要读取的文件路径")

    cached_dir = _repo_dir(owner, repo)
    local_file = cached_dir.joinpath(*rel.parts)
    if local_file.exists() and local_file.is_file():
        return local_file.read_text(encoding="utf-8", errors="replace")

    resolved_branch = branch.strip() or get_default_branch(owner, repo)
    raw_url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{quote(str(rel), safe='/')}"
    resp = requests.get(
        raw_url,
        headers=_headers(accept="application/vnd.github.raw+json"),
        params={"ref": resolved_branch},
        timeout=20,
    )
    if resp.status_code == 200:
        return resp.text
    if resp.status_code == 404:
        raise GitHubError(f"文件不存在: {owner}/{repo}/{rel}@{resolved_branch}")
    if resp.status_code == 403:
        raise GitHubError("GitHub API 请求被限流，请配置 GITHUB_TOKEN")
    raise GitHubError(f"读取文件失败: HTTP {resp.status_code}")

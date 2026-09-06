import os

import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException

load_dotenv()

app = FastAPI(title="GitHub代码读取工具")

GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# 从网页/文档复制仓库名时，- 常被替换成不可见或全角连字符，GitHub 只接受 ASCII 连字符
_HYPHEN_LIKE = "\u2010\u2011\u2012\u2013\u2014\u2212"


def normalize_repo_name(repo_name: str) -> str:
    for ch in _HYPHEN_LIKE:
        repo_name = repo_name.replace(ch, "-")
    return repo_name.strip("/")


@app.get("/")
def root():
    return {"msg": "服务启动成功！"}


@app.get("/read_file")
def read_file(repo_name: str, file_path: str):
    repo_name = normalize_repo_name(repo_name)
    file_path = file_path.strip("/")

    headers = {"Accept": "application/vnd.github.raw+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    url = f"{GITHUB_API}/repos/{repo_name}/contents/{file_path}"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"请求 GitHub API 失败: {exc}")

    if resp.status_code == 200:
        return {
            "repo_name": repo_name,
            "file_path": file_path,
            "file_content": resp.text,
        }

    if resp.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail="仓库或文件不存在，请检查 repo_name / file_path（默认分支会自动处理）",
        )
    if resp.status_code == 403:
        raise HTTPException(
            status_code=403,
            detail="GitHub API 请求被限流，可在 .env 中配置 GITHUB_TOKEN 后重启服务",
        )
    raise HTTPException(status_code=resp.status_code, detail=resp.text)

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000)

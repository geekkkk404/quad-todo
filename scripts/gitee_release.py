"""在 Gitee 创建发行版并上传 zip 附件（供 CI 调用）。

用法：python scripts/gitee_release.py <tag> <zip路径> [release-notes.md]
需要环境变量 GITEE_TOKEN（projects 权限）。
若该 tag 的发行版已存在则直接往里追加附件，可安全重复执行。
"""
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlencode

OWNER = "geekkkk404"
REPO = "quad-todo"
API = f"https://gitee.com/api/v5/repos/{OWNER}/{REPO}"


def call(url_suffix: str, token: str, *, payload: bytes | None = None,
         headers: dict | None = None):
    sep = "&" if "?" in url_suffix else "?"
    url = f"{API}/{url_suffix}{sep}access_token={token}"
    method = "POST" if payload is not None else "GET"
    req = urllib.request.Request(url, data=payload, headers=headers or {},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = resp.read()
        return json.loads(data.decode("utf-8")) if data.strip() else {}
    except urllib.error.HTTPError as e:
        # 绝不打印完整 url（含 token），只带路径与响应体，便于定位
        detail = e.read().decode("utf-8", "replace")[:500]
        raise SystemExit(f"Gitee API 失败 {e.code} [{url_suffix}]: {detail}")


def multipart(field: str, filename: str, content: bytes):
    b = uuid.uuid4().hex
    body = (f"--{b}\r\n"
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            f"Content-Type: application/zip\r\n\r\n").encode("utf-8")
    body += content + f"\r\n--{b}--\r\n".encode("utf-8")
    return body, {"Content-Type": f"multipart/form-data; boundary={b}"}


def main() -> None:
    tag, zip_path = sys.argv[1], sys.argv[2]
    notes_file = sys.argv[3] if len(sys.argv) > 3 else None
    token = os.environ["GITEE_TOKEN"]
    notes = Path(notes_file).read_text(encoding="utf-8") if notes_file else ""

    releases = call("releases?per_page=50", token)
    rel = next((r for r in releases if r.get("tag_name") == tag), None)
    if rel is None:
        # Gitee API v5：target_commitish 必填（即使 tag 已存在），表单编码而非 JSON
        form = urlencode({"tag_name": tag, "name": f"QuadTodo {tag}",
                          "body": notes, "prerelease": "false",
                          "target_commitish": "main"}).encode("utf-8")
        rel = call("releases", token, payload=form,
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        print("Gitee release created:", rel.get("tag_name"), "id", rel.get("id"))
    else:
        print("Gitee release exists:", rel.get("tag_name"), "id", rel.get("id"))

    zip_path = Path(zip_path)
    body, headers = multipart("file", zip_path.name, zip_path.read_bytes())
    result = call(f"releases/{rel['id']}/attach_files", token,
                  payload=body, headers=headers)
    print("attached:", result.get("name") if isinstance(result, dict) else result)
    print("URL:", rel.get("html_url") or f"https://gitee.com/{OWNER}/{REPO}/releases/tag/{tag}")


if __name__ == "__main__":
    main()

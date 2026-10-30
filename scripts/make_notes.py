"""生成发行版说明 dist/RELEASE.md（CI 与一键发版共用）。
更新内容从仓库根目录的 RELEASE_NOTES.md 读取——发版前只需改那一个 markdown。"""
import sys
from pathlib import Path

TAG = sys.argv[1]
VER = TAG.lstrip("v")
NOTES_FILE = Path(__file__).resolve().parent.parent / "RELEASE_NOTES.md"

updates = ""
if NOTES_FILE.exists():
    updates = NOTES_FILE.read_text(encoding="utf-8").strip()
if not updates:
    sys.exit("RELEASE_NOTES.md 不存在或为空：发版前请把本次更新内容（markdown 列表）写进去。")

TEXT = f"""# QuadTodo {TAG}

## 下载
- **QuadTodo-v{VER}-win64-portable.zip**：绿色免安装包（推荐）。内置 Python 运行时与全部依赖，解压后双击 `QuadTodo.bat` 即可，无需安装任何东西。用法见包内「使用说明.txt」。
- Source code：源码运行，需自备 Python 3.8+（仅标准库，无需 pip 安装）。

## 更新内容
{updates}
"""

Path("dist").mkdir(exist_ok=True)
Path("dist/RELEASE.md").write_text(TEXT, encoding="utf-8")
print("release notes written to dist/RELEASE.md")

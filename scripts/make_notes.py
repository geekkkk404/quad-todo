"""生成发行版说明 dist/RELEASE.md（CI 用）。发新版时更新本文件的更新内容。"""
import sys
from pathlib import Path

TAG = sys.argv[1]
VER = TAG.lstrip("v")

TEXT = f"""# QuadTodo {TAG}

## 下载
- **QuadTodo-v{VER}-win64-portable.zip**：绿色免安装包（推荐）。内置 Python 运行时与全部依赖，解压后双击 `QuadTodo.bat` 即可，无需安装任何东西。用法见包内「使用说明.txt」。
- Source code：源码运行，需自备 Python 3.8+（仅标准库，无需 pip 安装）。

## 更新内容
- 新增 Windows 绿色免安装包：embeddable Python 3.13 + tkinter/tcl 完整内置，零基础用户开箱即用
- 发行版流水线自动化：打 tag 后自动构建绿色包并发布到 GitHub Releases 与 Gitee 发行版
- 自带「开机自启-安装或取消.bat」，指向包内运行时，不依赖系统 Python
"""

Path("dist").mkdir(exist_ok=True)
Path("dist/RELEASE.md").write_text(TEXT, encoding="utf-8")
print("release notes written to dist/RELEASE.md")

"""QuadTodo — 桌面四象限待办小窗
置顶、无边框、自动折叠贴边（鼠标移上去展开），数据纯本地（脚本同目录 tasks.json）。
用法：双击运行；`python quad_todo.pyw --smoke` 为自检模式。
"""
import json
import os
import sys
from datetime import datetime
import tkinter as tk
import tkinter.font as tkfont
from tkinter import Menu, messagebox

APP_VERSION = "0.4"

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(APP_DIR, "tasks.json")

QUADS = [
    (1, "重要且紧急", "#c0392b", "立即做"),
    (2, "重要不紧急", "#d68910", "排时间做"),
    (3, "紧急不重要", "#2874a6", "想办法少做"),
    (4, "不紧急不重要", "#7f8c8d", "尽量不做"),
]
W, H = 680, 440
PILL_H = 180          # 折叠药丸宽度按文字实测自适应（self.pill_w）
FONT = "Microsoft YaHei UI"
MENU_BG, MENU_FG = "#ffffff", "#333333"                # 右键菜单白底深字
MENU_ACTIVE_BG, MENU_ACTIVE_FG = "#2f3640", "#ffffff"  # 悬停反色（同标题栏）


def load_data():
    if not os.path.exists(DATA_FILE):
        return {"window": {}, "tasks": []}
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("window", {})
        d.setdefault("tasks", [])
        return d
    except Exception:
        try:
            os.replace(DATA_FILE, DATA_FILE + ".corrupt")
        except OSError:
            pass
        return {"window": {}, "tasks": []}


class EditDialog(tk.Toplevel):
    """主题化编辑弹窗，替代 tkinter 自带的古董输入弹窗（无主题 + 默认羽毛图标，P-016）。
    OK 后 self.result = 输入原文（调用方自行 strip 判空），取消 = None。
    """

    def __init__(self, parent, title, label, initial=""):
        super().__init__(parent)
        self.result = None
        self.withdraw()                    # 先藏起，show() 居中后再亮，避免闪跳
        self.title(title)
        self.configure(bg="#f2f3f5")
        self.resizable(False, False)
        self.transient(parent)

        body = tk.Frame(self, bg="#f2f3f5")
        body.pack(fill="both", expand=True, padx=16, pady=(14, 12))
        tk.Label(body, text=label, bg="#f2f3f5", fg="#333333",
                 font=(FONT, 10)).pack(anchor="w")
        self.ent = tk.Entry(body, font=(FONT, 11), bg="white", relief="flat",
                            highlightthickness=1, highlightbackground="#d0d4d8",
                            highlightcolor="#2f3640", insertbackground="#333333")
        self.ent.pack(fill="x", pady=(4, 14), ipady=3)
        btns = tk.Frame(self, bg="#f2f3f5")
        btns.pack(fill="x", padx=16, pady=(0, 14))
        ok = tk.Label(btns, text="保存", bg="#2f3640", fg="white",
                      font=(FONT, 10), padx=18, pady=3, cursor="hand2")
        ok.pack(side="right")
        no = tk.Label(btns, text="取消", bg="#e4e6e9", fg="#333333",
                      font=(FONT, 10), padx=18, pady=3, cursor="hand2")
        no.pack(side="right", padx=(0, 8))

        self.ent.insert(0, initial)
        for w in (self, self.ent):
            w.bind("<Return>", lambda e: self.done())
            w.bind("<Escape>", lambda e: self.cancel())
        ok.bind("<Button-1>", lambda e: self.done())
        no.bind("<Button-1>", lambda e: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)

    def done(self):
        self.result = self.ent.get()
        self._close()

    def cancel(self):
        self.result = None
        self._close()

    def _close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def show(self):
        # 居中于父窗口显示；模态由调用方 wait_window 承接（P-014 教训：阻塞式生命周期）
        self.deiconify()
        self.update_idletasks()
        m = self.master
        x = m.winfo_rootx() + (m.winfo_width() - self.winfo_reqwidth()) // 2
        y = m.winfo_rooty() + (m.winfo_height() - self.winfo_reqheight()) // 2
        self.wm_geometry(f"+{x}+{y}")
        self.attributes("-topmost", True)  # 主窗常置顶，弹窗必须压在其上
        self.ent.focus_set()
        self.ent.select_range(0, "end")
        self.ent.icursor("end")
        self.wait_visibility()
        self.grab_set()


class App:
    def __init__(self, root):
        self.root = root
        self.data = load_data()
        self.tasks = self.data["tasks"]
        self.collapsed = False
        self._editing = False          # 有输入框/对话框打开时暂停自动折叠
        self._outside = 0              # 指针离开窗口的轮询计数
        self._pressing = False         # 鼠标左键按住（拖动中）时暂停自动折叠/展开
        self._menu_open = False        # 右键菜单弹出中（tk_popup 模态阻塞期间），暂停自动折叠
        self._ptr_at_collapse = None   # 上次折叠时的指针位置：没挪动就不自动展开
        self.quads = []                # [{id, canvas, inner, name_lbl, ...}]
        self._view = "quads"           # "quads" 四象限 | "arch" 归档箱
        self._add_cancel = None        # 正在打开的快速添加输入框的取消函数
        self.auto = tk.BooleanVar(value=self.data.get("window", {}).get("auto", True))

        self.f_title = tkfont.Font(family=FONT, size=10, weight="bold")
        self.f_task = tkfont.Font(family=FONT, size=10)
        self.f_done = tkfont.Font(family=FONT, size=10, overstrike=1)
        self.f_small = tkfont.Font(family=FONT, size=9)

        root.title("四象限待办")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.94)
        root.configure(bg="#f2f3f5")
        self._make_icon()

        self._build_header()
        self._build_body()
        self._build_arch()
        self._build_pill()
        self._build_menu()
        self._restore_pos()
        self.refresh()

        root.bind_all("<MouseWheel>", self._wheel)
        root.bind_all("<ButtonPress-1>", self._press_mark, add="+")
        root.bind_all("<ButtonRelease-1>", self._press_clear, add="+")
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.after(250, self._poll)
        if "--smoke" in sys.argv:
            root.after(500, root.destroy)

    # ---------- UI 构建 ----------
    def _build_header(self):
        h = tk.Frame(self.root, bg="#2f3640", height=30)
        h.pack(side="top", fill="x")
        h.pack_propagate(False)
        self.header = h

        t = tk.Label(h, text="四象限待办", bg="#2f3640", fg="white", font=self.f_title)
        t.pack(side="left", padx=10)
        self._bind_drag(h)
        self._bind_drag(t)

        for text, cmd, tip in (("—", self.collapse, "折叠到屏幕边缘"),
                               ("自动", self._toggle_auto, "鼠标离开时自动折叠/展开"),
                               ("箱", self._toggle_arch, "归档箱：收纳完成/告一段落的任务"),
                               ("✕", self.quit, "退出")):
            b = tk.Label(h, text=text, bg="#2f3640", fg="#bbbbbb",
                         font=self.f_small, padx=8, cursor="hand2")
            b.pack(side="right")
            b.bind("<Button-1>", lambda e, c=cmd: c())
            self._tip(b, tip)
            if text == "自动":
                self.auto_btn = b
            elif text == "箱":
                self.arch_btn = b

        self._update_auto_btn()
        h.bind("<Button-3>", lambda e: self._popup(self.ctx_menu, e.x_root, e.y_root))

    def _build_body(self):
        body = tk.Frame(self.root, bg="#f2f3f5")
        body.pack(side="top", fill="both", expand=True)
        self.body = body

        for r in range(2):
            body.grid_rowconfigure(r, weight=1, uniform="q")
        for c in range(2):
            body.grid_columnconfigure(c, weight=1, uniform="q")

        for i, (qid, name, color, hint) in enumerate(QUADS):
            cell = tk.Frame(body, bg="white", bd=1, relief="solid",
                            highlightthickness=0)
            cell.grid(row=i // 2, column=i % 2, padx=4, pady=4, sticky="nsew")

            head = tk.Frame(cell, bg=color, height=24)
            head.pack(side="top", fill="x")
            head.pack_propagate(False)
            nl = tk.Label(head, text=name, bg=color, fg="white",
                          font=self.f_title, cursor="hand2")
            nl.pack(side="left", padx=8)
            self._tip(nl, hint)
            add = tk.Label(head, text="＋", bg=color, fg="white",
                           font=self.f_title, cursor="hand2", padx=6)
            add.pack(side="right")
            add.bind("<Button-1>", lambda e, qi=i: self._add_open(qi))

            wrap = tk.Frame(cell, bg="white")
            wrap.pack(side="top", fill="both", expand=True)
            canvas = tk.Canvas(wrap, bg="white", bd=0, highlightthickness=0)
            sb = tk.Scrollbar(wrap, orient="vertical", width=8, command=canvas.yview)
            canvas.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            inner = tk.Frame(canvas, bg="white")
            canvas.create_window((0, 0), window=inner, anchor="nw", tags="inner")
            inner.bind("<Configure>",
                       lambda e, cv=canvas: cv.configure(scrollregion=cv.bbox("all")))
            canvas.bind("<Configure>",
                        lambda e, cv=canvas: cv.itemconfigure("inner", width=e.width))

            self.quads.append({"id": qid, "name": name, "color": color,
                               "hint": hint, "canvas": canvas, "inner": inner,
                               "name_lbl": nl})
        self.qmap = {q["id"]: q for q in self.quads}

    def _build_pill(self):
        p = tk.Frame(self.root, bg="#2f3640", cursor="hand2")
        # Tk 事件不会从子控件冒泡到父 Frame，标签必须各自绑定点击
        go = lambda e: self.expand()
        t1 = tk.Label(p, text="待\n办", bg="#2f3640", fg="white",
                      font=self.f_title, justify="center", cursor="hand2")
        t1.pack(side="top", pady=(8, 2))
        self.pill_cnt = tk.Label(p, text="", bg="#2f3640", fg="#e67e22",
                                 font=self.f_small, justify="center", cursor="hand2")
        self.pill_cnt.pack(side="top", pady=2)
        for w in (p, t1, self.pill_cnt):
            w.bind("<Button-1>", go)
        # 宽度取最宽一行实测值，避免不同 DPI/字体下文字被裁掉一半
        self.pill_w = max(self.f_title.measure("待"), self.f_small.measure("红区"),
                          self.f_small.measure("0")) + 12
        self.pill = p

    def _make_icon(self):
        # 程序内画 32px 四象限色块图标，替代 Tk 默认"羽毛"（弹窗标题栏可见）
        s = 32
        h = s // 2
        img = tk.PhotoImage(width=s, height=s)
        img.put(QUADS[0][2], to=(0, 0, h, h))
        img.put(QUADS[1][2], to=(h, 0, s, h))
        img.put(QUADS[2][2], to=(0, h, h, s))
        img.put(QUADS[3][2], to=(h, h, s, s))
        self.root.iconphoto(True, img)   # True：根窗口 + 之后所有 Toplevel 共用
        self._icon = img                 # 必须持引用，PhotoImage 被 GC 图标会丢

    def _new_menu(self, parent=None):
        # 扁平右键菜单：白底深字、悬停深底白字反色、去 3D 边框（P-016）
        return Menu(parent or self.root, tearoff=0, font=(FONT, 10),
                    bg=MENU_BG, fg=MENU_FG,
                    activebackground=MENU_ACTIVE_BG,
                    activeforeground=MENU_ACTIVE_FG,
                    borderwidth=1, relief="flat", activeborderwidth=0)

    def _build_menu(self):
        self.ctx_menu = self._new_menu()
        self.ctx_menu.add_command(label="折叠 / 展开", command=lambda: self.expand() if self.collapsed else self.collapse())
        self.ctx_menu.add_command(label="退出", command=self.quit)

    def _popup(self, menu, x, y):
        # Windows 上 tk_popup 模态阻塞到菜单收起，期间 after 定时器照常运行；
        # 不暂停的话 _poll 把"指针悬在菜单上"判为已离开，右键约 750ms 后
        # 主窗缩成药丸、菜单孤零零留下（P-014）。阻塞覆盖菜单全生命周期（含级联）。
        self._menu_open = True
        try:
            menu.tk_popup(x, y)
        finally:
            self._menu_open = False

    def _tip(self, widget, text):
        tip = tk.Toplevel(self.root)
        tip.withdraw()
        tip.overrideredirect(True)
        lbl = tk.Label(tip, text=text, bg="#333", fg="white",
                       font=self.f_small, padx=6, pady=2)

        def enter(_):
            if self.collapsed:
                return
            lbl.configure(text=text)
            tip.deiconify()
            tip.wm_geometry(f"+{widget.winfo_rootx()}+{widget.winfo_rooty() - 24}")
            tip.attributes("-topmost", True)

        def leave(_):
            tip.withdraw()

        widget.bind("<Enter>", enter)
        widget.bind("<Leave>", leave)

    # ---------- 数据 / 渲染 ----------
    def save(self):
        self.data["window"] = {"x": self._pos[0], "y": self._pos[1],
                               "auto": self.auto.get()}
        self.data["tasks"] = self.tasks
        tmp = DATA_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, DATA_FILE)
        except OSError:
            pass

    def refresh(self):
        for qi, q in enumerate(self.quads):
            for c in q["inner"].winfo_children():
                c.destroy()
            mine = [t for t in self.tasks
                    if t["quad"] == q["id"] and not t.get("archived")]
            undone = sum(1 for t in mine if not t["done"])
            q["name_lbl"].configure(text=f"{q['name']}  {undone and undone or ''}".strip())
            if not mine:
                tk.Label(q["inner"], text=f"点右上角 ＋ 添加：{q['hint']}",
                         bg="white", fg="#b8bdc2", font=self.f_small,
                         pady=10).pack(side="top", fill="x")
            for t in mine:
                self._make_row(qi, t)
        self._refresh_arch()
        if self.collapsed:
            self._update_pill()

    def _make_row(self, qi, task):
        row = tk.Frame(self.quads[qi]["inner"], bg="white")
        row.pack(side="top", fill="x")
        cb = tk.Checkbutton(
            row, text=task["title"], anchor="w", bg="white",
            activebackground="white", bd=0, highlightthickness=0,
            font=self.f_done if task["done"] else self.f_task,
            fg="#a6adb3" if task["done"] else "#333333",
            wraplength=280, justify="left",
            command=lambda t=task: self._toggle(t))
        cb.pack(side="left", fill="x", expand=True, padx=(6, 0), pady=1)
        cb.bind("<Double-Button-1>", lambda e, t=task: self._edit(t))
        cb.bind("<Button-3>", lambda e, t=task: self._row_menu(e, t))

    def _row_menu(self, event, task):
        m = self._new_menu()
        m.add_command(label="编辑…", command=lambda: self._edit(task))
        sub = self._new_menu(m)
        for qid, name, color, _ in QUADS:
            if qid != task["quad"]:
                sub.add_command(label=name, command=lambda q=qid: self._move(task, q))
        m.add_cascade(label="移动到", menu=sub)
        m.add_separator()
        m.add_command(label="归档（收进归档箱）", command=lambda: self._archive(task))
        m.add_command(label="删除", command=lambda: self._delete(task))
        self._popup(m, event.x_root, event.y_root)

    def _toggle(self, task):
        task["done"] = not task["done"]
        self.save()
        self.refresh()

    def _edit(self, task):
        if self._editing:
            return
        self._editing = True
        try:
            dlg = EditDialog(self.root, "编辑任务", "任务内容：", task["title"])
            dlg.show()
            dlg.wait_window()      # 阻塞到弹窗关闭，期间 after 定时器照常服务
            new = dlg.result
        finally:
            self._editing = False  # 异常路径同样复位（P-014 教训同款 try/finally）
        if new is not None and new.strip():
            task["title"] = new.strip()
            self.save()
        self.refresh()

    def _move(self, task, quad):
        task["quad"] = quad
        self.save()
        self.refresh()

    def _delete(self, task):
        self.tasks.remove(task)
        self.save()
        self.refresh()

    # ---------- 归档 ----------
    def _archive(self, task):
        task["archived"] = True
        task["archived_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.save()
        self.refresh()

    def _arch_restore(self, task):
        task["archived"] = False
        task.pop("archived_at", None)
        self.save()
        self.refresh()

    def _arch_delete(self, task):
        self.tasks.remove(task)
        self.save()
        self.refresh()

    def _arch_clear(self, e=None):
        n = sum(1 for t in self.tasks if t.get("archived"))
        if not n:
            return
        self._editing = True
        self.root.attributes("-topmost", False)
        ok = messagebox.askyesno("清空归档",
                                 f"彻底删除全部 {n} 条归档任务？不可恢复。",
                                 parent=self.root)
        self.root.attributes("-topmost", True)
        self._editing = False
        if ok:
            self.tasks[:] = [t for t in self.tasks if not t.get("archived")]
            self.save()
            self.refresh()

    def _toggle_arch(self):
        self._view = "arch" if self._view == "quads" else "quads"
        self._apply_view()

    def _apply_view(self):
        self.body.pack_forget()
        self.body_arch.pack_forget()
        if self._view == "arch":
            self.body_arch.pack(side="top", fill="both", expand=True)
            self.arch_btn.configure(fg="#2ecc71")
        else:
            self.body.pack(side="top", fill="both", expand=True)
            self.arch_btn.configure(fg="#bbbbbb")
        self.refresh()

    def _build_arch(self):
        body = tk.Frame(self.root, bg="#f7f7f8")
        bar = tk.Frame(body, bg="#f7f7f8")
        bar.pack(side="top", fill="x")
        tk.Label(bar, text="归档箱", bg="#f7f7f8", fg="#444",
                 font=self.f_title).pack(side="left", padx=8, pady=4)
        tk.Label(bar, text="↩ 恢复到原象限 · ✕ 彻底删除", bg="#f7f7f8",
                 fg="#999999", font=self.f_small).pack(side="left", padx=4)
        clr = tk.Label(bar, text="清空全部", bg="#f7f7f8", fg="#c0392b",
                       font=self.f_small, cursor="hand2", padx=8)
        clr.pack(side="right")
        clr.bind("<Button-1>", self._arch_clear)
        wrap = tk.Frame(body, bg="white")
        wrap.pack(side="top", fill="both", expand=True, padx=4, pady=(0, 4))
        self.arch_canvas = tk.Canvas(wrap, bg="white", bd=0, highlightthickness=0)
        sb = tk.Scrollbar(wrap, orient="vertical", width=8,
                          command=self.arch_canvas.yview)
        self.arch_canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.arch_canvas.pack(side="left", fill="both", expand=True)
        self.arch_inner = tk.Frame(self.arch_canvas, bg="white")
        self.arch_canvas.create_window((0, 0), window=self.arch_inner,
                                       anchor="nw", tags="inner")
        self.arch_inner.bind("<Configure>", lambda e: self.arch_canvas.configure(
            scrollregion=self.arch_canvas.bbox("all")))
        self.arch_canvas.bind("<Configure>", lambda e: self.arch_canvas.itemconfigure(
            "inner", width=e.width))
        self.body_arch = body

    def _refresh_arch(self):
        for c in self.arch_inner.winfo_children():
            c.destroy()
        items = [t for t in self.tasks if t.get("archived")]
        items.sort(key=lambda t: t.get("archived_at", ""), reverse=True)
        if not items:
            tk.Label(self.arch_inner,
                     text="空。在任务上右键 → 归档，完成的事就收进这里。",
                     bg="white", fg="#b8bdc2", font=self.f_small,
                     pady=10).pack(side="top", fill="x")
        for t in items:
            self._make_arch_row(t)

    def _make_arch_row(self, task):
        row = tk.Frame(self.arch_inner, bg="white")
        row.pack(side="top", fill="x")
        q = self.qmap.get(task["quad"])
        tk.Label(row, text=q["name"], bg=q["color"], fg="white",
                 font=self.f_small, padx=4).pack(side="left", padx=(6, 4), pady=3)
        lbl = tk.Label(row, text=task["title"], bg="white", fg="#555555",
                       font=self.f_task, anchor="w")
        lbl.pack(side="left", fill="x", expand=True)
        if task.get("archived_at"):
            tk.Label(row, text=task["archived_at"], bg="white", fg="#b0b6bb",
                     font=self.f_small).pack(side="right", padx=(0, 6))
        for text, cmd, color in (("✕", self._arch_delete, "#c0392b"),
                                 ("↩", self._arch_restore, "#2874a6")):
            b = tk.Label(row, text=text, bg="white", fg=color, font=self.f_small,
                         cursor="hand2", padx=5)
            b.pack(side="right")
            b.bind("<Button-1>", lambda e, c=cmd, t=task: c(t))
        lbl.bind("<Button-3>", lambda e, t=task: self._arch_menu(e, t))

    def _arch_menu(self, event, task):
        m = self._new_menu()
        m.add_command(label="恢复到原象限", command=lambda: self._arch_restore(task))
        m.add_command(label="彻底删除", command=lambda: self._arch_delete(task))
        self._popup(m, event.x_root, event.y_root)

    # ---------- 快速添加 ----------
    def _add_open(self, qi):
        if self._editing or self.collapsed:
            return
        self._editing = True
        state = {"done": False}
        q = self.quads[qi]
        ent = tk.Entry(q["inner"], font=self.f_task, bg="#fffbe8",
                       highlightthickness=1, highlightcolor=q["color"],
                       highlightbackground="#d0d4d8")
        ent.pack(side="top", fill="x", padx=6, pady=3, before=q["inner"].winfo_children()[0]
                 if q["inner"].winfo_children() else None)

        def _finish():
            self._editing = False
            self._add_cancel = None

        def commit(_=None):
            if state["done"]:
                return
            state["done"] = True
            title = ent.get().strip()
            _finish()
            ent.destroy()
            if title:
                self.tasks.append({"id": max([t["id"] for t in self.tasks], default=0) + 1,
                                   "title": title, "quad": q["id"], "done": False})
                self.save()
            self.refresh()

        def cancel(_=None):
            if state["done"]:
                return
            state["done"] = True
            _finish()
            try:
                ent.destroy()
            except tk.TclError:
                pass

        self._add_cancel = cancel

        ent.bind("<Return>", commit)
        ent.bind("<Escape>", cancel)
        ent.bind("<FocusOut>", cancel)
        ent.focus_set()

    # ---------- 折叠 / 展开 / 位置 ----------
    def _restore_pos(self):
        win = self.data.get("window", {})
        sw = self.root.winfo_screenwidth()
        x = win.get("x", sw - W - 20)
        y = win.get("y", 90)
        x = max(0, min(x, sw - 100))
        y = max(0, min(y, self.root.winfo_screenheight() - 100))
        self._pos = [x, y]
        self.root.geometry(f"{W}x{H}+{x}+{y}")

    def _toggle_auto(self):
        self.auto.set(not self.auto.get())
        self._update_auto_btn()
        self.save()

    def _update_auto_btn(self):
        self.auto_btn.configure(
            fg="#2ecc71" if self.auto.get() else "#777777")

    def collapse(self):
        if self.collapsed:
            return
        if self._add_cancel:          # 折叠前关掉没提交的输入框
            self._add_cancel()
        sw = self.root.winfo_screenwidth()
        w = int(self.pill_w)
        self.root.geometry(f"{w}x{PILL_H}+{sw - w - 2}+{self._pos[1]}")
        self.header.pack_forget()
        self.body.pack_forget()
        self.body_arch.pack_forget()
        self.pill.pack(side="top", fill="both", expand=True)
        self.collapsed = True
        self._ptr_at_collapse = (self.root.winfo_pointerx(),
                                 self.root.winfo_pointery())
        self._update_pill()
        self.root.attributes("-alpha", 0.85)

    def expand(self):
        if not self.collapsed:
            return
        self.collapsed = False
        self.pill.pack_forget()
        self.header.pack(side="top", fill="x")
        self._apply_view()
        # 贴着药丸展开（屏幕右缘、同一高度），指针必然落进窗口内；
        # 若展开到别处而指针停在药丸上，会陷入"展开-折叠"死循环
        sw = self.root.winfo_screenwidth()
        x = max(0, sw - W - 2)
        y = self._pos[1]
        self._pos = [x, y]
        self.root.geometry(f"{W}x{H}+{x}+{y}")
        self.root.attributes("-alpha", 0.94)

    def _update_pill(self):
        undone = sum(1 for t in self.tasks
                     if not t["done"] and not t.get("archived"))
        hot = sum(1 for t in self.tasks if not t["done"] and t["quad"] == 1
                  and not t.get("archived"))
        self.pill_cnt.configure(
            text=f"{undone}\n项" + (f"\n红区\n{hot}" if hot else ""))

    def _pointer_inside(self):
        w = self.root.winfo_containing(self.root.winfo_pointerx(),
                                       self.root.winfo_pointery())
        while w is not None:
            if w is self.root:
                return True
            parent = w.winfo_parent()
            w = self.root.nametowidget(parent) if parent else None
        return False

    def _press_mark(self, _):
        self._pressing = True

    def _press_clear(self, _):
        self._pressing = False

    def _poll(self):
        if self.auto.get() and not self._menu_open and not self._editing and not self._pressing:
            inside = self._pointer_inside()
            if inside:
                self._outside = 0
                if self.collapsed and self._ptr_at_collapse != (
                        self.root.winfo_pointerx(), self.root.winfo_pointery()):
                    # 指针自上次折叠后挪动过才算"悬停"：
                    # 鼠标停在药丸位置不动时保持收起，避免弹跳循环
                    self.expand()
            else:
                self._outside += 1
                if self._outside >= 3 and not self.collapsed:  # 约 750ms 后折叠
                    self._outside = 0
                    self.collapse()
        self.root.after(250, self._poll)

    def _bind_drag(self, widget):
        widget.bind("<Button-1>", self._drag_start)
        widget.bind("<B1-Motion>", self._drag_move)
        widget.bind("<ButtonRelease-1>", self._drag_end)

    def _drag_start(self, e):
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        x = max(0, min(e.x_root - self._dx,
                       self.root.winfo_screenwidth() - 60))
        y = max(0, e.y_root - self._dy)
        if not self.collapsed:
            self._pos = [x, y]
        self.root.geometry(f"+{x}+{y}")

    def _drag_end(self, e):
        if not self.collapsed:
            self.save()

    def _wheel(self, e):
        w = self.root.winfo_containing(e.x_root, e.y_root)
        target = None
        areas = [(q["canvas"], q["inner"]) for q in self.quads] + \
                [(self.arch_canvas, self.arch_inner)]
        while w is not None and target is None:
            for cv, inner in areas:
                if w is cv or w is inner:
                    target = cv
                    break
            parent = w.winfo_parent()
            w = self.root.nametowidget(parent) if parent else None
        if target is not None:
            target.yview_scroll(-e.delta // 120 * 3, "units")

    def quit(self):
        self.save()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()

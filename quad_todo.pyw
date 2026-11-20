"""QuadTodo — 桌面四象限待办小窗
置顶、无边框、自动折叠贴边（鼠标移上去展开），数据纯本地（脚本同目录 tasks.json）。
用法：双击运行；`python quad_todo.pyw --smoke` 为自检模式；
`--collapsed` 启动即折叠成贴边药丸（开机自启命令自带，手动双击不带、仍开完整窗）；
`--watchdog-check` 为看门狗短命检查模式（计划任务触发：判活→读取证日志→该拉就拉→即退）。
"""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import traceback
import winreg
from datetime import datetime
import tkinter as tk
import tkinter.font as tkfont
from tkinter import Menu, messagebox

APP_VERSION = "0.7"

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(APP_DIR, "tasks.json")

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"  # 开机自启落点（HKCU，免管理员）
AUTOSTART_NAME = "QuadTodo"                                      # Run 键值名（任务管理器「启动应用」页显示这个名字）
CRASH_LOG = os.path.join(tempfile.gettempdir(), "qt_exit.log")   # 取证日志（deepl-quick U-006 同机制）
SINGLE_INSTANCE_MUTEX = "quad_todo_singleton"                    # 防双开互斥名（看门狗判活也用它）
WATCHDOG_TASK_NAME = r"\quad-todo\watchdog"                      # 计划任务路径（任务计划程序可见，透明）
WATCHDOG_INTERVAL_MIN = 30                                       # 周期兜底检查间隔（分钟）
# 待机唤醒事件（Kernel-Power 507=退出连接待机）触发器；Subscription 内嵌 XML 须转义
WD_EVENT_QUERY = "*[System[Provider[@Name='Microsoft-Windows-Kernel-Power'] and (EventID=507)]]"
WD_SUBSCRIPTION_ESCAPED = (
    '&lt;QueryList&gt;&lt;Query Id="0" Path="System"&gt;'
    '&lt;Select Path="System"&gt;' + WD_EVENT_QUERY + '&lt;/Select&gt;&lt;/Query&gt;&lt;/QueryList&gt;'
)

QUADS = [
    (1, "重要且紧急", "#c0392b", "立即做"),
    (2, "重要不紧急", "#d68910", "排时间做"),
    (3, "紧急不重要", "#2874a6", "想办法少做"),
    (4, "不紧急不重要", "#7f8c8d", "尽量不做"),
]
W, H = 680, 440
PILL_H = 64           # 药丸高：两行数字（红区=0 时单行仍垂直居中）；宽按"99+"实测自适应（self.pill_w）
PILL_HOT_FG = "#e74c3c"  # 红区数字：提亮版红区色（#c0392b 在深底 #2f3640 上对比不足）
FONT = "Microsoft YaHei UI"
MENU_BG, MENU_FG = "#ffffff", "#333333"                # 右键菜单白底深字
MENU_ACTIVE_BG, MENU_ACTIVE_FG = "#2f3640", "#ffffff"  # 悬停反色（同标题栏）

# topmost 自愈（P-020）：Tk 的 -topmost 读取返回内部缓存，外部 SetWindowPos 降级后
# 仍报 True（探针实测），检测必须读真实样式位；治愈用 attributes(True) 即可（实测
# 缓存态下也真发 SetWindowPos，不带激活、不动几何）。ctypes 为标准库，零依赖铁律无涉。
_u32 = ctypes.WinDLL("user32")
_u32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
_u32.GetAncestor.restype = ctypes.c_void_p          # HWND 不可按 c_int 收发，负值 HWND_* 会被零扩展（P-020 教训）
_u32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
_u32.GetWindowLongW.restype = ctypes.c_long
GA_ROOT = 2
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x8


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


_u32.WindowFromPoint.argtypes = (_POINT,)   # 带序盲区检测用（P-021）；同族纪律：显式 argtypes/restype
_u32.WindowFromPoint.restype = ctypes.c_void_p


def _crashlog(msg):
    """取证日志（deepl-quick U-006 同机制）：pythonw 静默退出不留任何系统痕迹，
    启动/回调异常/致命错误/干净退出逐行落 %TEMP%\\qt_exit.log，死后可尸检；
    看门狗复活判据也读它（exit: clean = 用户主动退出，不拉活）。"""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts} pid={os.getpid()}] v{APP_VERSION} {msg}\n")
    except OSError:
        pass


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


# ---------------------------------------------------------------- topmost 检测（P-020 + P-021 带序盲区）

def _is_real_topmost(hwnd):
    """读 Windows 真实样式位判断 topmost。Tk 的 wm_attributes("-topmost") 返回内部
    缓存，外部程序经 Win32 SetWindowPos 直接降级后仍报 True（P-020 探针实测），
    必须问系统本人；winfo_id 可能是内部子窗句柄，先 GetAncestor(GA_ROOT) 上到真顶层。"""
    try:
        hwnd = int(hwnd)
    except (TypeError, ValueError):
        return False
    if not hwnd:
        return False
    try:
        root_hwnd = _u32.GetAncestor(hwnd, GA_ROOT) or hwnd
        ex = _u32.GetWindowLongW(root_hwnd, GWL_EXSTYLE)
        return bool(ex & WS_EX_TOPMOST)
    except Exception:
        return False


def _lost_topmost_band(hwnd, x, y, w, h):
    """带序盲区检测（deepl-quick U-008 同款）：位=1 但真实带序已掉（bit-on/band-off）
    ——窗中心 WindowFromPoint 命中的根窗口不是自己、且命中者不带 WS_EX_TOPMOST 位，
    即被普通带窗口盖住。位读取看不见该状态：系统对非 topmost 带窗口的 SetWindowLong
    写位静默过滤，位可在带已丢时残留为 1；该状态只能由系统级转换产生（RDP 重连/
    睡眠唤醒/全屏切换候选），公开 API 无法伪造。命中 topmost 带邻居不视为丢失
    ——带内不争顶（任务管理器等置顶工具盖住属刻意容忍，见账本「已知限制」）。"""
    try:
        hwnd = int(hwnd)
    except (TypeError, ValueError):
        return False
    if not hwnd:
        return False
    try:
        root_hwnd = _u32.GetAncestor(hwnd, GA_ROOT) or hwnd
        hit = _u32.WindowFromPoint(_POINT(int(x) + int(w) // 2, int(y) + int(h) // 2))
        if not hit:
            return False
        hit_root = _u32.GetAncestor(hit, GA_ROOT) or hit
        if hit_root == root_hwnd:
            return False
        ex = _u32.GetWindowLongW(hit_root, GWL_EXSTYLE)
        return not (ex & WS_EX_TOPMOST)
    except Exception:
        return False


# ---------------------------------------------------------------- 单实例互斥（防双开 + 看门狗判活）

def acquire_single_instance(name=SINGLE_INSTANCE_MUTEX):
    """防双开：自启药丸常驻时再手动双击会叠出两枚。成功分支的互斥句柄刻意随进程
    存活不关闭；互斥创建失败宁可放行也不拦启动（fail-open）。"""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = k32.CreateMutexW(None, False, name)
    if not handle:
        return True
    if ctypes.get_last_error() == 183:       # ERROR_ALREADY_EXISTS
        k32.CloseHandle(handle)
        return False
    return True


def _instance_alive(name=SINGLE_INSTANCE_MUTEX):
    """试创互斥判活（看门狗用）：ERROR_ALREADY_EXISTS=183 → 有活实例。成功=无实例
    （句柄立即关闭，让位给随后复活的实例）。fail-closed：句柄拿不到当「活着」，绝不误拉。"""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = k32.CreateMutexW(None, False, name)
    if not handle:
        return True
    alive = ctypes.get_last_error() == 183
    k32.CloseHandle(handle)
    return alive


# ---------------------------------------------------------------- 开机自启（注册表 HKCU Run 键，deepl-quick U-004 同机制）

def _autostart_cmd():
    """自启命令：pythonw 免登录闪控制台；--collapsed 使登录启动直接呈贴边药丸
    （手动双击不带此参数、仍开完整窗）。脚本路径取 __file__——从任何导入方调用
    都指向本文件（deepl-quick U-007 的 argv[0] 陷阱教训前置规避）。"""
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.basename(exe).lower() != "pythonw.exe" and os.path.exists(pyw):
        exe = pyw
    return f'"{exe}" "{os.path.abspath(__file__)}" --collapsed'


def autostart_enabled(key_path=RUN_KEY_PATH):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as k:
            winreg.QueryValueEx(k, AUTOSTART_NAME)
        return True
    except OSError:
        return False


def autostart_enable(key_path=RUN_KEY_PATH):
    """upsert：键不存在则建；文件移动后重新保存一次设置即自愈。"""
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
        winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ, _autostart_cmd())


def autostart_disable(key_path=RUN_KEY_PATH):
    """幂等：键或值不存在时静默成功。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, AUTOSTART_NAME)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------- 看门狗保活（deepl-quick U-007 同机制：OS 调度短命检查进程）
# 双侧五次死亡（QuadTodo 两次 + deepl-quick 三次，2026-11-15/11-17 尸检）都无 Python 层
# 痕迹=外部硬杀，防死做不到，只能死后快速复活。看门狗不设常驻进程：计划任务每次触发
# 临时起一个 --watchdog-check 实例，判活→读取证日志→该拉就拉→即退（秒级）。调度权在
# Windows 手里——检查进程被杀也无所谓，下个周期照常再跑，「谁看门看门狗」的递归在
# OS 层终结。触发器=每 30 分钟周期兜底 + 待机唤醒事件（507）即时检查（双侧死亡窗都
# 与待机转换重合）；登录场景由自启承担，双触发会在登录瞬间三实例竞态，不重复注册。

def _watchdog_paths():
    """(exe, script)：pythonw 优先（免控制台闪现）。脚本路径取 __file__ 而非
    sys.argv[0]——从任何导入方（探针/宿主进程）调用注册时 argv[0] 是调用方路径，
    任务动作会指错目标（deepl-quick U-007 E2E 实测教训）。"""
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.basename(exe).lower() != "pythonw.exe" and os.path.exists(pyw):
        exe = pyw
    return exe, os.path.abspath(__file__)


def _watchdog_cmd():
    exe, script = _watchdog_paths()
    return f'"{exe}" "{script}" --watchdog-check'


def _watchdog_task_xml(start=None, arm_triggers=True):
    """计划任务定义 XML（现拼免外部文件）。arm_triggers=False 仅供 smoke 的
    roundtrip 用：只验注册/查询/注销链路，不武装真实触发器，保证自检零副作用。"""
    exe, script = _watchdog_paths()
    args = f'"{script}" --watchdog-check'
    start = start or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    triggers = ""
    if arm_triggers:
        triggers = f'''    <TimeTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT{WATCHDOG_INTERVAL_MIN}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
    <EventTrigger>
      <Enabled>true</Enabled>
      <Subscription>{WD_SUBSCRIPTION_ESCAPED}</Subscription>
    </EventTrigger>
'''
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>QuadTodo watchdog: revive after silent process death; skips revive when the last exit was clean (user closed)</Description>
    <URI>/quad-todo/watchdog</URI>
  </RegistrationInfo>
  <Triggers>
{triggers}  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT2M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{exe}</Command>
      <Arguments>{args}</Arguments>
    </Exec>
  </Actions>
</Task>
'''


def _schtasks(args):
    """schtasks 子进程封装：失败抛 RuntimeError（rc+输出摘要）。解码用 mbcs
    （schtasks 输出走控制台 ANSI 代码页——跨进程编码两端都要显式）。"""
    try:
        p = subprocess.run(["schtasks"] + args, capture_output=True)
    except OSError as e:
        raise RuntimeError(f"schtasks spawn failed: {e}")
    if p.returncode != 0:
        detail = (p.stderr or b"").strip() or (p.stdout or b"").strip()
        raise RuntimeError(f"schtasks {' '.join(args)} exit {p.returncode}: "
                           f"{detail.decode('mbcs', errors='replace')[:200]}")


def watchdog_task_registered(task_name=WATCHDOG_TASK_NAME):
    try:
        _schtasks(["/query", "/tn", task_name])
        return True
    except RuntimeError:
        return False


def watchdog_task_enable(task_name=WATCHDOG_TASK_NAME, arm_triggers=True):
    """upsert：XML 落临时文件（UTF-16，schtasks 要求）→ /f 覆盖注册（幂等）。
    动作指向当前 pythonw+脚本，仓库移动后重存一次设置即自愈。"""
    xml = _watchdog_task_xml(arm_triggers=arm_triggers)
    fd, tmp = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "w", encoding="utf-16") as f:
            f.write(xml)
        _schtasks(["/create", "/f", "/tn", task_name, "/xml", tmp])
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def watchdog_task_disable(task_name=WATCHDOG_TASK_NAME):
    """幂等：任务不存在时静默成功（空目录 \\quad-todo 残留无害）。"""
    try:
        _schtasks(["/delete", "/f", "/tn", task_name])
    except RuntimeError:
        pass


def _log_tail_app_line(path=None):
    """取证日志末条「应用写的行」（wd-check 审计行不是状态，跳过）；无日志/全空 → None。
    path=None 延迟到调用时解析 CRASH_LOG——默认参数会在模块加载时绑死原路径，
    smoke 的临时目录重定向就失效了（deepl-quick 在册教训）。"""
    if path is None:
        path = CRASH_LOG
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return None
    for ln in reversed(lines):
        if "wd-check" not in ln:
            return ln
    return None


def wd_should_revive(tail):
    """纯函数：末条应用行决定复活意图。exit: clean = 用户主动退出，尊重不拉活；
    其余（startup / startup (revived) / FATAL / exit: mutex / 无日志）= 他杀或未知，拉活。"""
    if not tail:
        return True
    return "exit: clean" not in tail


def _watchdog_revive():
    """拉活。CREATE_BREAKAWAY_FROM_JOB 优先：计划任务给实例配作业对象，分离后复活体
    不随任务收尾连坐（deepl-quick 实测复活体存活 2m50s > PT2M 执行限）；作业拒绝
    分离时退普通 Popen。pythonw 无窗口，两秒后父进程即退。"""
    exe, script = _watchdog_paths()
    cmd = [exe, script, "--collapsed", "--revived"]
    try:
        subprocess.Popen(cmd, creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB)
    except OSError:
        subprocess.Popen(cmd)


def run_watchdog_check():
    """看门狗短命模式：由计划任务触发，判活 → 读取证日志末行 → 该拉就拉 → 即退。
    注意本进程不写普通 startup 行（会污染末行判据），只写 wd-check 审计行。"""
    if _instance_alive():
        return 0
    tail = _log_tail_app_line()
    if not wd_should_revive(tail):
        _crashlog("wd-check: skip (user closed)")
        return 0
    _crashlog(f"wd-check: revive (tail={tail})")
    _watchdog_revive()
    return 0


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


class SettingsDialog(tk.Toplevel):
    """设置弹窗：开机自启 + 看门狗保活（deepl-quick 同机制的工具内开关，P-022）。
    勾选即生效；勾选框初值每次打开实时查注册表/任务计划程序（唯一事实源=系统，
    不信 tasks.json 纸面值），写失败立即回弹并以红字提示。主题沿用 EditDialog。"""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.withdraw()
        self.title("设置")
        self.configure(bg="#f2f3f5")
        self.resizable(False, False)
        self.transient(app.root)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        body = tk.Frame(self, bg="#f2f3f5")
        body.pack(fill="both", expand=True, padx=16, pady=(14, 8))

        self.var_as = tk.BooleanVar(value=autostart_enabled())
        self.chk_as = tk.Checkbutton(body, text="开机自启（登录后自动运行，缩成右缘药丸）",
                                     variable=self.var_as, bg="#f2f3f5", fg="#333333",
                                     activebackground="#f2f3f5", font=(FONT, 10),
                                     anchor="w", highlightthickness=0,
                                     command=self._apply_autostart)
        self.chk_as.pack(fill="x")

        self.var_wd = tk.BooleanVar(value=app.watchdog_enabled)
        self.chk_wd = tk.Checkbutton(body, text="看门狗保活（进程意外死亡后自动拉起）",
                                     variable=self.var_wd, bg="#f2f3f5", fg="#333333",
                                     activebackground="#f2f3f5", font=(FONT, 10),
                                     anchor="w", highlightthickness=0,
                                     command=self._apply_watchdog)
        self.chk_wd.pack(fill="x", pady=(6, 0))

        try:
            wd_ok = watchdog_task_registered()
        except Exception:
            wd_ok = None
        state = {True: "已注册", False: "未注册", None: "查询失败"}[wd_ok]
        tk.Label(body, text=f"看门狗任务状态（以任务计划程序为准）：{state}",
                 bg="#f2f3f5", fg="#999999", font=(FONT, 8), wraplength=300,
                 justify="left", anchor="w").pack(fill="x", pady=(2, 0))

        self.lbl_hint = tk.Label(body, text="", bg="#f2f3f5", fg="#c0392b",
                                 font=(FONT, 8), wraplength=300,
                                 justify="left", anchor="w")
        self.lbl_hint.pack(fill="x", pady=(2, 0))

        no = tk.Label(self, text="关闭", bg="#e4e6e9", fg="#333333",
                      font=(FONT, 10), padx=18, pady=3, cursor="hand2")
        no.pack(side="bottom", anchor="e", padx=16, pady=(0, 12))
        no.bind("<Button-1>", lambda e: self.destroy())
        self.bind("<Escape>", lambda e: self.destroy())

    def _fail(self, var, e):
        # 写失败即回弹勾选框，让它永远反映系统真实态；错误留在弹窗内红字提示
        var.set(not var.get())
        self.lbl_hint.configure(text=f"写入失败：{e}")

    def _apply_autostart(self):
        try:
            if self.var_as.get():
                autostart_enable()
            else:
                autostart_disable()
        except OSError as e:
            self._fail(self.var_as, e)

    def _apply_watchdog(self):
        try:
            if self.var_wd.get():
                watchdog_task_enable()
            else:
                watchdog_task_disable()
            self.app.watchdog_enabled = bool(self.var_wd.get())
            self.app.settings["watchdog_enabled"] = self.app.watchdog_enabled
            self.app.save()
        except Exception as e:
            self._fail(self.var_wd, e)

    def show(self):
        # 居中于主窗显示；模态由调用方 wait_window 承接（P-014 教训同款）
        self.deiconify()
        self.update_idletasks()
        m = self.master
        x = m.winfo_rootx() + (m.winfo_width() - self.winfo_reqwidth()) // 2
        y = m.winfo_rooty() + (m.winfo_height() - self.winfo_reqheight()) // 2
        self.wm_geometry(f"+{x}+{y}")
        self.attributes("-topmost", True)
        self.wait_visibility()
        self.grab_set()


class App:
    def __init__(self, root):
        self.root = root
        self.root.report_callback_exception = self._report_cb_exc  # 回调异常落取证日志（原默认 print 到 None stderr＝静默吞掉）
        self.data = load_data()
        self.settings = self.data.setdefault("settings", {})       # 工具内开关（看门狗），随 tasks.json 持久化
        self.watchdog_enabled = bool(self.settings.get("watchdog_enabled", False))
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
        self._top_hwnd = None          # 主窗顶层 HWND（惰性解析，topmost 自愈用，P-020）
        self._band_loss_logged = False  # 带掉取证日志的每事件闩（健康拍复位，防洪）
        self._tips = []                # 全部 tooltip Toplevel：映射中暂停自愈，防重申把 tooltip 盖住
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
        self._watchdog_selfheal()
        if "--smoke" in sys.argv:
            root.after(500, root.destroy)

    def _report_cb_exc(self, exc, val, tb):
        _crashlog("callback exception: " + "".join(traceback.format_exception(exc, val, tb)))

    def _watchdog_selfheal(self):
        """自愈一致性：开关开但计划任务不在（用户在任务计划程序手删/换机搬运）→
        静默补注册。只在勾过看门狗时才查（默认关=零子进程开销）；失败仅落取证日志，
        绝不影响主窗。"""
        if not self.watchdog_enabled:
            return
        try:
            if not watchdog_task_registered():
                watchdog_task_enable()
                _crashlog("watchdog: task re-registered (self-heal)")
        except Exception as e:
            _crashlog(f"watchdog: self-heal failed: {e}")

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
        # 纯数字药丸（P-017）：总数白、红区红，颜色即语义——悬停即展开挂不了 tooltip
        p = tk.Frame(self.root, bg="#2f3640", cursor="hand2")
        # Tk 事件不会从子控件冒泡到父 Frame，标签必须各自绑定点击
        go = lambda e: self.expand()
        self.pill_total = tk.Label(p, text="", bg="#2f3640", fg="white",
                                   font=self.f_title, cursor="hand2")
        self.pill_hot = tk.Label(p, text="", bg="#2f3640", fg=PILL_HOT_FG,
                                 font=self.f_title, cursor="hand2")
        # expand+fill：红区=0 剩一行时，数字垂直居中而非堆在顶部
        self.pill_total.pack(side="top", expand=True, fill="both")
        self.pill_hot.pack(side="top", expand=True, fill="both")
        for w in (p, self.pill_total, self.pill_hot):
            w.bind("<Button-1>", go)
        # 宽度取"99+"实测值（>99 封顶显示），避免不同 DPI/字体下数字被裁掉一半
        self.pill_w = self.f_title.measure("99+") + 12
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
        self.ctx_menu.add_command(label="设置…", command=self._open_settings)
        self.ctx_menu.add_command(label="退出", command=self.quit)

    def _open_settings(self):
        # P-014/P-016 教训同款：阻塞式弹窗用 try/finally 管 _editing，
        # 弹窗打开期间 _poll 暂停自动折叠与 topmost 重申
        if self._editing:
            return
        self._editing = True
        try:
            dlg = SettingsDialog(self)
            dlg.show()
            dlg.wait_window()
        finally:
            self._editing = False

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
        self._tips.append(tip)
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
        self.data["settings"] = self.settings
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
        self.root.geometry(f"{w}x{PILL_H}+{sw - w}+{self._pos[1]}")  # 贴死右缘：边缘热区无限宽（P-017）
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
        x = max(0, sw - W)   # 右缘与贴边药丸对齐，折叠/展开不横跳
        y = self._pos[1]
        self._pos = [x, y]
        self.root.geometry(f"{W}x{H}+{x}+{y}")
        self.root.attributes("-alpha", 0.94)

    def _update_pill(self):
        undone = sum(1 for t in self.tasks
                     if not t["done"] and not t.get("archived"))
        hot = sum(1 for t in self.tasks if not t["done"] and t["quad"] == 1
                  and not t.get("archived"))
        # 0 也照常显示（灰白）：药丸是悬停展开的唯一入口，不能消失（P-003）
        self.pill_total.configure(text="99+" if undone > 99 else str(undone),
                                  fg="#95a5a6" if undone == 0 else "white")
        if hot:
            self.pill_hot.configure(text="99+" if hot > 99 else str(hot))
            self.pill_hot.pack(side="top", expand=True, fill="both")
        else:
            self.pill_hot.pack_forget()

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

    def _ensure_topmost(self):
        # topmost 自愈（P-020 + P-021 带序盲区）：①位被清（全屏覆盖/睡眠唤醒的样式降级）
        # →重申；②位在带掉（U-008：位=1 但窗口被埋进普通带，系统级转换产物，位读取
        # 看不见）→窗中心自命中检测兜底，重申前落一条取证日志（每事件闩，健康拍复位防洪）；
        # ③健康→不动。治愈走 attributes(True)——缓存态下也会真发 SetWindowPos，不带
        # 激活、不动几何；带内被置顶邻居盖住不在此列（刻意容忍，勿在此抢带顶）。
        if self._top_hwnd is None:
            hwnd = _u32.GetAncestor(self.root.winfo_id(), GA_ROOT)
            if not hwnd:
                return
            self._top_hwnd = hwnd
        try:
            if not _is_real_topmost(self._top_hwnd):
                self.root.attributes("-topmost", True)
            elif _lost_topmost_band(self._top_hwnd, self.root.winfo_rootx(),
                                    self.root.winfo_rooty(),
                                    self.root.winfo_width(), self.root.winfo_height()):
                if not self._band_loss_logged:
                    _crashlog("topmost band-loss detected (bit set, buried) -> re-assert")
                    self._band_loss_logged = True
                self.root.attributes("-topmost", True)
            else:
                self._band_loss_logged = False
        except tk.TclError:
            pass

    def _poll(self):
        # topmost 重申有 z 序副作用（主窗插回 topmost 带顶），拖动/对话框/右键菜单期间
        # 跳过，tooltip 映射中也跳过——否则会把 tooltip 压到主窗之下（deepl-quick P-016 同款教训）。
        if not self._pressing and not self._editing and not self._menu_open \
                and not any(t.winfo_ismapped() for t in self._tips):
            self._ensure_topmost()
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


# ---------------------------------------------------------------- 自检（P-006 纪律：数据/注册表/计划任务全走临时目录与 scratch，绝不碰真实件）

def run_smoke():
    global DATA_FILE, CRASH_LOG
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # CI 管道 locale 编码防崩（dq P-014 教训前置）
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    tmp = tempfile.mkdtemp(prefix="qt_smoke_")
    real_data = DATA_FILE
    DATA_FILE = os.path.join(tmp, "tasks.json")
    CRASH_LOG = os.path.join(tmp, "qt_exit.log")

    results = []

    def check(name, cond):
        results.append((name, bool(cond)))

    # 0 真实数据保护基线：全文自检前后哈希一致
    try:
        with open(real_data, "rb") as f:
            h0 = f.read()
    except OSError:
        h0 = None

    # 1 数据文件损坏兜底
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        f.write("{broken json")
    d = load_data()
    check("corrupt data recover", d == {"window": {}, "tasks": []}
          and os.path.exists(DATA_FILE + ".corrupt"))

    # 2 取证日志（pid/版本逐行）
    _crashlog("smoke entry test")
    _crashlog("FATAL: smoke traceback test")
    with open(CRASH_LOG, encoding="utf-8") as f:
        lines = f.read().strip().splitlines()
    check("crashlog pid/version", len(lines) == 2 and f"pid={os.getpid()}" in lines[0]
          and f"v{APP_VERSION}" in lines[0] and "FATAL:" in lines[1])

    # 3 开机自启：命令形状 + scratch 键 roundtrip（绝不碰真实 Run 键）
    cmd = _autostart_cmd()
    check("autostart cmd shape", cmd.startswith('"') and cmd.endswith('" --collapsed')
          and '" "' in cmd and ".pyw" in cmd and "pythonw" in cmd.lower())
    scratch = r"Software\quad_todo_smoke_test"
    try:
        check("autostart initial off", not autostart_enabled(scratch))
        autostart_enable(scratch)
        check("autostart enable", autostart_enabled(scratch))
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, scratch, 0, winreg.KEY_READ) as k:
            stored = winreg.QueryValueEx(k, AUTOSTART_NAME)[0]
        check("autostart stored cmd", stored == cmd)
        autostart_enable(scratch)                     # upsert（文件移动后重存自愈）
        check("autostart upsert", autostart_enabled(scratch))
        autostart_disable(scratch)
        check("autostart disable", not autostart_enabled(scratch))
        autostart_disable(scratch)                    # 幂等：键值已删，静默成功
        check("autostart disable idempotent", not autostart_enabled(scratch))
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, scratch)
        except OSError:
            pass

    # 4 单实例互斥 + 看门狗判活（scratch 名，不与真实运行实例冲突）
    mname = f"quad_todo_smoke_{os.getpid()}"
    check("mutex first", acquire_single_instance(mname) is True)
    check("mutex second", acquire_single_instance(mname) is False)
    m2 = f"quad_todo_smoke_alive_{os.getpid()}"
    check("alive none", _instance_alive(m2) is False)
    check("alive held", (acquire_single_instance(m2), _instance_alive(m2))[1] is True)

    # 5 看门狗复活判据纯函数（6 态）
    check("revive no-log", wd_should_revive(None) is True)
    check("revive clean", wd_should_revive(f"[2026-11-20 10:00:00 pid=1] v{APP_VERSION} exit: clean (mainloop returned)") is False)
    check("revive startup", wd_should_revive(f"[2026-11-20 10:00:00 pid=1] v{APP_VERSION} startup") is True)
    check("revive fatal", wd_should_revive(f"[2026-11-20 10:00:00 pid=1] v{APP_VERSION} FATAL: traceback") is True)
    check("revive mutex", wd_should_revive(f"[2026-11-20 10:00:00 pid=1] v{APP_VERSION} exit: mutex busy") is True)
    check("revive revived", wd_should_revive(f"[2026-11-20 10:00:00 pid=1] v{APP_VERSION} startup (revived)") is True)

    # 6 判据读取：末条应用行（跳过 wd-check 审计行）/ 空日志 / 缺日志
    with open(CRASH_LOG, "w", encoding="utf-8") as f:
        f.write("[1] app line A\n[2] wd-check: alive\n[3] wd-check: skip\n")
    check("tail skips audit", _log_tail_app_line() == "[1] app line A")
    with open(CRASH_LOG, "w", encoding="utf-8") as f:
        f.write("   \n")
    check("tail empty", _log_tail_app_line() is None)
    check("tail missing", _log_tail_app_line(os.path.join(tmp, "no_such.log")) is None)

    # 7 计划任务 XML：动作形状 / 触发器武装与裸版 / 良构
    xml_arm = _watchdog_task_xml(start="2030-01-01T00:00:00", arm_triggers=True)
    xml_bare = _watchdog_task_xml(start="2030-01-01T00:00:00", arm_triggers=False)
    check("wd xml action", "--watchdog-check" in xml_bare and "InteractiveToken" in xml_bare
          and "quad_todo" in xml_bare and "<Command>" in xml_bare)
    check("wd xml triggers armed", f"PT{WATCHDOG_INTERVAL_MIN}M" in xml_arm
          and "EventID=507" in xml_arm and "&lt;QueryList&gt;" in xml_arm)
    check("wd xml triggers bare", "<TimeTrigger>" not in xml_bare and "<EventTrigger>" not in xml_bare)
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(xml_arm)
        ET.fromstring(xml_bare)
        wf = True
    except ET.ParseError:
        wf = False
    check("wd xml wellformed", wf)

    # 8 schtasks roundtrip（scratch 平铺任务名、不武装触发器——自检零副作用，绝不触碰真实任务）
    scratch_task = r"\quad_todo_smoke_watchdog"
    try:
        check("wd task initial absent", not watchdog_task_registered(scratch_task))
        watchdog_task_enable(scratch_task, arm_triggers=False)
        check("wd task created", watchdog_task_registered(scratch_task))
        watchdog_task_enable(scratch_task, arm_triggers=False)
        check("wd task upsert", watchdog_task_registered(scratch_task))
    finally:
        watchdog_task_disable(scratch_task)
    check("wd task removed", not watchdog_task_registered(scratch_task))
    watchdog_task_disable(scratch_task)
    check("wd task disable idempotent", not watchdog_task_registered(scratch_task))

    # 9 topmost 检测纯函数：非法句柄契约
    check("real topmost invalid hwnd", _is_real_topmost(0) is False and _is_real_topmost(None) is False)
    check("band-loss invalid hwnd", _lost_topmost_band(0, 10, 10, 38, 64) is False
          and _lost_topmost_band(None, 10, 10, 38, 64) is False)

    # 10 UI 冒烟：临时数据构造 App（--smoke 分支 500ms 自毁），验证真实 tasks.json 未触碰
    import hashlib
    h_before = hashlib.sha256(h0).hexdigest() if h0 is not None else "(absent)"
    root = tk.Tk()
    App(root)
    root.mainloop()
    try:
        with open(real_data, "rb") as f:
            h_after = hashlib.sha256(f.read()).hexdigest()
    except OSError:
        h_after = "(absent)"
    check("real tasks.json untouched", h_before == h_after)

    passed = sum(1 for _n, ok in results if ok)
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"QuadTodo smoke: {passed}/{len(results)} passed (数据目录: {tmp})")
    return 0 if passed == len(results) else 1


def main():
    if "--watchdog-check" in sys.argv:      # 短命检查模式：不写 startup 行（会污染末行判据）
        sys.exit(run_watchdog_check())
    _crashlog("startup (revived)" if "--revived" in sys.argv else "startup")
    if not acquire_single_instance():
        _crashlog("exit: mutex busy (another instance already running)")
        return                   # 已有实例在跑（自启的那枚药丸就在右缘）——静默退出
    try:
        root = tk.Tk()
        app = App(root)
        if "--collapsed" in sys.argv:
            root.update_idletasks()   # 先让 _restore_pos 的 geometry 落地，折叠取到的位置才真实
            app.collapse()            # 登录启动直接呈药丸态（手动双击不带此参数、仍开完整窗）
        root.mainloop()
        _crashlog("exit: clean (mainloop returned)")   # 用户主动退出（✕ / 右键菜单）走这里
    except Exception:
        _crashlog("FATAL: " + traceback.format_exc())
        raise


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        sys.exit(run_smoke())
    main()

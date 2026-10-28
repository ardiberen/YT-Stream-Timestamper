"""Builds fake `tkinter` and `customtkinter` packages for headless smoke tests.

Widget kwargs are validated against the real CustomTkinter source (parsed from a git clone),
so a typo'd or unsupported option raises just like it would on Windows.
"""
import ast
import os
import sys

CTK_SRC = sys.argv[1]
OUT = sys.argv[2]

TK_PASSTHROUGH = {"justify", "wrap", "show", "state", "cursor", "takefocus", "padx", "pady", "textvariable",
                  "underline", "exportselection", "insertwidth", "spacing1", "spacing2", "spacing3", "undo",
                  "autoseparators", "maxundo", "tabs", "insertbackground", "validate", "validatecommand"}

sigs = {}
for root, _, files in os.walk(os.path.join(CTK_SRC, "customtkinter", "windows")):
    for f in files:
        if not f.endswith(".py"):
            continue
        t = ast.parse(open(os.path.join(root, f), encoding="utf-8").read())
        for c in t.body:
            if isinstance(c, ast.ClassDef) and c.name.startswith("CTk"):
                for n in c.body:
                    if isinstance(n, ast.FunctionDef) and n.name == "__init__":
                        args = [a.arg for a in n.args.args[1:] + n.args.kwonlyargs]
                        sigs[c.name] = (args, n.args.kwarg is not None)

os.makedirs(os.path.join(OUT, "tkinter"), exist_ok=True)
os.makedirs(os.path.join(OUT, "customtkinter"), exist_ok=True)

TK = r'''
class Variable:
    def __init__(self, master=None, value=None, name=None):
        self._v = value if value is not None else self._default
        self._cbs = []
    _default = ""
    def get(self): return self._v
    def set(self, v):
        self._v = v
        for cb in list(self._cbs): cb("x", "", "write")
    def trace_add(self, mode, cb): self._cbs.append(cb); return str(len(self._cbs))
class StringVar(Variable):
    _default = ""
    def get(self): return str(self._v)
class BooleanVar(Variable):
    _default = False
    def get(self): return bool(self._v)
class IntVar(Variable):
    _default = 0
class DoubleVar(Variable):
    _default = 0.0
CENTER = "center"
END = "end"
'''
open(os.path.join(OUT, "tkinter", "__init__.py"), "w").write(TK)
open(os.path.join(OUT, "tkinter", "messagebox.py"), "w").write(r'''
CALLS = []
ANSWERS = {}
def _rec(kind, *a, **k):
    CALLS.append((kind, a[1] if len(a) > 1 else k.get("message", "")))
    return ANSWERS.get(kind)
def showinfo(*a, **k): return _rec("showinfo", *a, **k)
def showwarning(*a, **k): return _rec("showwarning", *a, **k)
def showerror(*a, **k): return _rec("showerror", *a, **k)
def askyesno(*a, **k): return bool(_rec("askyesno", *a, **k))
def askyesnocancel(*a, **k): return _rec("askyesnocancel", *a, **k)
''')
open(os.path.join(OUT, "tkinter", "filedialog.py"), "w").write(r'''
RETURN = {"askdirectory": "", "askopenfilename": "", "askopenfilenames": ()}
def askdirectory(**k): return RETURN["askdirectory"]
def askopenfilename(**k): return RETURN["askopenfilename"]
def askopenfilenames(**k): return RETURN["askopenfilenames"]
''')

CTK = r'''
import tkinter
from tkinter import Variable, StringVar, IntVar, DoubleVar, BooleanVar, CENTER
import tkinter.filedialog as filedialog
SIGS = %r
TK_PASSTHROUGH = %r
AFTER_QUEUE = []
_mode = ["dark"]
def set_appearance_mode(m): _mode[0] = m.lower()
def get_appearance_mode(): return "Light" if _mode[0] == "light" else "Dark"
def set_default_color_theme(t): pass

class CTkFont:
    def __init__(self, family=None, size=None, weight="normal", **k): self.size, self.weight = size, weight

def _check(cls, kw):
    args, has_kw = SIGS[cls]
    for k in kw:
        if k in args: continue
        if has_kw and k in TK_PASSTHROUGH: continue
        raise ValueError(f"{cls}: unsupported option {k!r}")

class _W:
    _cls = None
    def __init__(self, master=None, **kw):
        if self._cls: _check(self._cls, kw)
        self.master, self.kw, self.children_ = master, dict(kw), []
        self._alive = True
        self._grid = None
        if master is not None and hasattr(master, "children_"): master.children_.append(self)
    def configure(self, **kw):
        if self._cls: _check(self._cls, kw)
        self.kw.update(kw)
    config = configure
    def cget(self, k): return self.kw.get(k)
    def grid(self, **kw):
        if kw or self._grid is None: self._grid = kw
        self.gridded = True
    def grid_remove(self): self.gridded = False
    def grid_forget(self): self.gridded = False
    def pack(self, **kw): self.packed = True
    def place(self, **kw): pass
    def grid_columnconfigure(self, *a, **k): pass
    def grid_rowconfigure(self, *a, **k): pass
    def grid_propagate(self, *a): pass
    def pack_propagate(self, *a): pass
    def bind(self, *a, **k): pass
    def destroy(self):
        self._alive = False
        if self.master is not None and self in getattr(self.master, "children_", []): self.master.children_.remove(self)
    def winfo_exists(self): return self._alive
    def winfo_children(self): return list(self.children_)
    def after(self, ms, fn=None, *a):
        if fn: AFTER_QUEUE.append((fn, a))
        return "after#"
    def lift(self, *a): pass
    def focus_force(self): pass
    def focus_set(self): pass
    def grab_set(self): pass
    def grab_release(self): pass
    def transient(self, *a): pass
    def protocol(self, name, fn): self._protocol = fn
    def title(self, t=None): self._title = t
    def geometry(self, g=None): pass
    def minsize(self, *a): pass
    def clipboard_clear(self): _W.clipboard = ""
    def clipboard_append(self, t): _W.clipboard += t
    def mainloop(self): pass
    def iconbitmap(self, *a, **k): self._icon = a
    def iconphoto(self, *a, **k): self._icon = a
    def update(self): pass

def run_after(limit=200):
    n = 0
    while AFTER_QUEUE and n < limit:
        fn, a = AFTER_QUEUE.pop(0); n += 1
        fn(*a)

class CTk(_W):
    _cls = "CTk"
class CTkToplevel(_W):
    _cls = "CTkToplevel"
class CTkFrame(_W):
    _cls = "CTkFrame"
class CTkScrollableFrame(_W):
    _cls = "CTkScrollableFrame"
class CTkLabel(_W):
    _cls = "CTkLabel"
class CTkButton(_W):
    _cls = "CTkButton"
    def invoke(self):
        if self.kw.get("state") != "disabled" and self.kw.get("command"): return self.kw["command"]()
class CTkCheckBox(_W):
    _cls = "CTkCheckBox"
class CTkSwitch(_W):
    _cls = "CTkSwitch"
class CTkProgressBar(_W):
    _cls = "CTkProgressBar"
    def set(self, v): self.value = v
class CTkOptionMenu(_W):
    _cls = "CTkOptionMenu"
class CTkComboBox(_W):
    _cls = "CTkComboBox"
class CTkEntry(_W):
    _cls = "CTkEntry"
    def get(self): return self.kw["textvariable"].get() if self.kw.get("textvariable") else ""
class CTkTextbox(_W):
    _cls = "CTkTextbox"
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw); self.buf = ""
    def _ok(self):
        pass
    def insert(self, index, text, *tags):
        if self.kw.get("state") == "disabled": return  # tk ignores edits while disabled
        self.buf = text + self.buf if index == "1.0" else self.buf + text
    def delete(self, a, b=None):
        if self.kw.get("state") == "disabled": return
        self.buf = ""
    def get(self, a, b=None): return self.buf + "\n"
    def see(self, *a): pass
    def tag_config(self, tag, **kw):
        if "font" in kw: raise AttributeError("CTkTextbox tag_config font not allowed")
class CTkImage:
    def __init__(self, light_image=None, dark_image=None, size=(20, 20)):
        _check("CTkImage", {"light_image": light_image, "dark_image": dark_image, "size": size})
        self.size = size
class CTkInputDialog:
    ANSWER = None
    def __init__(self, **kw): pass
    def get_input(self): return CTkInputDialog.ANSWER
''' % (sigs, TK_PASSTHROUGH)
open(os.path.join(OUT, "customtkinter", "__init__.py"), "w").write(CTK)
print("fakes built:", len(sigs), "ctk classes")

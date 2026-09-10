#!/usr/bin/env python3
"""鸿蒙 HDC 安装工具 - Tkinter 图形界面"""

import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Optional

from hdc_installer import HdcRunner, HdcError, FileScanner


class HdcApp:
    """HDC 安装工具主应用"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("鸿蒙 HDC 安装工具")
        self.root.minsize(680, 560)

        # 后端
        self._runner = HdcRunner()
        self._scanner = FileScanner()
        self._devices: list[dict[str, str]] = []
        self._files: list[Path] = []
        self._installing = False

        self._build_ui()

        # 启动时自动刷新设备
        self.root.after(100, self._refresh_devices)

    # ── UI 构建 ────────────────────────────────────────────────────────────

    def _build_ui(self):
        # 顶层内边距
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # 右上角作者署名（与标题同行，靠右）
        ttk.Label(main, text="作者：朱信志", anchor=tk.E).pack(fill=tk.X)

        # ── 设备区 ──
        dev_frame = ttk.LabelFrame(main, text="设备", padding=8)
        dev_frame.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(dev_frame, text="选择设备:").pack(side=tk.LEFT)
        self._device_var = tk.StringVar()
        self._device_combo = ttk.Combobox(
            dev_frame, textvariable=self._device_var,
            state="readonly", width=40,
        )
        self._device_combo.pack(side=tk.LEFT, padx=6)
        ttk.Button(dev_frame, text="刷新设备", command=self._refresh_devices).pack(side=tk.LEFT)

        # ── 路径区 ──
        path_frame = ttk.LabelFrame(main, text="安装路径", padding=8)
        path_frame.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(path_frame, text="路径:").pack(side=tk.LEFT)
        self._path_var = tk.StringVar()
        path_entry = ttk.Entry(path_frame, textvariable=self._path_var, width=48)
        path_entry.pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True)
        ttk.Button(path_frame, text="浏览目录", command=self._browse_dir).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(path_frame, text="浏览文件", command=self._browse_files).pack(side=tk.LEFT, padx=(0, 4))
        self._recursive_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(path_frame, text="递归扫描子目录", variable=self._recursive_var,
                         command=self._scan_files).pack(side=tk.LEFT, padx=(4, 0))

        # 路径输入变化时自动扫描
        self._path_var.trace_add("write", lambda *_: self._schedule_scan())

        # ── 文件列表区 ──
        file_frame = ttk.LabelFrame(main, text="待安装文件", padding=8)
        file_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        columns = ("select", "name")
        self._tree = ttk.Treeview(
            file_frame, columns=columns, show="headings",
            height=5, selectmode="none",
        )
        self._tree.heading("select", text="选择")
        self._tree.heading("name", text="文件名")
        self._tree.column("select", width=50, stretch=False, anchor="center")
        self._tree.column("name", width=500, anchor="w")
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tree_scroll = ttk.Scrollbar(file_frame, orient=tk.VERTICAL, command=self._tree.yview)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.configure(yscrollcommand=tree_scroll.set)

        # 点击切换选中状态
        self._tree.bind("<ButtonRelease-1>", self._on_tree_click)

        # 全选 / 取消全选 按钮
        btn_frame = ttk.Frame(file_frame)
        btn_frame.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(btn_frame, text="全选", command=lambda: self._set_all_checked(True)).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_frame, text="取消全选", command=lambda: self._set_all_checked(False)).pack(side=tk.LEFT)

        # ── 操作按钮区 ──
        action_frame = ttk.Frame(main)
        action_frame.pack(fill=tk.X, pady=(0, 4))

        self._install_btn = ttk.Button(action_frame, text="开始安装", command=self._start_install)
        self._install_btn.pack(side=tk.LEFT)
        ttk.Button(action_frame, text="清除日志", command=self._clear_log).pack(side=tk.RIGHT)

        # ── 日志区 ──
        log_frame = ttk.LabelFrame(main, text="安装日志", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        self._log_text = tk.Text(log_frame, height=10, wrap=tk.WORD, state=tk.DISABLED)
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=log_scroll.set)
        self._log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 日志颜色标签
        self._log_text.tag_configure("success", foreground="#16a34a")
        self._log_text.tag_configure("fail", foreground="#dc2626")
        self._log_text.tag_configure("warn", foreground="#d97706")
        self._log_text.tag_configure("info", foreground="#2563eb")

        # ── 状态栏 ──
        self._status_var = tk.StringVar(value="就绪")
        ttk.Label(main, textvariable=self._status_var, relief=tk.SUNKEN, anchor=tk.W).pack(fill=tk.X)

    # ── 设备操作 ───────────────────────────────────────────────────────────

    def _refresh_devices(self):
        """刷新设备下拉列表"""
        try:
            self._devices = self._runner.list_devices()
        except HdcError as e:
            self._devices = []
            messagebox.showerror("设备刷新失败", str(e))

        values = [d["serial"] for d in self._devices]
        self._device_combo["values"] = values

        if not self._devices:
            self._device_var.set("")
            self._status_var.set("未检测到已连接的鸿蒙设备")
        elif len(self._devices) == 1:
            self._device_var.set(values[0])
            self._status_var.set(f"已连接 1 个设备: {values[0]}")
        else:
            self._device_var.set(values[0])
            self._status_var.set(f"已连接 {len(self._devices)} 个设备")

    # ── 文件扫描 ───────────────────────────────────────────────────────────

    def _schedule_scan(self):
        """防抖：路径变化后延迟扫描"""
        if hasattr(self, "_scan_after_id"):
            self.root.after_cancel(self._scan_after_id)
        self._scan_after_id = self.root.after(500, self._scan_files)

    def _scan_files(self):
        """扫描路径并更新文件列表"""
        path = self._path_var.get().strip()
        if not path:
            self._clear_tree()
            return

        self._files = self._scanner.scan(path, recursive=self._recursive_var.get())
        self._refresh_tree()

    def _browse_dir(self):
        """浏览选择目录"""
        path = filedialog.askdirectory(title="选择包含 .hap/.hsp 文件的目录")
        if path:
            self._path_var.set(path)

    def _browse_files(self):
        """浏览选择文件"""
        files = filedialog.askopenfilenames(
            title="选择 .hap/.hsp 文件",
            filetypes=[("鸿蒙安装包", "*.hap *.hsp"), ("所有文件", "*.*")],
        )
        if files:
            # 设置为第一个文件所在目录，然后手动加入文件列表
            first = Path(files[0])
            self._path_var.set(str(first.parent))
            # 直接使用选中的文件覆盖扫描结果
            self._files = sorted(Path(f) for f in files)
            self._refresh_tree()

    # ── Treeview 操作 ──────────────────────────────────────────────────────

    def _refresh_tree(self):
        """根据 self._files 刷新 Treeview"""
        self._clear_tree()
        for f in self._files:
            self._tree.insert("", tk.END, values=("☑", f.name), tags=("checked",))

    def _clear_tree(self):
        for item in self._tree.get_children():
            self._tree.delete(item)

    def _on_tree_click(self, event):
        """点击行切换选中状态"""
        item = self._tree.identify_row(event.y)
        if not item:
            return
        current = self._tree.item(item, "values")[0]
        new_val = "☐" if current == "☑" else "☑"
        new_tag = "checked" if new_val == "☑" else "unchecked"
        name = self._tree.item(item, "values")[1]
        self._tree.item(item, values=(new_val, name), tags=(new_tag,))

    def _set_all_checked(self, checked: bool):
        val = "☑" if checked else "☐"
        tag = "checked" if checked else "unchecked"
        for item in self._tree.get_children():
            name = self._tree.item(item, "values")[1]
            self._tree.item(item, values=(val, name), tags=(tag,))

    def _get_selected_files(self) -> list[Path]:
        """获取选中（☑）的文件列表"""
        result = []
        for item in self._tree.get_children():
            val = self._tree.item(item, "values")[0]
            if val == "☑":
                idx = self._tree.index(item)
                if idx < len(self._files):
                    result.append(self._files[idx])
        return result

    # ── 安装操作 ───────────────────────────────────────────────────────────

    def _start_install(self):
        """开始安装（在子线程中执行）"""
        if self._installing:
            return

        # 校验
        serial = self._device_var.get().strip()
        if not serial:
            messagebox.showwarning("提示", "请先选择一个设备")
            return

        selected = self._get_selected_files()
        if not selected:
            messagebox.showwarning("提示", "没有选中任何待安装文件")
            return

        self._installing = True
        self._install_btn.configure(state=tk.DISABLED)
        self._status_var.set("安装中...")

        # 在子线程中执行安装，避免界面卡死
        thread = threading.Thread(
            target=self._do_install,
            args=(selected, serial),
            daemon=True,
        )
        thread.start()

    def _do_install(self, files: list[Path], serial: str):
        """子线程中执行安装，通过 root.after 更新 UI"""
        # 按类型排序：.hsp 优先于 .hap，同类内按文件名排序
        sorted_files = sorted(files, key=lambda f: (0 if f.suffix.lower() == ".hsp" else 1, f.name))
        total = len(sorted_files)
        success = 0
        failed = 0

        self._log(f"\n开始安装 {total} 个文件到设备 ({serial})...\n", "info")
        self._log("安装顺序: 先安装 .hsp（共享包），再安装 .hap（应用包）\n", "info")

        for i, file_path in enumerate(sorted_files, 1):
            name = file_path.name
            self._log(f"[{i}/{total}] 安装: {name} ... ", "info")

            try:
                ok, output = self._runner.install(str(file_path), serial=serial)
            except HdcError as e:
                ok, output = False, str(e)

            if ok:
                self._log("✓ 成功\n", "success")
                success += 1
            else:
                self._log("✗ 失败\n", "fail")
                failed += 1
                if output:
                    # 检测签名错误，高亮显示
                    for line in output.splitlines():
                        if "签名" in line or "证书" in line or "UDID" in line or "签名/调试证书" in line \
                                or "═══" in line or "⚠" in line or "★" in line:
                            self._log(f"  {line}\n", "warn")
                        else:
                            self._log(f"  {line}\n", "fail")

        # 汇总
        summary = f"\n安装完成: 共 {total} 个, 成功 {success} 个, 失败 {failed} 个\n"
        self._log(summary, "info")
        self.root.after(0, lambda: self._status_var.set(
            f"安装完成: 共 {total} 个, 成功 {success} 个, 失败 {failed} 个"
        ))
        self.root.after(0, lambda: self._install_btn.configure(state=tk.NORMAL))
        self._installing = False

    # ── 日志 ───────────────────────────────────────────────────────────────

    def _log(self, message: str, tag: str = ""):
        """向日志区追加内容（线程安全）"""
        def _append():
            self._log_text.configure(state=tk.NORMAL)
            self._log_text.insert(tk.END, message, tag if tag else ())
            self._log_text.configure(state=tk.DISABLED)
            self._log_text.see(tk.END)

        if threading.current_thread() is threading.main_thread():
            _append()
        else:
            self.root.after(0, _append)

    def _clear_log(self):
        """清除日志"""
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.delete("1.0", tk.END)
        self._log_text.configure(state=tk.DISABLED)


def main():
    root = tk.Tk()
    HdcApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

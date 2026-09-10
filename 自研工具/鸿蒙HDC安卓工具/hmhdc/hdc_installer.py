#!/usr/bin/env python3
"""鸿蒙 HDC 安装工具 - 通过 Python 脚本批量安装 .hap 和 .hsp 文件到鸿蒙设备"""

import argparse
import subprocess
import sys
import os
from pathlib import Path
from typing import Optional


class HdcError(Exception):
    """HDC 命令执行异常"""
    pass


class HdcRunner:
    """HDC 命令执行器"""

    def __init__(self, hdc_path: str = "hdc"):
        self._hdc_path = hdc_path

    def run(self, args: list[str], serial: Optional[str] = None) -> subprocess.CompletedProcess:
        """执行 hdc 命令并返回结果"""
        cmd = [self._hdc_path]
        if serial:
            cmd.extend(["-t", serial])
        cmd.extend(args)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            return result
        except FileNotFoundError:
            raise HdcError(f"找不到 hdc 命令，请确认 hdc 已加入系统 PATH 环境变量")
        except subprocess.TimeoutExpired:
            raise HdcError(f"hdc 命令执行超时: {' '.join(cmd)}")

    def list_devices(self) -> list[dict[str, str]]:
        """获取已连接设备列表"""
        result = self.run(["list", "targets"])
        if result.returncode != 0:
            raise HdcError(f"获取设备列表失败: {result.stderr.strip()}")

        devices = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line and not line.startswith("["):
                devices.append({"serial": line})
        return devices

    def get_device_uid(self, serial: Optional[str] = None) -> Optional[str]:
        """获取设备 UDID（用于调试签名）"""
        # 尝试多种方式获取 UDID
        cmds = [
            ["shell", "bm", "get", "-u"],
            ["shell", "param", "get", "const.ohos.serial"],
        ]
        for cmd_args in cmds:
            try:
                result = self.run(cmd_args, serial=serial)
                if result.returncode == 0:
                    uid = result.stdout.strip()
                    if uid and not uid.startswith("[") and uid != "":
                        return uid
            except HdcError:
                continue
        return None

    def install(self, file_path: str, serial: Optional[str] = None) -> tuple[bool, str]:
        """安装文件到设备，返回 (是否成功, 输出信息)"""
        result = self.run(["install", file_path], serial=serial)
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        output = stdout or stderr

        # 合并 stdout 和 stderr 用于错误检测（某些错误信息可能分布在两处）
        full_output = f"{stdout}\n{stderr}".strip()

        if result.returncode != 0:
            error_msg = self._parse_install_error(full_output, serial)
            return False, error_msg

        # hdc install 有时 returncode 为 0 但输出中包含失败信息
        if "success" in output.lower():
            return True, output

        # returncode 为 0 但没有 success 关键字 → 可能是隐式失败
        if "fail" in output.lower() or "error" in output.lower():
            error_msg = self._parse_install_error(full_output, serial)
            return False, error_msg

        # returncode 为 0 且无明确失败标识 → 视为成功
        return True, output

    def _parse_install_error(self, output: str, serial: Optional[str] = None) -> str:
        """解析安装失败原因，返回包含诊断信息的错误描述"""
        lines = []
        lines.append(output)

        # 检测签名/UID 相关错误
        signature_keywords = [
            "signature",
            "sign",
            "cert",
            "certificate",
            "provision",
            "profile",
            "udid",
            "uid",
            "debug",
            "not in",
            "whitelist",
            "bundle name",
            "appidentifier",
        ]
        output_lower = output.lower()
        is_signature_error = any(kw in output_lower for kw in signature_keywords)

        if is_signature_error:
            lines.append("")
            lines.append("═══════════════════════════════════════════════════════════")
            lines.append("⚠  签名/调试证书相关错误")
            lines.append("═══════════════════════════════════════════════════════════")
            lines.append("该错误通常意味着设备的 UDID 未添加到调试签名配置中。")
            lines.append("")
            lines.append("解决步骤:")
            lines.append("  1. 获取设备 UDID (可通过以下命令):")
            lines.append(f"     hdc shell bm get -u")
            lines.append(f"     hdc shell param get const.ohos.serial")

            # 尝试自动获取 UID
            uid = self.get_device_uid(serial)
            if uid:
                lines.append(f"")
                lines.append(f"  ★ 已自动检测到设备 UDID: {uid}")
                lines.append(f"     请将此 UDID 添加到华为开发者中心的调试签名配置中")

            lines.append("")
            lines.append("  2. 在华为开发者中心 (https://developer.huawei.com) 更新调试签名:")
            lines.append("     - 进入「证书管理」→「调试证书」")
            lines.append("     - 将设备 UDID 添加到调试设备列表")
            lines.append("     - 重新生成调试签名 Profile")
            lines.append("")
            lines.append("  3. 使用新的签名 Profile 重新签名 .hap/.hsp 文件")
            lines.append("  4. 重新运行安装")
            lines.append("═══════════════════════════════════════════════════════════")

        return "\n".join(lines)


class DeviceManager:
    """设备管理"""

    def __init__(self, runner: HdcRunner):
        self._runner = runner

    def get_devices(self) -> list[dict[str, str]]:
        """获取已连接设备列表"""
        return self._runner.list_devices()

    def select_device(self, serial: Optional[str] = None) -> Optional[str]:
        """
        选择目标设备
        - 指定了 serial：验证设备是否存在并返回
        - 未指定：1个设备直接使用，多个设备让用户选择
        返回设备序列号，失败返回 None
        """
        try:
            devices = self.get_devices()
        except HdcError as e:
            print(f"错误: {e}")
            return None

        if not devices:
            print("未检测到已连接的鸿蒙设备，请检查:")
            print("  1. 设备是否通过 USB 连接")
            print("  2. 设备是否开启了开发者模式")
            print("  3. 是否允许了 USB 调试")
            return None

        # 指定了序列号，直接验证
        if serial:
            for d in devices:
                if d["serial"] == serial:
                    return serial
            print(f"未找到序列号为 {serial} 的设备")
            print("当前已连接设备:")
            for d in devices:
                print(f"  - {d['serial']}")
            return None

        # 只有一个设备，直接使用
        if len(devices) == 1:
            device = devices[0]
            print(f"检测到设备: {device['serial']}")
            return device["serial"]

        # 多个设备，让用户选择
        print(f"检测到 {len(devices)} 个设备，请选择:")
        for i, d in enumerate(devices, 1):
            print(f"  {i}. {d['serial']}")

        while True:
            try:
                choice = input("请输入序号 (q 退出): ").strip()
                if choice.lower() == "q":
                    return None
                idx = int(choice) - 1
                if 0 <= idx < len(devices):
                    return devices[idx]["serial"]
                print("无效序号，请重新输入")
            except (ValueError, EOFError):
                print("无效输入，请重新输入")


class FileScanner:
    """文件扫描器"""

    EXTENSIONS = {".hap", ".hsp"}

    def scan(self, path: str, recursive: bool = False) -> list[Path]:
        """
        扫描 .hap/.hsp 文件
        - path 是文件且后缀匹配：直接返回
        - path 是目录：扫描其中 .hap/.hsp 文件
        - recursive=True 时递归扫描子目录
        """
        p = Path(path).resolve()

        if p.is_file():
            if p.suffix.lower() in self.EXTENSIONS:
                return [p]
            print(f"文件 {p} 不是 .hap 或 .hsp 文件")
            return []

        if p.is_dir():
            pattern = "**/*" if recursive else "*"
            files = [
                f for f in p.glob(pattern)
                if f.is_file() and f.suffix.lower() in self.EXTENSIONS
            ]
            if not files:
                scope = "（含子目录）" if recursive else ""
                print(f"目录 {p}{scope} 中未找到 .hap/.hsp 文件")
            return sorted(files)

        print(f"路径不存在: {p}")
        return []


class Installer:
    """安装执行器"""

    def __init__(self, runner: HdcRunner):
        self._runner = runner

    def install_files(
        self,
        files: list[Path],
        serial: Optional[str] = None,
        continue_on_error: bool = True,
    ) -> dict:
        """
        批量安装文件
        返回安装结果统计 {"total", "success", "failed", "details"}
        """
        total = len(files)
        success = 0
        failed = 0
        details = []

        print(f"\n准备安装 {total} 个文件到设备{' (' + serial + ')' if serial else ''}...")
        print("安装顺序: 先安装 .hsp（共享包），再安装 .hap（应用包）\n")

        # 按类型排序：.hsp 优先于 .hap，同类内按文件名排序
        sorted_files = sorted(files, key=lambda f: (0 if f.suffix.lower() == ".hsp" else 1, f.name))

        for i, file_path in enumerate(sorted_files, 1):
            name = file_path.name
            print(f"[{i}/{total}] 安装: {name} ... ", end="", flush=True)

            try:
                ok, output = self._runner.install(str(file_path), serial=serial)
            except HdcError as e:
                ok, output = False, str(e)

            if ok:
                print("✓ 成功")
                success += 1
                details.append({"file": name, "status": "success", "output": output})
            else:
                print("✗ 失败")
                failed += 1
                details.append({"file": name, "status": "failed", "output": output})
                # 显示错误信息
                if output:
                    for line in output.splitlines():
                        print(f"         {line}")

                if not continue_on_error:
                    print("\n安装中止（使用 --continue-on-error 可在失败后继续）")
                    break

        print(f"\n安装完成: 共 {total} 个, 成功 {success} 个, 失败 {failed} 个")
        return {"total": total, "success": success, "failed": failed, "details": details}


# ── CLI ──────────────────────────────────────────────────────────────────────


def cmd_devices(args):
    """列出已连接设备"""
    runner = HdcRunner()
    dm = DeviceManager(runner)

    try:
        devices = dm.get_devices()
    except HdcError as e:
        print(f"错误: {e}")
        return 1

    if not devices:
        print("未检测到已连接的鸿蒙设备")
        return 0

    print(f"已连接 {len(devices)} 个设备:")
    for d in devices:
        print(f"  - {d['serial']}")
    return 0


def cmd_install(args):
    """安装 .hap/.hsp 文件"""
    # 扫描文件
    scanner = FileScanner()
    files = scanner.scan(args.path, recursive=args.recursive)
    if not files:
        return 1

    # dry-run 模式：仅列出文件，不检测设备也不执行安装
    if args.dry_run:
        print(f"\n将安装以下 {len(files)} 个文件 (dry-run 模式，不实际执行):")
        for f in files:
            print(f"  - {f}")
        return 0

    # 选择设备
    runner = HdcRunner()
    dm = DeviceManager(runner)
    serial = dm.select_device(args.serial)
    if serial is None:
        return 1

    # 执行安装
    installer = Installer(runner)
    result = installer.install_files(files, serial=serial, continue_on_error=args.continue_on_error)

    return 0 if result["failed"] == 0 else 1


def main():
    # 提前拦截 --gui 参数（argparse 不处理顶层 flag）
    if "--gui" in sys.argv:
        from hdc_gui import main as gui_main
        gui_main()
        return 0

    parser = argparse.ArgumentParser(
        description="鸿蒙 HDC 安装工具 - 批量安装 .hap 和 .hsp 文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python hdc_installer.py --gui                           启动图形界面
  python hdc_installer.py devices                         列出已连接设备
  python hdc_installer.py install app.hap                 安装单个文件
  python hdc_installer.py install ./packages/             安装目录下所有 .hap/.hsp
  python hdc_installer.py install ./packages/ -r          递归扫描子目录
  python hdc_installer.py install app.hap -s DEVICE123    指定设备安装
  python hdc_installer.py install ./packages/ --dry-run   仅预览，不实际安装
        """,
    )
    parser.add_argument("--gui", action="store_true", help="启动图形界面")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # devices 子命令
    subparsers.add_parser("devices", help="列出已连接的鸿蒙设备")

    # install 子命令
    install_parser = subparsers.add_parser("install", help="安装 .hap/.hsp 文件到设备")
    install_parser.add_argument("path", help="要安装的文件路径或目录路径")
    install_parser.add_argument("-s", "--serial", help="指定目标设备序列号")
    install_parser.add_argument("-r", "--recursive", action="store_true", help="递归扫描子目录")
    install_parser.add_argument("--dry-run", action="store_true", help="仅显示将要安装的文件，不实际执行")
    install_parser.add_argument(
        "--continue-on-error",
        action="store_true",
        default=True,
        help="批量安装时某个文件失败后继续安装其余文件（默认启用）",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "devices":
        return cmd_devices(args)
    elif args.command == "install":
        return cmd_install(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())

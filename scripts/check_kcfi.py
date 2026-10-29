#!/usr/bin/env python3
"""
魅族 21 Pro (M2481) 编译期 ARM64 Clang kCFI 类型哈希强制门禁脚本
用法:
    python3 check_kcfi.py [vmlinux路径] [clang_bin目录]
环境变量支持:
    VMLINUX_PATH, CLANG_BIN
"""
import os
import re
import struct
import subprocess
import sys

vmlinux_path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("VMLINUX_PATH", "/home/ovo/m2481-kernel/common/vmlinux")
clang_bin = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("CLANG_BIN", "/home/ovo/m2481-kernel/clang-r487747c/bin")

nm_bin = os.path.join(clang_bin, "llvm-nm")
readelf_bin = os.path.join(clang_bin, "llvm-readelf")
objdump_bin = os.path.join(clang_bin, "llvm-objdump")

nm_out = subprocess.check_output([nm_bin, vmlinux_path], text=True)
syms = {}
for line in nm_out.splitlines():
    parts = line.split()
    if len(parts) == 3:
        syms[parts[2]] = (int(parts[0], 16), parts[1])

sec_out = subprocess.check_output([readelf_bin, "-S", "-W", vmlinux_path], text=True)
text_addr = None
text_off = None
text_size = None
for line in sec_out.splitlines():
    if " .text " in line:
        parts = line.split()
        idx = parts.index(".text")
        text_addr = int(parts[idx + 2], 16)
        text_off = int(parts[idx + 3], 16)
        text_size = int(parts[idx + 4], 16)
        break

with open(vmlinux_path, "rb") as f:
    f.seek(text_off)
    text_data = f.read(text_size)


def read_u32_before(addr):
    off = addr - text_addr - 4
    if 0 <= off <= len(text_data) - 4:
        return struct.unpack("<I", text_data[off : off + 4])[0]
    return None


# 1. 魅族 .ko 通过 kallsyms 查找并间接调用的内核函数前导码 kCFI 哈希校验
targets = [
    ("shrink_slab", 0xC3B4BEAC),
    ("do_shrink_slab", None),
    ("setup_per_zone_wmarks", 0xA540670C),
    ("clk_set_duty_cycle", None),
    ("__brelse", None),
    ("end_buffer_write_sync", 0xD2948EB7),
    ("find_lock_task_mm", 0xEA4A421A),
    ("sched_show_task", 0x1703DDE1),
    ("android_debug_symbol", 0x0EC970AB),
    ("kallsyms_lookup_name", 0xAA7E236F),
]

failed = False
print("=== 1. Direct Function Preamble kCFI Audit ===")
for name, expected in targets:
    if name in syms:
        addr, stype = syms[name]
        actual = read_u32_before(addr)
        kcfi_sym = syms.get(f"__kcfi_typeid_{name}", (None, None))[0]
        exp_str = f"0x{expected:08x}" if expected is not None else "N/A       "
        act_str = f"0x{actual:08x}" if actual is not None else "None      "
        kcfi_str = f"0x{kcfi_sym:08x}" if kcfi_sym is not None else "None      "
        ok = expected is None or actual == expected
        if not ok:
            failed = True
        match = "MATCH" if ok else "MISMATCH!!!"
        print(f"{name:28s} type={stype} addr=0x{addr:016x} actual={act_str} kcfi_sym={kcfi_str} expected={exp_str} -> {match}")
    else:
        failed = True
        print(f"{name:28s} NOT FOUND in vmlinux")

# 2. 魅族 OneMind 8 大 MM Vendor Hook __traceiter 调用点 kCFI 哈希校验
traceiter_targets = [
    ("__traceiter_android_vh_extfrag_for_order", 0x55DDC73D),
    ("__traceiter_android_vh_tune_watermark_ratio", 0xD25B6965),
    ("__traceiter_android_vh_mm_vip_alloc_harder", 0xFAB75373),
    ("__traceiter_android_vh_mm_vip_kvmalloc", 0xD25B6965),
    ("__traceiter_android_vh_mm_vip_alloc_harder_stat", 0x82360994),
    ("__traceiter_android_vh_ra_tuning_max_page", 0x4F5F504F),
    ("__traceiter_android_vh_memcg_filepages_protect", 0xCACB6617),
    ("__traceiter_android_vh_memcg_protect_lru_gen_add_folio", 0xB2BF7B95),
]

print("\n=== 2. Meizu OneMind MM Vendor Hook Traceiter Call-Site kCFI Audit ===")
for name, expected in traceiter_targets:
    if name not in syms:
        print(f"{name:55s} NOT FOUND in vmlinux")
        failed = True
        continue
    addr, _ = syms[name]
    asm = subprocess.check_output(
        [
            objdump_bin,
            "-d",
            f"--start-address={hex(addr)}",
            f"--stop-address={hex(addr + 0x90)}",
            vmlinux_path,
        ],
        text=True,
    )
    movk_vals = re.findall(r"movk\s+w17,\s+#0x([0-9a-fA-F]+)(?:,\s+lsl #16)?", asm)
    if len(movk_vals) >= 2:
        lo = int(movk_vals[0], 16)
        hi = int(movk_vals[1], 16)
        actual = (hi << 16) | lo
        ok = actual == expected
        if not ok:
            failed = True
        match = "MATCH" if ok else "MISMATCH!!!"
        print(f"{name:55s} callsite_kcfi=0x{actual:08x} expected=0x{expected:08x} -> {match}")
    else:
        print(f"{name:55s} ERROR: could not find movk w17 pair!")
        failed = True

if failed:
    sys.exit(1)
print("\n[ALL kCFI CHECKS PASSED 100%!]")

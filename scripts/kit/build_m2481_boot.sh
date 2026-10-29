#!/usr/bin/env bash
# 魅族 21 Pro (M2481) 纯净版内核一键编译、kCFI/krcrc 校验与 AVB 打包脚本
set -e

# ======== 根据你的实际环境修改以下路径变量 ========
export WORK_DIR="${WORK_DIR:-$HOME/m2481-kernel}"
export KERNEL_DIR="${KERNEL_DIR:-$WORK_DIR/common}"
export CLANG_BIN="${CLANG_BIN:-$WORK_DIR/clang-r487747c/bin}"
export STOCK_BOOT_PATH="${STOCK_BOOT_PATH:-$WORK_DIR/baseline/boot.img}"
export OUT_BOOT_PATH="${OUT_BOOT_PATH:-$WORK_DIR/m2481_custom_boot.img}"
# ===================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$KERNEL_DIR"
rm -f .version

# 如果尚未放置 .config，则自动复制已清理魅族内网路径的原厂配置
if [ ! -f ".config" ]; then
    echo "[INFO] Copying baseline/stock_kernel_clean.config to $KERNEL_DIR/.config ..."
    cp "$KIT_DIR/baseline/stock_kernel_clean.config" .config
fi

export PATH="$CLANG_BIN:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export KBUILD_BUILD_USER="${KBUILD_BUILD_USER:-build-user}"
export KBUILD_BUILD_HOST="${KBUILD_BUILD_HOST:-build-host}"
export KBUILD_BUILD_TIMESTAMP="${KBUILD_BUILD_TIMESTAMP:-Mon Mar  2 08:39:20 UTC 2026}"
export KBUILD_BUILD_VERSION="1"

echo "=== [Step 1/4] Building Kernel Image ==="
make -j$(nproc) ARCH=arm64 LLVM=1 LLVM_IAS=1 Image
strings arch/arm64/boot/Image | grep "Linux version 6.1.25"

echo "=== [Step 2/4] Running kCFI Type Hash Verification ==="
python3 "$SCRIPT_DIR/check_kcfi.py" "$KERNEL_DIR/vmlinux" "$CLANG_BIN"

echo "=== [Step 3/4] Running KMI Audit & Locking Binary __kcrctab CRCs ==="
if [ -f "$KERNEL_DIR/Module.symvers" ]; then
    python3 "$SCRIPT_DIR/check_krcrc_symvers.py" \
        "$KERNEL_DIR/Module.symvers" \
        "$KIT_DIR/baseline/stock_Module.symvers" \
        "$KERNEL_DIR/arch/arm64/boot/Image"
fi

echo "=== [Step 4/4] Packing 96MB Boot Image with Stock AVB0 + AVBf Footer ==="
python3 "$SCRIPT_DIR/repack_avb_boot.py" \
    "$KERNEL_DIR/arch/arm64/boot/Image" \
    "$STOCK_BOOT_PATH" \
    "$OUT_BOOT_PATH"

ls -la "$OUT_BOOT_PATH"
echo "[SUCCESS] Build completed: $OUT_BOOT_PATH"

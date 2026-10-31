#!/bin/bash
# KernelSU 编译后置处理：版本标识兼容性校准
# 用途：统一不同 KSU 变体的版本名格式，避免构建产物版本标识不一致

set -e

KSU_DIR="${1:-KernelSU}"
MODE="${2:-auto}"   # auto | bakasu | sukisu

# 版本标识常量（base64 编码，避免明文暴露）
declare -A PATTERNS
PATTERNS[bakasu]=$(echo 'UkVQT19OQU1FIDo9IEJha2FTVQ==' | base64 -d | base64)
PATTERNS[sukisu_branch]=$(echo 'QCQoY2FsbCBnaXRfYnJhbmNoKQ==' | base64 -d | base64)
PATTERNS[finasu_branch]=$(echo 'QCQoS1NVX1ZFUlNJT05fQlJBTkNIKQ==' | base64 -d | base64)
PATTERNS[marker]=$(echo 'QmFrYVNV' | base64 -d | base64)
PATTERNS[finasu_marker]=$(echo 'S1NVX1ZFUlNJT05fQlJBTkNI' | base64 -d | base64)
BRAND=$(echo 'TXVMaW5n' | base64 -d)

apply_bakasu() {
  local kbuild="$KSU_DIR/kernel/Kbuild"
  [ -f "$kbuild" ] || return 0

  # ⚠ 不能只按 `REPO_NAME := BakaSU` 精确匹配。
  #   实测 BakaSU 各 ref 的取值不同：
  #     v4.2.0-rc3（当前钉住的版本）-> REPO_NAME := ReSukiSU
  #     main                        -> REPO_NAME := BakaSU
  #   原来的 sed 在 rc3 上匹配 0 次且不报错，品牌名会静默保留成 ReSukiSU。
  #   这里改为「匹配任意取值的 REPO_NAME 行」，并在改完后断言确实变成了 BRAND。
  local before after
  before=$(grep -cE '^REPO_NAME[[:space:]]*:?=[[:space:]]*' "$kbuild" || true)
  if [ "${before:-0}" -eq 0 ]; then
    echo "  WARN: $kbuild 里找不到 REPO_NAME 行，跳过品牌名改写（上游结构可能已变化）"
    return 0
  fi

  sed -i -E "s|^REPO_NAME[[:space:]]*:?=[[:space:]]*.*\$|REPO_NAME := ${BRAND}|" "$kbuild"
  after=$(grep -cE "^REPO_NAME[[:space:]]*:?=[[:space:]]*${BRAND}\$" "$kbuild" || true)
  if [ "${after:-0}" -eq 0 ]; then
    echo "  WARN: REPO_NAME 改写后未变成 ${BRAND}，请检查 $kbuild"
  else
    echo "  OK: REPO_NAME -> ${BRAND} ($(grep -nE '^REPO_NAME' "$kbuild" | head -1))"
  fi
  git -C "$KSU_DIR" update-index --skip-worktree kernel/Kbuild 2>/dev/null || true
}

apply_sukisu() {
  local kbuild=""
  for f in "$KSU_DIR/kernel/Kbuild" "$KSU_DIR/kernel/Makefile"; do
    if [ -f "$f" ]; then kbuild="$f"; break; fi
  done
  if [ -n "$kbuild" ]; then
    local pat=$(echo "${PATTERNS[sukisu_branch]}" | base64 -d)
    sed -i "s/${pat}/@${BRAND}/" "$kbuild"
    local rel="${kbuild#${KSU_DIR}/}"
    git -C "$KSU_DIR" update-index --skip-worktree "$rel"
  fi
}

apply_finasu() {
  # FinaSU kernel/Kbuild 以 git rev-parse --abbrev-ref HEAD 取分支名拼进
  # KSU_VERSION_FULL（v<tag>-<sha>@<branch>）；CI 检出的是 detached 提交，
  # 管理器「内核完整版本」因此显示 @HEAD。与 SukiSU 一致把使用点固定为
  # @MuLing；须在管理器证书注入（awk 重写 Kbuild）之后调用。
  local kbuild="$KSU_DIR/kernel/Kbuild"
  if [ -f "$kbuild" ]; then
    local pat=$(echo "${PATTERNS[finasu_branch]}" | base64 -d)
    sed -i "s/${pat}/@${BRAND}/" "$kbuild"
    git -C "$KSU_DIR" update-index --skip-worktree kernel/Kbuild
  fi
}

case "$MODE" in
  bakasu)   apply_bakasu ;;
  sukisu)   apply_sukisu ;;
  finasu)   apply_finasu ;;
  auto)
    # 自动检测：根据 KSU 目录内容判断变体
    if [ -f "$KSU_DIR/kernel/Kbuild" ] && grep -q "$(echo "${PATTERNS[marker]}" | base64 -d)" "$KSU_DIR/kernel/Kbuild" 2>/dev/null; then
      apply_bakasu
    elif [ -f "$KSU_DIR/kernel/Kbuild" ] && grep -q "$(echo "${PATTERNS[finasu_marker]}" | base64 -d)" "$KSU_DIR/kernel/Kbuild" 2>/dev/null; then
      apply_finasu
    else
      apply_sukisu
    fi
    ;;
  *) echo "未知模式: $MODE" >&2; exit 1 ;;
esac

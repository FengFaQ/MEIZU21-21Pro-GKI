# 魅族 21 (M2461) 专属 kit 补丁

> 与 21 Pro（M2481）的 `MEIZU 21pro/device/patches/01-m2481-ko-krcrc-and-onemind.patch`
> **不是同一个补丁**，不能互换。两个机型的原厂基线不同，锚点也不同。

## 这个补丁是什么

把「魅族 21 线」目前在构建期由脚本动态注入的全部改动，固化成一份静态 patch：

| 脚本 | 负责内容 |
|---|---|
| `scripts/apply_kit_onemind.py` | OneMind 8 大 vendor hook（声明 / 调用点 / 导出）+ shrink_slab kCFI 保活 + `bad_version` 放行 + `memcg_protect_lru_gen_add_folio` 定义 |
| `scripts/apply_kmi_exports.py` | 厂商模块真正 NEED 的 5 个 KMI 符号导出（见下） |

**等价性已实测**：在一份纯净的 `android14-6.1-2023-10_r1` 树上
1. `git apply` 本补丁 → 干净应用
2. 再跑上面两个脚本 → **全部走「跳过」分支、零注入**
3. `git diff` 结果与本补丁完全一致

即：**本补丁 ≡ 脚本注入**。两条路径产出的内核相同。

## 覆盖的 12 个文件

| 文件 | 改动 |
|---|---|
| `include/trace/hooks/mm.h` | 6 个 OneMind hook 的 `DECLARE_HOOK`（无自建守卫，见下） |
| `include/trace/hooks/vmscan.h` | 2 个 MGLRU hook 的 `DECLARE_HOOK` |
| `drivers/android/vendor_hooks.c` | 8 个 hook 的 `EXPORT_TRACEPOINT_SYMBOL_GPL` + `MZ_DEFINE_VH(android_vh_smartzram_tune_swappiness)` |
| `drivers/base/power/wakeup.c` | `EXPORT_SYMBOL_GPL(pm_system_irq_wakeup)` |
| `include/linux/mm_inline.h` | `memcg_protect_lru_gen_add_folio` 前置声明 + `lru_gen_add_folio()` 内调用点 |
| `kernel/module/version.c` | `bad_version:` 放行（返回 1） |
| `mm/memcontrol.c` | 新增 `mem_cgroup_id_put2()` + 导出 |
| `mm/page_alloc.c` | 3 处 VIP hook 调用点 + 水位比（`low_high_wm_ratio`） |
| `mm/readahead.c` | `ra_tuning_max_page` 调用点（传 `&max_pages`） |
| `mm/util.c` | `mm_vip_kvmalloc` 调用点（声明区之后） |
| `mm/vmscan.c` | `shrink_slab` kCFI 保活 + `sort_folio` hook + `memcg_protect_lru_gen_add_folio` 定义 + `shrink_*`/`reclaim_pages` 导出 |
| `mm/vmstat.c` | `extfrag_for_order` 调用点 |

## 补了哪 5 个厂商 NEED 符号

用 `魅族21/device/module_kmi_manifest.txt` 的 NEED 列表逐条核对得出
（门禁 2 对这些只 WARN，不阻断，所以必须在这里补）：

```
__tracepoint_android_vh_smartzram_tune_swappiness   NEED 0x1523d65f
__tracepoint_mm_vmscan_kswapd_wake                  NEED 0xd5e35f51
mem_cgroup_id_put2                                  NEED 0x72e5c410
pm_system_irq_wakeup                                NEED 0x9becc101
reclaim_pages                                       NEED 0x2d55e4f4
```

缺了它们，对应厂商 `.ko` 加载会 `Unknown symbol` → 卡第一屏。

## 与 21 Pro 补丁的关键差异（**别照抄**）

1. **锚点不同**：21 Pro 的补丁以 `android_vh_adjust_kvmalloc_flags` 为锚点，
   这是**上游 AOSP 不存在**的 hook（21 Pro 自己的前置改动引入的）。
   魅族 21 用纯净 ACK 树，直接 apply 会在 `include/trace/hooks/mm.h` 与
   `mm/util.c` 两处失败（已实测）。
2. **`mm/util.c` 注入方式不同**：21 Pro 在同一行后面挂
   `trace_android_vh_mm_vip_kvmalloc(&kmalloc_flags)`；魅族 21 因为
   没有 `adjust_kvmalloc_flags`，改成插在 `kvmalloc_node()` 声明区末尾
   （`gfp_t kmalloc_flags = flags; void *ret;` 之后），传 `&flags`。
3. **不含 `scsi_execute_cmd_len`**：该符号**不在**魅族 21 的 NEED 列表里
   （21 Pro 需要是因为其 ufs_qcom 依赖）。
4. **不含 `android/abi_gki_protected_exports_aarch64` 删除**：21 Pro 补丁里有
   345 行的该文件删除，魅族 21 线不需要（构建期由工作流另行处理）。

## ⚠ 两条硬性规则（违反会「编译全绿、链接才炸」）

### 1. hook 块**不要**加自建守卫

```c
/* ❌ 错：第二次包含被挡掉，DEFINE_TRACE 永不生成 -> 20 个 undefined symbol */
#ifndef MZ_KIT_P4_MM_H
#define MZ_KIT_P4_MM_H
...
#endif

/* ✅ 对：什么都不加，靠上游 _TRACE_HOOK_MM_H 控制展开次数 */
DECLARE_HOOK(...);
```

原因：`<trace/hooks/vendor_hooks.h>`（`mm.h` 顶部 include）在其
`#ifdef TRACE_HEADER_MULTI_READ` 分支末尾有
`#undef TRACE_HEADER_MULTI_READ`（注释 `prevent additional recursion`）。
所以 `mm.h` **后半段**求值时该宏已不可见，任何自建守卫（含镜像写法
`#if !defined(X) || defined(TRACE_HEADER_MULTI_READ)`）在第二次包含时都为假。

### 2. `EXPORT_TRACEPOINT_SYMBOL_GPL` 必须放在 `CREATE_TRACE_POINTS` 单元

它展开成 `__tracepoint_##name` / `__traceiter_##name` / `__SCK__tp_func_##name`，
要求这些标识符在**本翻译单元**可见。

- 8 个 OneMind hook → 放 `drivers/android/vendor_hooks.c`（该文件有
  `#define CREATE_TRACE_POINTS` 并 include 了 `trace/hooks/{mm,vmscan}.h`）✅
- `mm_vmscan_kswapd_wake` → 必须放 `mm/vmscan.c`（第 70-71 行是
  `#define CREATE_TRACE_POINTS` + `#include <trace/events/vmscan.h>`）。
  **放 `vendor_hooks.c` 会报 3 个 undeclared**（已实测）。

## 验证方法（不需要跑 CI，几秒钟）

```bash
# 1) 语法：git apply --check 应干净通过
git apply --check 01-m2461-kmi-and-onemind.patch

# 2) 幂等：apply 之后再跑两个脚本，应当全部「跳过」
python3 scripts/apply_kit_onemind.py --kernel-root <tree>
python3 scripts/apply_kmi_exports.py --kernel-root <tree>

# 3) 等价：上面两步后 git diff 应当为空
git -C <tree> diff --stat
```

## 维护

- 上游 ACK 升级或脚本注入逻辑变更后，**重新生成**本补丁（而不是手工改）：
  在纯净树上跑完两个脚本，`git diff > 本文件`。
- 生成后必须重跑上面的「验证方法」三条。

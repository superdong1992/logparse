# 重启周期切分算法设计

> **状态**: 已确认，待实现

## 背景

原有算法仅依赖 indicator 进程的 PID 变化时间戳作为切分点，存在两个问题：

1. indicator 不是最早拉起的进程，导致更早启动的进程日志被划到上一个生命周期
2. 部分进程最早记录在 journal（无 PID），诊断日志中找不到这些早期条目

## 核心约束

- 板卡重启时，所有进程 PID 一定变化（一次重启 PID 必变）
- 支持独立重启的进程不多，且 indicator 一定是非独立重启进程
- journal 日志中不一定有 PID，只能通过序号（No）判断生命周期边界
- 日志可能丢失（No[1] 不一定存在）
- 切分点不能拆断任何进程的同 PID 连续段

## 算法

### 术语

- **indicator**：板卡重启标识进程（配置项 `board_restart_indicator`），PID 变化即表示板卡重启
- **白名单进程**（`board_restart_whitelist`）：不重名、不支持独立重启的进程，用于计算安全切分点

### Step 1：检测板卡重启

indicator 进程 PID 变化 → 判定为板卡重启。

### Step 2：确定安全切分点

只看白名单内进程，收集它们的旧 PID 和新 PID 条目：

- `old_pid_end` = 白名单内所有进程旧 PID 最后一条时间戳的最大值
- `new_pid_start` = 白名单内所有进程新 PID 第一条时间戳的最小值
- 安全区间 = [old_pid_end, new_pid_start]
- 初始切分点 = old_pid_end（保证不拆断旧 PID 段）

如果 old_pid_end > new_pid_start（重叠），取 old_pid_end（优先保证同 PID 不被拆断）。

### Step 3：Journal 序号前移

对白名单内每个进程：

1. 从诊断日志获取该进程旧 PID 的最后一个 No（如 No[500]）
2. 在该进程的全部条目（诊断 + journal）中，找序号从旧 No 附近跳到小号的第一条
3. 该条时间戳 = 该进程的候选前移点
4. 取所有进程候选前移点中的最早值 = `journal_earliest`

前移约束：`journal_earliest` 不能小于 `old_pid_end`（不能破坏安全约束）。

最终切分点 = max(journal_earliest, old_pid_end)

### Step 4：非白名单进程处理

不在白名单里的进程（可能重名或支持独立重启）不参与切分点计算，按最终切分点被动分配到对应周期。

## 配置

```yaml
mechanism_modules:
  module1:
    board_restart_indicator: "dhcp"          # 板卡重启触发信号
    board_restart_whitelist:                 # 参与切分计算的可靠进程
      - "svc_a"
      - "svc_b"
      - "svc_c"
```

## 示例

```
进程 A (indicator): PID=100, 诊断 00:00~06:05, journal 00:01~06:04
                    PID=200, 诊断 06:15~12:00, journal 06:08~11:55

进程 B (白名单):   PID=300, 诊断 00:00~06:12
                    PID=400, 诊断 06:20~12:00

进程 C (白名单):   PID=500, 诊断 00:00~06:03, journal 00:02~06:02
                    PID=600, 诊断 06:25~12:00, journal 06:02~11:50

进程 D (非白名单): PID=700, 诊断 00:00~06:10
                    PID=800, 诊断 06:18~12:00
```

**Step 1**: A(indicator) PID 100→200 → 板卡重启

**Step 2**: 白名单进程 B、C
- old_pid_end = max(B:06:12, C:06:03) = 06:12
- new_pid_start = min(B:06:20, C:06:02) = 06:02
- 安全区间 [06:12, 06:20]，初始切分点 = 06:12

**Step 3**: Journal 前移
- 进程 B：旧 PID=300 最后 No 从诊断获取，journal 中无序号跳变（假设只有诊断日志）
- 进程 C：旧 PID=500 最后 No=600，journal 中 No 从高跳到低 → No[1] at 06:02 → 候选 06:02
- journal_earliest = 06:02
- 最终切分点 = max(06:02, 06:12) = 06:12（安全约束优先）

**Step 4**: 进程 D 按切分点 06:12 被动分配

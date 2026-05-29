# 生命周期切分 DFX 日志解读指南

本文说明生命周期切分报错时，终端日志应该怎么看，以及什么时候需要展开 `result.json` 中的完整证据。

## 快速入口

`parse` 命令默认不再逐条打印生命周期切分 raw error，而是输出数量汇总：

```bash
生命周期切分诊断: ERROR=1 WARNING=1 INFO=1
定位: python cli.py mech-lifecycles <task_id> -s <slot_id> -m <module_name> --show-boundaries
```

拿到 `task_id` 后，优先执行：

```bash
python cli.py mech-lifecycles <task_id> -s <slot_id> -m <module_name> --show-boundaries
```

默认输出是 compact 视图，只保留“主问题对象 + 关键证据 + 一条 hint”。如果需要查看完整 `protected_boundaries`、`evidence` 和全部 hint，再执行：

```bash
python cli.py mech-lifecycles <task_id> -s <slot_id> -m <module_name> --show-boundaries --boundary-detail full
```

`result.json` 仍然保留完整结构化证据，终端只是默认收敛展示。

## 严重级别

- `ERROR`：边界不可靠，需要优先定位。典型事件是 `unsafe_cycle_split` 的 `action=kept` 或 `restart_boundary_overlap`。
- `WARNING`：发现风险但解析器已尝试自动修正。典型事件是 `same_pid_adjusted`、`same_pid_adjusted_backward`。
- `INFO`：辅助诊断信息。默认 compact 不展开详情，只显示数量提示；需要时用 `--boundary-detail full`。

## 默认视图怎么读

### restart_boundary_overlap

表示可靠进程的新 PID 第一条日志时间不晚于旧 PID 最后一条日志时间。默认只展示真正造成 overlap 的端点进程：

```text
[ERROR] restart_boundary_overlap reason=new_pid_start_le_old_pid_end scope=board split=...
    overlap new_start=2026-01-03T00:00:09+08:00 <= old_end=2026-01-03T00:00:10+08:00
    old-side svc_a-300@board role=whitelist old_end=... raw=...
    new-side dhcp-200@board role=indicator new_start=... raw=...
    hint python cli.py mech-logs ...
```

定位时先看 `overlap` 行确认重叠窗口，再看 `old-side` 和 `new-side`。如果两边是同一个进程，默认只打印一行 `boundary old_pid->new_pid`。无关 protected 进程不会在默认视图出现，避免干扰判断。

需要确认所有参与判断的白名单/indicator 进程时，用 `--boundary-detail full`。

### unsafe_cycle_split / same_pid_*

表示切点会拆断普通同 PID 进程，或解析器为避免拆断而调整了切点。默认展示第一个冲突进程、切点前后日志，以及阻止继续移动切点的 protected blocker：

```text
[ERROR] unsafe_cycle_split action=kept reason=no_safe_gap_candidate scope=board split=...
    conflict other-500@board before=... after=...
    before diagnostic|slot_1/diag.log seq=0 raw=...
    after diagnostic|slot_1/diag.log seq=1 raw=...
    blocker dhcp@board role=indicator old_end=... new_start=...
    hint python cli.py mech-logs ...
```

重点判断 `conflict` 进程是否确实跨越切点；再看 `blocker` 为什么切点不能继续移动。

### protected_forced_split

表示 indicator 或白名单进程在已分段窗口内仍发生 PID 变化，解析器强制补切。默认展示发生 PID 变化的 protected 进程及 old/new 证据。

如果该事件频繁出现，优先检查 `board_restart_indicator` 和 `board_restart_whitelist` 是否包含了会独立重启的进程。

### suspect_pid_bounce

表示 indicator PID 出现 `A -> B -> A` 回跳。默认展示三条参与判断的证据。重点确认这些日志是否来自同一次板卡生命周期，以及是否存在 PID 复用或日志乱序。

### scoped_cpu_split / suspect_over_split

这两类默认按 `INFO` 收敛展示，只在汇总中体现数量。它们通常用于解释 CPU 局部切分或疑似过切，不代表板卡生命周期一定失败。

需要查看上下文 evidence 时使用：

```bash
python cli.py mech-lifecycles <task_id> -s <slot_id> -m <module_name> --show-boundaries --boundary-detail full
```

## full 视图字段

`--boundary-detail full` 会展开完整结构化证据：

- `protected`：参与边界判断的 indicator/白名单进程，包含旧 PID 集合、新 PID、旧 PID 结束时间、新 PID 开始时间。
- `conflict`：被切点拆断的普通进程及 before/after 时间。
- `evidence`：原始日志定位信息，包含 `source`、`source_file`、`sequence`、`raw_excerpt`。
- `hint`：建议执行的下一步命令；full 视图会打印全部 hint。

`raw_excerpt` 只用于肉眼确认，完整日志以 `mech_modules/<module>/<slot>/<cycle>/.../*.log` 落盘结果为准。

## 建议排查顺序

1. 先看 `parse` 汇总，确认 `ERROR/WARNING/INFO` 数量。
2. 用提示命令进入 `mech-lifecycles --show-boundaries` compact 视图。
3. 优先处理 `[ERROR]`，看主问题对象和第一条 hint。
4. 对 `restart_boundary_overlap`，只盯 `old-side/new-side` 两个端点进程。
5. 对 `unsafe_cycle_split` 和 `same_pid_*`，先盯 `conflict`，再看 `blocker`。
6. compact 信息不够时，再切到 `--boundary-detail full` 查看完整证据。

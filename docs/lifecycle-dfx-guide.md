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
- `WARNING`：发现风险但解析器已尝试自动修正。典型事件是 `same_pid_adjusted`、`same_pid_adjusted_backward`、`protected_forced_split`、`suspect_pid_bounce`。
- `INFO`：辅助诊断信息。默认 compact 只显示每条 INFO 的类型、scope、split 和一条上下文定位行，完整 raw evidence 需要用 `--boundary-detail full`。

## 默认视图怎么读

### restart_boundary_overlap

表示可靠进程的新 PID 第一条日志时间不晚于旧 PID 最后一条日志时间。默认只展示真正造成 overlap 的端点进程：

```text
[ERROR] restart_boundary_overlap reason=new_pid_start_le_old_pid_end scope=board split=...
    overlap new_start=2026-01-03T00:00:09+08:00 <= old_end=2026-01-03T00:00:10+08:00
    conflict-pair svc_a-300@board old_end=... overlaps dhcp-200@board new_start=...
    old-side svc_a-300@board role=whitelist old_end=... raw=...
    new-side dhcp-200@board role=indicator new_start=... raw=...
    hint python cli.py mech-logs ...
```

先看 `conflict-pair`：左侧是旧 PID 最后一条日志所在进程，右侧是新 PID 第一条日志所在进程，这两个端点发生了时间重叠。再看 `old-side` 和 `new-side` 的原始摘要确认证据。无关 protected 进程不会在默认视图出现。

`restart_boundary_overlap` 的 protected 证据只取相邻两次 indicator PID 变化之间的窗口；如果某个白名单进程在下一次启动后才重新出现，它不会被当成本次 overlap 的冲突证据。

如果 compact 里显示的是同一个进程的 `boundary proc@... old_pid->new_pid`，说明 overlap 来自这个 protected 进程自己的 PID 边界。若两条日志肉眼看属于同一个生命周期，优先检查该进程是否在同一板卡生命周期内发生了独立 PID 变化，或同名多实例被配置进了 `board_restart_whitelist`。

### unsafe_cycle_split / same_pid_*

表示切点会拆断普通同 PID 进程，或解析器为避免拆断而调整了切点。默认展示第一个冲突进程、切点前后日志，以及阻止继续移动切点的 protected blocker：

```text
[ERROR] unsafe_cycle_split action=kept reason=no_safe_gap_candidate scope=board split=...
    conflict other-500@board spans split=... before=... after=...
    before diagnostic|slot_1/diag.log seq=0 raw=...
    after diagnostic|slot_1/diag.log seq=1 raw=...
    blocked-by dhcp@board role=indicator safe_gap=(old_end, new_start]
    hint python cli.py mech-logs ...
```

`conflict ... spans split=...` 的意思是：这个普通进程同一个 PID 的日志横跨了切点。`blocked-by` 的意思是：为了保护这个 indicator/白名单进程的新旧 PID 边界，切点不能继续随意移动。

如果结构化证据缺失，compact 不会伪造 `-@board before=- after=-` 这类假定位，而会提示 `evidence unavailable`。此时应切到 `--boundary-detail full` 查看原始结构或重新解析生成新的 `result.json`。

### protected_forced_split

表示 indicator 或白名单进程在已分段窗口内仍发生 PID 变化，解析器强制补切：

```text
[WARNING] protected_forced_split reason=protected_pid_change scope=board split=...
    pid-change svc_a@board role=whitelist 300 -> 400 split=...
    old diagnostic|slot_1/svc_a.log seq=0 raw=...
    new diagnostic|slot_1/svc_a.log seq=0 raw=...
```

重点看 `pid-change`，它告诉你哪个 protected 进程从哪个 PID 变到哪个 PID。如果该事件频繁出现，优先检查 `board_restart_indicator` 和 `board_restart_whitelist` 是否包含会独立重启的进程。

### suspect_pid_bounce

表示 indicator PID 出现 `A -> B -> A` 回跳：

```text
[WARNING] suspect_pid_bounce reason=indicator_pid_bounce scope=board split=...
    pid-bounce dhcp@board 100 -> 200 -> 100
    pid_bounce_1 diagnostic|slot_1/dhcp.log seq=0 raw=...
    pid_bounce_2 diagnostic|slot_1/dhcp.log seq=0 raw=...
    pid_bounce_3 diagnostic|slot_1/dhcp.log seq=0 raw=...
```

重点确认这三条日志是否来自同一次板卡生命周期，以及是否存在 PID 复用或日志乱序。

### scoped_cpu_split / suspect_over_split

这两类默认按 `INFO` 收敛展示，不展开 raw evidence，但会给出每条事件的 scope/split 和一条上下文定位行，最后再给类型分布：

```text
[INFO] scoped_cpu_split reason=cpu_local_split scope=cpu:1 split=...
    context dhcp-10@1 role=context_before time=...
[INFO] suspect_over_split reason=protected_merge_has_no_pid_conflict scope=board split=...
    context dhcp-100@board role=over_split_left time=...
INFO 诊断 2 个: scoped_cpu_split=1 suspect_over_split=1，使用 --boundary-detail full 查看
```

它们通常用于解释 CPU 局部切分或疑似过切，不代表板卡生命周期一定失败。需要查看上下文 evidence 时再使用 `--boundary-detail full`。

## full 视图字段

`--boundary-detail full` 会先保留 compact 的关键定位行，再展开完整结构化证据：

- `protected`：参与边界判断的 indicator/白名单进程，包含旧 PID 集合、新 PID、旧 PID 结束时间、新 PID 开始时间。
- `conflict`：被切点拆断的普通进程及 before/after 时间。
- `evidence`：原始日志定位信息，包含 `source`、`source_file`、`sequence`、`raw_excerpt`。
- `hint`：建议执行的下一步命令；full 视图会打印全部 hint。

`raw_excerpt` 只用于肉眼确认，完整日志以 `mech_modules/<module>/<slot>/<cycle>/.../*.log` 落盘结果为准。

## 建议排查顺序

1. 先看 `parse` 汇总，确认 `ERROR/WARNING/INFO` 数量。
2. 用提示命令进入 `mech-lifecycles --show-boundaries` compact 视图。
3. 优先处理 `[ERROR]`，看主问题对象和第一条 hint。
4. 对 `restart_boundary_overlap`，先看 `conflict-pair`。
5. 对 `unsafe_cycle_split` 和 `same_pid_*`，先看 `conflict ... spans split`，再看 `blocked-by`。
6. 对 `protected_forced_split`，先看 `pid-change`。
7. compact 信息不够时，再切到 `--boundary-detail full` 查看完整证据。

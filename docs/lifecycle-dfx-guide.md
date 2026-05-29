# 生命周期切分 DFX 日志解读指南

本文说明机制模块生命周期切分报错和诊断日志的含义，以及如何从输出快速定位原始日志证据。

## 快速入口

解析完成后优先执行：

```bash
python cli.py mech-lifecycles <task_id> -s <slot_id> -m <module_name> --show-boundaries
```

如果看到 `生命周期可靠性: false`，说明该 slot 至少存在一个不可自动消解的生命周期边界问题。继续查看 `[ERROR]` 级别事件和 `hint` 中给出的 `mech-logs` 命令。

## 严重级别

- `ERROR`：边界不可完全信任。典型场景是 `unsafe_cycle_split kept` 或 `restart_boundary_overlap`，解析器为了保护 indicator/白名单进程重启边界，保留了一个会切断普通同 PID 进程的切点，或可靠进程的新旧 PID 时间重叠。
- `WARNING`：解析器发现风险但已经自动修正。典型场景是 `same_pid_adjusted` 或 `same_pid_adjusted_backward`。
- `INFO`：辅助诊断。典型场景是 CPU 子卡局部切分、冗余切点裁剪等。

## 常见事件

### unsafe_cycle_split

表示最终切点仍会拆断某个 `(slot, process_name, pid, cpu_id)` 连续段。

重点字段：

- `action=kept`：没有找到安全替代切点，通常会导致 `lifecycle_reliable=false`。
- `split`：原始切点。
- `adjusted`：尝试修正后的切点；如果最终保留原切点，日志里仍会记录曾尝试移动到哪里。
- `conflict`：被切断的普通进程和前后时间。
- `protected`：阻止继续移动切点的 indicator/白名单进程 PID 边界。

定位方法：

1. 看 `conflict <proc>-<pid>@<scope> before=... after=...`，确认被切断的是哪个进程。
2. 看紧随其后的 `evidence`，里面有 `source|source_file|seq|raw`。
3. 执行 `hint` 里的 `mech-logs` 命令，把 `<board_cycle>` 或 `<cpu_cycle>` 替换为 `mech-lifecycles` 输出的周期目录。

### restart_boundary_overlap

表示可靠进程的新 PID 第一条日志时间不晚于旧 PID 最后一条日志时间。

重点字段：

- `old_pid_end`：可靠进程旧 PID 最后一条日志时间。
- `new_pid_start`：可靠进程新 PID 第一条日志时间。
- `split`：解析器采用的兜底切点，通常是 `old_pid_end + 1us`。

这类问题通常意味着日志时间戳乱序、不同来源时间不一致，或白名单进程并不适合作为可靠边界。

### same_pid_adjusted / same_pid_adjusted_backward

表示原始切点会拆断普通同 PID 进程，但解析器已经找到更安全的切点。

- `adjusted` 是最终使用的切点。
- `conflict` 是触发修正的普通进程。
- 这类事件默认是 `WARNING`，通常不代表解析失败，但建议确认修正后的周期是否符合业务预期。

### protected_forced_split

表示在一个已分段窗口内，indicator 或白名单进程仍出现 PID 变化，因此解析器强制补充切点。

这通常说明原始 indicator 切点不足以覆盖所有可靠进程，或白名单进程重启信号比 indicator 更完整。

## evidence 字段

`evidence` 是定位原始日志的最小证据集，每条包含：

- `role`：证据角色，例如 `conflict_before`、`conflict_after`、`protected_old`、`protected_new`。
- `timestamp`：该条日志时间。
- `source`：`diagnostic` 或 `journal`。
- `source_file`：来源文件。
- `sequence`：解析到的 `No[n]`，没有序号时为 `0`。
- `raw_excerpt`：原始日志摘要，最多保留一小段用于肉眼确认。

`raw_excerpt` 只用于定位，不替代完整日志。完整内容以 `mech_modules/<module>/<slot>/<cycle>/.../*.log` 落盘结果为准。

## 建议排查顺序

1. 先看 `severity`。优先处理 `ERROR`。
2. 看 `kind/action/reason`。判断是不可解冲突、重叠、还是自动修正。
3. 看 `conflict`。确认普通进程是否确实跨越切点。
4. 看 `protected`。确认 indicator/白名单进程的新旧 PID 边界是否合理。
5. 看 `evidence`。用 `source_file + raw_excerpt` 回到原始日志。
6. 执行 `hint` 命令。查看按周期落盘后的进程日志。
7. 如果反复出现 `restart_boundary_overlap`，优先检查白名单配置和日志时间戳质量。

## 配置建议

`board_restart_indicator` 和 `board_restart_whitelist` 应只包含不重名、不支持独立重启、能代表板卡重启边界的进程。

如果某白名单进程可能独立重启，或不同来源日志时间经常交错，它会降低切分可靠性。此时应从白名单移除，避免把普通进程行为误当成板卡生命周期边界。

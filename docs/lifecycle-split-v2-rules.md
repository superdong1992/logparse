# 生命周期切分 v2 规则说明

本文档归档 2026-05-31 讨论确认的新生命周期切分规则。v2 是全新模块设计，不在旧 `indicator / whitelist / protected / safe_gap / protected_forced_split / same_pid_adjusted` 逻辑上继续打补丁。

现有 `docs/lifecycle-split-logic.md` 仍描述旧实现，不作为 v2 规则依据。

## 目标

v2 生命周期切分采用：

```text
正向边界证据约束求解
+ same PID 一致性校验
+ board/CPU 分层作用域
+ 中文 DFX 可解释输出
```

输入 entry 结构保持现状。新模块内部重新实现生命周期判断，输出边界、scope、周期、证据解释和问题诊断。

## 进程分类

所有生命周期相关进程名先通过 `process_name_mapping` 归一化为 canonical name。分类判断只使用 canonical name。

`process_universe` 定义为：

```text
v2 输入 entries 中，归一化后且 PID 非空的 observed canonical process 集合
```

无 PID entry 不参与 same PID 一致性校验。若某条 entry 被用于正向边界证据但缺少必要 PID，则属于 `invalid_lifecycle_evidence`。

进程分类合同：

```text
process_universe
├─ multi_instance_processes
│  └─ 同名多实例进程
└─ unique processes
   ├─ reliable_processes
   │  └─ 可靠进程，也叫生命周期锚点进程
   └─ ordinary unique processes
      └─ 普通唯一进程
```

硬规则：

- `reliable_processes` 必须是唯一进程子集。
- `reliable_processes` 与 `multi_instance_processes` 必须互斥。
- `reliable_processes` 不再区分 board/cpu；边界 scope 由日志实际 scope 决定。
- 同一个 canonical process 同时出现在 `reliable_processes` 和 `multi_instance_processes` 时，配置非法。
- 普通唯一进程是 `process_universe - reliable_processes - multi_instance_processes`。
- 可靠进程也参与 same PID 一致性校验。

| 分类 | PID changed 生成边界 | 参与 same PID 一致性校验 | 可多 PID 并存 |
| --- | --- | --- | --- |
| 可靠进程 | 是，按日志实际 scope 生成 board 或 CPU 边界 | 是 | 否 |
| 普通唯一进程 | 否 | 是 | 否 |
| 同名多实例进程 | 否 | 否 | 是 |

### 可靠进程

可靠进程是生命周期绑定进程，配置集合为 `reliable_processes`。单个成员可称 reliable process，但不新增名为 `reliable_process` 的输出字段。

规则：

- 可靠进程不会在同一个生命周期内独立重启。
- 可靠进程不分 board/cpu 配置；无 `cpu_id` 或 `cpu_id=0` 的观测属于 board scope，其他 `cpu_id` 属于对应 CPU scope。
- 如果一次生命周期重启前后该可靠进程两边都拉起，则 PID 必须不同。
- 非相邻生命周期允许 PID 复用，例如 `cycle1 pid=100`、`cycle2 pid=200`、`cycle3 pid=100`。
- 可靠进程不是必现进程。某个生命周期没出现可靠进程，或某种设备形态上某些可靠进程一直不存在，都不算冲突。

### 普通唯一进程

普通唯一进程可能在生命周期内独立重启，因此它的 PID changed 不单独生成生命周期边界。

普通唯一进程仍参与 same PID 一致性校验，因为唯一进程如果跨一次生命周期重启前后都被拉起，PID 必须不同。

### 同名多实例进程

同名多实例进程配置在 `multi_instance_processes` 中。

规则：

- 它们可能在同一 `slot/scope/process` 下同时存在多个 PID。
- 不参与 same PID 一致性校验。
- 不作为可靠进程边界证据。

## 配置语义

新配置放在 `config.yaml` 的 `lifecycle_split` 段：

```yaml
lifecycle_split:
  # 将诊断日志和 journal 日志中的不同进程名统一为 canonical name。
  # 后续 reliable_processes 和 multi_instance_processes 都只写 canonical name。
  process_name_mapping:
    canonical_proc_name:
      - alias_in_diag
      - alias_in_journal

  # 生命周期绑定的可靠进程。
  # 这些进程不是必现列表；缺席不代表冲突。
  # reliable_processes 与 multi_instance_processes 必须互斥。
  # 边界 scope 由日志实际 scope 决定，不在配置里拆 board/cpu。
  reliable_processes:
    - canonical_lifecycle_proc

  # 同名多实例进程。
  # 这些进程不参与 same PID 一致性校验，也不作为可靠边界证据。
  multi_instance_processes:
    - canonical_multi_instance_proc
```

业务事实前提：

```text
board 和 CPU 生命周期重启前后观测间隔至少 30 秒。
```

该前提只用于简化切分模型：

- 不设计 observation fence。
- 不使用 `+1us` 制造切点。
- 不处理同 timestamp 新旧生命周期混杂。
- 不提供重启间隔配置项。
- 不输出重启间隔过短类 issue，也不把 gap 过短包装成用户侧 `invalid_lifecycle_evidence`。

如果实现构造出小于 30 秒的生命周期边界证据，说明证据配对、scope 分组、PID run 压缩或 journal 序号回绕检测存在代码 bug；应在开发/测试阶段作为内部断言失败或 `LifecycleSolverInvariantError` 暴露，不作为正常业务结果继续输出。

## scope key

v2 只允许两类 scope key：

```text
BoardScopeKey = (slot, "board", null)
CpuScopeKey   = (slot, "cpu", cpu_id)
```

规则：

- board scope 的 `cpu_id` 必须为 null。
- CPU scope 的 `cpu_id` 必须非空。
- CPU 正向证据缺少 `cpu_id` 时，记录 `invalid_lifecycle_evidence`。
- CPU scope universe 由该 slot 内可观测 CPU entries 的 `cpu_id` 集合定义。
- 不因为 board boundary 下发而凭空生成没有任何 CPU entry 的 phantom CPU scope。

## 正向边界证据

以下证据生成 `must-have-boundary` 约束：

- board scope 中可靠进程 PID changed。
- CPU scope 中可靠进程 PID changed。
- board journal 序号回绕。
- CPU journal 序号回绕。

统一语义：

```text
old_observed < boundary <= new_observed
candidate_time = new_observed.timestamp
```

说明：

- 正向证据给出的是边界区间，不是真实物理重启瞬间。
- 候选切点是离散 timestamp，来自该证据的 `new_observed.timestamp`。
- 每条有效正向证据必须自带至少一个区间内候选点。
- 普通唯一进程 PID changed 不能生成生命周期边界，因为它可能只是进程在同一生命周期内独立重启。
- 缺 timestamp、缺必要 PID、CPU evidence 缺 `cpu_id`、时间顺序非法、journal 序号缺失导致无法判断回绕，属于 `invalid_lifecycle_evidence` 这类输入/解析异常。
- gap 小于 30 秒不属于 `invalid_lifecycle_evidence`，应视为实现构造了不可能的生命周期证据。

### 正向约束生成算法

`build_positive_constraints(scope_entries)` 必须按以下合同实现：

1. 对 entry 做 canonical process name normalization。
2. 按 `BoardScopeKey` / `CpuScopeKey` 分组。
3. 对每个 scope 内的可靠进程，按 timestamp 升序提取 PID-bearing 观测。
4. 将连续相同 PID 的观测压缩为 PID run：

```text
run = (process, pid, first_observed, last_observed)
```

5. 相邻 PID run 若 PID 不同，生成可靠进程 PID changed 约束：

```text
old_observed = previous_run.last_observed
new_observed = next_run.first_observed
old_observed < boundary <= new_observed
candidate_time = new_observed.timestamp
```

6. `A -> B -> A` 生成两条相邻 transition 约束，不把非相邻同 PID 合并成一个大区间。
7. journal 序号回绕按 scope key 检测：同一 scope 内 journal 序号从大变小或重置时，生成 journal 序号回绕约束。
8. journal 序号回绕的 `old_observed` 是回绕前最后一条 journal 观测，`new_observed` 是回绕后第一条 journal 观测。

## board / CPU 作用域

### origin boundary

求解器真正采用的原始边界称为 origin boundary。

board boundary 只保留一个原始边界对象：

```text
origin_scope = board
slot = X
cpu_id = null
timestamp = T
```

不为每个 CPU 复制一份新的原始 boundary。

### effective boundary

某个 scope 真正用于日志分 cycle 和 same PID 一致性校验的边界集合称为 effective boundaries。

board scope：

```text
effective_boundaries(board)
= board origin boundaries
```

CPU scope：

```text
effective_boundaries(cpu_i)
= inherited board boundaries + cpu_i local origin boundaries
```

board boundary 对 CPU 来说是 inherited boundary：

```text
origin_scope = board
effective_scope = cpu
inherited = true
```

它不是 CPU 自己产生的新 boundary，但会参与 CPU 日志切分和 same PID 一致性校验。

### fixed effective boundary 与 local origin candidate

CPU 求解必须区分：

```text
fixed_effective_boundaries = inherited board boundaries
local_origin_candidates = CPU 本 scope 正向证据产生的候选点
```

规则：

- inherited board boundary 只能作为 fixed effective boundary。
- inherited board boundary 不能进入 CPU-local origin candidate 集合。
- inherited board boundary 不能出现在 CPU-local origin output 中。
- CPU-local solver 不能移动、复制、重新选择 inherited board boundary。
- CPU-local solver 先用 fixed effective boundaries 判断哪些 CPU 正向约束已被覆盖；未覆盖的约束才进入 CPU-local stabbing 求解。

### 分层求解规则

求解顺序：

1. 按 `slot` 求 board-scope origin boundaries。
2. board origin boundaries 下发到所有 CPU scope，成为 CPU 的 inherited/fixed effective boundaries。
3. 按 `slot + cpu_id` 求每个 CPU-local origin boundaries。

作用域规则：

- board boundary 影响整个 slot 下已有 CPU scope。
- CPU-local boundary 只影响本 CPU。
- CPU-local boundary 不反向影响 board，也不影响其他 CPU。
- CPU 正向证据被 inherited board boundary 覆盖时，只标记为 `pre_satisfied_by_inherited=true`，不在此阶段最终挂 support。
- support 归属必须在最终 effective boundaries 确定后统一回填。

## timestamp 切分与 cycle 生成

`boundary_time` 表示新生命周期首次可观测证据点，不表示物理重启瞬间。

日志归属以 effective boundary 序列为准：

- 第一个 boundary 之前的日志归 `cycle 0`。
- 从某个 `boundary_time` 开始，直到下一个 boundary 之前的日志归同一个新 cycle。
- 最后一个 boundary 之后的日志归最后一个 cycle。
- 如果某个 scope 没有 effective boundary，则该 scope 的所有日志归入 `cycle 0`。

工程合同：

- `boundary_time` 是后一 cycle 的起点。
- 下游应使用已分配的 `cycle_index` 或 boundary 序列做归属，不应使用 `start_time <= timestamp <= end_time` 这类闭区间判断。
- `cycle.start_time` / `cycle.end_time` 是观测展示字段，不是日志归属匹配规则。
- `cycle.end_time` 推荐取该 cycle 内最后一条日志 timestamp。
- 如需表示下一周期起点，单独输出 `next_boundary_time`。

effective boundaries 必须按 timestamp 升序去重：

```text
effective_boundaries(scope) = sort_unique_by_timestamp(inherited_boundaries + local_origin_boundaries)
```

同一 scope 内相同 timestamp 的多个 boundary 合并成一个 effective boundary。若 inherited board boundary 与 CPU-local boundary timestamp 相同，保留 inherited board boundary；CPU-local evidence 只在最终 evidence 回填阶段作为 support 或 wide_support 解释，不新增重复 boundary。

## 边界求解

求解器从离散候选点中选择最少 origin boundaries 覆盖待求解的正向约束。

约束形式：

```text
old_observed < boundary <= new_observed
```

local origin candidate 来源：

- 可靠进程新 PID 首次观测 timestamp。
- journal 序号回绕后第一条日志 timestamp。

inherited board boundary 不是 CPU-local candidate；它只作为 CPU scope 的 fixed effective boundary 参与覆盖判断。

第一版不把每条普通日志时间都作为候选点。

可采用一维离散区间 stabbing 贪心：

1. 先用 `fixed_effective_boundaries` 标记已覆盖约束。
2. 对未覆盖约束按右端点排序。
3. 维护最近一次已选 local origin boundary。
4. 如果已选 local origin boundary 覆盖当前约束，则跳过。
5. 否则在当前约束区间内选择最靠右的 local origin candidate。

无候选点的语义：

- 有效正向约束必须自带区间内候选点，通常就是 `new_observed.timestamp`。
- 如果缺候选点来自缺 timestamp、缺必要 PID、时间顺序非法等结构性问题，生成 `invalid_lifecycle_evidence`。
- 如果有效约束在求解时仍找不到候选点，说明实现 invariant 被破坏，应抛 `LifecycleSolverInvariantError` 或等价内部错误，不作为正常生命周期 issue 输出。

## 可靠进程同 lifecycle 多 PID 校验

该校验基于最终 effective boundaries 生成的 cycle index。

步骤：

1. 对每个 scope，用 effective boundaries 给可靠进程日志分配 `cycle_index`。
2. 对每个 key 收集同一 cycle 内出现过的 PID：

```text
(slot, effective_scope, cpu_id, canonical_reliable_process_name, cycle_index)
```

3. 如果同一个 key 内 PID 集合数量大于 1，记录 `reliable_process_multiple_pid_in_cycle`。
4. issue 必须携带 `observed_pids`、`pid_runs`、`cycle_window`、`expected_boundary_intervals`、`covered_boundaries`，用于解释“为什么同一 lifecycle 里会有多个 PID”。
5. 对应 scope 和受影响 cycle 标记 `lifecycle_reliable=false`。

冲突处理：

- 不自动删除边界。
- 不自动新增边界。
- 不移动边界。
- 只记录 error-level issue，并输出中文 DFX。

## same PID 一致性校验

same PID 一致性校验基于最终 effective boundaries 生成的 cycle index，不基于任意两条日志之间直接数 boundary。

步骤：

1. 对每个 scope，用 effective boundaries 给日志分配 `cycle_index`。
2. 对每个唯一进程 key 收集出现过的 cycle：

```text
(slot, effective_scope, cpu_id, canonical_process_name, pid)
```

3. 如果同一个 key 出现在相邻两个 cycle，记录 `same_pid_single_boundary_conflict`。
4. 如果同一个 key 只出现在同一个 cycle，合法。
5. 如果同一个 key 出现在非相邻 cycle，合法，因为允许非相邻生命周期 PID 复用。

示例：

```text
pid=123 出现在 cycle 1
=> 合法

pid=123 出现在 cycle 1 和 cycle 2
=> 冲突

pid=123 出现在 cycle 1 和 cycle 3
=> 合法
```

若同一个 key 出现在多个相邻 cycle 对中，只生成一个 issue，并在 issue 中列出 `conflicting_cycle_pairs`，避免重复刷屏。

`conflicting_cycle_pairs` 中每个 pair 至少包含：

- `scope_key`
- 左右 `cycle_index`
- 中间 effective boundary 的 timestamp 或 id

CPU scope 的 same PID 一致性校验必须使用 CPU effective boundaries：

```text
inherited board boundaries + CPU-local boundaries
```

冲突处理：

- 不自动删除边界。
- 不自动新增边界。
- 不移动边界。
- 记录 `same_pid_single_boundary_conflict`。
- 对应 scope 和受影响 cycle 标记 `lifecycle_reliable=false`。

## 生命周期可靠性

文档中区分两个概念：

- 可靠进程：进程分类，配置集合为 `reliable_processes`。
- `lifecycle_reliable`：生命周期切分结果是否可靠。

不使用裸 `reliable` 表示切分结果可信度。

可靠性粒度必须序列化输出：

- `slot.lifecycle_reliable`
- `scope.lifecycle_reliable`
- `cycle.lifecycle_reliable`

传播规则：

- `reliable_process_multiple_pid_in_cycle` 是 error-level lifecycle issue。
- `same_pid_single_boundary_conflict` 是 error-level lifecycle issue。
- `invalid_lifecycle_evidence` 是 error-level issue。
- error-level issue 会导致其所在 `scope.lifecycle_reliable=false`。
- 只要任一 board/cpu scope 的 `lifecycle_reliable=false`，整个 `slot.lifecycle_reliable=false`。
- 如果某个 cycle 被 error-level issue 影响，则该 `cycle.lifecycle_reliable=false`。

## 证据解释

正向约束求解后，必须用最终 effective boundaries 统一回看每条证据覆盖了几个 boundary：

```text
覆盖 1 个：tight_support
覆盖 2 个及以上：wide_support
```

解释：

- `tight_support` 表示该证据能明确支持一个 boundary。
- `wide_support` 只说明区间内至少发生过一次重启，不能定位具体哪一个 boundary。
- `wide_support` 不能用于合并多个 boundary。

输出要求：

- `boundary.support_evidence` 只放 `tight_support`。
- `result.evidence` 记录所有证据解释，包括 `tight_support` 和 `wide_support`。
- `wide_support` 放在 `result.evidence` 中，不挂到单个 boundary，也不作为 issue。
- CPU evidence 若只覆盖一个 inherited board boundary，则作为 `tight_support` 挂到原 board origin boundary，support 中记录 `evidence_scope=cpu`。
- CPU evidence 若覆盖 inherited board boundary 后又覆盖 CPU-local boundary，或覆盖多个 inherited board boundaries，则为 `wide_support`，只进入 `result.evidence`。

## 输出与中文 DFX

第一版输出：

```python
LifecycleSplitResult:
    scopes: list[LifecycleScopeResult]
    boundaries: list[LifecycleBoundary]
    cycles: list[LifecycleCycle]
    evidence: list[LifecycleEvidence]
    issues: list[LifecycleIssue]
    lifecycle_reliable: bool
```

`LifecycleScopeResult` 至少包含：

- `scope_key`
- `scope`
- `scope_label_zh`
- `slot`
- `cpu_id`
- `origin_boundaries`
- `effective_boundaries`
- `cycle_indices`
- `lifecycle_reliable`

`LifecycleBoundary` 至少包含：

- `origin_scope`
- `origin_scope_label_zh`
- `effective_scopes`
- `slot`
- `cpu_id`
- `timestamp`
- `support_evidence`
- `type`
- `type_label_zh`
- `title_zh`
- `explanation_zh`

`LifecycleCycle` 至少包含：

- `scope`
- `scope_label_zh`
- `slot`
- `cpu_id`
- `cycle_index`
- `start_time`
- `end_time`
- `next_boundary_time`
- `lifecycle_reliable`

`LifecycleEvidence` 至少包含：

- `type`
- `type_label_zh`
- `scope`
- `scope_label_zh`
- `slot`
- `cpu_id`
- `process_name`
- `support_type`
- `support_type_label_zh`
- `covered_boundaries`
- `title_zh`
- `explanation_zh`

`LifecycleIssue` 至少包含：

- `type`
- `type_label_zh`
- `severity`
- `severity_label_zh`
- `scope`
- `scope_label_zh`
- `slot`
- `cpu_id`
- `related_process`
- `related_boundaries`
- `affected_cycles`
- `conflicting_cycle_pairs`
- `observed_pids`
- `cycle_window`
- `pid_runs`
- `expected_boundary_intervals`
- `covered_boundaries`
- `rule_zh`
- `facts_zh`
- `current_result_zh`
- `conflict_reason_zh`
- `impact_zh`
- `action_zh`
- `title_zh`
- `explanation_zh`

中文 DFX 要求：

- 英文枚举字段可以保留给机器读取，但必须同时提供中文 label。
- `explanation_zh` 必须写出证据类型、作用域、支持类型、可靠性状态的中文含义。
- 每个 issue 必须能回答：适用规则、观测事实、当前切分结果、矛盾原因、影响范围、处理结果。
- 用户不需要理解 `origin_scope`、`effective_scope`、`wide_support` 等英文枚举也能判断结果。

### boundary 解释模板

用于解释“为什么采用这个切点”，不写“矛盾原因”。

```text
标题：
采用生命周期边界 <timestamp>

适用规则：
可靠进程 PID 变化 / journal 序号回绕表示对应区间内至少存在一次生命周期边界。

观测事实：
列出证据来源、旧观测、新观测、PID 或 journal 序号变化、观测时间。

候选区间：
列出主要证据区间，例如 old_observed < boundary <= new_observed。

当前切点：
求解器选择 <timestamp> 作为该 scope 的已采用 boundary；该 timestamp 是后一 cycle 的起点，不是物理重启瞬间。

影响范围：
说明这是 board boundary 还是 CPU-local boundary。
如果是 board boundary，说明它会作为继承边界下发到 CPU，不表示 CPU 又产生了一次新切分。
如果是 CPU-local boundary，说明它只影响对应 CPU。

处理结果：
该 boundary 用于生成后续 cycle。
```

### same PID issue 解释模板

```text
标题：
唯一进程 same PID 跨相邻生命周期

适用规则：
唯一进程如果跨一次生命周期重启前后都被拉起，则 PID 必须不同。同名多实例进程除外。

观测事实：
当前切分把同一唯一进程 <process>/<pid> 分到了相邻 cycle <N> 和 <N+1>。

当前切分：
两个相邻 cycle 之间的 effective boundary 是 <timestamp>。

矛盾原因：
按唯一进程规则，若一次生命周期重启前后该进程都出现，PID 应不同；现在 PID 未变化，因此当前切分结果与该规则矛盾。系统不自动判断是少切、多切还是证据误判，只标记不可靠。

影响范围：
说明影响 slot / board / cpu / cycle。

处理结果：
不自动补切、删切、移动边界；对应 scope 和 slot 标记 lifecycle_reliable=false。
```

### invalid evidence issue 解释模板

```text
标题：
生命周期证据结构无效

适用规则：
正向边界证据必须包含可解析 timestamp、必要 PID 或 journal 序号，以及 CPU 证据所需的 cpu_id。

观测事实：
列出缺失或非法字段，以及对应原始日志定位信息。

当前输入状态：
该证据无法构造成 old_observed < boundary <= new_observed 的有效约束。

矛盾原因：
输入/解析结果缺少生命周期切分所需的结构化字段，无法按 v2 规则参与求解。

影响范围：
说明影响 slot / board / cpu / process。

处理结果：
记录 invalid_lifecycle_evidence；对应 scope 和 slot 标记 lifecycle_reliable=false。
```

### config issue 解释模板

```text
标题：
生命周期配置非法

适用规则：
reliable_processes 与 multi_instance_processes 必须互斥，且配置进程名必须使用 canonical name。

观测事实：
列出冲突的 canonical process 和所在配置项。

当前配置状态：
同一个进程被配置成互斥类别。

矛盾原因：
同一个进程不能同时作为可靠进程和同名多实例进程。

影响范围：
说明影响 lifecycle_split 配置解析。

处理结果：
拒绝该配置或记录配置错误，不进入正常生命周期求解。
```

### evidence 解释模板

用于解释证据如何被使用，尤其是 `wide_support`。

```text
标题：
宽区间证据无法定位具体边界

适用规则：
正向证据，如可靠进程 PID 变化或 journal 序号回绕，只表示观测区间内至少存在一次生命周期边界。

观测事实：
证据区间覆盖多个已采用 boundary。

解释：
该证据只能说明这个时间范围内至少发生过一次生命周期重启，不能判断具体支持哪一个 boundary。这是低定位精度证据，不是不可靠 issue。

处理结果：
记录为 wide_support，不挂到单个 boundary，不用于合并 boundary。
```

## 明确不做

- 不兼容旧 `board_restart_indicator / board_restart_whitelist`。
- 不在旧生命周期切分逻辑上增量打补丁。
- 不处理无 timestamp / 无 PID 的生命周期切分场景。
- 不做冲突后的自动补切、删切、移动切点。
- 不提供重启间隔配置项。
- 不把正向约束无法满足作为正常业务 issue。
- 不把 gap 小于 30 秒作为用户侧 DFX 问题输出。
- 不用英文枚举替代中文可读 DFX。

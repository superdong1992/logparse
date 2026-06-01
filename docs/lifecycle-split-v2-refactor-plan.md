# 生命周期切分 v2 重构计划

本文档记录生命周期切分 v2 的实施计划。v2 是完全重构，不在旧 `CycleDetector` 的 `indicator / whitelist / protected / safe_gap / protected_forced_split / same_pid_adjusted` 逻辑上继续修补。

规则依据见 `docs/lifecycle-split-v2-rules.md`。规则文档是权威合同，实施计划必须服从规则文档。

## 目标

实现独立的新生命周期切分模块：

```text
输入：沿用当前 entry 结构
内部：约束求解 + same PID 一致性校验 + board/CPU 分层
输出：scopes / boundaries / cycles / evidence / issues / lifecycle_reliable，并提供中文解释
```

新模块要让使用者能直接看懂：

- 为什么这里切。
- 哪条规则支持切分。
- 哪条规则和当前切分矛盾。
- 为什么只报不可靠而不自动修复。

## 实现边界

需要做：

- 新建或重写生命周期切分核心模块，建议命名为 `backend/parsing/lifecycle_splitter.py`。
- `backend/plugins/mechanisms/module1.py` 改为调用新模块。
- `config.yaml` 改成新的 `lifecycle_split` 配置，并补充中文注释和示例。
- 测试按 v2 规则重写。

不做：

- 不兼容旧 `board_restart_indicator / board_restart_whitelist`。
- 不把新规则塞回旧 `CycleDetector` 分支里打补丁。
- 不保留旧 `protected_forced_split`、`same_pid_adjusted`、`safe_gap` 输出语义。
- 不处理无 timestamp / 无 PID 的生命周期切分场景。

## 阶段 1：数据结构与配置

新增 v2 内部数据结构：

- `LifecycleSplitConfig`
- `LifecycleSplitResult`
- `LifecycleScopeResult`
- `LifecycleBoundary`
- `LifecycleCycle`
- `LifecycleEvidence`
- `LifecycleIssue`
- `PositiveBoundaryConstraint`
- `BoundaryCandidate`

输出结构：

```python
LifecycleSplitResult:
    scopes: list[LifecycleScopeResult]
    boundaries: list[LifecycleBoundary]
    cycles: list[LifecycleCycle]
    evidence: list[LifecycleEvidence]
    issues: list[LifecycleIssue]
    lifecycle_reliable: bool
```

配置字段：

```yaml
lifecycle_split:
  process_name_mapping:
    canonical_proc_name:
      - alias_in_diag
      - alias_in_journal

  reliable_processes:
    - canonical_lifecycle_proc

  multi_instance_processes:
    - canonical_multi_instance_proc
```

实现要求：

- 所有 entry 进入求解前先做 process name canonical normalization。
- 后续可靠进程、多实例进程判断都只使用 canonical name。
- 当前实现中 `reliable_processes` 是统一列表；旧 `reliable_processes.board` / `reliable_processes.cpu` 兼容输入会合并为统一列表。
- `reliable_processes` 与 `multi_instance_processes` 必须互斥；冲突配置直接判非法。
- 普通唯一进程由 `process_universe - reliable_processes - multi_instance_processes` 自动推导。
- `process_universe` 只包含 v2 输入 entries 中 canonical 后且 PID 非空的 observed process。
- 30 秒最小重启间隔是业务事实前提，不是配置项，不做用户侧 gap 过短 issue。

## 阶段 2：scope key 与进程分类

scope key：

```text
BoardScopeKey = (slot, "board", null)
CpuScopeKey   = (slot, "cpu", cpu_id)
```

实现要求：

- CPU scope 的 `cpu_id` 必须非空。
- CPU evidence 缺 `cpu_id` 记录 `invalid_lifecycle_evidence`。
- CPU scope universe 由该 slot 内可观测 CPU entries 的 `cpu_id` 集合定义。
- 不因为 board boundary 下发而生成 phantom CPU scope。

进程分类：

| 分类 | PID changed 生成边界 | 参与 same PID 一致性校验 | 可多 PID 并存 |
| --- | --- | --- | --- |
| 可靠进程 | 是 | 是 | 否 |
| 普通唯一进程 | 否 | 是 | 否 |
| 同名多实例进程 | 否 | 否 | 是 |

分类规则：

- 可靠进程是唯一进程子集。
- 同名多实例进程不参与硬性生命周期约束。
- 普通唯一进程可能在生命周期内独立重启，因此它的 PID changed 不生成边界。
- 所有唯一进程，包括可靠进程和普通唯一进程，都参与 same PID 一致性校验。

## 阶段 3：正向约束生成

按 scope 生成正向边界约束：

- board 可靠进程 PID changed。
- CPU 可靠进程 PID changed。
- board journal 序号回绕。
- CPU journal 序号回绕。

每条正向证据生成：

```text
old_observed < boundary <= new_observed
candidate_time = new_observed.timestamp
```

实现伪代码合同：

1. canonicalize entries。
2. 按 `BoardScopeKey` / `CpuScopeKey` 分组。
3. 对每个 scope 内的可靠进程，按 timestamp 升序提取 PID-bearing 观测。
4. 将连续相同 PID 的观测压缩为 PID run。
5. 相邻 PID run 若 PID 不同，生成 PID changed 约束。
6. `A -> B -> A` 生成两条相邻 transition 约束。
7. journal 序号回绕按同一 scope 内序号从大变小或重置检测。
8. journal 序号回绕以回绕前最后一条 journal 观测和回绕后第一条 journal 观测构造约束。

普通唯一进程 PID changed 不生成正向边界约束。

结构性无效证据记录为 `invalid_lifecycle_evidence`，包括被用于正向边界求解的证据缺 timestamp、缺必要 PID、CPU evidence 缺 `cpu_id` 或时间顺序非法。journal 缺 PID 或缺 `No[]` 序号是正常日志形态，不属于该 issue。gap 小于 30 秒不是该 issue；若出现，视为内部 invariant 失败。

## 阶段 4：board / CPU 分层求解

先求 origin boundaries：

1. 按 `slot` 求 board-scope origin boundaries。
2. 将 board origin boundaries 下发到该 slot 下已有 CPU scope，形成 inherited/fixed effective boundaries。
3. 按 `slot + cpu_id` 求 CPU-local origin boundaries。

CPU 求解必须区分：

```text
fixed_effective_boundaries = inherited board boundaries
local_origin_candidates = CPU 本 scope 正向证据产生的候选点
```

CPU 证据处理：

- 如果 CPU 正向证据区间被 fixed inherited board boundary 覆盖，标记 `pre_satisfied_by_inherited=true`，不进入 CPU-local 求解。
- 该标记不是最终 support；最终 support / wide_support 在 evidence 回填阶段统一计算。
- 未被 fixed boundary 覆盖的 CPU 约束才进入 CPU-local stabbing 求解。

作用域限制：

- board boundary 只有一个 `origin_scope=board` 的原始对象。
- CPU 视角下的 board boundary 是 inherited/effective boundary，不复制为 CPU origin boundary。
- CPU-local boundary 只影响本 CPU。
- CPU-local boundary 不影响 board 或其他 CPU。
- CPU-local solver 不能移动、复制、重新选择 inherited board boundary。
- inherited board boundary 不能进入 CPU-local candidate 集合，也不能出现在 CPU-local origin output 中。

## 阶段 5：离散区间求解

使用一维离散区间 stabbing 贪心：

1. 先用 `fixed_effective_boundaries` 标记已覆盖约束。
2. 对未覆盖约束按右端点排序。
3. 维护最近一次已选 local origin boundary。
4. 如果最近 local origin boundary 已覆盖当前约束，则当前约束满足。
5. 否则在当前约束区间内选择最靠右的 local origin candidate。

local origin candidate 来源：

- 可靠进程新 PID 首次观测 timestamp。
- journal 序号回绕后第一条日志 timestamp。

inherited board boundary 不是 CPU-local candidate；它只作为 fixed effective boundary 参与覆盖判断。

求解结果是 origin boundaries，用于生成各 scope 的 effective boundaries。

无候选点处理：

- 有效正向约束必须自带区间内候选点，通常就是 `new_observed.timestamp`。
- 如果缺候选点来自结构性无效证据，记录 `invalid_lifecycle_evidence`。
- 如果有效约束在求解时仍找不到候选点，抛 `LifecycleSolverInvariantError` 或等价内部错误，不作为正常生命周期 issue 输出。

## 阶段 6：cycle 生成

用 effective boundaries 生成 cycles。

board scope：

```text
effective_boundaries(board) = board origin boundaries
```

CPU scope：

```text
effective_boundaries(cpu_i) = sort_unique_by_timestamp(inherited board boundaries + cpu_i local origin boundaries)
```

同 timestamp 合并规则：

- 同一 scope 内相同 timestamp 的多个 boundary 合并成一个 effective boundary。
- inherited board boundary 与 CPU-local boundary timestamp 相同时，保留 inherited board boundary。
- CPU-local evidence 在最终 evidence 回填阶段作为 support 或 wide_support 解释，不新增重复 boundary。

日志归属规则：

- 第一个 boundary 之前的日志归 `cycle 0`。
- 从某个 `boundary_time` 开始，直到下一个 boundary 之前的日志归同一个新 cycle。
- 最后一个 boundary 之后的日志归最后一个 cycle。
- 没有 effective boundary 时，所有日志归入 `cycle 0`。

输出要求：

- 每条日志归属以 `cycle_index` 或 boundary 序列为准。
- `cycle.start_time` / `cycle.end_time` 只做观测展示，不作为闭区间匹配合同。
- `cycle.end_time` 推荐取该 cycle 内最后一条日志 timestamp。
- 如需表示下一周期起点，输出 `next_boundary_time`。

## 阶段 7：same PID 一致性校验

对所有唯一进程执行 same PID 一致性校验。

校验基于 cycle index：

1. 对每个 scope，用 effective boundaries 给日志分配 `cycle_index`。
2. 对每个唯一进程 key 收集出现过的 cycle：

```text
(slot, effective_scope, cpu_id, canonical_process_name, pid)
```

3. 如果同一个 key 出现在相邻两个 cycle，记录 `same_pid_single_boundary_conflict`。
4. 如果同一个 key 只出现在同一个 cycle，合法。
5. 如果同一个 key 出现在非相邻 cycle，合法。

CPU scope 必须使用 inherited board boundaries + CPU-local boundaries 进行校验。

发现冲突：

- 记录一个合并后的 `same_pid_single_boundary_conflict` issue。
- issue 内列出 `conflicting_cycle_pairs`，每个 pair 带左右 cycle_index 和中间 effective boundary timestamp/id。
- 对应 scope/cycle 标记 `lifecycle_reliable=false`。
- slot 级 `lifecycle_reliable=false`。
- 不自动删除边界。
- 不自动新增边界。
- 不移动边界。

## 阶段 8：可靠性传播

区分两个概念：

- 可靠进程：进程分类，配置集合为 `reliable_processes`。
- `lifecycle_reliable`：生命周期切分结果是否可靠。

传播规则：

- `same_pid_single_boundary_conflict` 是 error-level lifecycle issue。
- `invalid_lifecycle_evidence` 是 error-level issue。
- error-level issue 会导致其所在 `scope.lifecycle_reliable=false`。
- 任一 board/cpu scope 不可靠，都会导致整个 `slot.lifecycle_reliable=false`。
- 被 error-level issue 影响的 cycle 标记 `cycle.lifecycle_reliable=false`。

## 阶段 9：support 与 evidence 回填

对每条正向证据使用最终 effective boundaries 统计覆盖数量：

```text
覆盖 1 个：tight_support
覆盖 2 个及以上：wide_support
```

回填规则：

- `tight_support` 挂到对应 origin boundary 的 `support_evidence`。
- CPU evidence 若只覆盖一个 inherited board boundary，挂到原 board origin boundary，并记录 `evidence_scope=cpu`。
- `wide_support` 记录到顶层 `result.evidence`，不挂到单个 boundary。
- `wide_support` 不用于定位具体 boundary，也不合并多个 boundary。
- `result.evidence` 记录所有证据解释，包括 `tight_support` 和 `wide_support`。

## 阶段 10：中文 DFX 输出

所有 boundary、evidence 和 issue 必须有中文可读解释。

英文枚举字段必须配中文 label：

- `type_label_zh`
- `scope_label_zh`
- `support_type_label_zh`
- `severity_label_zh`

### Boundary 解释

解释“为什么采用这个切点”，包含：

```text
适用规则
观测事实
候选区间
当前切点
影响范围
处理结果
```

不写“矛盾原因”。必须写清证据来源、旧观测、新观测、采用 timestamp、该 timestamp 是后一 cycle 起点；board 下发 CPU 时必须说明这是继承边界，不是 CPU 又产生一次新切分。

### Issue 解释

解释“哪里冲突/为什么不可靠”，包含：

```text
适用规则
观测事实
当前切分/输入状态
矛盾原因
影响范围
处理结果
```

必须覆盖至少三类 issue：

- `same_pid_single_boundary_conflict`
- `invalid_lifecycle_evidence`
- 配置非法类冲突

### Evidence 解释

解释证据如何被使用，尤其是 `wide_support`，包含：

```text
适用规则
观测事实
解释
处理结果
```

`wide_support` 文案必须说明：这是低定位精度证据，不是不可靠 issue。

## 阶段 11：测试

新增或重写测试场景：

- 可靠进程 PID changed 生成 board boundary。
- CPU 可靠进程 PID changed 只生成对应 CPU-local boundary，board 和其他 CPU 不受影响。
- board journal 序号回绕生成 board boundary。
- CPU journal 序号回绕只生成对应 CPU boundary。
- board boundary 覆盖 CPU wrap 时，CPU wrap 不进入 CPU-local 求解。
- inherited board boundary 不复制成 CPU origin boundary。
- CPU-local solver 不能移动、复制、重新选择 inherited board boundary。
- inherited board boundary 与 CPU-local evidence 同 timestamp 时，保留 inherited boundary，CPU evidence 只作为 evidence 解释。
- CPU evidence 只覆盖一个 inherited board boundary 时成为 board origin boundary 的 tight_support。
- CPU evidence 同时覆盖 inherited board boundary 和 CPU-local boundary 时成为 wide_support。
- CPU evidence 覆盖多个 inherited board boundaries 时成为 wide_support。
- 可靠进程缺席不报冲突。
- 设备形态中某可靠进程一直不存在，不报冲突。
- 普通唯一进程 PID changed 不单独切生命周期。
- reliable 与 multi-instance 配置冲突时报配置非法。
- 旧 `reliable_processes.board` 与 `reliable_processes.cpu` 重叠时兼容合并，不再报配置非法。
- `reliable_processes` 与 `multi_instance_processes` 重叠时报配置非法。
- 配置冲突在 canonical mapping 后检查。
- process_name_mapping 在 reliable 边界生成前生效。
- process_name_mapping 在 same PID key 生成前生效。
- CPU cycle 使用 inherited board boundary + CPU-local boundary。
- CPU same PID 校验使用 inherited board boundary + CPU-local boundary。
- same PID 校验基于 cycle_index，而不是任意两条日志之间数 boundary。
- 唯一进程 same PID 出现在相邻 cycle，输出中文冲突并标记 scope/cycle/slot 不可靠。
- 可靠进程 same PID 出现在相邻 cycle 也会报冲突。
- same PID 多个相邻 cycle pair 合并成一个 issue，并列出 `conflicting_cycle_pairs`。
- same PID 只出现在同一 cycle 合法。
- same PID 出现在非相邻 cycle 合法。
- multi-instance 进程不参与 same PID 一致性校验。
- CPU-local same PID 冲突只标记对应 CPU scope/cycle 和 slot 不可靠，不污染 board/其他 CPU scope。
- board same PID 冲突标记 board scope/cycle 和 slot 不可靠。
- 未受 error issue 影响的 cycle 保持 `lifecycle_reliable=true`。
- 宽区间覆盖多个 boundary 时记录为 `wide_support`，且不挂到单个 boundary。
- `wide_support` 不合并多个 boundary，也不生成 issue。
- `tight_support` 和 `wide_support` 都出现在 `result.evidence`。
- 被用于正向边界求解的证据缺 timestamp、缺必要 PID、CPU evidence 缺 cpu_id 或时间顺序非法时输出 `invalid_lifecycle_evidence`；普通 journal 缺 PID 或缺 `No[]` 序号不输出该 issue。
- 有效约束无候选点触发 `LifecycleSolverInvariantError`，不输出正常业务 issue。
- boundary / issue / evidence 输出中文解释，并符合各自模板。
- 中文 DFX 中包含进程名、PID、cycle pair、boundary timestamp、support 类型中文说明。

## 验收标准

- 新生命周期切分结果不依赖旧 indicator/whitelist 语义。
- 新配置注释能直接说明每个字段的业务含义和示例。
- 进程分类、scope key、board/CPU effective boundary、cycle index、lifecycle_reliable 粒度没有实现分叉空间。
- inherited/fixed/candidate/origin 四个概念无冲突：inherited board boundary 绝不作为 CPU-local origin candidate。
- 冲突场景不自动修复，只输出不可靠诊断。
- 每条 v2 硬规则至少有一个正例或负例测试。
- 用户阅读中文 DFX 后能直接判断“哪条规则被违反”，不需要反推内部算法。
- 旧生命周期文档不会被误认为 v2 规则依据。

# 反射弧 · 开发计划书

版本 v1 ｜ 2026-09-22 ｜ 目标仓库：https://github.com/zkmluck/jev-reflex-gate

> 进展（2026-09-22）：
> 阶段 0、1 完成；阶段 2 除真实 Jev 客户端外，还加了 Qwen 替身客户端与契约校验层
> （见 `docs/qwen-prompt.md`），因为 Jev 账户尚未充值，而 Qwen 走百炼免费额度即可先把链路跑通。
> 阶段 5（评测与阈值校准）已完成：30 条评测集跑出误放行 0、误拦 0、正确 25/30，
> 阈值定为 0.20 / 0.65 / 0.90，并且发现"风险"与"没价值"必须分成两个问题（见 `docs/eval.md`）。
> 阶段 3、4（接入 DevAgent-Workspace）尚未开始。

## 一、一句话目标

给多智能体工作台装一道"动作前闸门"：在执行不可逆的对外写操作之前，用 Jev 的一次调用拿回三个类型化决策，按"不确定走廊"决定**放行 / 拦下 / 降级**，并留下可回放的审计记录。

## 二、要解决的具体缺口

目标工作台 `DevAgent-Workspace` 会自动分析 GitHub 仓库并产出报告、测试骨架、文档建议和 PR 草稿。它的"手"在 `github_api/pr_client.py`：

```python
if create_pr:
    create_github_pr(repo_url, ...)   # 建分支 → 提交 .devagent/ 产物 → 必要时 fork → 开 PR
```

这一串动作不可逆、对外可见、会惊动真人维护者；而触发它的只是一个布尔参数，开关之前**没有任何一处做过判断**。

规则的 `if` 太僵硬（写不出"这份报告值不值得打扰维护者"），每一步都送给前沿大模型又太慢太贵。中间那层又快又便宜的小判断，正是 Jev 的位置。

## 三、已确认的事实（本次开发的硬约束）

| 项 | 事实 |
| --- | --- |
| 端点 | `POST https://jevtypesafeai.com/api/v1/decide` |
| 鉴权 | `Authorization: Bearer $JEV_API_KEY`（`jv_live_...`） |
| 官方直连（可选） | `https://api.typesafe.ai/v1/systemone`，请求结构相同 |
| 三种问题 | `choice`（≤255 选项）、`score`（2–10 级）、`noul`（0–1 校准概率） |
| 批处理 | 多个问题在同一次往返中并行评估，共享 state 成本 |
| 性能与价格 | 70–500ms；$0.42/M 输入 token，输出免费，约 $0.0004/次决策 |
| 错误码 | `402` = 预付余额用尽 |
| 输出特性 | 答案形状由请求固定，不会产出非法类型，无需解析容错 |
| 目标接入点 | `DevAgent-Workspace/github_api/pr_client.py` 的 `create_github_pr()` 调用之前 |
| 运行环境 | conda 环境 `py310`，Python 3.10.21；工作台已有 fastapi / gradio / langgraph / chromadb / pygithub / python-dotenv / requests |
| 当前缺口 | `.env` 中没有 `JEV_API_KEY` |

完整契约见 `docs/jev-decision-api.md`。

## 四、本期采用的默认取舍

有三处细节尚未由需求方拍板。为了让开发能推进，本期先按下列默认执行；**改其中任何一条都不影响整体架构**，只影响策略层的一个分支。

| 待决项 | 本期默认 | 理由 | 改动代价 |
| --- | --- | --- | --- |
| 拦截范围 | Phase 1 只拦对外写操作（建分支、提交、fork、开 PR）；本地破坏性动作只记录不拦 | 对外动作不可逆且会惊动他人，风险最集中；范围小便于先跑通 | 小，增加一类 action 注册即可 |
| 判定为危险时的行为 | 三档分流；中间地带默认**降级为草稿 PR 并标记待复核**，不阻塞流程 | 不需要改造 Gradio 交互就能落地；草稿不惊动维护者 | 中，Phase 3 加人工确认按钮 |
| API 接入 | 先用 `MockJev` 离线跑通全链路；真 API 通过环境变量 `JEV_API_KEY` 切换，默认托管端点 | 没有 key 也能开发与测试，且 Mock 便于写确定性单测 | 小，客户端层已经抽象 |

## 五、架构

```
调用方（工作台 / CLI）
        │
        ▼
  gate.guard(action)            ← 唯一对外入口
        │  1. 组装 state（白名单字段，脱敏）
        │  2. 生成三个类型化问题
        ▼
  decide.call_jev(state, questions)
        │  → 走 MockJev 或真实 HTTP 客户端
        ▼
  policy.verdict(answers)       ← 不确定走廊分流
        │  allow / block / review
        ▼
  audit.record(...)             ← JSONL 留痕，可回放
        │
        ▼
  Decision(verdict, reasons, raw_answers)
```

| 文件 | 职责 | 关键约束 |
| --- | --- | --- |
| `jev_reflex/models.py` | `Action` / `Decision` / `Verdict` 数据结构 | 纯 dataclass，无外部依赖 |
| `jev_reflex/decide.py` | 真实 Jev HTTP 客户端 | 超时 8s、最多 1 次重试；区分 `401`/`402`/`5xx`/超时；key 只从环境变量读，日志脱敏 |
| `jev_reflex/policy.py` | 问题模板、阈值、state 白名单、分流规则 | 阈值集中在配置里，便于校准 |
| `jev_reflex/gate.py` | 对外入口 `guard_github_pr(...)` / `guard(action)` | 失败与超时一律降级为 `review`，绝不静默放行 |
| `jev_reflex/audit.py` | 追加写 JSONL 审计日志 | 不写 key、不写完整文件内容；支持按 `action_id` 回放 |
| `jev_reflex/mock.py` | `MockJev`：确定性伪概率，离线跑通 | 与真实响应结构完全一致 |
| `jev_reflex/config.py` | 阈值、端点、超时、开关 | 支持环境变量覆盖 |
| `jev_reflex/qwen.py` | Qwen 替身客户端（OpenAI 兼容接口） | 每个回答都必须通过契约校验 |
| `jev_reflex/contract.py` | 契约校验：类型、取值域、概率归一 | 不合格一律判失败，由闸门降级为 `review` |
| `jev_reflex/prompts.py` | 给 Qwen 的"扮演 Jev"提示词 | 提示词与校验层共同构成"类型保证" |

## 六、四个问题怎么问

一次调用同时问四个问题（英文 instructions，实测更稳；state 保持原文）：

| 名称 | 类型 | instructions | criteria |
| --- | --- | --- | --- |
| `block` | noul | "Should this external write action be blocked right now?" | 无 |
| `noise` | noul | "Would this pull request be noise for the maintainers — empty, placeholder, duplicate or otherwise adding no real value?" | 无 |
| `risk_class` | choice | "What kind of risk does this action carry?" | `routine` / `sensitive_content` / `unauthorized_push` / `low_value_spam` / `duplicate` |
| `blast_radius` | score | "How large is the blast radius if this goes wrong?" | `["trivial, only our own scratch space", "affects our own fork", "notifies a maintainer", "writes to someone else's repository", "could damage a third party repository"]` |

> 这里原计划只问三个问题，把"没价值"塞进 `risk_class`。评测集实测推翻了它：
> 空产物、占位文件、重复 PR 这类动作，模型给出很低的风险值是**正确的**——它们不危险。
> 风险和理由是两根轴，必须分开问，否则任何阈值都分不开"干净的提交"和"没意义的提交"。

## 七、state 只放什么（成本与隐私）

`state` 是决策的唯一输入，也是成本与隐私的全部来源。白名单字段：

```
repo:               owner/name
base_branch:        目标基线分支
head_branch:        将要创建的分支名
is_fork:            是否 fork 到自己的账号
can_push:           是否对原仓库有推送权限
artifacts:          [{name, bytes, sha256[:8]}]，仅文件名、体积、摘要
artifact_count:     产物文件数量
pr_title:           PR 标题
pr_body_excerpt:    PR 正文前 200 字
draft_flag:         是否已是草稿
create_pr:          调用方是否要求创建 PR
```

**明确不发送**：产物文件的完整内容、任何 token、`.env` 内容、本地绝对路径中的用户名。

预计 state 约 250–400 token，单次决策成本 ≈ $0.0004。

## 八、分流规则：不确定走廊

```
if block >= 0.65 or risk_class in {"unauthorized_push", "sensitive_content"}:
        verdict = block        # 拒绝执行，返回理由
elif noise >= 0.90:
        verdict = review       # 不危险，但不值得打扰人：降级为草稿
elif block < 0.20 and risk_class == "routine":
        verdict = allow        # 直接执行
else:
        verdict = review       # 中间地带：降级为 draft PR，标记待复核
```

几个刻意的设计：

- **问句方向是"该不该拦下"，不是"安不安全"**。这样退化的模型（比如把所有问题都答成 0.99）
  会导致处处拦下——人会立刻发现并去修；反过来则可能一路静默放行。
- **超时、网络失败、401/402、格式异常 → 一律 `review`**。护栏坏了就降级，不会静默放行。
- `unauthorized_push` 与 `sensitive_content` 是一票否决类，不看概率。
- 阈值已用 30 条评测集对 qwen-plus 校准为 **0.20 / 0.65 / 0.90**（见 `docs/eval.md`）。
  换模型或换场景都要重跑 `python examples/calibrate.py`。

## 九、审计日志

每次决策追加一行 JSONL（默认 `output/reflex/audit.jsonl`）：

```json
{
  "ts": "2026-09-22T10:31:02+08:00",
  "action_id": "8f2c1a4e",
  "action_type": "github_pr",
  "repo": "zkmluck/errAnalyst",
  "state_digest": "sha256:3b1f...",
  "answers": {"block": 0.62, "risk_class": "low_value_spam", "blast_radius": 4.0},
  "verdict": "review",
  "reasons": ["allow=0.62 in uncertain band"],
  "thresholds": {"allow_below": 0.20, "block_above": 0.65, "noise_above": 0.90},
  "latency_ms": 214,
  "cost_usd": 0.00011,
  "client": "jev",
  "human_override": null
}
```

回放即"当时为什么放行"：按 `action_id` 取出这一行，读出概率、风险类别、阈值和判定理由。日志里永远不出现 key 与文件正文。

## 十、目录结构

```
jev-reflex-gate/
  reflex/
    __init__.py
    config.py
    models.py
    decide.py
    policy.py
    gate.py
    audit.py
    mock.py
  tests/
    test_policy.py
    test_gate.py
    test_decide_contract.py
  examples/
    demo_guard.py
    replay_audit.py
  docs/
    plan.md
    jev-decision-api.md
    eval.md            # Phase 5 产出
  pyproject.toml
  README.md
```

## 十一、接入现有工作台（最小改动）

目标：把改动压在 `agent_manager.run_pipeline` 的 `create_pr` 分支里，三到五行：

```python
from jev_reflex import guard_github_pr

decision = guard_github_pr(repo_url, final["artifacts_dir"], final)
if decision.blocked:
    final["logs"].append(f"[reflex] 已拦下 PR：{decision.reasons}")
else:
    pr_info = create_github_pr(repo_url, final["artifacts_dir"], draft=decision.downgrade_to_draft, ...)
```

接入开关放在配置里，关掉即回到今天的行为，便于对照。

## 十二、实施阶段与验收

| 阶段 | 产出 | 验收标准 | 预估 |
| --- | --- | --- | --- |
| 0 脚手架 | 包结构、`models.py`、`config.py`、`pyproject.toml` | `python -c "import reflex"` 通过 | 0.5h |
| 1 Mock 全链路 | `mock.py`、`gate.py`、`audit.py`、`examples/demo_guard.py` | 离线跑通三种判决（allow / block / review）各一条，并生成审计日志 | 2h |
| 2 真实客户端 | `decide.py` + 契约测试 | 用真 `JEV_API_KEY` 跑通一次调用；缺 key、`402`、超时三种异常均有单测 | 2h |
| 3 策略与分流 | `policy.py` 完整版 | 阈值可配；一票否决类生效；超时降级为 review | 2h |
| 4 接入工作台 | `agent_manager.py` 的接入改动 + Gradio 显示裁决 | 界面能看到"放行 / 拦下 / 降级"及理由 | 3h |
| 5 阈值校准 | `docs/eval.md`、评测集 | 20–30 条样本跑出混淆矩阵；误放行率 = 0 | 3h |
| 6 可选扩展 | 人工确认交互；本地破坏性动作纳入护栏 | 按需 | — |

## 十三、评测与校准

样本构成（构造 + 从真实产物中抽取，脱敏）：

| 类别 | 条数 | 期望判决 |
| --- | --- | --- |
| 正常的仓库分析报告 PR | 10 | allow |
| 低价值/垃圾 PR（空产物、重复内容） | 5 | review 或 block |
| 含敏感内容（token、密钥、个人路径） | 5 | block |
| 越权推送（对他人仓库无权限） | 5 | block |
| 与已有 PR 重复 | 5 | review |

关注指标，按重要性排序：**误放行率**（危险样本被判 allow，最不可接受）、误拦率、升级率、单次成本。结果记入 `docs/eval.md`。

## 十四、风险与对策

| 风险 | 对策 |
| --- | --- |
| API 抖动或超时 | 超时即"不确定"，降级为 draft，绝不静默放行 |
| 余额耗尽（402） | 明确报错 + 降级；日志记录 `client_error` |
| 阈值拍脑袋 | Phase 5 用评测集校准，阈值集中在配置里 |
| 把敏感内容塞进 state | state 白名单 + 只发摘要，不发送文件正文 |
| key 泄漏 | 只读环境变量；日志脱敏；`.env` 已在 `.gitignore` |
| Mock 与真实 API 行为漂移 | 契约测试锁定响应结构；真实调用与 Mock 共用同一套数据模型 |
| 护栏拖慢流水线 | 单次 70–500ms，只在对外写操作前调用一次 |

## 十五、成本

一次决策约 $0.0004（state ≈ 250–400 token，输出免费）。按每天 100 次 PR 判断估算：约 $0.04/天，$1.2/月。Mock 阶段零成本。

## 十六、完成标准（DoD）

1. 危险样本全部被拦下或降级，误放行率为 0。
2. 正常样本不被误拦（误拦率可接受范围内）。
3. 无 key、断网、超时、402 四种异常下，护栏都走降级而不是放行。
4. 每次判决在审计日志里都能回放"当时为什么放行"。
5. 接入点可在配置里一键关闭，关闭后回到接入前的行为。
6. 全链路有单测覆盖，离线（Mock）即可跑。

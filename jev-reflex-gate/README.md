# Jev 反射弧 · jev-reflex-gate

> 给 agent 装一层"动手前的直觉"：在不可逆动作真正执行之前，用 Jev 的类型化决策判断这一步该放行、该拦下，还是该交给人确认。

## 现在就能跑（不需要任何 key）

```bash
python -m unittest discover -s tests -t .     # 全部单测，离线
python examples/demo_guard.py                 # 三种判决各走一遍，并回放判决理由
python examples/print_qwen_prompt.py          # 打印给 Qwen 的"扮演 Jev"提示词
```

## 它解决什么问题

多智能体工作台在对外写操作上通常只有一个布尔开关：

```python
if create_pr:
    create_github_pr(repo_url, ...)   # 建分支、提交、必要时 fork、开 PR，一口气做完
```

一旦打开，agent 会建分支、提交产物、在需要时 fork 别人的仓库、然后开 PR——不可逆、对外可见、会惊动真人维护者。而开关之前，没有任何一处问过"这一步现在该不该做"。

写死的 `if` 太僵硬，每次都叫前沿大模型又太慢太贵。中间那层又快又便宜的小判断，正是 Jev 的位置。

## 一次调用，四个类型化问题

| 问题 | 类型 | 拿回什么 |
| --- | --- | --- |
| 这个动作现在该被拦下吗 | noul | 0–1 的校准概率，越高越该拦 |
| 这个 PR 是不是没价值 | noul | 0–1 的概率，越高越是噪音 |
| 风险属于哪一类 | choice | 正常 / 内容敏感 / 越权推送 / 低价值垃圾 / 重复 |
| 影响面多大 | score | 5 级有序量表 |

四个问题在同一次往返里并行评估，共享 state 成本。

**风险和理由是两回事**：空产物、占位文件、重复 PR 并不危险，只是没意义。评测集上
实测发现，把它们塞进同一个问题里，模型给的风险值会很低也完全合理，于是任何阈值都
分不开"干净的正常提交"和"没价值的提交"——这两件事必须分开问。

## 分流策略：不确定走廊

| 概率 | 判决 | 去向 |
| --- | --- | --- |
| 高（≥ 0.65）或类别是一票否决类 | `block` | 拒绝，并给出理由 |
| 噪音高（≥ 0.90） | `review` | 不危险但没价值，降级为草稿 |
| 低（< 0.20）且类别为 routine、噪音也低 | `allow` | 直接执行 |
| 其余 | `review` | 降级为草稿 PR，标记待复核 |

**闸门自己出问题时，判决一定是 `review`**：超时、断网、401、402、返回格式异常，全部降级，绝不静默放行。

## 三个可选客户端

| `REFLEX_CLIENT` | 用什么 | 需要 | 什么时候用 |
| --- | --- | --- | --- |
| `mock`（默认） | 内置假 Jev，确定性启发式 | 无 | 开发、单测、离线演示 |
| `jev` | 真实 Jev Decision API | `JEV_API_KEY` + 账户余额 | 生产环境：70–500ms，答案类型由构造保证 |
| `qwen` | Qwen 扮演 Jev | `QWEN_API_KEY` | 没有 Jev 余额时的替身；类型由我们的校验器保证，校准弱一些 |

## 用 Qwen 当替身（百炼 / DashScope）

1. 在百炼控制台取一个 API-KEY，写进 `.env`：

```
QWEN_API_KEY=sk-...
QWEN_MODEL=qwen-plus
REFLEX_CLIENT=qwen
```

2. 默认走百炼的 OpenAI 兼容接口 `https://dashscope.aliyuncs.com/compatible-mode/v1`，任何 OpenAI 兼容服务（Ollama / vLLM / LM Studio）都可以用 `QWEN_BASE_URL` 换掉。

3. 提示词在 `jev_reflex/prompts.py`，完整可复制版在 `docs/qwen-prompt.md`。

### 用 Qwen 时最重要的一点

真 Jev 不会产出非法类型；大模型会。所以我们把保证拆成两半：提示词负责让模型答对形状，`contract.py` 负责在它答错时**拒绝**。Qwen 只要给出一个不在选项里的类别、一个越过 0–1 的概率，或者干脆用散文回答，闸门就得到 `bad_response`，判决落到"待复核"。

详见 `docs/qwen-prompt.md`。

## 接入现有工作台（改动压在三到五行）

```python
from jev_reflex import guard_github_pr

decision = guard_github_pr(repo_url, final["artifacts_dir"], proxy_state=final)
if decision.blocked:
    final["logs"].append(f"[reflex] 已拦下 PR：{decision.reasons}")
else:
    pr_info = create_github_pr(
        repo_url,
        final["artifacts_dir"],
        draft=decision.downgrade_to_draft,   # 中间地带自动变草稿
        ...
    )
```

`REFLEX_ENABLED=0` 可以整个绕过闸门，回到接入前的行为，便于对照。

## 设计上的几条硬规矩

- **state 白名单**：只发文件名、体积、摘要、标题和正文前 200 字；产物正文、token、本机路径都不离开本机。
- **发送前后都脱敏**：`jv_live_`、`sk-`、`ghp_`、`AKIA` 等形态一律替换成 `[redacted]`——而这个标记本身又是"含敏感内容"的证据，会反映在风险判断里。
- **契约校验**：模型输出必须通过 `contract.validate_answers`，不合格即判失败。
- **审计留痕**：每次判决追加一行 JSONL，按 `action_id` 可回放"当时为什么放行"；日志里没有 key，也没有产物正文。

## 目录

```
jev_reflex/
  models.py     数据结构（Action / Artifact / Decision / Verdict）
  config.py     阈值、端点、开关
  policy.py     四个问题、state 白名单、脱敏、分流规则
  gate.py       对外入口 guard() / guard_github_pr()
  decide.py     真实 Jev API 客户端（标准库实现）
  qwen.py       Qwen 替身客户端
  contract.py   契约校验：不合格的输出一律拒绝
  prompts.py    给 Qwen 的提示词
  mock.py       离线假 Jev
  audit.py      JSONL 审计与回放
  evalkit.py    评测集加载、指标口径、阈值扫描
eval/           30 条评测集 + 模型原始答案落盘
tests/          71 个单测，全部离线
examples/       演示、回放、打印提示词
docs/
  plan.md             开发计划书
  jev-decision-api.md Jev Decision API 契约
  qwen-prompt.md      Qwen 替身提示词
  eval.md             评测与阈值校准结果
  eval-baseline.md    改造前的基线结果（三问答设计）
```

## 状态

- [x] 开发计划书（`docs/plan.md`）
- [x] 包结构、数据模型、配置
- [x] 离线假 Jev：三种判决各跑通一条
- [x] 审计日志与回放
- [x] 真实 Jev 客户端（401/402/超时/重试均有单测；账号需充值才能真正跑通）
- [x] Qwen 替身客户端 + 契约校验 + 提示词
- [x] 30 条评测集与阈值校准（误放行 0、误拦 0、正确 25/30）
- [ ] 接入 DevAgent-Workspace（`agent_manager.py` 的 `create_pr` 分支）

## 参考

- 项目背景与设计概要：`docs/plan.md`
- Jev Decision API 契约：`docs/jev-decision-api.md`
- Jev / TypeSafe AI：https://jevtypesafeai.com/zh

# Jev Decision API 契约（已核对）

来源：https://jevtypesafeai.com/skill/SKILL.md
说明：该站点是独立第三方提供的托管代理，非 TypeSafe AI 官方。

## 端点与鉴权

```
POST https://jevtypesafeai.com/api/v1/decide
Authorization: Bearer $JEV_API_KEY        # jv_live_... 形式
Content-Type: application/json
```

官方直连（可选，同一套请求结构）：`https://api.typesafe.ai/v1/systemone`

## 三种问题类型

- **choice** — 从最多 255 个带标签选项中选一个。`criteria` 传 `{ key: "该选项的含义" }`。返回胜出的 `choice`、每个选项的 `probabilities`、以及 `confidence`。
- **score** — 把输入放到 2–10 级的有序量表上。`criteria` 传等级描述数组（低 → 高）。返回可带小数的 `score` 与完整分布。
- **noul** — 一个已校准的是/否，返回 0 到 1 的 `noul`。只传 `instructions`。适合闸门与护栏。

多个问题可在一次往返里并行评估，共享 state 成本。

## 请求

```json
{
  "state": "Customer: I've been charged twice and nobody has replied for 3 days.",
  "questions": {
    "route": {
      "type": "choice",
      "instructions": "Where should this go?",
      "criteria": {
        "billing": "billing, payments or refunds",
        "bug": "the product is broken",
        "account": "login or access"
      }
    },
    "urgency": {
      "type": "score",
      "instructions": "How urgent is this?",
      "criteria": ["routine", "today", "urgent", "critical, about to churn"]
    },
    "escalate": {
      "type": "noul",
      "instructions": "Escalate to a human now?"
    }
  }
}
```

## 响应

```json
{
  "answers": {
    "route":    { "type": "choice", "choice": "billing", "confidence": 1.0,
                  "probabilities": { "billing": 0.87, "bug": 0.08, "account": 0.05 } },
    "urgency":  { "type": "score", "score": 3.0,
                  "probabilities": { "0": 0.0, "3": 1.0 } },
    "escalate": { "type": "noul", "noul": 0.94 }
  },
  "usage": { "input_tokens": 62, "cost_usd": 0.000026, "credits_remaining_usd": 4.99 }
}
```

答案的形状由请求固定，因此可以直接分支判断，无需解析容错：

```python
if answers["escalate"]["noul"] > 0.7:
    handoff_to_human()
route(answers["route"]["choice"])
```

## 工程约定

- `state` 只保留决策真正需要的内容——按输入 token 计费。
- 相关问题合并进一次调用，不要拆成多次。
- 用 `confidence` / `probabilities` 自动处理高置信的简单情形，只升级不确定的少数。
- key 只放环境变量 `JEV_API_KEY`，绝不写进日志、绝不提交进仓库。
- `402` 表示预付余额用尽，需要去价格页充值。
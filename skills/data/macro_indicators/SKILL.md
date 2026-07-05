---
name: macro-indicators
description: 全球宏观指标数据接口 — 覆盖中美两国经济增长、通胀、货币政策、金融条件、财政/债务等 120+ 字段。数据通过 FRED API + akshare + FiscalData + TreasuryDirect 四源采集，由 data_sources/macro_data.py 统一封装。
owner_group: 开发3组（数据）
domain: data
status: live
---

# 宏观指标数据接口

## 1. 适用范围

适用任务：
- 宏观周期定位（CAI/FCI/通胀得分计算）
- 中美双经济体增长-通胀-政策三角验证
- 资产配置权重生成（A股/中债/黄金/南华工业/美股）
- 5 时点回归测试（T+1Y / T+1Q / T-1Y / T-5Y / T-10Y）

谁消费这些数据：
- `agents/macro/agent.py` (MacroAgent) — 7 层流水线消费
- `skills/macro/layer0_tracking/` — 双经济体追踪 Skill
- `skills/macro/layer1_cai_fci/` — CAI/FCI 计算 Skill

边界说明：
- FRED 数据最远可回溯至 1940s，但短期部分 series 仅有 10 年历史
- akshare 中国数据只覆盖最新 ~20 年，早期时点（T-10Y=2016）部分字段无历史值
- FiscalData API 公共端点，无鉴权，限流约 60 req/min
- TreasuryDirect 为 HTML 页面解析，可能因页面结构变更而失效
- FRED API 需要 `.env` 中配置 `FRED_API_KEY`

## 2. 执行数据源

真实数据获取逻辑位于：

```text
data_sources/macro_data.py          # 主数据源：FRED + akshare 双源采集
data_sources/macro_format.py         # 格式转换层：→ Agent 消费格式
data_sources/fiscaldata_client.py    # 美国财政数据：FiscalData REST API
data_sources/treasury_client.py      # 美国国债拍卖：TreasuryDirect HTML 解析
```

推荐调用方式：

```python
from data_sources.macro_data import MacroDataSource
from data_sources.macro_format import convert_to_agent_format

source = MacroDataSource(config={"fred_api_key": "your_key"})
raw_data = source.fetch_macro_data(target_date="2026-06-01")
formatted = convert_to_agent_format(raw_data)
```

## 3. 输入参数

### 必填参数

| 参数 | 类型 | 说明 | 示例 |
|---|---|---|---|
| target_date | string | 目标时点 (YYYY-MM-DD) | "2026-06-01" |

### 可选参数（config dict）

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| fred_api_key | string | os.getenv("FRED_API_KEY") | FRED API 密钥 |
| akshare_enabled | bool | True | 是否启用 akshare 采集 |

## 4. 输出格式

### 原始输出 (fetch_macro_data)

```json
{
  "us": {"ffr": {"value": 3.63, "source": "fred", "series_id": "FEDFUNDS"}},
  "china": {"cpi_yoy_cn": {"value": 1.2, "source": "akshare"}},
  "cross_border": {"usd_cnh": {"value": 7.2, "source": "akshare"}},
  "commodities": {"gold_price": {"value": 3200, "source": "fred_computed"}},
  "market_pricing": {},
  "reflexivity": {},
  "fiscal": {"avg_interest_cost_value": 3.69, "source": "fiscaldata_api"},
  "treasury": {},
  "meta": {
    "fetch_time": "2026-06-28T21:00:00",
    "target_date": "2026-06-01",
    "fred_available": true,
    "akshare_available": true,
    "errors": []
  }
}
```

### Agent 消费格式 (convert_to_agent_format)

```json
{
  "field_count": 120,
  "real_count": 95,
  "fallback_count": 15,
  "real_pct": 79.2,
  "data_status": "live",
  "source_availability": {
    "fred": true,
    "akshare": true,
    "fiscaldata": true,
    "treasurydirect": false
  },
  "blocks": {
    "us": {"ffr": 3.63, "ffr_source": "fred"},
    "china": {"cpi_yoy_cn": 1.2, "cpi_yoy_cn_source": "akshare"}
  },
  "raw_fields": {}
}
```

### data_status 判定规则

| 状态 | 条件 | Agent 行为 |
|------|------|-----------|
| `live` | 真实数据 ≥ 70%，且 FRED 或 akshare 可用 | 正常计算 confidence |
| `degraded` | 真实数据 30%-70%，或仅单一源可用 | confidence 上限 0.6 |
| `mock` | 真实数据 < 30%，或双源均不可用 | direction=neutral, confidence≤0.3 |

### 错误输出

```json
{
  "us": {},
  "china": {},
  "meta": {
    "fred_available": false,
    "akshare_available": false,
    "fast_degraded": true,
    "errors": ["FRED+akshare 双源不可用，全部 fallback"]
  }
}
```

## 5. 数据获取流程

1. 检查 `FRED_API_KEY` 和 `akshare` 可用性
2. **快速降级**: FRED + akshare 双源不可用 → 直接返回 fallback
3. FRED 采集: 31 个 series，分 7 批串行，批间 2s 间隔
4. akshare 中国: ~20 个宏观指标，用 `_cn_date_match()` 按目标日期取历史值
5. akshare 美国/市场: ISM PMI、S&P 500、VIX、汇率、商品
6. FiscalData: 平均借贷成本、联邦债务、MTS 收支
7. TreasuryDirect: 国债拍卖收益率、投标倍数
8. fallback 填充: 仅填充核心字段缺失项
9. 衍生计算: 利差、ERP、股指比价

## 6. 数据源覆盖矩阵

| 数据源 | 字段数 | 类型 | 限流 |
|--------|--------|------|------|
| FRED API | 35 | 节点型 (可回溯) | 120 req/min |
| akshare | ~60 | 快照型 (最新) | 无官方限制 |
| FiscalData API | 4 | 结构化 REST | ~60 req/min |
| TreasuryDirect | 3 | HTML 解析 | 无 |

## 7. 已知限制

- akshare `macro_china_shrzgm`(社融) 频繁超时，已降级为 M2 代理
- akshare `macro_china_exports_yoy` 返回 NaN，已降级为 fallback
- CCFI 运价指数 akshare 无独立接口，走 fallback
- CPI/PPI 在 2016 年前为 index level(100+)，2016 后为 yoy%(-5~+10)，需自动判定
- FiscalData MTS 分类 ID 可能随 API 版本变更

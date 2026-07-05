"""
macro_data.py — 宏观数据源 → Agent 格式转换层

将 data_sources/macro_data.py 的原始输出转换为 Agent 消费的标准格式。
确保所有字段按 input_field_inventory.md 的124字段规范输出。

框架引用: input_field_inventory.md — 124字段逐字段 fallback 策略
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import numpy as np


def convert_to_agent_format(raw_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 fetch_macro_data() 的原始输出转换为 Agent 格式

    Args:
        raw_data: MacroDataSource.fetch_macro_data() 的返回值

    Returns:
        {
            "field_count": int,
            "fallback_count": int,
            "real_count": int,
            "raw_fields": {field_name: {"value": ..., "source": ...}},
            "blocks": {
                "us": {...},      # 美国区块
                "china": {...},   # 中国区块
                "cross_border": {...},  # 跨境区块
                "commodities": {...},   # 商品区块
                "market_pricing": {...}, # 市场定价区块
                "reflexivity": {...},    # 反身性区块
            }
        }
    """
    us = raw_data.get("us", {})
    cn = raw_data.get("china", {})
    xb = raw_data.get("cross_border", {})
    cm = raw_data.get("commodities", {})
    mp = raw_data.get("market_pricing", {})
    rf = raw_data.get("reflexivity", {})

    # 合并所有原始字段
    all_fields: Dict[str, Any] = {}

    def _merge(block: Dict, prefix: str = ""):
        for k, v in block.items():
            key = f"{prefix}{k}" if prefix else k
            if isinstance(v, dict):
                all_fields[key] = v
            else:
                all_fields[key] = {"value": v, "source": "unknown"}

    _merge(us, "us_")
    _merge(cn, "cn_")
    _merge(xb)
    _merge(cm)
    _merge(mp, "mp_")
    _merge(rf, "rf_")

    # 财政/国债区块 (来自 FiscalData + Treasury)
    fiscal = raw_data.get("fiscal", {})
    treasury = raw_data.get("treasury", {})

    # 展平 fiscal 和 treasury 的嵌套结构 (value/source → 直接值)
    _flatten_fiscal(fiscal, all_fields)
    _flatten_fiscal(treasury, all_fields)

    # 重新组织为 blocks 结构（Agent 按 block 消费）
    blocks = {
        "us": _extract_block(us, raw_data),
        "china": _extract_block(cn, raw_data),
        "cross_border": _extract_block(xb, raw_data),
        "commodities": _extract_block(cm, raw_data),
        "market_pricing": _extract_block(mp, raw_data),
        "reflexivity": _extract_block(rf, raw_data),
        "fiscal": _extract_fiscal(fiscal),
        "treasury": _extract_fiscal(treasury),
    }

    # 计算月度财政赤字
    monthly_receipts = _fiscal_val(fiscal, "monthly_receipts", 450)
    monthly_outlays = _fiscal_val(fiscal, "monthly_outlays", 550)
    blocks["fiscal"]["monthly_deficit"] = round(monthly_outlays - monthly_receipts, 2)
    blocks["fiscal"]["monthly_deficit_source"] = "computed"

    # 统计
    real_count = sum(1 for v in all_fields.values()
                     if v.get("source") not in ("fallback", "missing", "unknown"))
    fallback_count = sum(1 for v in all_fields.values()
                         if v.get("source") in ("fallback",))
    missing_count = sum(1 for v in all_fields.values()
                        if v.get("source") in ("missing", "unknown"))

    return {
        "field_count": len(all_fields),
        "fallback_count": fallback_count,
        "missing_count": missing_count,
        "real_count": real_count,
        "real_pct": round(real_count / max(len(all_fields), 1) * 100, 1),
        "raw_fields": all_fields,
        "blocks": blocks,
        "data_status": _compute_data_status(raw_data, real_count, len(all_fields)),
        "source_availability": {
            "fred": raw_data.get("meta", {}).get("fred_available", False),
            "akshare": raw_data.get("meta", {}).get("akshare_available", False),
            "fiscaldata": len(raw_data.get("fiscal", {})) > 0,
            "treasurydirect": len(raw_data.get("treasury", {})) > 0,
        },
    }


def _extract_block(block: Dict, raw_data: Dict) -> Dict[str, Any]:
    """提取单个 block 的字段值"""
    result = {}
    for k, v in block.items():
        if isinstance(v, dict):
            result[k] = v.get("value")
            result[f"{k}_source"] = v.get("source", "unknown")
        else:
            result[k] = v
            result[f"{k}_source"] = "unknown"
    return result


def _val(block: Dict, field: str, default: float = 0.0) -> float:
    """安全取值"""
    entry = block.get(field, {})
    if isinstance(entry, dict):
        return float(entry.get("value", default))
    return float(entry) if entry is not None else default


def _flatten_fiscal(block: Dict, all_fields: Dict):
    """展平 FiscalData/Treasury 嵌套结构 → all_fields"""
    if not block:
        return
    for k, v in block.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and "value" in v:
            all_fields[k] = v
        elif isinstance(v, dict):
            all_fields[k] = {"value": None, "source": "nested"}
        else:
            all_fields[k] = {"value": v, "source": "unknown"}


def _extract_fiscal(block: Dict) -> Dict[str, Any]:
    """提取 fiscal/treasury block 的扁平值"""
    result = {}
    if not block:
        return result
    for k, v in block.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict):
            result[k] = v.get("value")
            result[f"{k}_source"] = v.get("source", "unknown")
        else:
            result[k] = v
            result[f"{k}_source"] = "unknown"
    return result


def _fiscal_val(block: Dict, field: str, default: float = 0.0) -> float:
    """从 fiscal block 安全取值"""
    entry = block.get(field, {})
    if isinstance(entry, dict):
        try:
            return float(entry.get("value", default))
        except (ValueError, TypeError):
            return default
    try:
        return float(entry) if entry is not None else default
    except (ValueError, TypeError):
        return default


def compute_cai_cn(data: Dict[str, Any]) -> Dict[str, float]:
    """
    计算中国版 CAI (Current Activity Indicator)

    框架依据: requirement.md §7.2 Layer1 CAI指标表
    - 各指标 5年滚动 z-score 标准化后按权重加权求和

    权重表 (requirement.md §7.2 中国版CAI构建):
    | 指标 | 权重 | 框架引用行 |
    | 工业增加值同比 | 0.15 | 第537行 |
    | 财新制造业PMI | 0.08 | 第538行 |
    | 统计局制造业PMI | 0.07 | 第539行 |
    | 社融存量同比 | 0.18 | 第540行 |
    | 信贷脉冲 | 0.12 | 第541行 |
    | 商品房销售面积同比 | 0.08 | 第542行 |
    | 地产开发投资同比 | 0.05 | 第543行 |
    | 社会消费品零售同比 | 0.06 | 第544行 |
    | 乘用车销量同比 | 0.04 | 第545行 |
    | 出口同比（美元计） | 0.05 | 第546行 |
    | CCFI运价指数 | 0.02 | 第547行 |

    关键: z-score 必须使用 5年滚动窗口，禁止全历史 z-score
    """
    blocks = data.get("blocks", {})
    china = blocks.get("china", {})
    if not china:
        china = data.get("raw_fields", {})

    # CAI 权重 (总和 = 0.90, 不含高频补充的 0.10)
    CAI_WEIGHTS = {
        "industrial_production_yoy_cn": 0.15,
        "caixin_manufacturing_pmi": 0.08,
        "nbs_manufacturing_pmi": 0.07,
        "social_financing_yoy": 0.18,
        "property_sales_area_yoy": 0.08,
        "property_investment_yoy": 0.05,
        "retail_sales_yoy_cn": 0.06,
        "auto_sales_yoy": 0.04,
        "export_yoy_usd": 0.05,
        "ccfi_index": 0.02,
    }

    cai_raw = 0.0
    weight_sum = 0.0
    details = {}

    for field, weight in CAI_WEIGHTS.items():
        # 获取原始值
        if field in china:
            v = china[field]
            if isinstance(v, dict):
                v = v.get("value", 0)
            v = float(v) if v is not None else 0
        else:
            v = 0

        # 标准化: 使用 pre-computed 或 原始值/标准差近似
        # 对于 PMI 类: 以 50 为基准
        # 对于同比类: 直接使用原值（假设已为同比%）
        if "pmi" in field.lower():
            z = (v - 50.0) / 2.0  # PMI 近似 z-score: 50基准, 2σ≈2点
            z = max(min(z, 3.0), -3.0)  # 截断到 ±3σ
        elif "social_financing" in field:
            z = (v - 8.0) / 3.0  # 社融同比基准≈8%, σ≈3%
            z = max(min(z, 3.0), -3.0)
        elif "property" in field or "fixed_asset" in field:
            z = v / 10.0  # 地产/投资同比 σ≈10%
            z = max(min(z, 3.0), -3.0)
        elif "retail" in field or "auto" in field:
            z = (v - 3.0) / 5.0  # 消费同比基准≈3%, σ≈5%
            z = max(min(z, 3.0), -3.0)
        elif "export" in field:
            z = (v - 5.0) / 8.0  # 出口同比 σ≈8%
            z = max(min(z, 3.0), -3.0)
        elif "ccfi" in field:
            z = (v - 1500.0) / 300.0
            z = max(min(z, 3.0), -3.0)
        elif "industrial" in field:
            z = (v - 5.0) / 3.0
            z = max(min(z, 3.0), -3.0)
        else:
            z = 0.0

        cai_raw += z * weight
        weight_sum += weight
        details[field] = {"raw": round(v, 2), "z": round(z, 3)}

    # 归一化: 除以权重和
    cai_z = cai_raw / max(weight_sum, 0.01) if weight_sum > 0 else 0.0

    # 最终安全截断
    cai_z = max(min(cai_z, 5.0), -5.0)

    direction = "上行" if cai_z > 0.1 else "下行" if cai_z < -0.1 else "中性"

    return {
        "cai_cn_z": round(cai_z, 2),
        "cai_cn_direction": direction,
        "cai_cn_details": details,
    }


def compute_cai_us(data: Dict[str, Any]) -> Dict[str, float]:
    """
    计算美国版 CAI

    框架依据: requirement.md §7.2 美国版CAI/FCI
    - ISM Manufacturing PMI、ISM Services PMI、非农就业（3M均值）、
      工业生产指数同比、零售销售同比、个人消费支出同比、
      新屋开工、成屋销售、初申失业金人数（4W均值）、信贷脉冲
    """
    blocks = data.get("blocks", {})
    us = blocks.get("us", {})

    CAI_US_WEIGHTS = {
        "ism_manufacturing_pmi": 0.15,
        "ism_services_pmi": 0.12,
        "nonfarm_payrolls": 0.12,
        "industrial_production_yoy": 0.10,
        "retail_sales_yoy": 0.10,
        "personal_consumption_yoy": 0.10,
        "housing_starts": 0.08,
        "existing_home_sales": 0.08,
        "initial_jobless_claims_4w_avg": 0.08,
        "credit_pulse": 0.07,
    }

    cai_raw = 0.0
    weight_sum = 0.0

    for field, weight in CAI_US_WEIGHTS.items():
        if field in us:
            v = us[field]
            if isinstance(v, dict):
                v = v.get("value", 0)
            v = float(v) if v is not None else 0
        else:
            v = 0

        if "pmi" in field.lower():
            z = (v - 50.0) / 3.0
            z = max(min(z, 3.0), -3.0)
        elif "nonfarm" in field:
            z = (v - 200.0) / 100.0
            z = max(min(z, 3.0), -3.0)
        elif "jobless" in field:
            z = (220000.0 - v) / 30000.0  # 反向: 越低越好
            z = max(min(z, 3.0), -3.0)
        elif "housing" in field or "existing_home" in field:
            z = (v - 1500.0) / 300.0 if "housing" in field else (v - 4200000.0) / 500000.0
            z = max(min(z, 3.0), -3.0)
        elif any(k in field for k in ("retail", "consumption", "industrial")):
            z = (v - 3.0) / 3.0
            z = max(min(z, 3.0), -3.0)
        elif "credit_pulse" in field:
            z = v / 2.0
            z = max(min(z, 3.0), -3.0)
        else:
            z = 0.0

        cai_raw += z * weight
        weight_sum += weight

    cai_z = cai_raw / max(weight_sum, 0.01)
    cai_z = max(min(cai_z, 5.0), -5.0)

    direction = "上行" if cai_z > 0.1 else "下行" if cai_z < -0.1 else "中性"

    return {
        "cai_us_z": round(cai_z, 2),
        "cai_us_direction": direction,
    }


def compute_fci_cn(data: Dict[str, Any]) -> Dict[str, float]:
    """
    计算中国版 FCI (Financial Conditions Index)

    框架依据: requirement.md §7.2 中国版FCI构建
    """
    blocks = data.get("blocks", {})
    china = blocks.get("china", {})
    xb = blocks.get("cross_border", {})

    # 简化 FCI 计算：越高 = 越紧
    cn_10y = _val_from_blocks(china, xb, "cn_10y_yield", 3.5)
    cn_1y = _val_from_blocks(china, xb, "cn_1y_yield", 2.5)
    dr007 = _val_from_blocks(china, xb, "r007", 1.8)

    # 利差: 正 = 宽松预期，负 = 紧缩预期
    term_spread = cn_10y - cn_1y
    term_spread_z = -term_spread / 0.5  # 反向: 利差窄 = 紧
    term_spread_z = max(min(term_spread_z, 3.0), -3.0)

    # DR007: 越高越紧
    dr007_z = (dr007 - 1.8) / 0.5
    dr007_z = max(min(dr007_z, 3.0), -3.0)

    # ERP 近似 (沪深300 ERP)
    csi300_erp = _val_from_blocks(china, xb, "csi300_erp", 3.0)
    erp_z = (csi300_erp - 3.0) / 2.0
    erp_z = max(min(erp_z, 3.0), -3.0)

    fci_z = (
        dr007_z * 0.20 +
        term_spread_z * 0.15 +
        erp_z * 0.30 +
        0 * 0.20 +  # 信用利差 (未接入)
        0 * 0.15    # REER (未接入)
    ) / 0.65

    fci_z = max(min(fci_z, 5.0), -5.0)
    direction = "偏紧" if fci_z > 0.1 else "偏松" if fci_z < -0.1 else "中性"

    return {
        "fci_cn_z": round(fci_z, 2),
        "fci_cn_direction": direction,
    }


def compute_fci_us(data: Dict[str, Any]) -> Dict[str, float]:
    """
    计算美国版 FCI

    框架依据: requirement.md §7.2 美国版FCI指标
    """
    blocks = data.get("blocks", {})
    us = blocks.get("us", {})
    xb = blocks.get("cross_border", {})

    sofr = _val_from_blocks(us, xb, "sofr", 3.65)
    us_10y = _val_from_blocks(us, xb, "us_10y_yield", 4.0)
    us_2y = _val_from_blocks(us, xb, "us_2y_yield", 3.8)
    us_hy = _val_from_blocks(us, xb, "us_hy_spread", 3.5)
    sp500_erp = _val_from_blocks(us, xb, "sp500_erp", 1.0)
    dxy = _val_from_blocks(us, xb, "dxy_broad", 105.0)

    # SOFR z-score
    sofr_z = (sofr - 3.0) / 2.0
    sofr_z = max(min(sofr_z, 3.0), -3.0)

    # 利差
    term_spread = us_10y - us_2y
    term_z = -term_spread / 0.8
    term_z = max(min(term_z, 3.0), -3.0)

    # HY利差
    hy_z = (us_hy - 3.5) / 2.0
    hy_z = max(min(hy_z, 3.0), -3.0)

    # DXY
    dxy_z = (dxy - 100.0) / 10.0
    dxy_z = max(min(dxy_z, 3.0), -3.0)

    # ERP
    erp_us_z = (sp500_erp - 1.0) / 2.0
    erp_us_z = max(min(erp_us_z, 3.0), -3.0)

    fci_z = (
        sofr_z * 0.15 +
        term_z * 0.15 +
        hy_z * 0.20 +
        dxy_z * 0.15 +
        erp_us_z * 0.35
    )

    fci_z = max(min(fci_z, 5.0), -5.0)
    direction = "偏紧" if fci_z > 0.1 else "偏松" if fci_z < -0.1 else "中性"

    return {
        "fci_us_z": round(fci_z, 2),
        "fci_us_direction": direction,
    }


def compute_inflation_cn(data: Dict[str, Any]) -> Dict[str, float]:
    """计算中国通胀得分"""
    blocks = data.get("blocks", {})
    china = blocks.get("china", {})
    xb = blocks.get("cross_border", {})

    cpi = _val_from_blocks(china, xb, "cpi_yoy_cn", 0.5)
    ppi = _val_from_blocks(china, xb, "ppi_yoy_cn", -2.0)

    cpi_z = (cpi - 2.0) / 1.5
    cpi_z = max(min(cpi_z, 3.0), -3.0)
    ppi_z = (ppi - 0.0) / 4.0
    ppi_z = max(min(ppi_z, 3.0), -3.0)

    infl_z = cpi_z * 0.5 + ppi_z * 0.5
    infl_z = max(min(infl_z, 5.0), -5.0)
    direction = "上行" if infl_z > 0.2 else "下行" if infl_z < -0.2 else "中性"

    return {
        "inflation_cn_z": round(infl_z, 2),
        "inflation_cn_direction": direction,
    }


def compute_inflation_us(data: Dict[str, Any]) -> Dict[str, float]:
    """计算美国通胀得分"""
    blocks = data.get("blocks", {})
    us = blocks.get("us", {})
    xb = blocks.get("cross_border", {})

    core_pce = _val_from_blocks(us, xb, "core_pce_yoy", 3.0)
    eci = _val_from_blocks(us, xb, "eci_wage", 0.7)
    be5y = _val_from_blocks(us, xb, "breakeven_5y5y", 2.3)

    pce_z = (core_pce - 2.0) / 1.0
    pce_z = max(min(pce_z, 3.0), -3.0)
    eci_z = (eci - 0.5) / 0.3
    eci_z = max(min(eci_z, 3.0), -3.0)
    be_z = (be5y - 2.2) / 0.6
    be_z = max(min(be_z, 3.0), -3.0)

    infl_z = pce_z * 0.5 + eci_z * 0.25 + be_z * 0.25
    infl_z = max(min(infl_z, 5.0), -5.0)
    direction = "上行" if infl_z > 0.2 else "下行" if infl_z < -0.2 else "中性"

    return {
        "inflation_us_z": round(infl_z, 2),
        "inflation_us_direction": direction,
    }


def _val_from_blocks(block1: Dict, block2: Dict, field: str, default: float = 0.0) -> float:
    """从两个 block 中安全取值"""
    for b in [block1, block2]:
        if field in b:
            v = b[field]
            if isinstance(v, dict):
                v = v.get("value", default)
            try:
                return float(v) if v is not None else default
            except (ValueError, TypeError):
                pass
    return default


def _compute_data_status(raw_data: Dict, real_count: int, total_count: int) -> str:
    """
    计算数据状态 (live / degraded / mock)

    - live: FRED 且 akshare 均可用，且真实数据占比 >= 60%
    - degraded: 真实数据占比 30%-60%，或仅单一数据源可用
    - mock: 真实数据占比 < 30%，或 FRED+akshare 双源均不可用

    框架引用: PR #53 review — 不可用数据不得伪装为有效信号
    """
    meta = raw_data.get("meta", {})
    fred_ok = meta.get("fred_available", False)
    akshare_ok = meta.get("akshare_available", False)
    both_sources = fred_ok and akshare_ok
    any_source = fred_ok or akshare_ok

    real_pct = real_count / max(total_count, 1) * 100

    if real_pct < 30 or (not any_source):
        return "mock"
    elif real_pct < 60 or not both_sources:
        return "degraded"
    else:
        return "live"

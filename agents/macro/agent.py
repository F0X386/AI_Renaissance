"""
宏观周期 Agent - 专家4组

signal_type: macro
Skill 域: skills/macro/
核心能力：利率/汇率/PMI 解读，大周期位置判断

7层流水线: Layer 0 → Layer 5 + Layer 4.5
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from agents.base import BaseAgent
from agents.signal import Signal, neutral_signal


class MacroAgent(BaseAgent):
    """宏观周期 Agent（专家4组）"""

    signal_type = "macro"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(name="宏观周期Agent", config=config or {})
        self.load_skills_from_domain("macro")
        self._macro_source = None

    def _get_macro_source(self):
        """懒加载宏观数据源"""
        if self._macro_source is None:
            try:
                from data_sources.macro_data import MacroDataSource
                self._macro_source = MacroDataSource(self.config)
            except ImportError:
                self.log("macro_data 模块未安装", "warning")
                self._macro_source = None
        return self._macro_source

    def analyze(self, stock_code: str) -> Signal:
        """执行宏观周期分析 7 层流水线"""
        self.log(f"开始宏观周期分析：{stock_code}")

        # 尝试从 config 获取预计算数据
        precomputed = self.config.get("_precomputed_data") if self.config else None

        if precomputed:
            data = precomputed
        else:
            source = self._get_macro_source()
            if source is None:
                return neutral_signal(
                    confidence=0.1,
                    reasoning="宏观数据源不可用",
                    source=self.name,
                    stock_code=stock_code,
                    signal_type=self.signal_type,
                )
            raw_data = source.fetch_macro_data()
            from data_sources.macro_format import convert_to_agent_format
            data = convert_to_agent_format(raw_data)

        # 7 层流水线
        layer0 = self._run_layer0(data)
        layer1 = self._run_layer1(data)
        layer2 = self._run_layer2(layer1)
        layer2_5 = self._run_layer2_5(data, layer1)
        layer3 = self._run_layer3(data, layer1)
        layer4 = self._run_layer4(layer1, layer3)
        layer4_5 = self._run_layer4_5(data, layer4)
        layer5 = self._run_layer5(layer2, layer4, layer4_5)

        # 提取 data_status（来自 macro_format.convert_to_agent_format）
        data_status = data.get("data_status", "degraded")
        source_avail = data.get("source_availability", {})

        # 综合判断
        direction, reasoning, signals, confidence = self._synthesize(
            layer0, layer1, layer2, layer2_5, layer3, layer4, layer4_5, layer5,
            data_status=data_status,
        )

        return Signal(
            direction=direction,
            confidence=confidence,
            reasoning=reasoning,
            signals=signals,
            source=self.name,
            signal_type=self.signal_type,
            stock_code=stock_code,
            meta={
                "output_version": "0.3",
                "skill_name": "macro-analysis-skill",
                "owner_group": "专家4组（宏观方向）",
                "target": "全球宏观周期定位与资产配置",
                "period": datetime.now().strftime("%Y-%m-%d"),
                "time_horizon": "mid",
                "risk_level": "medium",
                "data_status": data_status,
                "source_availability": source_avail,
                "key_findings": signals[:3] if len(signals) >= 3 else signals,
                "evidence": [],
                "risk_notes": [],
                "uncertainties": (
                    ["data_status=mock: 多数数据为 fallback 值，统计推断不可靠"]
                    if data_status == "mock"
                    else ["data_status=degraded: 部分数据缺失，结论置信度受限"]
                    if data_status == "degraded"
                    else []
                ),
                "needs_human_review": data_status in ("mock", "degraded"),
                "layer_outputs": {
                    "layer0": layer0,
                    "layer1": layer1,
                    "layer2": layer2,
                    "layer2_5": layer2_5,
                    "layer3": layer3,
                    "layer4": layer4,
                    "layer4_5": layer4_5,
                    "layer5": layer5,
                },
                "reasoning_chain_md": self._build_reasoning_chain(
                    layer0, layer1, layer2, layer2_5, layer3, layer4, layer4_5, layer5
                ),
                "raw_fields": data.get("raw_fields", {}),
                "field_count": data.get("field_count", 0),
                "fallback_count": data.get("fallback_count", 0),
                "missing_count": data.get("missing_count", 0),
                "real_pct": data.get("real_pct", 0),
            },
        )

    # ========================================================================
    # Layer 0: 双经济体追踪
    # ========================================================================
    def _run_layer0(self, data: Dict) -> Dict:
        """双经济体指标面板 + 6通道触发状态"""
        blocks = data.get("blocks", {})
        us_block = blocks.get("us", {})
        cn_block = blocks.get("china", {})
        xb_block = blocks.get("cross_border", {})

        # 两国5维度指标
        panel = {
            "us": {
                "growth": {
                    "ism_mfg": us_block.get("ism_manufacturing_pmi", 50),
                    "ism_svc": us_block.get("ism_services_pmi", 50),
                    "nonfarm": us_block.get("nonfarm_payrolls", 200),
                    "gdp_yoy": us_block.get("gdp_yoy", 2),
                },
                "inflation": {
                    "core_pce": us_block.get("core_pce_yoy", 3),
                    "cpi": us_block.get("cpi_yoy", 3),
                },
                "policy": {"ffr": us_block.get("ffr", 3.6)},
                "liquidity": {
                    "sofr": us_block.get("sofr", 3.6),
                    "effr": us_block.get("effr", 3.6),
                },
            },
            "china": {
                "growth": {
                    "nbs_pmi": cn_block.get("nbs_manufacturing_pmi", 50),
                    "caixin_pmi": cn_block.get("caixin_manufacturing_pmi", 50),
                    "ip_yoy": cn_block.get("industrial_production_yoy_cn", 5),
                },
                "inflation": {
                    "cpi": cn_block.get("cpi_yoy_cn", 0.5),
                    "ppi": cn_block.get("ppi_yoy_cn", -2),
                },
                "policy": {
                    "lpr_1y": cn_block.get("lpr_1y", 3.0),
                    "mlf": cn_block.get("mlf_rate", 2.0),
                },
                "credit": {
                    "tsf_yoy": cn_block.get("social_financing_yoy", 8),
                    "m1": cn_block.get("m1_yoy_cn", 5),
                    "m2": cn_block.get("m2_yoy_cn", 8),
                },
            },
        }

        # 6通道检查
        cn_us_spread = xb_block.get("cn_us_10y_spread", 0)
        dxy = xb_block.get("dxy_broad", 105)
        vix = xb_block.get("vix", 18)

        channels = {
            "ch1_利差→资本流": _trigger(cn_us_spread < -1, "中美利差倒挂显著"),
            "ch2_美元→大宗": _trigger(dxy > 110, "美元强势压制大宗"),
            "ch3_美联储→风险偏好": _trigger(vix > 25, "VIX升高风险偏好收缩"),
            "ch4_中国信贷→全球大宗": _trigger(False, "待计算信贷脉冲"),
            "ch5_全球PMI→中国出口": _trigger(False, "待采集全球PMI"),
            "ch6_地缘→风险溢价": _trigger(False, "待评"),
        }

        return {"panel": panel, "channels": channels, "trigger_count": sum(1 for c in channels.values() if c["triggered"])}

    # ========================================================================
    # Layer 1: CAI/FCI/通胀得分
    # ========================================================================
    def _run_layer1(self, data: Dict) -> Dict:
        """计算CAI/FCI/通胀"""
        from data_sources.macro_format import compute_cai_cn, compute_cai_us, compute_fci_cn, compute_fci_us, compute_inflation_cn, compute_inflation_us

        return {
            "cai": {
                "china": compute_cai_cn(data),
                "us": compute_cai_us(data),
            },
            "fci": {
                "china": compute_fci_cn(data),
                "us": compute_fci_us(data),
            },
            "inflation": {
                "china": compute_inflation_cn(data),
                "us": compute_inflation_us(data),
            },
        }

    # ========================================================================
    # Layer 2: 周期定位
    # ========================================================================
    def _run_layer2(self, layer1: Dict) -> Dict:
        """四象限定位"""
        cai_cn = layer1.get("cai", {}).get("china", {}).get("cai_cn_z", 0)
        cai_us = layer1.get("cai", {}).get("us", {}).get("cai_us_z", 0)
        infl_cn = layer1.get("inflation", {}).get("china", {}).get("inflation_cn_z", 0)
        infl_us = layer1.get("inflation", {}).get("us", {}).get("inflation_us_z", 0)

        def _quadrant(cai_z: float, infl_z: float) -> str:
            if cai_z > 0 and infl_z > 0.2:
                return "过热"
            elif cai_z > 0 and infl_z <= 0.2:
                return "复苏"
            elif cai_z <= 0 and infl_z > 0.2:
                return "滞胀"
            else:
                return "衰退"

        return {
            "china_quadrant": _quadrant(cai_cn, infl_cn),
            "us_quadrant": _quadrant(cai_us, infl_us),
            "china_cai_z": cai_cn,
            "us_cai_z": cai_us,
        }

    # ========================================================================
    # Layer 2.5: 枢纽变量
    # ========================================================================
    def _run_layer2_5(self, data: Dict, layer1: Dict) -> Dict:
        """汇率 + 商品信号"""
        blocks = data.get("blocks", {})
        cm = blocks.get("commodities", {})
        xb = blocks.get("cross_border", {})

        copper_gold = cm.get("copper_gold_ratio", 0.12)
        oil_gold = cm.get("oil_gold_ratio", 0.035)

        cn_us_spread = xb.get("cn_us_10y_spread", 0)
        usd_cnh = xb.get("usd_cnh", 7.2)

        fx_signal = "贬值压力" if cn_us_spread < -1 else "升值倾向" if cn_us_spread > 1 else "震荡"
        commodity_signal = "全球经济谨慎" if copper_gold < 0.1 else "通胀预期上行" if oil_gold > 0.05 else "中性"

        return {
            "fx_direction": fx_signal,
            "commodity_signal": commodity_signal,
            "copper_gold_ratio": copper_gold,
            "usd_cnh": usd_cnh,
        }

    # ========================================================================
    # Layer 3: 市场定价
    # ========================================================================
    def _run_layer3(self, data: Dict, layer1: Dict) -> Dict:
        """市场定价提取"""
        blocks = data.get("blocks", {})
        xb = blocks.get("cross_border", {})

        csi300_erp = xb.get("csi300_erp", 3.0)
        sp500_erp = xb.get("sp500_erp", 1.0)

        return {
            "csi300_erp": csi300_erp,
            "sp500_erp": sp500_erp,
            "cn_market_signal": "估值偏高" if csi300_erp < 2 else "估值合理" if csi300_erp < 5 else "估值偏低",
            "us_market_signal": "估值偏高" if sp500_erp < 0.5 else "估值合理" if sp500_erp < 3 else "估值偏低",
        }

    # ========================================================================
    # Layer 4: 预期差
    # ========================================================================
    def _run_layer4(self, layer1: Dict, layer3: Dict) -> Dict:
        """预期差信号"""
        cai_cn = layer1.get("cai", {}).get("china", {}).get("cai_cn_z", 0)
        cai_us = layer1.get("cai", {}).get("us", {}).get("cai_us_z", 0)

        signals = []

        # 中国增长预期差
        if cai_cn > 0.5:
            signals.append({"type": "B", "name": "中国增长预期差", "direction": "bullish",
                          "actual": cai_cn, "intensity": min(100, int(max(0, cai_cn) / 3 * 100)),
                          "related_assets": ["A股", "商品"]})
        elif cai_cn < -0.5:
            signals.append({"type": "B", "name": "中国增长预期差", "direction": "bearish",
                          "actual": cai_cn, "intensity": min(100, int(abs(cai_cn) / 3 * 100)),
                          "related_assets": ["债券", "防御股"]})

        # 美国增长预期差
        if cai_us > 0.5:
            signals.append({"type": "B", "name": "美国增长预期差", "direction": "bullish",
                          "actual": cai_us, "intensity": min(100, int(max(0, cai_us) / 3 * 100)),
                          "related_assets": ["美股"]})
        elif cai_us < -0.5:
            signals.append({"type": "B", "name": "美国增长预期差", "direction": "bearish",
                          "actual": cai_us, "intensity": min(100, int(abs(cai_us) / 3 * 100)),
                          "related_assets": ["美股"]})

        bullish_count = sum(1 for s in signals if s["direction"] == "bullish")
        bearish_count = sum(1 for s in signals if s["direction"] == "bearish")
        direction = "bullish" if bullish_count > bearish_count else "bearish" if bearish_count > bullish_count else "neutral"

        return {"signals": signals, "direction": direction, "bullish_count": bullish_count, "bearish_count": bearish_count}

    # ========================================================================
    # Layer 4.5: 反身性
    # ========================================================================
    def _run_layer4_5(self, data: Dict, layer4: Dict) -> Dict:
        """反身性压力评估"""
        signal_count = len(layer4.get("signals", []))
        # 简化: 信号越多，拥挤度可能越高
        crowding = min(75, signal_count * 15)
        level = "green" if crowding < 30 else "yellow" if crowding < 50 else "orange" if crowding < 70 else "red"

        return {
            "crowding_score": crowding,
            "crowding_level": level,
            "paradigm_stability": "stable",
            "correction_factor": 1.0 if level == "green" else 0.75 if level == "yellow" else 0.5 if level == "orange" else 0.25,
        }

    # ========================================================================
    # Layer 5: 资产配置
    # ========================================================================
    def _run_layer5(self, layer2: Dict, layer4: Dict, layer4_5: Dict) -> Dict:
        """资产配置建议"""
        cn_quadrant = layer2.get("china_quadrant", "衰退")

        # 基础配置
        base_weights = {"A股": 15, "中债": 35, "南华工业": 15, "黄金": 15, "美股": 20}

        # 根据周期调整
        if cn_quadrant == "复苏":
            base_weights["A股"] = 21
            base_weights["中债"] = 33
            base_weights["南华工业"] = 11
            base_weights["黄金"] = 12
        elif cn_quadrant == "过热":
            base_weights["A股"] = 12
            base_weights["南华工业"] = 22
            base_weights["黄金"] = 10
        elif cn_quadrant == "滞胀":
            base_weights["A股"] = 10
            base_weights["黄金"] = 25
            base_weights["中债"] = 30
        elif cn_quadrant == "衰退":
            base_weights["A股"] = 10
            base_weights["中债"] = 45
            base_weights["黄金"] = 15

        # 反身性修正
        factor = layer4_5.get("correction_factor", 1.0)

        return {
            "weights": {k: round(v * factor, 1) for k, v in base_weights.items()},
            "total": round(sum(base_weights.values()) * factor, 1),
            "quadrant": cn_quadrant,
            "correction_applied": factor < 1.0,
        }

    # ========================================================================
    # 综合判断
    # ========================================================================
    def _synthesize(self, l0, l1, l2, l25, l3, l4, l45, l5, data_status: str = "degraded") -> tuple:
        """综合各层输出为最终 Signal

        data_status: "live" | "degraded" | "mock"
          - mock: direction 强制 neutral, confidence 上限 0.3
          - degraded: confidence 上限 0.6
          - live: 正常计算
        """
        cai_cn = l2.get("china_cai_z", 0)
        cn_q = l2.get("china_quadrant", "衰退")
        us_q = l2.get("us_quadrant", "衰退")
        l4_dir = l4.get("direction", "neutral")

        weights = l5.get("weights", {})
        weight_lines = [f"{k} 配置{int(v)}%" for k, v in sorted(weights.items(), key=lambda x: -x[1])]

        reasoning = f"A股 {weight_lines[0] if weight_lines else 'N/A'} | "
        reasoning += f"中债 {weight_lines[1] if len(weight_lines) > 1 else 'N/A'} | "
        reasoning += f"南华工业 {weight_lines[2] if len(weight_lines) > 2 else 'N/A'} | "
        reasoning += f"黄金 {weight_lines[3] if len(weight_lines) > 3 else 'N/A'} | "
        reasoning += f"美股 {weight_lines[4] if len(weight_lines) > 4 else 'N/A'}"

        signals = [f"{k} 配置{int(v)}%" for k, v in sorted(weights.items(), key=lambda x: -x[1])]

        # --- 方向判断 ---
        if data_status == "mock":
            direction = "neutral"
            reasoning += " | [MOCK] 数据不可用，强制中性"
        elif cn_q == "复苏" and l4_dir in ("bullish", "neutral"):
            direction = "bullish"
        elif cn_q in ("滞胀", "衰退"):
            direction = "bearish"
        else:
            direction = "neutral" if abs(cai_cn) < 0.3 else ("bullish" if cai_cn > 0 else "bearish")

        # --- 置信度上限 ---
        raw_confidence = _clamp(0.7 + abs(cai_cn) * 0.05, 0.3, 0.9)
        if data_status == "mock":
            confidence = min(raw_confidence, 0.3)
            reasoning += f" | 置信度上限 0.3 (mock)"
        elif data_status == "degraded":
            confidence = min(raw_confidence, 0.6)
        else:
            confidence = raw_confidence

        return direction, reasoning, signals, confidence

    def _build_reasoning_chain(self, l0, l1, l2, l25, l3, l4, l45, l5) -> str:
        """生成推理链 Markdown"""
        cai_cn = l1.get("cai", {}).get("china", {}).get("cai_cn_z", 0)
        cai_us = l1.get("cai", {}).get("us", {}).get("cai_us_z", 0)
        infl_cn = l1.get("inflation", {}).get("china", {}).get("inflation_cn_z", 0)
        infl_us = l1.get("inflation", {}).get("us", {}).get("inflation_us_z", 0)
        cn_q = l2.get("china_quadrant", "衰退")
        us_q = l2.get("us_quadrant", "衰退")

        chain = "## 推理链\n\n"
        chain += f"### Layer 0: 双经济体追踪\n"
        chain += f"- 传导通道触发: {l0.get('trigger_count', 0)}/6\n\n"
        chain += f"### Layer 1: CAI/FCI\n"
        chain += f"- 中国 CAI: {cai_cn:.2f}σ | 美国 CAI: {cai_us:.2f}σ\n"
        chain += f"- 中国通胀: {infl_cn:.2f}σ | 美国通胀: {infl_us:.2f}σ\n\n"
        chain += f"### Layer 2: 周期定位\n"
        chain += f"- 中国: **{cn_q}** | 美国: **{us_q}**\n\n"
        chain += f"### Layer 2.5: 枢纽变量\n"
        chain += f"- 汇率: {l25.get('fx_direction', 'N/A')} | 商品: {l25.get('commodity_signal', 'N/A')}\n\n"
        chain += f"### Layer 4: 预期差\n"
        chain += f"- 方向: {l4.get('direction', 'N/A')} | 看多: {l4.get('bullish_count', 0)} 看空: {l4.get('bearish_count', 0)}\n\n"
        chain += f"### Layer 4.5: 反身性\n"
        chain += f"- 拥挤度: {l45.get('crowding_level', 'N/A')} ({l45.get('crowding_score', 0)}) | 范式: {l45.get('paradigm_stability', 'N/A')}\n\n"
        chain += f"### Layer 5: 配置\n"
        for k, v in l5.get("weights", {}).items():
            chain += f"- {k}: {v}%\n"

        return chain


def _trigger(condition: bool, desc: str) -> Dict:
    return {"triggered": condition, "description": desc}


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))

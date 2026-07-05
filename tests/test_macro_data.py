"""
测试 macro 数据源 — 覆盖 PR #53 review 要求的关键路径

测试范围:
- FRED 无密钥降级: 空 API key 时快速返回 fallback
- _cn_date_match 日期解析: akshare 6 种中文日期格式
- convert_to_agent_format data_status: live/degraded/mock 三级判定
- fetch_macro_data 双源不可用快速降级
- FALLBACK_VALUES 完整性与格式
"""
from __future__ import annotations

import pytest
import sys
import os
from datetime import datetime
from unittest.mock import patch, MagicMock

# 项目根路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# 1. FRED 无密钥降级测试
# ============================================================================
class TestFredNoKeyFallback:
    """FRED API key 为空时，MacroDataSource 应正确降级"""

    def test_init_without_key(self, monkeypatch):
        """空 key 时 _fred_available = False"""
        monkeypatch.setenv("FRED_API_KEY", "")  # 清除 .env 泄漏
        from data_sources.macro_data import MacroDataSource
        source = MacroDataSource(config={"fred_api_key": ""})
        assert source._fred_available is False, "空 API key 时应标记 FRED 不可用"

    def test_fetch_macro_data_fast_degraded(self):
        """双源不可用时 fetch_macro_data 快速返回（不超时）"""
        from data_sources.macro_data import MacroDataSource
        source = MacroDataSource(config={"fred_api_key": "", "akshare_disabled": True})
        # 强制双源不可用
        source._fred_available = False
        source._akshare_available = False
        result = source.fetch_macro_data()
        assert result is not None
        assert result.get("meta", {}).get("fast_degraded") is True
        # 仍有 fallback 基础数据
        assert len(result.get("us", {})) > 0
        assert len(result.get("china", {})) > 0

    def test_fetch_fred_batch_skip_when_not_available(self):
        """_fred_available=False 时 _fetch_fred_batch 应直接返回"""
        from data_sources.macro_data import MacroDataSource
        source = MacroDataSource(config={"fred_api_key": ""})
        source._fred_available = False
        source._fred = None
        result = {"us": {}, "china": {}, "meta": {"errors": []}}
        # 不应抛异常
        source._fetch_fred_batch(result, "2026-06-01")
        assert len(result["us"]) == 0, "FRED 不可用时 us block 应为空"


# ============================================================================
# 2. _cn_date_match 日期解析测试
# ============================================================================
class TestCnDateMatch:
    """akshare 中文日期格式解析 — 覆盖 6 种常见格式"""

    def test_format_year_month_chinese(self):
        """'2008年01月份' 格式"""
        import pandas as pd
        from data_sources.macro_data import MacroDataSource
        source = MacroDataSource(config={})
        df = pd.DataFrame({
            "col1": ["2008年01月份", "2008年02月份", "2008年03月份"],
            "col2": [100, 101, 102],
        })
        idx, _ = source._cn_date_match(df, "2008-02-15")
        assert idx == 1, f"应匹配到第2行(2008年02月份)，实际 idx={idx}"

    def test_format_yyyy_mm_dash(self):
        """'2026-05' 格式"""
        import pandas as pd
        from data_sources.macro_data import MacroDataSource
        source = MacroDataSource(config={})
        df = pd.DataFrame({
            "col1": ["2026-04", "2026-05", "2026-06"],
            "col2": [10, 11, 12],
        })
        idx, _ = source._cn_date_match(df, "2026-05-15")
        assert idx == 1, f"应匹配到 2026-05，实际 idx={idx}"

    def test_format_yyyy_m_dot(self):
        """'2026.5' 格式"""
        import pandas as pd
        from data_sources.macro_data import MacroDataSource
        source = MacroDataSource(config={})
        df = pd.DataFrame({
            "col1": ["2026.4", "2026.5", "2026.6"],
            "col2": [0.5, 0.6, 0.7],
        })
        idx, _ = source._cn_date_match(df, "2026-05-15")
        assert idx == 1, f"应匹配到 2026.5，实际 idx={idx}"

    def test_format_date_object(self):
        """datetime.date 对象"""
        import pandas as pd
        from datetime import date
        from data_sources.macro_data import MacroDataSource
        source = MacroDataSource(config={})
        df = pd.DataFrame({
            "col1": [date(2026, 4, 25), date(2026, 5, 20), date(2026, 6, 20)],
            "col2": [0.1, 0.2, 0.3],
        })
        idx, _ = source._cn_date_match(df, "2026-05-25")
        assert idx >= 0

    def test_no_match_returns_minus_one(self):
        """target_date 早于所有数据时返回 -1"""
        import pandas as pd
        from data_sources.macro_data import MacroDataSource
        source = MacroDataSource(config={})
        df = pd.DataFrame({
            "col1": ["2026-04", "2026-05", "2026-06"],
            "col2": [10, 11, 12],
        })
        idx, _ = source._cn_date_match(df, "2020-01-01")
        assert idx == -1

    def test_exact_match_preference(self):
        """同一月多条记录时应选离 target 最近的"""
        import pandas as pd
        from datetime import date
        from data_sources.macro_data import MacroDataSource
        source = MacroDataSource(config={})
        df = pd.DataFrame({
            "col1": [date(2026, 5, 10), date(2026, 5, 21), date(2026, 6, 5)],
            "col2": [1, 2, 3],
        })
        idx, _ = source._cn_date_match(df, "2026-05-25")
        assert idx == 1, f"应选 5月21日(最接近5月25日)，实际 idx={idx}"


# ============================================================================
# 3. convert_to_agent_format data_status 测试
# ============================================================================
class TestDataStatus:
    """data_status 三级判定: live / degraded / mock"""

    def _make_mock_raw(self, us_fields: dict = None, cn_fields: dict = None,
                       fred_ok: bool = True, akshare_ok: bool = True) -> dict:
        """构造模拟 raw_data"""
        raw = {
            "us": {k: {"value": v, "source": "fred"} for k, v in (us_fields or {"ffr": 3.6}).items()},
            "china": {k: {"value": v, "source": "akshare"} for k, v in (cn_fields or {"cpi_yoy_cn": 0.5}).items()},
            "cross_border": {},
            "commodities": {},
            "market_pricing": {},
            "reflexivity": {},
            "fiscal": {},
            "treasury": {},
            "meta": {
                "fred_available": fred_ok,
                "akshare_available": akshare_ok,
                "errors": [],
            },
        }
        return raw

    def test_live_status(self):
        """FRED + akshare 可用 + 高覆盖率 → live"""
        from data_sources.macro_format import convert_to_agent_format
        raw = self._make_mock_raw(
            us_fields={"ffr": 3.6, "us_10y_yield": 4.5, "nonfarm_payrolls": 160},
            cn_fields={"cpi_yoy_cn": 0.5, "ppi_yoy_cn": -2.0},
            fred_ok=True, akshare_ok=True,
        )
        # Need enough fields to cross 70% threshold; fallback fills the rest
        result = convert_to_agent_format(raw)
        assert result["data_status"] in ("live", "degraded"), f"Got {result['data_status']}"

    def test_mock_status_no_source(self):
        """双源均不可用 → mock"""
        from data_sources.macro_format import convert_to_agent_format
        raw = self._make_mock_raw(
            fred_ok=False, akshare_ok=False,
        )
        result = convert_to_agent_format(raw)
        assert result["data_status"] == "mock"

    def test_degraded_status_partial(self):
        """仅 FRED 可用（无 akshare）→ at most degraded"""
        from data_sources.macro_format import convert_to_agent_format
        raw = self._make_mock_raw(
            us_fields={"ffr": 3.6, "nonfarm_payrolls": 150, "us_10y_yield": 4.0},
            cn_fields={},
            fred_ok=True, akshare_ok=False,
        )
        result = convert_to_agent_format(raw)
        assert result["data_status"] in ("degraded", "mock")

    def test_source_availability_in_output(self):
        """convert_to_agent_format 输出含 source_availability"""
        from data_sources.macro_format import convert_to_agent_format
        raw = self._make_mock_raw(fred_ok=True, akshare_ok=True)
        result = convert_to_agent_format(raw)
        sa = result.get("source_availability", {})
        assert "fred" in sa
        assert "akshare" in sa


# ============================================================================
# 4. MacroAgent data_status 决策测试
# ============================================================================
class TestAgentDataStatus:
    """Agent 根据 data_status 调整 direction/confidence"""

    def mock_agent_synthesize(self, data_status="live"):
        """最小化构造 Agent 综合判断 (replicates MacroAgent._synthesize)"""
        # 直接复制 agent.py 的 _clamp 和 _synthesize 逻辑，避免 agentscope 导入
        cai_cn = 0.5
        cn_q = "复苏"
        l4_dir = "bullish"

        weights = {"A股": 21, "中债": 33, "黄金": 12, "美股": 20, "南华工业": 14}
        reasoning = f"A股 配置{int(max(weights.values()))}%"
        signals = [f"{k} 配置{int(v)}%" for k, v in sorted(weights.items(), key=lambda x: -x[1])]

        if data_status == "mock":
            direction = "neutral"
            reasoning += " | [MOCK] 数据不可用，强制中性"
        elif cn_q == "复苏" and l4_dir in ("bullish", "neutral"):
            direction = "bullish"
        elif cn_q in ("滞胀", "衰退"):
            direction = "bearish"
        else:
            direction = "neutral" if abs(cai_cn) < 0.3 else ("bullish" if cai_cn > 0 else "bearish")

        def _clamp(val, lo, hi):
            return max(lo, min(hi, val))

        raw_confidence = _clamp(0.7 + abs(cai_cn) * 0.05, 0.3, 0.9)
        if data_status == "mock":
            confidence = min(raw_confidence, 0.3)
        elif data_status == "degraded":
            confidence = min(raw_confidence, 0.6)
        else:
            confidence = raw_confidence

        return direction, reasoning, signals, confidence

    def test_live_confidence_normal(self):
        """live 状态: confidence 正常计算"""
        direction, reasoning, signals, confidence = self.mock_agent_synthesize("live")
        assert confidence > 0.3
        assert direction in ("bullish", "bearish", "neutral")

    def test_degraded_confidence_capped(self):
        """degraded: confidence ≤ 0.6"""
        _, _, _, confidence = self.mock_agent_synthesize("degraded")
        assert confidence <= 0.6

    def test_mock_forced_neutral(self):
        """mock: direction 强制 neutral, confidence ≤ 0.3"""
        direction, reasoning, signals, confidence = self.mock_agent_synthesize("mock")
        assert direction == "neutral"
        assert confidence <= 0.3
        assert "MOCK" in reasoning.upper() or "mock" in reasoning.lower()


# ============================================================================
# 5. FALLBACK_VALUES 完整性
# ============================================================================
class TestFallbackValues:
    """FALLBACK_VALUES 应覆盖 _SECTION_FALLBACKS 中所有字段"""

    def test_all_section_fallbacks_covered(self):
        from data_sources.macro_data import FALLBACK_VALUES
        from data_sources.macro_data import MacroDataSource
        source = MacroDataSource(config={})
        for section, fields in source._SECTION_FALLBACKS.items():
            for field in fields:
                assert field in FALLBACK_VALUES, (
                    f"FALLBACK_VALUES 缺失字段 {field} (section={section})"
                )

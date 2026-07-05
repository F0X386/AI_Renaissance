"""
FiscalData Treasury API 客户端
==============================
数据源: 美国财政部 FiscalData (https://fiscaldata.treasury.gov)
接入指标:
  - #16 美国平均借贷成本 (avg_interest_rates)
  - 联邦债务余额 (debt_to_penny)
  - 月度财政收支 (MTS 替代方案)

API 特点: 公开 REST API，返回 JSON，无需认证

框架引用: xlsx对比表 #15 #16
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

FISCALDATA_BASE = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
REQUEST_TIMEOUT = 30


class FiscalDataClient:
    """FiscalData Treasury API 客户端"""

    def __init__(self):
        self._cache: Dict[str, Any] = {}

    # ========================================================================
    # 公开方法
    # ========================================================================
    def fetch_all(self) -> Dict[str, Any]:
        """采集所有 FiscalData 指标"""
        result = {}
        errors = []

        for name, method in [
            ("avg_interest_cost", self.get_avg_interest_cost),
            ("federal_debt", self.get_federal_debt),
            ("monthly_receipts", self.get_monthly_receipts),
            ("monthly_outlays", self.get_monthly_outlays),
        ]:
            try:
                result[name] = method()
                time.sleep(0.5)
            except Exception as e:
                errors.append(f"FiscalData.{name}: {e}")
                result[name] = {"value": None, "source": "error", "error": str(e)}

        if errors:
            result["_errors"] = errors
            logger.warning(f"FiscalData 错误: {len(errors)}")

        return result

    # ========================================================================
    # #16 美国平均借贷成本
    # API: /v2/accounting/od/avg_interest_rates
    # 返回所有未偿国债的加权平均利率
    # ========================================================================
    def get_avg_interest_cost(self) -> Dict[str, Any]:
        """获取美国平均借贷成本 (%)"""
        endpoint = f"{FISCALDATA_BASE}/v2/accounting/od/avg_interest_rates"
        params = {
            "sort": "-record_date",
            "page[size]": 5,
            "format": "json",
        }

        try:
            resp = requests.get(endpoint, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            records = data.get("data", [])
            if records:
                latest = records[0]
                rate_str = latest.get("avg_interest_rate_amt", "0")
                rate = float(str(rate_str).replace(",", ""))
                record_date = latest.get("record_date", "unknown")
                return {
                    "value": round(rate, 4),
                    "source": "fiscaldata_api",
                    "date": record_date,
                    "unit": "percent",
                    "desc": f"未偿国债加权平均利率 ({latest.get('security_desc', '')})",
                }

        except Exception as e:
            logger.warning(f"FiscalData avg_interest_cost: {e}")

        return {"value": 3.0, "source": "fallback", "source_note": "FiscalData API 不可用"}

    # ========================================================================
    # 联邦债务余额 (Debt to the Penny)
    # API: /v2/accounting/od/debt_to_penny
    # ========================================================================
    def get_federal_debt(self) -> Dict[str, Any]:
        """获取联邦债务总额（美元）"""
        endpoint = f"{FISCALDATA_BASE}/v2/accounting/od/debt_to_penny"
        params = {
            "sort": "-record_date",
            "page[size]": 5,
            "format": "json",
        }

        try:
            resp = requests.get(endpoint, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            records = data.get("data", [])
            if records:
                latest = records[0]
                debt_str = latest.get("tot_pub_debt_out_amt", "0")
                debt = float(str(debt_str).replace(",", ""))
                record_date = latest.get("record_date", "unknown")
                return {
                    "value": round(debt / 1e12, 4),
                    "source": "fiscaldata_api",
                    "date": record_date,
                    "unit": "trillion_usd",
                    "desc": "联邦公共债务余额",
                }

        except Exception as e:
            logger.warning(f"FiscalData debt_to_penny: {e}")

        return {"value": 35.0, "source": "fallback", "source_note": "FiscalData API 不可用"}

    # ========================================================================
    # 月度财政收入 (MTS 替代方案)
    # API: /v1/accounting/mts/mts_table_4
    # #15 财政部月度收入 (MTS)
    # ========================================================================
    def get_monthly_receipts(self) -> Dict[str, Any]:
        """获取月度财政收入（十亿美元）— MTS Table 4 按 classification_id 过滤总额"""
        endpoint = f"{FISCALDATA_BASE}/v1/accounting/mts/mts_table_4"
        params = {
            "filter": "classification_id:eq:58528530",
            "sort": "-record_date",
            "page[size]": 1,
            "format": "json",
        }

        try:
            resp = requests.get(endpoint, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            records = data.get("data", [])
            if records:
                latest = records[0]
                amt_str = latest.get("current_fytd_net_rcpt_amt", "null")
                if amt_str and amt_str != "null":
                    amt = float(str(amt_str).replace(",", ""))
                    record_date = latest.get("record_date", "unknown")
                    return {
                        "value": round(amt / 1e9, 2),
                        "source": "fiscaldata_api",
                        "date": record_date,
                        "unit": "billion_usd_fytd",
                        "desc": f"财年累计财政收入 ({latest.get('classification_desc','')})",
                    }

        except Exception as e:
            logger.warning(f"FiscalData mts receipts: {e}")

        return {"value": 450.0, "source": "fallback", "source_note": "FiscalData MTS 不可用"}

    def get_monthly_outlays(self) -> Dict[str, Any]:
        """获取月度财政支出（十亿美元）"""
        endpoint = f"{FISCALDATA_BASE}/v1/accounting/mts/mts_table_4"
        params = {
            "filter": "classification_id:eq:58528540",
            "sort": "-record_date",
            "page[size]": 1,
            "format": "json",
        }

        try:
            resp = requests.get(endpoint, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            records = data.get("data", [])
            if records:
                latest = records[0]
                amt_str = latest.get("current_fytd_net_outly_amt", "null")
                if amt_str and amt_str != "null":
                    amt = float(str(amt_str).replace(",", ""))
                    record_date = latest.get("record_date", "unknown")
                    return {
                        "value": round(amt / 1e9, 2),
                        "source": "fiscaldata_api",
                        "date": record_date,
                        "unit": "billion_usd_fytd",
                        "desc": f"财年累计财政支出 ({latest.get('classification_desc','')})",
                    }

        except Exception as e:
            logger.warning(f"FiscalData mts outlays: {e}")

        return {"value": 550.0, "source": "fallback", "source_note": "FiscalData MTS 不可用"}

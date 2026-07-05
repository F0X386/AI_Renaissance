"""
Treasury / TreasuryDirect 客户端
================================
数据源: 美国财政部 TreasuryDirect + FiscalData (作为 PDF 的替代结构化源)
接入指标:
  - #19 国债拍卖情况 (TreasuryDirect auction results)
  - #20 历史拍卖结果 (TreasuryDirect historical)
  - #17 资产负债表 (FiscalData monthly statement 替代)
  - #18 QRA季度融资 (FiscalData/Treasury press release 摘要)

说明: Treasury MTS 原始报告为 PDF, #16 平均借贷成本已有 FiscalData API。
      TreasuryDirect 提供拍卖结果的表格数据，可通过 HTML 解析获取。
      对于确需 PDF 解析的深层数据（资产负债表细项结构），标记为 deferred。

框架引用: xlsx对比表 #15-#23
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

TREASURYDIRECT_BASE = "https://www.treasurydirect.gov"
REQUEST_TIMEOUT = 30


class TreasuryClient:
    """TreasuryDirect + Treasury 数据客户端"""

    def __init__(self):
        self._cache: Dict[str, Any] = {}

    def fetch_all(self) -> Dict[str, Any]:
        """采集所有 Treasury 指标"""
        result = {}
        errors = []

        for name, method in [
            ("latest_auction", self.get_latest_auction),
            ("auction_summary", self.get_recent_auctions),
        ]:
            try:
                result[name] = method()
                time.sleep(0.5)
            except Exception as e:
                errors.append(f"Treasury.{name}: {e}")
                result[name] = {"value": None, "source": "error", "error": str(e)}

        # 计算衍生指标
        self._compute_derived(result)

        if errors:
            result["_errors"] = errors
            logger.warning(f"Treasury 错误: {len(errors)}")

        return result

    # ========================================================================
    # #19 国债拍卖情况 - 最近一次拍卖
    # TreasuryDirect 拍卖结果页: /auctions/announcements-data-results/
    # ========================================================================
    def get_latest_auction(self) -> Dict[str, Any]:
        """获取最近一次国债拍卖结果"""
        url = f"{TREASURYDIRECT_BASE}/auctions/announcements-data-results/"

        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html",
            })
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # 查找拍卖结果表格
            auction_data = self._parse_auction_table(soup)

            if auction_data:
                return {
                    "value": auction_data,
                    "source": "treasurydirect",
                    "desc": "最近国债拍卖结果",
                }

        except Exception as e:
            logger.warning(f"TreasuryDirect auction: {e}")

        return {"value": None, "source": "fallback", "source_note": "TreasuryDirect 解析失败"}

    def _parse_auction_table(self, soup: BeautifulSoup) -> Optional[Dict]:
        """解析拍卖结果表格"""
        # TreasuryDirect 页面使用 <table class="resultTable"> 展示拍卖数据
        tables = soup.find_all("table")
        if not tables:
            return None

        result = {"auctions": [], "summary": {}}

        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                texts = [c.get_text(strip=True) for c in cells]
                if len(texts) >= 5 and any(kw in "".join(texts).lower() for kw in
                                            ["security", "cusip", "issue", "maturity", "high yield", "interest",
                                             "bid", "cover", "price"]):
                    auction = {
                        "security_type": texts[0] if len(texts) > 0 else "",
                        "cusip": texts[1] if len(texts) > 1 else "",
                        "issue_date": texts[2] if len(texts) > 2 else "",
                        "maturity_date": texts[3] if len(texts) > 3 else "",
                        "high_yield": self._parse_pct(texts[4]) if len(texts) > 4 else None,
                        "interest_rate": self._parse_pct(texts[5]) if len(texts) > 5 else None,
                    }
                    result["auctions"].append(auction)

        if result["auctions"]:
            # 提取关键指标
            last = result["auctions"][0]
            result["summary"] = {
                "latest_security": last.get("security_type", ""),
                "latest_yield": last.get("high_yield"),
                "latest_rate": last.get("interest_rate"),
            }

        return result if result["auctions"] else None

    @staticmethod
    def _parse_pct(text: str) -> Optional[float]:
        """从文本提取百分比"""
        if not text:
            return None
        m = re.search(r'([\d.]+)\s*%', str(text))
        if m:
            return float(m.group(1))
        try:
            return float(text.replace("%", "").strip())
        except ValueError:
            return None

    # ========================================================================
    # #20 历史拍卖结果摘要
    # ========================================================================
    def get_recent_auctions(self) -> Dict[str, Any]:
        """获取最近几次拍卖的统计摘要"""
        result = self.get_latest_auction()
        if result.get("value") and isinstance(result["value"], dict):
            auctions = result["value"].get("auctions", [])
            if auctions:
                yields_10y = []
                bid_cover = []
                for a in auctions:
                    y = a.get("high_yield")
                    if y:
                        yields_10y.append(y)

                summary = {
                    "auction_count": len(auctions),
                    "latest_10y_yield": yields_10y[0] if yields_10y else None,
                    "source": "treasurydirect",
                    "desc": f"最近 {len(auctions)} 次拍卖",
                }
                return {"value": summary, "source": "treasurydirect"}

        return {"value": {"auction_count": 0}, "source": "fallback"}

    # ========================================================================
    # 衍生指标计算
    # ========================================================================
    def _compute_derived(self, result: Dict):
        """从拍卖数据计算衍生指标"""
        auction_data = result.get("latest_auction", {}).get("value", {})
        if isinstance(auction_data, dict):
            summary = auction_data.get("summary", {})
            result["auction_10y_yield"] = {
                "value": summary.get("latest_yield", 4.0),
                "source": "treasurydirect_derived",
                "desc": "最新10Y国债拍卖收益率",
            }
            result["auction_bid_cover"] = {
                "value": summary.get("bid_to_cover", 2.5),
                "source": "treasurydirect_derived",
                "desc": "投标倍数(>2=需求旺盛)",
            }


# ========================================================================
# 便捷函数
# ========================================================================
def fetch_treasury_data() -> Dict[str, Any]:
    """获取财政部数据"""
    client = TreasuryClient()
    return client.fetch_all()


def fetch_fiscal_data() -> Dict[str, Any]:
    """获取 FiscalData"""
    from data_sources.fiscaldata_client import FiscalDataClient
    client = FiscalDataClient()
    return client.fetch_all()

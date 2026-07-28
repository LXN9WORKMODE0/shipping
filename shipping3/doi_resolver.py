"""
DOI 解析模块
通过 DOI 获取文献 PDF 链接
"""

import re
import logging
from typing import Optional, Dict
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

from config import DOI_CONFIG

logger = logging.getLogger(__name__)


class DOIResolver:
    """DOI 解析器"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        self.scihub_urls = DOI_CONFIG.get("scihub_urls", [])

    def resolve(self, doi: str) -> Optional[str]:
        """
        解析 DOI 获取 PDF 链接

        Args:
            doi: 文献 DOI

        Returns:
            PDF 下载链接，失败返回 None
        """
        if not doi:
            return None

        # 清理 DOI 格式
        doi = self._clean_doi(doi)
        logger.info(f"解析 DOI: {doi}")

        # 方法1: 通过 CrossRef API 获取出版商链接
        pdf_url = self._get_crossref_link(doi)
        if pdf_url:
            logger.info(f"通过 CrossRef 找到链接: {pdf_url}")
            return pdf_url

        # 方法2: 尝试出版社直链
        pdf_url = self._get_publisher_link(doi)
        if pdf_url:
            logger.info(f"找到出版社链接: {pdf_url}")
            return pdf_url

        # 方法3: 尝试 Unpaywall (Open Access)
        pdf_url = self._get_unpaywall_link(doi)
        if pdf_url:
            logger.info(f"通过 Unpaywall 找到链接: {pdf_url}")
            return pdf_url

        # 方法4: 尝试 Sci-Hub
        if DOI_CONFIG.get("use_scihub"):
            pdf_url = self._get_scihub_link(doi)
            if pdf_url:
                logger.info(f"通过 Sci-Hub 找到链接: {pdf_url}")
                return pdf_url

        logger.warning(f"无法解析 DOI: {doi}")
        return None

    def _clean_doi(self, doi: str) -> str:
        """清理 DOI 格式"""
        doi = doi.strip()
        # 移除常见前缀
        for prefix in ["https://doi.org/", "http://doi.org/", "doi:"]:
            if doi.startswith(prefix):
                doi = doi[len(prefix):]
        return doi

    def _get_crossref_link(self, doi: str) -> Optional[str]:
        """通过 CrossRef API 获取 PDF 链接"""
        try:
            url = f"https://api.crossref.org/works/{doi}"
            response = self.session.get(url, timeout=15)
            response.raise_for_status()

            data = response.json().get("message", {})

            # 尝试从 link 字段获取 PDF 链接
            links = data.get("link", [])
            for link in links:
                content_version = link.get("content-version", "")
                link_url = link.get("URL", "")
                if content_version in ["vor", "published"] and link_url:
                    if ".pdf" in link_url.lower():
                        return link_url

            return None

        except Exception as e:
            logger.debug(f"CrossRef API 请求失败: {e}")
            return None

    def _get_unpaywall_link(self, doi: str) -> Optional[str]:
        """通过 Unpaywall API 获取 Open Access 链接"""
        try:
            # 使用一个示例邮箱，实际使用可替换为真实邮箱
            url = f"https://api.unpaywall.org/v2/{doi}?email=research@example.com"
            response = self.session.get(url, timeout=15)
            response.raise_for_status()

            data = response.json()
            best_oa = data.get("best_oa_location", {})

            if best_oa:
                # 优先获取 PDF 链接
                pdf_url = best_oa.get("url_for_pdf")
                if pdf_url:
                    return pdf_url
                # 其次获取 landing page
                return best_oa.get("url_for_landing_page")

            return None

        except Exception as e:
            logger.debug(f"Unpaywall API 请求失败: {e}")
            return None

    def _get_publisher_link(self, doi: str) -> Optional[str]:
        """通过出版社获取 PDF 链接"""
        try:
            url = f"https://doi.org/{doi}"
            response = self.session.get(url, allow_redirects=True, timeout=15)

            # 检查是否是 PDF 直接链接
            content_type = response.headers.get("content-type", "")
            if "pdf" in content_type.lower():
                return response.url

            # 解析 landing page 寻找 PDF 链接
            soup = BeautifulSoup(response.text, "html.parser")

            # 多种可能的选择器
            selectors = [
                "a[href*='.pdf']",
                "a[id*='pdf']",
                "a[class*='pdf']",
                "a[title*='PDF']",
                "a[title*='Full Text']",
                "button[data-url*='.pdf']",
                "iframe[src*='.pdf']",
            ]

            for selector in selectors:
                element = soup.select_one(selector)
                if element:
                    pdf_url = element.get("href") or element.get("data-url") or element.get("src")
                    if pdf_url:
                        # 处理相对路径
                        if not pdf_url.startswith("http"):
                            pdf_url = urljoin(response.url, pdf_url)
                        if ".pdf" in pdf_url.lower():
                            return pdf_url

            return None

        except Exception as e:
            logger.debug(f"出版社链接解析失败: {e}")
            return None

    def _get_scihub_link(self, doi: str) -> Optional[str]:
        """通过 Sci-Hub 获取下载链接"""
        # 更新 Sci-Hub URLs（更可靠的镜像）
        scihub_mirrors = [
            "https://sci-hub.se",
            "https://sci-hub.st",
            "https://sci-hub.ru",
            "https://sci-hub.wf",
        ]

        for scihub_url in scihub_mirrors:
            try:
                search_url = f"{scihub_url}/{doi}"
                logger.debug(f"尝试 Sci-Hub: {search_url}")

                response = self.session.get(search_url, timeout=30)
                if response.status_code != 200:
                    continue

                # 解析页面寻找 PDF 链接
                soup = BeautifulSoup(response.text, "html.parser")

                # 尝试从嵌入的 iframe 获取（Sci-Hub 常用结构）
                iframe = soup.find("iframe")
                if iframe:
                    src = iframe.get("src", "")
                    if src and ("pdf" in src.lower() or "sci-hub" in src.lower()):
                        return src

                # 多种可能的选择器
                selectors = [
                    "a[href*='.pdf']",
                    "a[href*='download']",
                    "a[download*='pdf']",
                ]

                for selector in selectors:
                    element = soup.select_one(selector)
                    if element:
                        pdf_url = element.get("href")
                        if pdf_url:
                            if not pdf_url.startswith("http"):
                                pdf_url = urljoin(search_url, pdf_url)
                            if ".pdf" in pdf_url.lower() or "download" in pdf_url.lower():
                                return pdf_url

            except Exception as e:
                logger.debug(f"Sci-Hub {scihub_url} 失败: {e}")
                continue

        return None

    def get_metadata(self, doi: str) -> Optional[Dict]:
        """
        获取 DOI 元数据

        Args:
            doi: 文献 DOI

        Returns:
            元数据字典
        """
        doi = self._clean_doi(doi)

        try:
            url = f"https://api.crossref.org/works/{doi}"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()

            data = response.json().get("message", {})

            return {
                "title": data.get("title", [""])[0] if data.get("title") else "",
                "authors": [a.get("given", "") + " " + a.get("family", "")
                           for a in data.get("author", [])],
                "year": data.get("published-print", {}).get("date-parts", [[0]])[0][0],
                "journal": data.get("container-title", [""])[0] if data.get("container-title") else "",
                "doi": doi,
                "publisher": data.get("publisher", ""),
            }

        except Exception as e:
            logger.error(f"获取元数据失败: {e}")
            return None
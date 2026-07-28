"""
PDF 下载模块
支持自动重试和断点续传
"""

import os
import time
import logging
from pathlib import Path
from typing import List, Optional
import requests
from tqdm import tqdm

from config import DOWNLOAD_CONFIG, PAPERS_DIR, PROXY_CONFIG

logger = logging.getLogger(__name__)


class PDFDownloader:
    """PDF 下载器"""

    def __init__(self, save_dir: Optional[Path] = None):
        self.save_dir = save_dir or PAPERS_DIR
        self.save_dir.mkdir(parents=True, exist_ok=True)

        self.config = DOWNLOAD_CONFIG.copy()

        # 设置请求 session
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })

        # 设置代理
        if PROXY_CONFIG.get("enabled"):
            proxies = {
                "http": PROXY_CONFIG.get("http_proxy", ""),
                "https": PROXY_CONFIG.get("https_proxy", "")
            }
            self.session.proxies.update(proxies)

    def download(
        self,
        url: str,
        filename: Optional[str] = None,
        show_progress: bool = True
    ) -> Optional[Path]:
        """
        下载 PDF 文件

        Args:
            url: PDF 链接
            filename: 保存文件名（可选）
            show_progress: 是否显示进度条

        Returns:
            保存的文件路径，失败返回 None
        """
        if not url:
            logger.error("URL 为空")
            return None

        # 生成文件名
        if not filename:
            # 从 URL 提取或使用时间戳
            filename = self._generate_filename(url)

        filepath = self.save_dir / filename

        # 检查文件是否已存在
        if filepath.exists():
            logger.info(f"文件已存在: {filename}")
            return filepath

        # 尝试下载
        for attempt in range(self.config["max_retries"]):
            try:
                logger.info(f"下载 ({attempt + 1}/{self.config['max_retries']}): {url[:50]}...")

                response = self.session.get(
                    url,
                    stream=True,
                    timeout=self.config["timeout"]
                )
                response.raise_for_status()

                # 获取文件大小
                total_size = int(response.headers.get("content-length", 0))

                # 写入文件
                with open(filepath, "wb") as f:
                    if show_progress and total_size:
                        pbar = tqdm(total=total_size, unit="B", unit_scale=True, desc=filename)

                    for chunk in response.iter_content(chunk_size=self.config["chunk_size"]):
                        if chunk:
                            f.write(chunk)
                            if show_progress and total_size:
                                pbar.update(len(chunk))

                    if show_progress and total_size:
                        pbar.close()

                logger.info(f"下载完成: {filepath}")
                return filepath

            except requests.exceptions.Timeout:
                logger.warning(f"下载超时 (尝试 {attempt + 1}/{self.config['max_retries']})")
            except requests.exceptions.RequestException as e:
                logger.warning(f"下载失败 (尝试 {attempt + 1}/{self.config['max_retries']}): {e}")
            except Exception as e:
                logger.error(f"未知错误: {e}")
                break

            # 重试前等待
            if attempt < self.config["max_retries"] - 1:
                time.sleep(self.config["retry_delay"])

        # 清理失败的文件
        if filepath.exists():
            try:
                os.remove(filepath)
            except:
                pass

        logger.error(f"下载失败: {url}")
        return None

    def download_from_doi(self, doi: str, title: Optional[str] = None) -> Optional[Path]:
        """
        通过 DOI 下载 PDF

        Args:
            doi: 文献 DOI
            title: 文献标题（用于文件名）

        Returns:
            保存的文件路径
        """
        from doi_resolver import DOIResolver

        resolver = DOIResolver()
        pdf_url = resolver.resolve(doi)

        if not pdf_url:
            logger.error(f"无法解析 DOI: {doi}")
            return None

        # 生成文件名
        if title:
            filename = self._sanitize_filename(title) + ".pdf"
        else:
            filename = self._sanitize_filename(doi) + ".pdf"

        return self.download(pdf_url, filename)

    def download_batch(
        self,
        items: List[dict],
        show_progress: bool = True
    ) -> List[dict]:
        """
        批量下载

        Args:
            items: [{"doi": xxx, "title": xxx, "pdf_url": xxx}, ...]
            show_progress: 是否显示进度条

        Returns:
            下载结果列表
        """
        results = []

        if show_progress:
            items = tqdm(items, desc="批量下载", unit="paper")

        for item in items:
            doi = item.get("doi", "")
            title = item.get("title", "")
            pdf_url = item.get("pdf_url", "")  # 已有 PDF 链接

            if not doi:
                results.append({"success": False, "error": "DOI 为空", **item})
                continue

            filepath = None

            # 优先使用已有的 PDF 链接（来自 OpenAlex/Semantic Scholar）
            if pdf_url:
                logger.info(f"使用已有 PDF 链接: {pdf_url[:50]}...")
                filename = self._sanitize_filename(title) + ".pdf" if title else None
                filepath = self.download(pdf_url, filename)

            # 如果没有 PDF 链接或下载失败，尝试 DOI 解析
            if not filepath:
                filepath = self.download_from_doi(doi, title)

            results.append({
                "success": filepath is not None,
                "filepath": str(filepath) if filepath else None,
                "doi": doi,
                "title": title
            })

            # 避免请求过快
            time.sleep(1)

        return results

    def _generate_filename(self, url: str) -> str:
        """从 URL 生成文件名"""
        # 尝试从 URL 提取有意义的名称
        name = url.split("/")[-1]
        name = name.split("?")[0]

        # 清理非法字符
        if not name.endswith(".pdf"):
            name += ".pdf"

        return self._sanitize_filename(name)

    def _sanitize_filename(self, filename: str) -> str:
        """清理文件名中的非法字符"""
        # 替换非法字符
        illegal_chars = r'[<>:"/\\|?*]'
        filename = str(filename).strip()
        filename = filename.replace("/", "-").replace("\\", "-")

        # 限制长度
        if len(filename) > 200:
            name, ext = os.path.splitext(filename)
            filename = name[:200 - len(ext)] + ext

        return filename
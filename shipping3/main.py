"""
外文文献自动检索下载工具
命令行入口
"""

import sys
import os
import argparse
import logging
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from config import LOG_CONFIG, PAPERS_DIR
from scholar_search import ScholarSearcher
from doi_resolver import DOIResolver
from pdf_downloader import PDFDownloader

# 配置日志
logging.basicConfig(
    level=getattr(logging, LOG_CONFIG["level"]),
    format=LOG_CONFIG["format"],
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_CONFIG["file"], encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)


def cmd_search(args):
    """搜索命令"""
    logger.info("=" * 50)
    logger.info(f"开始搜索: {args.query}")
    logger.info(f"保存目录: {PAPERS_DIR}")

    searcher = ScholarSearcher()

    # 执行搜索
    year_from = args.year_from
    year_to = args.year_to

    papers = searcher.search(
        query=args.query,
        year_from=year_from,
        year_to=year_to,
        max_results=args.max_results
    )

    # 打印结果
    print(f"\n{'='*60}")
    print(f"找到 {len(papers)} 篇文献:")
    print(f"{'='*60}\n")

    for i, paper in enumerate(papers, 1):
        print(f"[{i}] {paper.title}")
        print(f"    作者: {', '.join(paper.authors[:3])}")
        print(f"    年份: {paper.year}, 引用: {paper.citation}")
        if paper.doi:
            print(f"    DOI: {paper.doi}")
        print(f"    期刊: {paper.venue}")
        print()

    # 询问是否下载
    if args.download and papers:
        print("开始下载 PDF...")
        downloader = PDFDownloader()

        for paper in papers:
            if paper.doi:
                filepath = downloader.download_from_doi(paper.doi, paper.title)
                if filepath:
                    print(f"✓ 下载成功: {filepath}")
                else:
                    print(f"✗ 下载失败: {paper.title}")
            else:
                print(f"⚠ 无 DOI，跳过: {paper.title}")


def cmd_doi(args):
    """DOI 下载命令"""
    logger.info("=" * 50)
    logger.info("开始 DOI 批量下载")

    # 读取 DOI 文件
    doi_file = Path(args.file)
    if not doi_file.exists():
        logger.error(f"文件不存在: {args.file}")
        return

    dois = doi_file.read_text(encoding="utf-8").strip().split("\n")
    dois = [d.strip() for d in dois if d.strip()]

    logger.info(f"共读取 {len(dois)} 个 DOI")

    # 下载
    downloader = PDFDownloader()
    results = downloader.download_batch([{"doi": doi} for doi in dois])

    # 统计结果
    success = sum(1 for r in results if r["success"])
    failed = len(results) - success

    print(f"\n{'='*60}")
    print(f"下载完成: 成功 {success}, 失败 {failed}")
    print(f"{'='*60}")


def cmd_resolve(args):
    """DOI 解析命令"""
    logger.info("=" * 50)
    logger.info(f"解析 DOI: {args.doi}")

    resolver = DOIResolver()

    # 获取元数据
    metadata = resolver.get_metadata(args.doi)
    if metadata:
        print(f"\n文献信息:")
        print(f"  标题: {metadata.get('title')}")
        print(f"  作者: {', '.join(metadata.get('authors', []))}")
        print(f"  年份: {metadata.get('year')}")
        print(f"  期刊: {metadata.get('journal')}")
        print(f"  出版社: {metadata.get('publisher')}")
        print(f"  DOI: {metadata.get('doi')}")
    else:
        print("无法获取元数据")

    # 获取 PDF 链接
    if args.get_pdf:
        pdf_url = resolver.resolve(args.doi)
        if pdf_url:
            print(f"\nPDF 链接: {pdf_url}")

            # 询问是否下载
            if args.download:
                print("\n开始下载...")
                downloader = PDFDownloader()
                filepath = downloader.download_from_doi(args.doi)
                if filepath:
                    print(f"✓ 下载成功: {filepath}")
                else:
                    print("✗ 下载失败")
        else:
            print("\n无法获取 PDF 链接")


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description="外文文献自动检索下载工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  # 搜索文献
  python main.py search "machine learning" --year 2020-2024 --max 50

  # 搜索并自动下载
  python main.py search "deep learning" --download

  # DOI 批量下载
  python main.py doi papers/doi_list.txt

  # 解析 DOI 获取信息
  python main.py resolve 10.1038/nature14539
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # search 命令
    search_parser = subparsers.add_parser("search", help="搜索文献")
    search_parser.add_argument("query", help="搜索关键词")
    search_parser.add_argument("--year", dest="year_range", help="年份范围，如 2020-2024")
    search_parser.add_argument("--year-from", type=int, help="起始年份")
    search_parser.add_argument("--year-to", type=int, help="结束年份")
    search_parser.add_argument("--max", dest="max_results", type=int, default=20, help="最大结果数")
    search_parser.add_argument("--download", action="store_true", help="自动下载找到的文献")

    # doi 命令
    doi_parser = subparsers.add_parser("doi", help="通过 DOI 批量下载")
    doi_parser.add_argument("file", help="DOI 列表文件（每行一个 DOI）")

    # resolve 命令
    resolve_parser = subparsers.add_parser("resolve", help="解析 DOI 获取信息")
    resolve_parser.add_argument("doi", help="文献 DOI")
    resolve_parser.add_argument("--get-pdf", action="store_true", help="获取 PDF 链接")
    resolve_parser.add_argument("--download", action="store_true", help="下载 PDF")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # 解析年份范围
    if hasattr(args, "year_range") and args.year_range:
        try:
            year_from, year_to = map(int, args.year_range.split("-"))
            args.year_from = year_from
            args.year_to = year_to
        except:
            pass

    # 执行对应命令
    if args.command == "search":
        cmd_search(args)
    elif args.command == "doi":
        cmd_doi(args)
    elif args.command == "resolve":
        cmd_resolve(args)


if __name__ == "__main__":
    main()
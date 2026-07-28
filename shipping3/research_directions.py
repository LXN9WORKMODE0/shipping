"""
三峡枢纽航运文献调研脚本
针对三个调研方向自动检索和下载外文文献
"""

import sys
import os
from pathlib import Path
from datetime import datetime
import json

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

import logging
from config import LOG_CONFIG, PAPERS_DIR
from doi_resolver import DOIResolver
from pdf_downloader import PDFDownloader
from platform_searchers import UnifiedSearcher

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


# 调研方向配置（使用更宽泛的关键词，不限制时间范围）
RESEARCH_DIRECTIONS = {
    "direction_1": {
        "name": "经济社会效益",
        "description": "三峡枢纽航运功能对经济社会发展的贡献，包括：航运经济效益（货运量、运输成本、区域经济发展）、社会效益（就业、通航安全、民生改善）、黄金水道建设等",
        "keywords_cn": ["三峡枢纽", "航运效益", "经济社会效益", "黄金水道"],
        "keywords_en": [
            "Three Gorges shipping",
            "shipping economic benefit",
            "Yangtze River shipping",
            "inland waterway transport economic",
            "Yangtze River Golden Waterway"
        ]
    },
    "direction_2": {
        "name": "节能减排",
        "description": "三峡枢纽航运在交通物流领域的节能减排贡献，包括：水运相比公路/铁路的碳减排、船舶运输能效提升、绿色航运、多式联运减排等",
        "keywords_cn": ["三峡枢纽", "节能减排", "碳减排", "绿色航运"],
        "keywords_en": [
            "Three Gorges carbon emission",
            "shipping energy efficiency",
            "green shipping",
            "multimodal transport carbon",
            "inland waterway emission reduction",
            "water transport CO2 reduction"
        ]
    },
    "direction_3": {
        "name": "水运通过能力",
        "description": "三峡枢纽的水运通过能力以及潜力挖掘方式和进展，包括：船闸通航能力/效率、船闸/通航管理、通航调度、优化调度、货运量提升空间、扩能改造、航道整治等",
        "keywords_cn": ["三峡船闸", "通航能力", "航运调度", "扩能改造"],
        "keywords_en": [
            "Three Gorges lock capacity",
            "ship lock throughput",
            "navigation scheduling optimization",
            "inland waterway capacity",
            "Yangtze River navigation efficiency",
            "lock expansion"
        ]
    }
}


def search_direction(direction_key: str, max_results: int = 30, download: bool = False):
    """
    调研指定方向

    Args:
        direction_key: 方向键，如 "direction_1"
        max_results: 最大结果数
        download: 是否下载 PDF
    """
    direction = RESEARCH_DIRECTIONS[direction_key]
    name = direction["name"]
    keywords = direction["keywords_en"]

    print(f"\n{'='*70}")
    print(f"📚 调研方向: {name}")
    print(f"📋 描述: {direction['description']}")
    print(f"{'='*70}\n")

    logger.info(f"开始调研方向: {name}")

    # 创建该方向的保存目录
    direction_dir = PAPERS_DIR / direction_key
    direction_dir.mkdir(parents=True, exist_ok=True)

    all_papers = []

    # 初始化统一搜索引擎（多平台搜索）
    unified_searcher = UnifiedSearcher()
    print(f"   可用平台: {', '.join(unified_searcher.available_platforms)}")

    # 使用多个关键词搜索（不限制时间范围）
    for keyword in keywords:
        print(f"\n🔍 搜索关键词: {keyword}")

        try:
            # 不设置 year_from 和 year_to，即不限制时间范围
            papers = unified_searcher.search(
                query=keyword,
                limit=max_results // len(keywords)
                # 移除了 year_from 和 year_to 参数，实现无时间限制搜索
            )

            if papers:
                print(f"   找到 {len(papers)} 篇文献")
                all_papers.extend(papers)
            else:
                print(f"   未找到文献")

        except Exception as e:
            logger.error(f"搜索 {keyword} 出错: {e}")
            print(f"   ⚠️ 搜索出错: {e}")

    # 去重（统一搜索器已内置去重，这里再做一层保险）
    unique_papers = {}
    for paper in all_papers:
        # 使用 DOI 或标准化标题作为 key
        if paper.doi:
            key = paper.doi.lower().strip()
        else:
            import re
            key = re.sub(r'[^\w\s]', '', paper.title.lower().strip())
            key = re.sub(r'\s+', ' ', key)

        if key not in unique_papers:
            unique_papers[key] = paper

    papers = list(unique_papers.values())
    print(f"\n📊 共找到 {len(papers)} 篇不重复的文献（含多个平台）")

    # 保存搜索结果
    results_file = direction_dir / "search_results.txt"
    with open(results_file, "w", encoding="utf-8") as f:
        f.write(f"三峡枢纽航运 - {name}\n")
        f.write(f"调研时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"文献数量: {len(papers)}\n")
        f.write("="*70 + "\n\n")

        for i, paper in enumerate(papers, 1):
            f.write(f"[{i}] {paper.title}\n")
            f.write(f"    作者: {', '.join(paper.authors[:3])}")
            if len(paper.authors) > 3:
                f.write(" et al.")
            f.write(f"\n")
            f.write(f"    年份: {paper.year}, 引用: {paper.citation}\n")
            if paper.doi:
                f.write(f"    DOI: {paper.doi}\n")
            f.write(f"    期刊: {paper.venue}\n")
            if paper.source:
                f.write(f"    来源: {paper.source}\n")
            if paper.abstract:
                abstract = paper.abstract[:300] + "..." if len(paper.abstract) > 300 else paper.abstract
                f.write(f"    摘要: {abstract}\n")
            f.write(f"\n")

    print(f"📁 搜索结果已保存到: {results_file}")

    # 下载PDF
    if download and papers:
        print(f"\n📥 开始下载 PDF...")

        downloader = PDFDownloader(save_dir=direction_dir)

        # 带标题的批量下载（包含已有的 Open Access PDF 链接）
        items = [{"doi": p.doi, "title": p.title, "pdf_url": p.pdf_url} for p in papers if p.doi]
        results = downloader.download_batch(items)

        success = sum(1 for r in results if r["success"])
        print(f"   下载完成: 成功 {success}/{len(items)} 篇")

        # 保存下载结果
        with open(direction_dir / "download_results.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    # 输出文献摘要
    print(f"\n📝 文献摘要:")
    print("-"*70)
    for i, paper in enumerate(papers[:10], 1):  # 输出前10篇
        print(f"\n[{i}] {paper.title}")
        if paper.abstract:
            abstract = paper.abstract[:400] + "..." if len(paper.abstract) > 400 else paper.abstract
            print(f"    {abstract}")
        if paper.doi:
            print(f"    DOI: {paper.doi}")

    return papers


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="三峡枢纽航运文献调研工具")
    parser.add_argument("--direction", "-d", choices=["1", "2", "3", "all"],
                       default="all", help="调研方向: 1-经济社会效益, 2-节能减排, 3-水运通过能力")
    parser.add_argument("--max", "-m", type=int, default=30, help="每个关键词最大结果数")
    parser.add_argument("--download", action="store_true", help="是否下载PDF")
    parser.add_argument("--list", action="store_true", help="列出所有调研方向")

    args = parser.parse_args()

    # 列出方向
    if args.list:
        print("\n📚 三峡枢纽航运调研方向:\n")
        for key, value in RESEARCH_DIRECTIONS.items():
            print(f"  {key}: {value['name']}")
            print(f"    {value['description']}")
            print()
        return

    # 调研
    directions_to_run = []
    if args.direction == "all":
        directions_to_run = list(RESEARCH_DIRECTIONS.keys())
    else:
        directions_to_run = [f"direction_{args.direction}"]

    for direction_key in directions_to_run:
        try:
            search_direction(direction_key, max_results=args.max, download=args.download)
        except Exception as e:
            logger.error(f"调研 {direction_key} 出错: {e}")
            print(f"❌ 调研失败: {e}")

    print(f"\n✅ 全部调研完成！")
    print(f"📁 文献保存在: {PAPERS_DIR}")


if __name__ == "__main__":
    main()
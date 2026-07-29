from __future__ import annotations

import json
from pathlib import Path

import pytest

from shipping_pipeline.reference_catalog import (
    REFERENCE_OVERRIDE_SCHEMA_VERSION,
    ReferenceCatalogError,
    ReferenceCatalogRunner,
    attach_reference_catalog_to_markdown,
    extract_local_reference,
    format_gbt_reference,
    render_bibtex,
)


def test_extract_article_metadata_and_spaced_doi(tmp_path: Path) -> None:
    markdown = """# 船舶大型化条件下的船闸管理对策

文 / 张义军 梅竞艳

DOI 编码： 1 0<sub>.</sub> 1 3646/j <sub>.</sub>cnki<sub>.</sub>42- 1 395/u<sub>.</sub>20 1 5 <sub>.</sub>07 <sub>.</sub>006

文章编号：1006-7973（2015）07-0026-02
"""
    result = extract_local_reference(
        markdown,
        paper_id="paper-1",
        paper_title="船舶大型化条件下的船闸管理对策",
        workspace_paper_id="workspace-paper-1",
        markdown_path=tmp_path / "document.md",
        source_run_id="run-1",
    )

    assert result["authors"] == ["张义军", "梅竞艳"]
    assert result["doi"] == "10.13646/j.cnki.42-1395/u.2015.07.006"
    assert result["article_number"] == "1006-7973(2015)07-0026-02"
    assert result["year"] == 2015
    assert result["issue"] == "7"
    assert result["pages"] == "26-27"
    assert result["provenance"]["pages"][0]["source_type"] == (
        "deterministic_derivation"
    )


def test_extract_embedded_citation_and_thesis_metadata(tmp_path: Path) -> None:
    article = extract_local_reference(
        """引用本文：刘祖伟，王忠民，潘诚，等. 平台研究[J]. 人民长江，2024，55（增1）：221-225
# 平台研究
刘祖伟，王忠民，潘诚，侯国佼
""",
        paper_id="article",
        paper_title="平台研究",
        workspace_paper_id="article-workspace",
        markdown_path=tmp_path / "article.md",
        source_run_id="brief-1",
    )
    assert article["journal"] == "人民长江"
    assert article["volume"] == "55"
    assert article["issue"] == "S1"
    assert article["pages"] == "221-225"

    thesis = extract_local_reference(
        """(申请工学硕士学位论文)
# 船舶积压条件下三峡过坝运输组织优化研究
研 究 生：殷同乐
2018 年 5 月
学位授予单位 武汉理工大学 学位授予日期 2018年6月
""",
        paper_id="thesis",
        paper_title="船舶积压条件下三峡过坝运输组织优化研究",
        workspace_paper_id="thesis-workspace",
        markdown_path=tmp_path / "thesis.md",
        source_run_id="brief-2",
    )
    assert thesis["entry_type"] == "master_thesis"
    assert thesis["authors"] == ["殷同乐"]
    assert thesis["year"] == 2018
    assert thesis["institution"] == "武汉理工大学"
    assert thesis["degree"] == "硕士学位论文"

    affiliated_author_line = extract_local_reference(
        """# 船型标准化率对三峡船闸实际通过能力的影响
郭子坚，侯坤超，王文渊，彭 云( 大连理工大学 建设工程学部)
文章编号:1002-4972(2017)05-0109-04
""",
        paper_id="affiliated",
        paper_title="船型标准化率对三峡船闸实际通过能力的影响",
        workspace_paper_id="affiliated-workspace",
        markdown_path=tmp_path / "affiliated.md",
        source_run_id="brief-3",
    )
    assert affiliated_author_line["authors"] == [
        "郭子坚",
        "侯坤超",
        "王文渊",
        "彭云",
    ]


def test_runner_keeps_partial_reference_and_writes_audit(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    paper_dir = workspace / "workspace-paper" / "normalized"
    paper_dir.mkdir(parents=True)
    (paper_dir / "document.md").write_text(
        "# 未知年份论文\n\n张三\n",
        encoding="utf-8",
    )
    project = {
        "project_id": "project-1",
        "name": "测试综述",
        "topic": "测试",
        "papers": [
            {
                "paper_id": "paper-1",
                "workspace_paper_id": "workspace-paper",
                "paper_title": "未知年份论文",
            }
        ],
    }
    project_path = tmp_path / "project.json"
    project_path.write_text(
        json.dumps(project, ensure_ascii=False),
        encoding="utf-8",
    )
    synthesis_dir = tmp_path / "synthesis-run"
    (synthesis_dir / "input").mkdir(parents=True)
    (synthesis_dir / "input" / "source_runs.jsonl").write_text(
        json.dumps({"paper_id": "paper-1", "run_id": "brief-1"}) + "\n",
        encoding="utf-8",
    )

    result = ReferenceCatalogRunner(workspace).run(
        project_path=project_path,
        synthesis_run_dir=synthesis_dir,
        run_id="references-1",
    )

    assert result["status"] == "completed_with_gaps"
    assert result["reference_count"] == 1
    assert result["partial_count"] == 1
    audit = Path(result["audit_path"]).read_text(encoding="utf-8")
    assert '"paper_id": "paper-1"' in audit
    assert "journal" in audit
    assert Path(result["review_path"]).is_file()
    assert Path(result["bibtex_path"]).is_file()


def test_override_requires_field_level_source(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    paper_dir = workspace / "workspace-paper" / "normalized"
    paper_dir.mkdir(parents=True)
    (paper_dir / "document.md").write_text(
        "# 论文\n\n张三\n",
        encoding="utf-8",
    )
    project_path = tmp_path / "project.json"
    project_path.write_text(
        json.dumps(
            {
                "project_id": "project",
                "papers": [
                    {
                        "paper_id": "paper",
                        "paper_title": "论文",
                        "workspace_paper_id": "workspace-paper",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    synthesis_dir = tmp_path / "synthesis"
    (synthesis_dir / "input").mkdir(parents=True)
    (synthesis_dir / "input" / "source_runs.jsonl").write_text(
        '{"paper_id":"paper","run_id":"brief"}\n',
        encoding="utf-8",
    )
    override_path = tmp_path / "overrides.json"
    override_path.write_text(
        json.dumps(
            {
                "schema_version": REFERENCE_OVERRIDE_SCHEMA_VERSION,
                "records": [
                    {
                        "paper_id": "paper",
                        "fields": {"journal": "期刊"},
                        "field_sources": {},
                        "sources": [
                            {
                                "source_id": "source",
                                "source_type": "external",
                                "source_name": "来源",
                                "url": "https://example.test",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ReferenceCatalogError, match="没有来源关联"):
        ReferenceCatalogRunner(workspace).run(
            project_path=project_path,
            synthesis_run_dir=synthesis_dir,
            overrides_path=override_path,
            run_id="references-invalid",
        )


def test_renderers_include_partial_marker() -> None:
    reference = {
        "number": 1,
        "reference_id": "ref-abc",
        "paper_id": "paper",
        "entry_type": "article",
        "title": "测试论文",
        "authors": ["张三"],
        "year": 2020,
        "journal": None,
        "volume": None,
        "issue": None,
        "pages": None,
        "doi": None,
        "institution": None,
        "status": "partial",
        "missing_fields": ["journal", "pages", "volume_or_issue"],
    }
    assert "题录待补" in format_gbt_reference(reference)
    bibtex = render_bibtex([reference])
    assert "@article{ref_abc" in bibtex
    assert "题录待补" in bibtex


def test_attach_catalog_numbers_exact_sources_and_appends_cited_only() -> None:
    catalog = {
        "references": [
            {
                "number": 1,
                "reference_id": "ref-a",
                "paper_id": "paper-a",
                "entry_type": "article",
                "title": "论文甲",
                "authors": ["张三"],
                "year": 2020,
                "journal": "测试期刊",
                "volume": None,
                "issue": "1",
                "pages": "1-2",
                "doi": None,
                "status": "complete",
                "missing_fields": [],
            },
            {
                "number": 2,
                "reference_id": "ref-b",
                "paper_id": "paper-b",
                "entry_type": "article",
                "title": "论文乙",
                "authors": ["李四"],
                "year": 2021,
                "journal": "测试期刊",
                "volume": None,
                "issue": "2",
                "pages": "3-4",
                "doi": None,
                "status": "complete",
                "missing_fields": [],
            },
        ]
    }
    rendered, count = attach_reference_catalog_to_markdown(
        "# 草稿\n\n正文。（证据来源：论文乙）\n",
        catalog,
        [
            {"paper_id": "paper-a", "paper_title": "论文甲"},
            {"paper_id": "paper-b", "paper_title": "论文乙"},
        ],
    )

    assert "（证据来源：论文乙[1]）" in rendered
    assert "## 参考文献" in rendered
    assert "[1] 李四." in rendered
    assert "张三." not in rendered
    assert count == 1


def test_attach_catalog_rejects_unmapped_source() -> None:
    with pytest.raises(ReferenceCatalogError, match="无法精确映射"):
        attach_reference_catalog_to_markdown(
            "正文。（证据来源：不存在的论文）",
            {
                "references": [
                    {
                        "number": 1,
                        "paper_id": "paper",
                        "title": "论文",
                    }
                ]
            },
            [{"paper_id": "paper", "paper_title": "论文"}],
        )

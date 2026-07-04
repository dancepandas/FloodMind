"""Tests for ExperienceTree — CRUD, search, seal, merge, archive, bump_hotness."""

import os

import pytest

from floodmind.memory.experience_tree import ExperienceLeaf, ExperienceTree, SummaryNode


@pytest.fixture
def tree(temp_dir):
    return ExperienceTree(persist_dir=temp_dir)


@pytest.fixture
def sample_leaf():
    return ExperienceLeaf(
        node_id="leaf-1",
        experience_id="exp-1",
        path=["水文预报", "敖江流域", "霍口水库"],
        label="霍口水库预报",
        node_type="case",
        task_description="敖江流域霍口水库断面洪水预报",
        domain_keywords=["aojiang", "huokou", "forecast"],
        skill_used="example-skill",
        steps_summary="下载数据; 运行模型; 导出结果",
        pitfalls=["数据文件格式不统一"],
        solutions=["增加格式校验步骤"],
        code_snippets=["validate_format(input_file)"],
        final_outcome="success",
        importance=0.8,
    )


class TestExperienceTreeCRUD:
    def test_add_leaf(self, tree, sample_leaf):
        parent = ["水文预报", "敖江流域"]
        leaf = tree.add_leaf(sample_leaf, parent)
        assert leaf.node_id is not None
        stats = tree.get_stats()
        assert stats["leaf_cases"] >= 1

    def test_find_node(self, tree, sample_leaf):
        tree.add_leaf(sample_leaf, ["水文预报", "敖江流域"])
        found = tree.find_node(["水文预报"])
        assert found is not None
        assert found.label == "水文预报"

    def test_get_leaves(self, tree, sample_leaf):
        tree.add_leaf(sample_leaf, ["水文预报", "敖江流域"])
        domain = tree.find_node(["水文预报"])
        leaves = tree.get_leaves(domain.node_id)
        assert len(leaves) >= 1

    def test_get_all_leaves(self, tree, sample_leaf):
        tree.add_leaf(sample_leaf, ["水文预报", "敖江流域"])
        all_leaves = tree.get_all_leaves()
        assert len(all_leaves) >= 1

    def test_update_leaf(self, tree, sample_leaf):
        tree.add_leaf(sample_leaf, ["水文预报", "敖江流域"])
        tree.update_leaf(sample_leaf.node_id, pitfalls=["新发现: 时间戳格式需标准化"], solutions=["统一为 ISO8601"])
        leaf = tree._leaves.get(sample_leaf.node_id)
        assert "时间戳" in leaf.pitfalls[0]
        assert "ISO8601" in leaf.solutions[0]
        assert leaf.updated_at


class TestExperienceTreeSeal:
    def test_seal_branch(self, tree, sample_leaf):
        for i in range(5):
            leaf = ExperienceLeaf(
                node_id=f"leaf-{i}",
                experience_id=f"exp-{i}",
                path=["水文预报", "敖江流域", f"案例{i}"],
                label=f"案例{i}",
                task_description=f"预报任务{i}",
                domain_keywords=["aojiang"],
                final_outcome="success",
                importance=0.6,
            )
            tree.add_leaf(leaf, ["水文预报", "敖江流域"])

        branches = tree.get_branches_needing_seal(threshold=3)
        assert len(branches) >= 1

        tree.seal_branch(["水文预报", "敖江流域"], "敖江流域预报经验摘要")
        summary = tree.get_summary(["水文预报", "敖江流域"])
        assert summary is not None
        assert "经验" in summary.summary_text


class TestExperienceTreeDedup:
    def test_find_duplicate_groups(self, tree):
        for i in range(3):
            leaf = ExperienceLeaf(
                node_id=f"dup-{i}",
                experience_id=f"exp-{i}",
                path=["通用", "测试"],
                label="相同任务",
                task_description="identical task description for all three",
                domain_keywords=["test"],
                steps_summary="相同的步骤",
                final_outcome="success",
                importance=0.5,
            )
            tree.add_leaf(leaf, ["通用"])

        groups = tree.find_duplicate_groups(similarity_threshold=0.5)
        assert len(groups) >= 1
        assert len(groups[0]) >= 2

    def test_merge_leaves(self, tree):
        for i in range(2):
            leaf = ExperienceLeaf(
                node_id=f"merge-{i}",
                experience_id=f"exp-{i}",
                path=["通用", "待合并"],
                label="任务",
                task_description="similar task",
                domain_keywords=["test"],
                steps_summary="步骤摘要",
                pitfalls=["坑点A"] if i == 0 else ["坑点B"],
                solutions=["方案A"] if i == 0 else ["方案B"],
                final_outcome="success",
                importance=0.5,
            )
            tree.add_leaf(leaf, ["通用"])

        merged = ExperienceLeaf(
            node_id="",
            experience_id="",
            path=["通用", "合并后"],
            label="合并结果",
            task_description="merged task",
            domain_keywords=["test"],
            steps_summary="合并的步骤摘要",
            pitfalls=["坑点A", "坑点B"],
            solutions=["方案A", "方案B"],
            final_outcome="success",
            importance=0.9,
        )
        old_ids = ["merge-0", "merge-1"]
        result = tree.merge_leaves(old_ids, merged, ["通用"])
        assert result is not None
        stats = tree.get_stats()
        assert stats["leaf_cases"] >= 1


class TestExperienceTreeHotness:
    def test_bump_hotness(self, tree, sample_leaf):
        tree.add_leaf(sample_leaf, ["水文预报", "敖江流域"])
        tree.bump_hotness(sample_leaf.node_id)
        leaf = tree._leaves[sample_leaf.node_id]
        assert leaf.hit_count == 1
        assert leaf.last_hit_at

    def test_archive_leaf(self, tree, sample_leaf):
        tree.add_leaf(sample_leaf, ["水文预报", "敖江流域"])
        tree.archive_leaf(sample_leaf.node_id)
        assert sample_leaf.node_id not in tree._leaves
        assert sample_leaf.node_id in tree._archived


class TestExperienceTreePersistence:
    def test_round_trip(self, temp_dir):
        tree = ExperienceTree(persist_dir=temp_dir)
        leaf = ExperienceLeaf(
            node_id="p-1",
            experience_id="ep-1",
            path=["测试", "持久化"],
            label="持久化测试",
            task_description="测试持久化",
            domain_keywords=["test"],
            final_outcome="success",
            importance=0.5,
        )
        tree.add_leaf(leaf, ["测试"])

        tree2 = ExperienceTree(persist_dir=temp_dir)
        stats = tree2.get_stats()
        assert stats["leaf_cases"] >= 1


class TestExperienceTreeFeedback:
    def test_mark_helpful(self, tree, sample_leaf):
        tree.add_leaf(sample_leaf, ["水文预报", "敖江流域"])
        leaf = tree._leaves[sample_leaf.node_id]
        leaf.success_count += 1
        leaf.base_importance = 0.6
        leaf.recompute_importance()
        assert leaf.importance > 0.5
        assert leaf.success_count == 1

    def test_mark_not_helpful(self, tree, sample_leaf):
        tree.add_leaf(sample_leaf, ["水文预报", "敖江流域"])
        leaf = tree._leaves[sample_leaf.node_id]
        leaf.failure_count += 1
        leaf.recompute_importance()
        assert leaf.importance < 0.5
        assert leaf.failure_count == 1

    def test_recompute_importance_balanced(self, tree, sample_leaf):
        tree.add_leaf(sample_leaf, ["水文预报", "敖江流域"])
        leaf = tree._leaves[sample_leaf.node_id]
        leaf.base_importance = 0.5
        leaf.hit_count = 5
        leaf.success_count = 3
        leaf.failure_count = 1
        new_imp = leaf.recompute_importance()
        # 0.4*0.5 + 0.3*0.75 + 0.3*0.25 = 0.2 + 0.225 + 0.075 = 0.5
        assert 0.4 < new_imp < 0.6

    def test_default_feedback_fields(self):
        leaf = ExperienceLeaf(
            node_id="f-1",
            path=["测试"],
            label="测试反馈",
            task_description="test",
        )
        assert leaf.success_count == 0
        assert leaf.failure_count == 0
        assert leaf.base_importance == 0.5

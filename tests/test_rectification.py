import tempfile
import unittest
from pathlib import Path

from exchange import Exchange


class RectificationTraceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Exchange(Path(self.temp.name) / "rect.db")
        self.rect = self.service.rectification
        # 三个原问题：设施、服务、安全各一项
        self.rect.open_case(
            "case-1", "云岭景区", "限期整改通知〔2026〕7号",
            [("facility", "游客中心无障碍坡道缺失"),
             ("service", "投诉受理时限未公示"),
             ("safety", "游船救生衣配备不足")],
            "2026-11-01", "监管一处",
        )
        self.req = {row["seq"]: row["id"] for row in self.rect.requirements("case-1")}

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def _submit_first_round(self):
        return self.rect.submit(
            "b-1", "case-1",
            [(self.req[1], {"measure": "坡道施工中照片", "status": "在建"}),
             (self.req[2], {"measure": "时限公示牌照片"}),
             (self.req[3], {"measure": "救生衣采购单", "count": 20})],
            "2026-11-05", "云岭景区办公室",
        )

    def test_case_and_requirements_record_each_original_issue(self):
        requirements = self.rect.requirements("case-1")
        self.assertEqual([row["seq"] for row in requirements], [1, 2, 3])
        self.assertEqual({row["category"] for row in requirements},
                         {"facility", "service", "safety"})
        with self.assertRaises(KeyError):
            self.rect.requirements("missing-case")

    def test_resubmission_keeps_returned_original_as_immutable_version(self):
        self._submit_first_round()
        # 复核退回设施项 v1，写明原因
        self.rect.decide("rv-1", self.req[1], 1, "复核员甲", "return",
                         "仅有在建照片，无完工验收材料", "2026-11-06")
        # 补交 v2
        self.rect.submit(
            "b-2", "case-1",
            [(self.req[1], {"measure": "坡道完工验收单", "inspector": "第三方"})],
            "2026-11-08", "云岭景区办公室",
        )
        versions = self.rect.material_versions(self.req[1])
        self.assertEqual([v["version"] for v in versions], [1, 2])
        # 被退回的原件仍在，且被标记为已被取代但内容未被改写
        self.assertTrue(versions[0]["superseded"])
        self.assertEqual(versions[0]["payload"], {"measure": "坡道施工中照片", "status": "在建"})
        self.assertFalse(versions[1]["superseded"])
        # 退回记录与审核者判断仍可查回
        trace = self.rect.requirement_trace(self.req[1])
        self.assertEqual(trace["reviews"][0]["decision"], "return")
        self.assertEqual(trace["reviews"][0]["reason"], "仅有在建照片，无完工验收材料")
        self.assertEqual(trace["reviews"][0]["reviewer"], "复核员甲")

    def test_partial_pass_does_not_auto_pass_other_requirements(self):
        self._submit_first_round()
        self.rect.decide("rv-2", self.req[2], 1, "复核员乙", "pass", "公示信息完整", "2026-11-06")
        self.rect.decide("rv-3", self.req[3], 1, "复核员甲", "return", "配备数量仍不达标", "2026-11-06")
        conclusion = self.rect.case_conclusion("case-1")
        states = {t["requirement"]["seq"]: t["state"] for t in conclusion["requirements"]}
        self.assertEqual(states, {1: "pending", 2: "passed", 3: "returned"})
        self.assertFalse(conclusion["publishable"])
        self.assertEqual({gap["seq"] for gap in conclusion["gaps"]}, {1, 3})
        # 没交材料的设施项保持 pending，不能因其他项通过而完成
        self.assertEqual(conclusion["gaps"][0]["state"], "pending")

    def test_revoke_review_reopens_gap_and_blocks_downstream_publication(self):
        self._submit_first_round()
        for seq, review_id, reviewer, decision, reason in [
            (1, "rv-1", "复核员甲", "pass", "坡道验收合格"),
            (2, "rv-2", "复核员乙", "pass", "公示信息完整"),
            (3, "rv-3", "复核员甲", "pass", "救生衣补足并核验"),
        ]:
            self.rect.decide(review_id, self.req[seq], 1, reviewer, decision, reason, "2026-11-06")
        conclusion = self.rect.case_conclusion("case-1")
        self.assertTrue(conclusion["publishable"])
        publication = self.rect.draft_publication("p-1", "case-1", "监管一处", "2026-11-07")
        self.assertEqual(publication["state"], "draft")
        self.assertEqual(len(publication["snapshot"]["requirements"]), 3)

        # 撤销安全项的通过复核
        self.rect.revoke_review("rv-3", "复核组长", "验收单签章存疑，需重新核验", "2026-11-09")
        reopened = self.rect.case_conclusion("case-1")
        self.assertFalse(reopened["publishable"])
        self.assertEqual(reopened["gap_count"], 1)
        self.assertEqual(reopened["gaps"][0]["category"], "safety")
        # 拟发布被阻断
        with self.assertRaises(PermissionError):
            self.rect.draft_publication("p-2", "case-1", "监管一处", "2026-11-09")
        # 已生成的历史快照保留，并能识别为不再被当前结论支持
        records = self.rect.publications("case-1")
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]["currently_valid"])
        # 撤销事件与原因进入审计血缘
        events = [e["action"] for e in self.service.history("rv-3")]
        self.assertEqual(events, ["rect.review.decide", "rect.review.revoke"])

    def test_redecide_after_revoke_can_close_gap_again(self):
        self._submit_first_round()
        self.rect.decide("rv-3", self.req[3], 1, "复核员甲", "return", "数量不足", "2026-11-06")
        self.rect.submit(
            "b-2", "case-1",
            [(self.req[3], {"measure": "救生衣补货单", "count": 60})],
            "2026-11-08", "云岭景区办公室",
        )
        # 针对新版本通过；旧退回决定仍保留
        self.rect.decide("rv-4", self.req[3], 2, "复核员甲", "pass", "补货后数量达标", "2026-11-09")
        trace = self.rect.requirement_trace(self.req[3])
        self.assertEqual(trace["state"], "passed")
        self.assertEqual(trace["basis_review"]["id"], "rv-4")
        self.assertEqual(len(trace["reviews"]), 2)
        self.assertEqual(trace["reviews"][0]["decision"], "return")
        self.assertTrue(trace["has_unreviewed_latest"] is False)

    def test_trace_returns_requirement_evidence_versions_and_judgments_together(self):
        self._submit_first_round()
        self.rect.decide("rv-2", self.req[2], 1, "复核员乙", "pass", "公示信息完整", "2026-11-06")
        trace = self.rect.requirement_trace(self.req[2])
        self.assertEqual(trace["requirement"]["description"], "投诉受理时限未公示")
        self.assertEqual(trace["requirement"]["scenic_area"], "云岭景区")
        self.assertEqual(trace["state"], "passed")
        self.assertEqual(len(trace["materials"][0]["digest"]), 64)
        self.assertEqual(trace["basis_review"]["reviewer"], "复核员乙")
        self.assertEqual(trace["basis_review"]["material_version"], 1)
        # 结论 -> 要求 -> 证据版本 -> 审核判断全链路可查
        self.assertEqual(trace["materials"][0]["payload"]["measure"], "时限公示牌照片")

    def test_newer_unreviewed_material_is_flagged(self):
        self._submit_first_round()
        self.rect.decide("rv-2", self.req[2], 1, "复核员乙", "pass", "公示信息完整", "2026-11-06")
        # 通过后景区又自行补交一版，尚未经复核
        self.rect.submit(
            "b-3", "case-1",
            [(self.req[2], {"measure": "更新后的公示牌"})],
            "2026-11-10", "云岭景区办公室",
        )
        trace = self.rect.requirement_trace(self.req[2])
        self.assertEqual(trace["state"], "passed")
        self.assertTrue(trace["has_unreviewed_latest"])
        self.assertEqual(trace["latest_material_version"], 2)

    def test_invalid_decisions_and_cross_case_materials_rejected(self):
        self._submit_first_round()
        with self.assertRaises(ValueError):
            self.rect.decide("rv-x", self.req[1], 1, "复核员甲", "approve", "无效决定值", "2026-11-06")
        with self.assertRaises(ValueError):
            self.rect.decide("rv-y", self.req[1], 1, "复核员甲", "pass", "  ", "2026-11-06")
        with self.assertRaises(ValueError):
            self.rect.decide("rv-z", self.req[1], 9, "复核员甲", "pass", "版本不存在", "2026-11-06")
        # 另开一案，材料不能挂到别的案件的要求项
        self.rect.open_case("case-2", "松涛景区", "限期整改通知〔2026〕8号",
                            [("safety", "监控盲区")], "2026-11-02", "监管二处")
        foreign = self.rect.requirements("case-2")[0]["id"]
        with self.assertRaises(ValueError):
            self.rect.submit("b-9", "case-1", [(foreign, {"measure": "越界材料"})],
                             "2026-11-05", "外部单位")
        self.rect.decide("rv-ok", self.req[1], 1, "复核员甲", "pass", "先形成有效复核", "2026-11-06")
        with self.assertRaises(ValueError):
            self.rect.revoke_review("rv-ok", "复核组长", "  ", "2026-11-09")
        # 空原因未生效，正式撤销成功
        self.rect.revoke_review("rv-ok", "复核组长", "依据有误", "2026-11-09")
        with self.assertRaises(ValueError):
            self.rect.revoke_review("rv-ok", "复核组长", "重复撤销", "2026-11-10")
        with self.assertRaises(KeyError):
            self.rect.revoke_review("rv-missing", "复核组长", "撤销不存在的复核", "2026-11-10")

    def test_full_closure_then_publish_snapshot_is_immutable_and_valid(self):
        self._submit_first_round()
        for seq, review_id in [(1, "rv-1"), (2, "rv-2"), (3, "rv-3")]:
            self.rect.decide(review_id, self.req[seq], 1, "复核员甲", "pass", "核验通过", "2026-11-06")
        publication = self.rect.draft_publication("p-1", "case-1", "监管一处", "2026-11-07")
        records = self.rect.publications("case-1")
        self.assertTrue(records[0]["currently_valid"])
        self.assertEqual(records[0]["digest"], publication["digest"])
        # 快照内逐项绑定要求、材料版本、审核者与理由
        entry = publication["snapshot"]["requirements"][0]
        self.assertEqual(entry["material_version"], 1)
        self.assertEqual(entry["reviewer"], "复核员甲")
        self.assertEqual(entry["reason"], "核验通过")


if __name__ == "__main__":
    unittest.main()

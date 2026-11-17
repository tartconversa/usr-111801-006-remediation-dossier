import argparse
import json
from .service import Exchange


def run_exchange_demo(service):
    service.catalog.register("source", "资料室", "甲地", "review",
                             [("finding", "服务观察", "text", False)], "2026-11-17")
    service.catalog.ingest("item-1", "source", {"finding": "指引可见"}, "2026-11-17")
    service.grants.issue("g-1", "source", "复核组", "质量复核", "2026-12-31", "2026-11-17")
    service.policies.set("甲地", "review", "质量复核", "allow", "2026-11-17", "资料室")
    revision = service.products.assemble("dossier", "复核资料", ["item-1"], "2026-11-17", "资料室")
    receipt = service.delivery.send("d-1", "dossier", revision, "复核组", "质量复核", "2026-11-17")
    print("资料交付回执：", json.dumps(receipt, ensure_ascii=False, sort_keys=True))


def run_rectification_demo(service):
    rect = service.rectification
    rect.open_case(
        "case-demo", "云岭景区", "限期整改通知〔2026〕7号",
        [("facility", "游客中心无障碍坡道缺失"),
         ("service", "投诉受理时限未公示"),
         ("safety", "游船救生衣配备不足")],
        "2026-11-01", "监管一处",
    )
    req = {row["seq"]: row["id"] for row in rect.requirements("case-demo")}
    rect.submit(
        "b-1", "case-demo",
        [(req[1], {"measure": "坡道施工中照片"}),
         (req[2], {"measure": "时限公示牌照片"}),
         (req[3], {"measure": "救生衣采购单", "count": 20})],
        "2026-11-05", "云岭景区办公室",
    )
    rect.decide("rv-1", req[1], 1, "复核员甲", "return", "仅在建照片，无完工验收材料", "2026-11-06")
    rect.decide("rv-2", req[2], 1, "复核员乙", "pass", "公示信息完整", "2026-11-06")
    rect.decide("rv-3", req[3], 1, "复核员甲", "return", "配备数量仍不达标", "2026-11-06")
    print("首轮结论（局部通过，其余留缺口）：",
          json.dumps(rect.case_conclusion("case-demo")["gaps"], ensure_ascii=False))
    # 补交不抹掉退回原件
    rect.submit("b-2", "case-demo",
                [(req[1], {"measure": "坡道完工验收单"}),
                 (req[3], {"measure": "救生衣补货单", "count": 60})],
                "2026-11-08", "云岭景区办公室")
    rect.decide("rv-4", req[1], 2, "复核员甲", "pass", "验收材料齐全", "2026-11-09")
    rect.decide("rv-5", req[3], 2, "复核员甲", "pass", "补货后数量达标", "2026-11-09")
    print("全项通过可发布：", rect.case_conclusion("case-demo")["publishable"])
    publication = rect.draft_publication("p-1", "case-demo", "监管一处", "2026-11-10")
    # 撤销一次复核，下游拟发布结论必须反映缺口
    rect.revoke_review("rv-5", "复核组长", "验收签章存疑", "2026-11-11")
    print("撤销后缺口：", json.dumps(rect.case_conclusion("case-demo")["gaps"], ensure_ascii=False))
    print("历史发布快照当前有效性：", rect.publications("case-demo")[0]["currently_valid"])
    print("追溯链路示例（安全项材料版本数）：",
          len(rect.requirement_trace(req[3])["materials"]))


def main():
    parser = argparse.ArgumentParser(description="景区资料本地协作演练")
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    service = Exchange(args.database)
    try:
        run_exchange_demo(service)
        run_rectification_demo(service)
    finally:
        service.close()


if __name__ == "__main__":
    main()

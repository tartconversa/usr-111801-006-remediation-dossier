import hashlib
import json

from .audit import write


REVIEW_DECISIONS = {"pass", "return"}
REQUIREMENT_STATES = {"passed", "returned", "pending"}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Rectification:
    """整改要求、提交材料与复核决定的逐项追溯服务。

    材料按要求项形成不可变版本链，复核逐要求项挂在具体材料版本上；
    要求项与案件的结论都由当前有效复核实时推导，撤销复核后结论自动反映缺口。
    """

    def __init__(self, connection):
        self.connection = connection

    # ---- 整改案件与要求项 -------------------------------------------------

    def open_case(self, case_id, scenic_area, notice, requirements, at, actor):
        if not requirements:
            raise ValueError("整改案件至少包含一个要求项")
        if any(not description or not description.strip() for _, description in requirements):
            raise ValueError("整改要求描述不能为空")
        with self.connection:
            self.connection.execute(
                "INSERT INTO rect_cases(id,scenic_area,notice,opened_at) VALUES(?,?,?,?)",
                (case_id, scenic_area, notice, at),
            )
            self.connection.executemany(
                "INSERT INTO rect_requirements(id,case_id,seq,category,description) VALUES(?,?,?,?,?)",
                [
                    (f"{case_id}-r{seq}", case_id, seq, category, description)
                    for seq, (category, description) in enumerate(requirements, start=1)
                ],
            )
            write(
                self.connection, at, actor, "rect.case.open", case_id,
                {"scenic_area": scenic_area, "requirements": len(requirements)},
            )

    def requirements(self, case_id):
        rows = self.connection.execute(
            "SELECT id,seq,category,description FROM rect_requirements WHERE case_id=? ORDER BY seq",
            (case_id,),
        ).fetchall()
        if not rows:
            self._require_case(case_id)
        return [dict(row) for row in rows]

    # ---- 材料提交（不可变版本链，补交不抹掉退回原件） ----------------------

    def submit(self, batch_id, case_id, items, at, actor, note=""):
        """items: [(requirement_id, payload)]，同一批次可覆盖多个要求项。"""
        if not items:
            raise ValueError("提交批次缺少材料")
        requirement_ids = [requirement_id for requirement_id, _ in items]
        if len(set(requirement_ids)) != len(requirement_ids):
            raise ValueError("同一批次内要求项重复提交")
        self._require_case(case_id)
        placeholders = ",".join("?" for _ in requirement_ids)
        owned = {
            row["id"]
            for row in self.connection.execute(
                f"SELECT id FROM rect_requirements WHERE case_id=? AND id IN ({placeholders})",
                [case_id, *requirement_ids],
            ).fetchall()
        }
        if owned != set(requirement_ids):
            raise ValueError("材料不属于该整改案件的要求项")
        created = []
        with self.connection:
            self.connection.execute(
                "INSERT INTO submission_batches(id,case_id,submitted_at,submitter,note) VALUES(?,?,?,?,?)",
                (batch_id, case_id, at, actor, note),
            )
            for requirement_id, payload in items:
                if not isinstance(payload, dict) or not payload:
                    raise ValueError("材料内容不能为空")
                row = self.connection.execute(
                    "SELECT COALESCE(MAX(version),0) AS latest FROM requirement_materials WHERE requirement_id=?",
                    (requirement_id,),
                ).fetchone()
                version = row["latest"] + 1
                material_id = f"{requirement_id}-v{version}"
                canonical = _canonical(payload)
                digest = hashlib.sha256(canonical.encode()).hexdigest()
                if version > 1:
                    self.connection.execute(
                        "UPDATE requirement_materials SET superseded=1 WHERE requirement_id=? AND version=?",
                        (requirement_id, version - 1),
                    )
                self.connection.execute(
                    "INSERT INTO requirement_materials(id,requirement_id,version,batch_id,payload,digest,submitted_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (material_id, requirement_id, version, batch_id, canonical, digest, at),
                )
                created.append({"material": material_id, "requirement": requirement_id, "version": version})
            write(
                self.connection, at, actor, "rect.material.submit", batch_id,
                {"case": case_id, "items": created},
            )
        return created

    def material_versions(self, requirement_id):
        """返回要求项的全部材料版本，退回原件与补交件一并保留。"""
        self._require_requirement(requirement_id)
        rows = self.connection.execute(
            "SELECT id,version,batch_id,payload,digest,submitted_at,superseded "
            "FROM requirement_materials WHERE requirement_id=? ORDER BY version",
            (requirement_id,),
        ).fetchall()
        return [
            {
                **dict(row),
                "payload": json.loads(row["payload"]),
                "superseded": bool(row["superseded"]),
            }
            for row in rows
        ]

    # ---- 逐项复核（局部通过不影响其他要求项） ------------------------------

    def decide(self, review_id, requirement_id, material_version, reviewer, decision, reason, at):
        if decision not in REVIEW_DECISIONS:
            raise ValueError("复核决定无效")
        if not reason or not reason.strip():
            raise ValueError("复核必须写明判断理由")
        self._require_requirement(requirement_id)
        material = self.connection.execute(
            "SELECT id FROM requirement_materials WHERE requirement_id=? AND version=?",
            (requirement_id, material_version),
        ).fetchone()
        if material is None:
            raise ValueError("复核的材料版本不存在")
        with self.connection:
            self.connection.execute(
                "INSERT INTO rect_reviews(id,requirement_id,material_version,reviewer,decision,reason,reviewed_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (review_id, requirement_id, material_version, reviewer, decision, reason, at),
            )
            write(
                self.connection, at, reviewer, "rect.review.decide", review_id,
                {"requirement": requirement_id, "material_version": material_version,
                 "decision": decision, "reason": reason},
            )

    def revoke_review(self, review_id, reviewer, reason, at):
        review = self.connection.execute("SELECT * FROM rect_reviews WHERE id=?", (review_id,)).fetchone()
        if review is None:
            raise KeyError(review_id)
        if review["revoked"]:
            raise ValueError("该复核已撤销")
        if not reason or not reason.strip():
            raise ValueError("撤销必须写明原因")
        with self.connection:
            self.connection.execute(
                "UPDATE rect_reviews SET revoked=1,revoked_at=?,revoke_reason=? WHERE id=?",
                (at, reason, review_id),
            )
            write(
                self.connection, at, reviewer, "rect.review.revoke", review_id,
                {"requirement": review["requirement_id"],
                 "material_version": review["material_version"],
                 "previous_decision": review["decision"], "reason": reason},
            )

    def reviews(self, requirement_id, include_revoked=True):
        self._require_requirement(requirement_id)
        sql = (
            "SELECT id,material_version,reviewer,decision,reason,reviewed_at,revoked,revoked_at,revoke_reason "
            "FROM rect_reviews WHERE requirement_id=? ORDER BY reviewed_at,id"
        )
        rows = self.connection.execute(sql, (requirement_id,)).fetchall()
        reviews = [dict(row) for row in rows if include_revoked or not row["revoked"]]
        for review in reviews:
            review["revoked"] = bool(review["revoked"])
        return reviews

    # ---- 逐项结论与全案结论（实时推导，撤销即反映缺口） --------------------

    def requirement_trace(self, requirement_id):
        """从结论查回要求、全部证据版本与审核者当时的判断。"""
        self._require_requirement(requirement_id)
        requirement = self.connection.execute(
            "SELECT r.*,c.scenic_area FROM rect_requirements r "
            "JOIN rect_cases c ON c.id=r.case_id WHERE r.id=?",
            (requirement_id,),
        ).fetchone()
        materials = self.material_versions(requirement_id)
        reviews = self.reviews(requirement_id)
        active = [review for review in reviews if not review["revoked"]]
        active_pass = next(
            (review for review in reversed(active) if review["decision"] == "pass"), None
        )
        active_return = next(
            (review for review in reversed(active) if review["decision"] == "return"), None
        )
        latest_version = materials[-1]["version"] if materials else 0
        if active_pass is not None:
            state = "passed"
            basis = active_pass
        elif active_return is not None:
            state = "returned"
            basis = active_return
        else:
            state = "pending"
            basis = None
        return {
            "requirement": {
                "id": requirement["id"], "case_id": requirement["case_id"],
                "seq": requirement["seq"], "category": requirement["category"],
                "description": requirement["description"], "scenic_area": requirement["scenic_area"],
            },
            "state": state,
            "basis_review": basis,
            "latest_material_version": latest_version,
            "has_unreviewed_latest": basis is not None and basis["material_version"] < latest_version,
            "materials": materials,
            "reviews": reviews,
        }

    def case_conclusion(self, case_id):
        self._require_case(case_id)
        case = self.connection.execute(
            "SELECT * FROM rect_cases WHERE id=?", (case_id,),
        ).fetchone()
        items = [self.requirement_trace(row["id"]) for row in self.connection.execute(
            "SELECT id FROM rect_requirements WHERE case_id=? ORDER BY seq", (case_id,),
        ).fetchall()]
        gaps = [
            {
                "requirement": trace["requirement"]["id"],
                "seq": trace["requirement"]["seq"],
                "category": trace["requirement"]["category"],
                "state": trace["state"],
                "has_unreviewed_latest": trace["has_unreviewed_latest"],
            }
            for trace in items
            if trace["state"] != "passed"
        ]
        return {
            "case": dict(case),
            "publishable": not gaps,
            "gap_count": len(gaps),
            "gaps": gaps,
            "requirements": items,
        }

    # ---- 拟发布结论（快照留痕，撤销后旧快照可识别缺口） --------------------

    def draft_publication(self, publication_id, case_id, publisher, at):
        conclusion = self.case_conclusion(case_id)
        if not conclusion["publishable"]:
            with self.connection:
                write(
                    self.connection, at, publisher, "rect.publication.blocked", publication_id,
                    {"case": case_id, "gaps": conclusion["gaps"]},
                )
            raise PermissionError(conclusion["gaps"])
        snapshot = self._publication_snapshot(conclusion)
        digest = hashlib.sha256(_canonical(snapshot).encode()).hexdigest()
        with self.connection:
            self.connection.execute(
                "INSERT INTO rect_publications(id,case_id,created_at,publisher,state,snapshot,digest) "
                "VALUES(?,?,?,?,?,?,?)",
                (publication_id, case_id, at, publisher, "draft", _canonical(snapshot), digest),
            )
            write(
                self.connection, at, publisher, "rect.publication.draft", publication_id,
                {"case": case_id, "digest": digest},
            )
        return {"id": publication_id, "case_id": case_id, "state": "draft",
                "snapshot": snapshot, "digest": digest}

    def publications(self, case_id):
        self._require_case(case_id)
        rows = self.connection.execute(
            "SELECT id,created_at,publisher,state,snapshot,digest FROM rect_publications "
            "WHERE case_id=? ORDER BY created_at,id",
            (case_id,),
        ).fetchall()
        result = []
        for row in rows:
            result.append({
                "id": row["id"], "created_at": row["created_at"], "publisher": row["publisher"],
                "state": row["state"], "digest": row["digest"],
                "snapshot": json.loads(row["snapshot"]),
                "currently_valid": self._publication_matches(row["digest"], json.loads(row["snapshot"])),
            })
        return result

    def _publication_snapshot(self, conclusion):
        return {
            "case_id": conclusion["case"]["id"],
            "scenic_area": conclusion["case"]["scenic_area"],
            "notice": conclusion["case"]["notice"],
            "requirements": [
                {
                    "requirement": trace["requirement"]["id"],
                    "seq": trace["requirement"]["seq"],
                    "category": trace["requirement"]["category"],
                    "description": trace["requirement"]["description"],
                    "state": trace["state"],
                    "material_version": trace["basis_review"]["material_version"],
                    "review": trace["basis_review"]["id"],
                    "reviewer": trace["basis_review"]["reviewer"],
                    "reason": trace["basis_review"]["reason"],
                    "reviewed_at": trace["basis_review"]["reviewed_at"],
                }
                for trace in conclusion["requirements"]
            ],
        }

    def _publication_matches(self, digest, snapshot):
        """重算当前结论摘要，判断历史发布快照是否仍被当前证据与复核支持。"""
        current = self.case_conclusion(snapshot["case_id"])
        if not current["publishable"]:
            return False
        return hashlib.sha256(_canonical(self._publication_snapshot(current)).encode()).hexdigest() == digest

    # ---- 内部校验 ----------------------------------------------------------

    def _require_case(self, case_id):
        if self.connection.execute("SELECT 1 FROM rect_cases WHERE id=?", (case_id,)).fetchone() is None:
            raise KeyError(case_id)

    def _require_requirement(self, requirement_id):
        if self.connection.execute(
            "SELECT 1 FROM rect_requirements WHERE id=?", (requirement_id,),
        ).fetchone() is None:
            raise KeyError(requirement_id)

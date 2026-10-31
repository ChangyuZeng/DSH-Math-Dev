"""Deterministic research assessment; no claim of mathematical verification."""

import hashlib

from schema import digest, require, ref


def reference(obj):
    return {"id": obj["id"], "rev": obj["rev"]}


def key(value):
    return value["id"], value["rev"]


class ResearchView:
    def __init__(self, store, *, check_files=True):
        self.store = store
        self.memo = {}
        self.visiting = set()
        self.bad_evidence = { (v["id"], v["rev"]) for v in store.integrity_issues() } if check_files else set()
        self.withdrawn = {e["control"]["review_id"] for e in store.events
                          if e.get("control", {}).get("action") == "review.withdraw"}

    def obj(self, value):
        return self.store.get(value["id"], value["rev"])

    def reviews(self, target):
        return [v[0] for v in self.store.records.values()
                if v[0]["kind"] == "Review" and v[0]["data"]["target"] == target]

    def latest_decision(self, target):
        obj = self.obj(target)
        method = "program" if obj["kind"] == "Obligation" and obj["data"]["check_method"] == "program" else "human"
        return next((r for r in reversed(self.reviews(target)) if r["data"]["method"] == method), None)

    def basis(self, target, candidate=None):
        """Bind full immutable records, transitive premises and their human decisions.

        Association edges (candidate_for and unselected candidates) are not premises.
        Latest review IDs are included even when withdrawn; no fallback to old passes.
        """
        ref(target)
        selected = self.obj(target)
        require(selected["kind"] in ("Note", "Obligation"), "REFERENCE_KIND", "Review target must be a Note or Obligation")
        if candidate is not None:
            ref(candidate)
            require(selected["kind"] == "Obligation" and candidate in selected["data"]["candidates"],
                    "TARGET_MISMATCH", "Selected candidate is not linked to this obligation revision")
        objects, dependencies, evidence, decisions = {}, {}, {}, {}
        visited = set()

        def add(value, dependency=False):
            item = self.obj(value)
            k = key(value)
            objects[k] = {"ref": value, "sha256": digest(item)}
            if dependency:
                dependencies[k] = value
                review = self.latest_decision(value)
                decisions[k] = {"target": value, "review_id": review["id"] if review else None,
                                "withdrawn": bool(review and review["id"] in self.withdrawn)}
            if k in visited:
                return
            visited.add(k)
            data = item["data"]
            if item["kind"] == "Problem" and data["parent"]:
                add(data["parent"])
            if "problem_ref" in data:
                add(data["problem_ref"])
            for e in data.get("evidence", []):
                evidence[key(e)] = e
                add(e)
            for d in data.get("uses", []):
                add(d, True)
            if dependency and item["kind"] == "Obligation":
                review = self.latest_decision(value)
                if review and review["data"]["candidate"]:
                    add(review["data"]["candidate"], True)

        add(target)
        if candidate:
            add(candidate, True)
        return {"target": target, "candidate": candidate,
                "objects": [objects[k] for k in sorted(objects)],
                "dependencies": [dependencies[k] for k in sorted(dependencies)],
                "evidence": [evidence[k] for k in sorted(evidence)],
                "decisions": [decisions[k] for k in sorted(decisions)]}

    def blockers(self, target, candidate=None):
        basis = self.basis(target, candidate)
        reasons = []
        for entry in basis["objects"]:
            value = entry["ref"]
            if self.store.get(value["id"])["rev"] != value["rev"]:
                reasons.append("outdated:" + value["id"])
            if key(value) in self.bad_evidence:
                reasons.append("attachment_integrity:" + value["id"])
        for value in basis["evidence"]:
            evidence = self.obj(value)["data"]
            if evidence["source_status"] == "unverified_source":
                reasons.append("unverified_source:" + value["id"])
            if evidence["citation"] and any(c["assessment"] != "claimed_satisfied" for c in evidence["citation"]["condition_map"]):
                reasons.append("source_condition_gap:" + value["id"])
        for dependency in basis["dependencies"]:
            result = self.assess(dependency)
            if result["state"] not in ("accepted", "closed"):
                reasons.append("premise_not_accepted:" + dependency["id"])
        return sorted(set(reasons))

    def pass_requirements(self, target, candidate):
        obj = self.obj(target)
        data = obj["data"]
        reasons = self.blockers(target, candidate)
        if obj["kind"] == "Note":
            if data["note_type"] != "argument":
                reasons.append("full_argument_required")
            if data["gaps"]:
                reasons.append("unresolved_gaps")
            body_hash = hashlib.sha256(data["body"].encode("utf-8")).hexdigest()
            proof = any(self.obj(e)["data"]["evidence_type"] == "proof_text"
                        and self.obj(e)["data"]["source_status"] == "provided"
                        and any(a["sha256"] == body_hash for a in self.obj(e)["data"]["attachments"])
                        for e in data["evidence"])
            if not proof:
                reasons.append("exact_proof_text_attachment_required")
        else:
            if data["check_method"] != "human":
                reasons.append("program_review_requires_fixed_checker")
            if data["state"] != "candidate" or candidate is None:
                reasons.append("accepted_candidate_required")
        return sorted(set(reasons))

    def preview(self, target, candidate=None):
        basis = self.basis(target, candidate)
        obj = self.obj(target)
        reasons = self.pass_requirements(target, candidate)
        return {"basis": basis, "basis_hash": digest(basis), "eligible_for_pass": not reasons,
                "blockers": reasons,
                "required_scope": obj["data"]["statement" if obj["kind"] == "Note" else "scope"],
                "notice": "Human must read the exact proof, assumptions and completion criteria; this is not a proof checker."}

    def assess(self, target):
        k = key(target)
        if k in self.memo:
            return self.memo[k]
        if k in self.visiting:
            return {"ref": target, "state": "needs_recheck", "reasons": ["acceptance_cycle"], "review_id": None}
        self.visiting.add(k)
        obj = self.obj(target)
        require(obj["kind"] in ("Note", "Obligation"), "REFERENCE_KIND", "Only research claims have acceptance states")
        review = self.latest_decision(target)
        candidate = review["data"]["candidate"] if review else None
        reasons = self.blockers(target, candidate)
        state = "unreviewed" if obj["kind"] == "Note" else obj["data"]["state"]
        if obj["kind"] == "Note" and obj["data"]["note_type"] == "failed_attempt":
            state = "failed_attempt"
        if review:
            if review["data"]["method"] == "program":
                from checking import checker_hash
                if (review["data"]["execution"]["start_event_id"], 1) in self.bad_evidence:
                    reasons.append("checker_source_integrity")
                if review["data"]["execution"]["checker_sha256"] != checker_hash(review["data"]["execution"]["checker_id"]):
                    reasons.append("checker_source_changed")
            if review["id"] in self.withdrawn:
                reasons.append("review_withdrawn")
            if digest(self.basis(target, candidate)) != review["data"]["basis_hash"]:
                reasons.append("review_basis_changed")
            if review["data"]["verdict"] == "pass":
                state = "accepted" if obj["kind"] == "Note" else "closed"
            elif review["data"]["verdict"] == "fail":
                state = "rejected" if obj["kind"] == "Note" else "open"
            else:
                state = "unreviewed" if obj["kind"] == "Note" else "open" if review["data"]["method"] == "program" else "candidate"
        if reasons:
            state = "needs_recheck"
        result = {"ref": target, "state": state, "reasons": sorted(set(reasons)),
                  "review_id": review["id"] if review else None,
                  "review_history": [r["id"] for r in self.reviews(target)]}
        self.visiting.remove(k)
        self.memo[k] = result
        return result

    def snapshot(self):
        states = [self.assess(reference(v[-1])) for v in self.store.records.values()
                  if v[-1]["kind"] in ("Note", "Obligation")]
        routes = {}
        for event in self.store.events:
            control = event.get("control") or {}
            if control.get("action") == "route.update":
                routes[control["route_id"]] = {**control, "rev": control["expected_rev"] + 1,
                                               "event_id": event["event_id"]}
        from checking import attempts
        return {"claims": states, "routes": routes, "checks": attempts(self.store),
                "withdrawn_reviews": sorted(self.withdrawn), "problem_truth": "not_determined_by_software"}

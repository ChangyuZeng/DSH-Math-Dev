"""Readable, relative-link reports with literal rendering of untrusted content."""

import html
import json
import re
from urllib.parse import quote

from domain import ResearchView, reference


def literal(value):
    text = value if type(value) is str else json.dumps(value, ensure_ascii=False, indent=2)
    fence = "`" * max(3, 1 + max((len(x) for x in re.findall(r"`+", text)), default=0))
    return fence + "text\n" + text + "\n" + fence + "\n"


def label(text):
    return html.escape(str(text)).replace("|", "&#124;").replace("\n", " ").replace("[", "&#91;").replace("]", "&#93;")


def anchor(obj):
    return "object-" + obj["id"].encode().hex() + "-" + str(obj["rev"])


def page_for(obj):
    return {"Problem": "problem", "Note": "notes", "Obligation": "obligations", "Evidence": "evidence",
            "Review": "reviews", "Run": "timeline"}[obj["kind"]] + ".md"


def link(store, value):
    obj = store.get(value["id"], value["rev"])
    return f'[{label(obj["id"])}@{obj["rev"]}]({page_for(obj)}#{anchor(obj)})'


def render(store, figures=None):
    figures = figures or {}
    snapshot = store.snapshot()
    view = ResearchView(store)
    seq = snapshot["state"]["seq"]
    pages = {name: f"# {title}\n\n固定提交序号：{seq}。有效状态为该快照的投影；历史 pass 不等于当前仍有效。\n\n"
             for name, title in (("problem.md", "目标与完成条件"), ("notes.md", "研究笔记与失败尝试"),
                                 ("obligations.md", "证明义务"), ("evidence.md", "证据与来源定位"),
                                 ("reviews.md", "审核与程序检查"), ("timeline.md", "提交与干预时间线"))}
    for versions in store.records.values():
        for obj in versions:
            data = obj["data"]
            page = page_for(obj)
            text = f'<a id="{anchor(obj)}"></a>\n\n## {label(obj["id"])}@{obj["rev"]} · {obj["kind"]}\n\n'
            text += f'提交者：{label(obj["actor"]["kind"])} / {label(obj["actor"]["id"])}；时间：{label(obj["created_at"])}。\n\n'
            if obj["kind"] in ("Note", "Obligation"):
                assessment = view.assess(reference(obj))
                text += f'**有效状态：{assessment["state"]}**\n\n' + literal(assessment["reasons"])
            if "problem_ref" in data:
                text += "目标：" + link(store, data["problem_ref"]) + "\n\n"
            for field, title in (("uses", "前提"), ("evidence", "证据"), ("candidates", "候选"),
                                 ("candidate_for", "候选归属"), ("dependencies", "审核依赖")):
                if data.get(field):
                    text += title + "：" + "、".join(link(store, r) for r in data[field]) + "\n\n"
            if obj["kind"] == "Review":
                text += "审核目标：" + link(store, data["target"]) + "\n\n"
                text += f'方法：{data["method"]}；原始 verdict：{data["verdict"]}；撤回：{obj["id"] in view.withdrawn}。\n\n'
            if obj["kind"] == "Evidence":
                text += f'来源状态：{data["source_status"]}；provided 只代表材料已提供，不代表语义已核实。\n\n'
                for a in data["attachments"]:
                    path = "research/" + a["relative_path"]
                    text += f'- [{label(a["logical_name"])}]({quote(path)}) · SHA256 `{a["sha256"]}` · {a["bytes"]} bytes\n'
                    if obj["data"]["evidence_type"] == "figure" and a["sha256"] in figures:
                        text += f'\n![数学示意图，不能替代证明]({figures[a["sha256"]]})\n\n' 
                text += "\n"
                if data["citation"]:
                    text += "### 来源定位与条件映射\n\n" + literal(data["citation"])
                    for condition in data["citation"]["condition_map"]:
                        if condition["obligation"]:
                            text += "条件缺口义务：" + link(store, condition["obligation"]) + "\n\n"
            if obj["kind"] == "Note":
                text += "### 完整正文（按原文保存）\n\n" + literal(data["body"])
            text += "### 完整领域字段\n\n" + literal(data) + "\n"
            pages[page] += text
    pages["timeline.md"] += "## 原始提交与控制事件\n\n"
    for e in store.events:
        action = e["control"].get("action", e["kind"])
        pages["timeline.md"] += f'### seq {e["seq"]} · {action}\n\n' + literal({
            "operation_id": e["operation_id"], "event_id": e["event_id"], "time": e["time"],
            "actor": e["actor"], "origin": e["origin"], "control": e["control"], "receipt": e["result"]}) + "\n"
    pages["timeline.md"] += "## 未完成检查（不自动重跑）\n\n" + literal(snapshot["state"]["research"]["checks"])
    claims = [v[-1] for v in store.records.values() if v[-1]["kind"] in ("Note", "Obligation")]
    # Numeric Mermaid IDs and fixed labels prevent user text from injecting graph syntax.
    nodes = {(o["id"], o["rev"]): "n" + str(i) for i, o in enumerate(claims)}
    graph = ["flowchart LR"]
    for obj in claims:
        node = nodes[(obj["id"], obj["rev"])]
        state = view.assess(reference(obj))["state"]
        graph.append(f'  {node}["{obj["id"]} rev {obj["rev"]}: {state}"]')
    for obj in claims:
        node = nodes[(obj["id"], obj["rev"])]
        for r in obj["data"]["uses"]:
            target = (r["id"], r["rev"])
            if target not in nodes:
                nodes[target] = "h" + str(len(nodes))
                graph.append(f'  {nodes[target]}["{r["id"]} rev {r["rev"]}: historical premise"]')
            graph.append(f'  {nodes[target]} --> {node}')
        review = view.latest_decision(reference(obj))
        if review and review["data"]["candidate"]:
            r = review["data"]["candidate"]
            target = (r["id"], r["rev"])
            if target not in nodes:
                nodes[target] = "h" + str(len(nodes))
                graph.append(f'  {nodes[target]}["{r["id"]} rev {r["rev"]}: historical candidate"]')
            graph.append(f'  {nodes[target]} -. selected candidate .-> {node}')
    graph_text = "\n".join(graph) + "\n"
    pages["dependencies.mmd"] = graph_text
    overview = f"# 数学研究记录 · 离线快照 seq {seq}\n\n"
    overview += "本包只记录已提交的研究对象和检查依据；不声称解决开放问题，不包含私有内部思维链。\n\n"
    overview += "[目标](problem.md) · [笔记](notes.md) · [义务](obligations.md) · [证据与来源](evidence.md) · [审核](reviews.md) · [时间线](timeline.md)\n\n"
    overview += f'事件链末端：`{snapshot["state"]["head_hash"]}`  \n状态 SHA256：`{snapshot["state_sha256"]}`\n\n'
    overview += "## 当前义务与缺口\n\n| 对象 | 状态 | 待复核原因 | 下一步 |\n|---|---|---|---|\n"
    for obj in claims:
        assessment = view.assess(reference(obj))
        if obj["kind"] == "Obligation" or assessment["state"] == "needs_recheck":
            overview += f'| {link(store, reference(obj))} | {assessment["state"]} | {label(", ".join(assessment["reasons"]))} | {label(obj["data"].get("next_action", "审阅当前版本"))} |\n'
    overview += "\n## 依赖图\n\n图仅表示保存的依赖和当前状态。\n\n```mermaid\n" + graph_text + "```\n\n"
    overview += "## 离线核对\n\n在本目录运行，使用 Python 标准库，无需原始 campaign、登录或模型：\n\n"
    overview += "```bash\npython3 -B runtime/verify_export.py .\npython3 -B runtime/cli.py research replay\n```\n\n"
    overview += "首条命令核对清单和附件，并实际复算有确定输出的有限见证检查；不重新执行未完成或执行错误的尝试。源代码来自受信本机，哈希提供一致性校验而不是第三方签名。\n\n"
    overview += "DSH Session、公开流及 provider usage：本离线阶段未采集，不补造。未提交 staging、OAuth/环境文件、无关文件和恢复备份原始残尾不在导出清单；恢复事件元数据仍保留。正文按字节保持，已知凭据模式将阻止导出，但这不是通用脱敏器。\n"
    pages["README.md"] = overview
    return pages

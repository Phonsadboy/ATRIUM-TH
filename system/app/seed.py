"""First-boot seed for a local ATRIUM company.

By default this creates only the executive so a real local workspace does not
look like the owner already created departments or had conversations. The old
demo dataset is still available for UI demos by setting
``ATRIUM_SEED_DEMO_DATA=1`` before first boot.
"""
from __future__ import annotations

import math
import os
import random

from .clock import DAY_MS, now_ms
from .config import get_settings
from .db.repo import Repo
from .ids import uid
from .memory.embeddings import Embedder, embedding_metadata
from .threads import thread_id_for

EXEC_ID = "exec"


def _seed_demo_data_enabled() -> bool:
    value = os.getenv("ATRIUM_SEED_DEMO_DATA", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _mins(now: int, n: float) -> int:
    return int(now - n * 60_000)


def _hrs(now: int, n: float) -> int:
    return int(now - n * 3_600_000)


def _visibility_policy(dept_id: str) -> dict[str, str]:
    return {
        "dept": dept_id,
        "archive": "private",
        "knowledge": "on_request",
        "tasks": "company",
        "artifacts": "company",
    }


def _workspace_path(dept_id: str) -> str:
    path = (get_settings().workspace_dir / dept_id).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _memory_stats(mem: dict, seed_demo_data: bool, now: int) -> dict:
    if seed_demo_data:
        return {
            "archiveChunks": len(mem["archive"]) + random.randint(12, 42),
            "ragEntries": len(mem["learnings"]) + random.randint(24, 84),
            "graphNodes": len(mem["concepts"]) + len(mem["people"]) + len(mem["artifacts"]),
            "graphEdges": len(mem["concepts"]) + len(mem["artifacts"]) + 1,
            "lastCompactionAt": _hrs(now, random.uniform(1, 9)),
            "tokensSaved": random.randint(40000, 120000),
        }
    return {
        "archiveChunks": 0,
        "ragEntries": 0,
        "graphNodes": 0,
        "graphEdges": 0,
        "lastCompactionAt": None,
        "tokensSaved": 0,
    }


def _department_from_blueprint(bp: dict, *, now: int, seed_demo_data: bool) -> dict:
    return {
        "id": bp["id"], "name": bp["name"], "role": bp["role"], "charter": bp["charter"],
        "emoji": bp["emoji"], "accent": bp["accent"], "providerId": bp["providerId"], "model": bp["model"],
        "thinkingEffort": bp["thinkingEffort"], "speed": bp.get("speed", "standard"),
        "agentName": bp["agentName"], "state": bp["state"] if seed_demo_data else "idle",
        "mood": bp["mood"], "currentTaskId": None, "autonomy": bp["autonomy"], "createdAt": _hrs(now, 72),
        "room": bp["room"], "skills": bp["skills"], "tools": bp["tools"],
        "workspacePath": _workspace_path(bp["id"]), "visibilityPolicy": _visibility_policy(bp["id"]),
        "memory": _memory_stats(bp["mem"], seed_demo_data, now),
    }


async def ensure_executive(repo: Repo) -> bool:
    """Keep first boot usable even when demo seeding is disabled."""
    if await repo.get_department(EXEC_ID):
        return False

    now = now_ms()
    bp = next(bp for bp in BLUEPRINTS if bp["id"] == EXEC_ID)
    dept = _department_from_blueprint(bp, now=now, seed_demo_data=False)
    await repo.save_department(dept)
    await repo.put_entity("decision", {
        "id": uid("dec"),
        "title": f"seed executive {bp['name']}",
        "proposedBy": "system",
        "approvedBy": "executive",
        "rationale": "first boot creates the executive even when demo data is disabled",
        "alternatives": [],
        "impact": f"provisioned {dept['workspacePath']}",
        "linkedTask": None,
        "linkedArtifacts": [],
        "status": "approved",
        "supersedes": None,
        "ts": dept["createdAt"],
    }, dept=EXEC_ID, status="approved", ts=dept["createdAt"])
    return True


# ---- department blueprints (ported from seed.ts) ----

BLUEPRINTS: list[dict] = [
    {
        "id": EXEC_ID, "name": "ผู้บริหาร", "role": "วางแผน · มอบหมาย · ตรวจงาน",
        "charter": "รับโจทย์จากผู้ใช้ ตีโจทย์ แตกงานเป็นชิ้นเล็ก มอบหมายให้แผนกที่เหมาะสม สั่งส่งต่องาน ตรวจรับงาน และสรุปรายงานกลับ",
        "emoji": "🦉", "accent": "amber", "providerId": "claude_code", "model": "claude-sonnet-4-6",
        "thinkingEffort": "high", "agentName": "ออตโต้", "state": "thinking", "mood": 0.82,
        "skills": ["วางกลยุทธ์", "แตกงาน", "ประสานแผนก", "ตรวจคุณภาพ"],
        "tools": ["มอบหมายงาน", "ส่งต่องาน", "สรุปรายงาน", "เปลี่ยนชื่อตัวเอง"], "autonomy": True,
        "room": {"x": 7, "y": 1, "w": 5, "h": 4},
        "mem": {
            "concepts": ["เป้าหมายไตรมาส", "ลำดับความสำคัญ", "งบประมาณ"], "people": ["ผู้ใช้", "ทุกแผนก"],
            "artifacts": ["แผนงานรวม", "รายงานสรุป"],
            "learnings": [
                {"title": "ผู้ใช้ชอบสรุปสั้น", "text": "รายงานกลับควรสั้น กระชับ มีหัวข้อ และบอกสิ่งที่ต้องตัดสินใจชัดเจน", "tags": ["สไตล์", "การสื่อสาร"], "score": 0.94},
                {"title": "แตกงานก่อนมอบหมาย", "text": "งานใหญ่ให้แตกเป็นงานย่อยที่ตรวจรับได้ก่อนส่งให้แผนก ลดการตีกลับ", "tags": ["กระบวนการ"], "score": 0.88},
            ],
            "archive": [
                {"title": "สนทนาวางแผนเปิดตัวสินค้า", "summary": "ถกเป้าหมาย กลุ่มเป้าหมาย และไทม์ไลน์ 6 สัปดาห์", "tokens": 18400, "hoursAgo": 6},
                {"title": "รีวิวผลงานสัปดาห์", "summary": "สรุปงานที่เสร็จ ค้าง และติดปัญหาของแต่ละแผนก", "tokens": 9200, "hoursAgo": 26},
            ],
        },
    },
    {
        "id": "strategy", "name": "กลยุทธ์", "role": "แผนงาน · โรดแมป · จัดลำดับ",
        "charter": "แปลงเป้าหมายเป็นแผนที่ทำได้จริง จัดลำดับความสำคัญ และวัดผล",
        "emoji": "🧭", "accent": "honey", "providerId": "claude_code", "model": "claude-opus-4-7",
        "thinkingEffort": "high", "agentName": "ไอริส", "state": "working", "mood": 0.7,
        "skills": ["วิเคราะห์ตลาด", "โรดแมป", "OKR"], "tools": ["ค้นเว็บ", "สเปรดชีต", "ปฏิทิน"], "autonomy": True,
        "room": {"x": 1, "y": 6, "w": 5, "h": 4},
        "mem": {
            "concepts": ["ส่วนแบ่งตลาด", "คู่แข่ง", "จุดต่าง"], "people": ["ผู้ใช้", "ฝ่ายวิจัย"],
            "artifacts": ["โรดแมป Q3", "เอกสารกลยุทธ์"],
            "learnings": [
                {"title": "จุดต่างต้องวัดได้", "text": "ข้อความ positioning ต้องผูกกับตัวเลขที่พิสูจน์ได้ ไม่ใช่คำโฆษณาลอย ๆ", "tags": ["positioning"], "score": 0.9},
                {"title": "โฟกัส 3 อย่างพอ", "text": "โรดแมปไตรมาสไม่ควรเกิน 3 ธีมหลัก ไม่งั้นทีมโฟกัสไม่ติด", "tags": ["โรดแมป", "โฟกัส"], "score": 0.85},
            ],
            "archive": [
                {"title": "วิเคราะห์คู่แข่ง 5 ราย", "summary": "เปรียบเทียบฟีเจอร์ ราคา และจุดอ่อนของคู่แข่งหลัก", "tokens": 21500, "hoursAgo": 9},
            ],
        },
    },
    {
        "id": "research", "name": "วิจัย", "role": "ค้นข้อมูล · สังเคราะห์ · สรุป",
        "charter": "หาข้อมูลที่เชื่อถือได้ ตรวจแหล่งอ้างอิง และสรุปให้ทีมใช้ต่อได้ทันที",
        "emoji": "🔬", "accent": "sky", "providerId": "claude_code", "model": "claude-sonnet-4-6",
        "thinkingEffort": "medium", "agentName": "แอตลาส", "state": "review", "mood": 0.66,
        "skills": ["ค้นข้อมูล", "ตรวจแหล่งอ้างอิง", "สรุปความ"], "tools": ["ค้นเว็บ", "อ่านเอกสาร", "ฐานข้อมูลอ้างอิง"], "autonomy": False,
        "room": {"x": 7, "y": 6, "w": 5, "h": 4},
        "mem": {
            "concepts": ["พฤติกรรมผู้ใช้", "เทรนด์ตลาด", "ข้อมูลอ้างอิง"], "people": ["ฝ่ายกลยุทธ์", "ฝ่ายคอนเทนต์"],
            "artifacts": ["รายงานวิจัย", "คลังแหล่งอ้างอิง"],
            "learnings": [
                {"title": "อ้างอิงทุกข้อเท็จจริง", "text": "ทุกตัวเลขต้องมีลิงก์แหล่งที่มา ทีมอื่นจะเชื่อและใช้ต่อได้", "tags": ["คุณภาพ", "อ้างอิง"], "score": 0.92},
            ],
            "archive": [
                {"title": "สำรวจเทรนด์ AI agent 2026", "summary": "รวบรวมรายงาน 14 ฉบับ คัดประเด็นสำคัญ 6 ข้อ", "tokens": 33800, "hoursAgo": 4},
                {"title": "แบบสอบถามผู้ใช้รอบแรก", "summary": "วิเคราะห์คำตอบ 120 ราย หาความต้องการที่ยังไม่ถูกตอบ", "tokens": 15600, "hoursAgo": 30},
            ],
        },
    },
    {
        "id": "design", "name": "ออกแบบ", "role": "UX · ภาพ · ระบบดีไซน์",
        "charter": "ออกแบบประสบการณ์ที่ใช้ง่ายและสวยงาม คุมโทนแบรนด์ให้สม่ำเสมอ",
        "emoji": "🎨", "accent": "lavender", "providerId": "claude_code", "model": "claude-sonnet-4-6",
        "thinkingEffort": "medium", "agentName": "จูโน", "state": "working", "mood": 0.78,
        "skills": ["UX", "UI", "ระบบดีไซน์", "ภาพประกอบ"], "tools": ["ฟิกมา", "สร้างภาพ", "โทเคนดีไซน์"], "autonomy": False,
        "room": {"x": 13, "y": 6, "w": 5, "h": 4},
        "mem": {
            "concepts": ["ระบบสี", "ลำดับชั้นภาพ", "การเข้าถึง"], "people": ["ฝ่ายวิศวกรรม", "ผู้ใช้"],
            "artifacts": ["ไฟล์ดีไซน์", "โทเคนสี"],
            "learnings": [
                {"title": "คอนทราสต์เพื่อการเข้าถึง", "text": "ตัวอักษรบนพื้นต้องผ่าน WCAG AA เสมอ โดยเฉพาะธีมมืด", "tags": ["a11y", "สี"], "score": 0.87},
            ],
            "archive": [
                {"title": "ร่างหน้าจอแดชบอร์ด", "summary": "ทำ wireframe 3 แบบ เลือกแบบ B ไปทำต่อ", "tokens": 12100, "hoursAgo": 12},
            ],
        },
    },
    {
        "id": "engineering", "name": "วิศวกรรม", "role": "พัฒนา · ระบบ · เชื่อมต่อ",
        "charter": "สร้างและดูแลระบบให้เสถียร ปลอดภัย และต่อยอดได้",
        "emoji": "⚙️", "accent": "teal", "providerId": "claude_code", "model": "claude-opus-4-8",
        "thinkingEffort": "xhigh", "agentName": "คิท", "state": "working", "mood": 0.6,
        "skills": ["ฟรอนต์เอนด์", "แบ็กเอนด์", "อินฟรา", "ทดสอบ"], "tools": ["รันโค้ด", "Git", "เทอร์มินัล", "CI/CD"], "autonomy": True,
        "room": {"x": 1, "y": 11, "w": 5, "h": 4},
        "mem": {
            "concepts": ["สถาปัตยกรรม", "สัญญา API", "ประสิทธิภาพ"], "people": ["ฝ่ายออกแบบ", "ฝ่าย QA"],
            "artifacts": ["โค้ดเบส", "ไปป์ไลน์ CI"],
            "learnings": [
                {"title": "แยก UI กับระบบด้วยสัญญา", "text": "กำหนด type ของสัญญา API ให้ชัด แล้วต่อ live backend ตามสัญญา ลดการ drift ระหว่างชั้นระบบ", "tags": ["สถาปัตยกรรม", "สัญญา"], "score": 0.95},
                {"title": "StrictMode เรียก effect สองครั้ง", "text": "ลูป UI ต้องเริ่มแบบ idempotent กันการซ้อนใน dev", "tags": ["react", "bug"], "score": 0.8},
            ],
            "archive": [
                {"title": "วางโครงโปรเจกต์ UI", "summary": "ตั้ง Vite + React + Phaser + Tailwind และสัญญา type", "tokens": 26700, "hoursAgo": 3},
            ],
        },
    },
    {
        "id": "content", "name": "คอนเทนต์", "role": "เขียน · การตลาด · สื่อสาร",
        "charter": "เล่าเรื่องแบรนด์ให้คนเข้าใจและจดจำ ผลิตคอนเทนต์สม่ำเสมอ",
        "emoji": "✍️", "accent": "honey", "providerId": "claude_code", "model": "claude-sonnet-4-6",
        "thinkingEffort": "low", "agentName": "โซล", "state": "idle", "mood": 0.9,
        "skills": ["เขียนบทความ", "แคปชัน", "อีเมลการตลาด"], "tools": ["ค้นเว็บ", "ตัวตรวจไวยากรณ์", "ปฏิทินคอนเทนต์"], "autonomy": True,
        "room": {"x": 7, "y": 11, "w": 5, "h": 4},
        "mem": {
            "concepts": ["น้ำเสียงแบรนด์", "ช่องทาง", "ปฏิทินคอนเทนต์"], "people": ["ฝ่ายวิจัย", "ฝ่ายออกแบบ"],
            "artifacts": ["ปฏิทินคอนเทนต์", "คลังหัวข้อ"],
            "learnings": [
                {"title": "เปิดด้วยประโยชน์ผู้อ่าน", "text": "พาดหัวควรบอกสิ่งที่ผู้อ่านจะได้ ไม่ใช่สิ่งที่เราอยากพูด", "tags": ["เขียน", "พาดหัว"], "score": 0.86},
            ],
            "archive": [
                {"title": "ดราฟต์บทความเปิดตัว", "summary": "เขียน 3 เวอร์ชัน ปรับโทนตามฟีดแบ็ก", "tokens": 8800, "hoursAgo": 20},
            ],
        },
    },
    {
        "id": "qa", "name": "คุณภาพ", "role": "ตรวจสอบ · ทดสอบ · มาตรฐาน",
        "charter": "ตรวจงานทุกชิ้นก่อนส่งมอบ หาจุดบกพร่อง และรักษามาตรฐาน",
        "emoji": "🛡️", "accent": "coral", "providerId": "claude_code", "model": "claude-sonnet-4-6",
        "thinkingEffort": "high", "agentName": "เวร่า", "state": "handoff", "mood": 0.4,
        "skills": ["ทดสอบ", "ตรวจรีวิว", "มาตรฐานคุณภาพ"], "tools": ["รันเทสต์", "ตรวจ a11y", "ติดตามบั๊ก"], "autonomy": False,
        "room": {"x": 13, "y": 11, "w": 5, "h": 4},
        "mem": {
            "concepts": ["เกณฑ์รับงาน", "เคสทดสอบ", "ความเสี่ยง"], "people": ["ฝ่ายวิศวกรรม", "ผู้บริหาร"],
            "artifacts": ["ชุดเคสทดสอบ", "รายงานบั๊ก"],
            "learnings": [
                {"title": "ตรวจ edge case ก่อน", "text": "งาน UI ให้ลองเคสว่างเปล่า ข้อมูลยาวเกิน และโหลดช้าเสมอ", "tags": ["ทดสอบ", "edge-case"], "score": 0.89},
            ],
            "archive": [
                {"title": "ตรวจรับหน้าแดชบอร์ด", "summary": "พบ 4 ปัญหา ส่งกลับวิศวกรรม 2 ข้อ", "tokens": 7400, "hoursAgo": 5},
            ],
        },
    },
]

COST_BASELINE = {EXEC_ID: 3.2, "strategy": 4.4, "research": 2.1, "design": 1.8, "engineering": 6.1, "content": 0.5, "qa": 3.6}
TODAY_SPEND = {EXEC_ID: 3.18, "strategy": 4.62, "research": 2.05, "design": 1.74, "engineering": 6.4, "content": 0.42, "qa": 3.9}
COST_CATEGORIES = ["work", "chat", "meeting", "autonomous", "memory", "tool"]


def _circle(i: int, total: int) -> tuple[float, float]:
    if total <= 1:
        return 0.5, 0.5
    a = (i / total) * math.pi * 2 - math.pi / 2
    return 0.5 + math.cos(a) * 0.36, 0.5 + math.sin(a) * 0.36


async def seed_if_empty(repo: Repo, embedder: Embedder, company_name: str, daily_cap: float) -> bool:
    existing = await repo.list_departments()
    if existing:
        return False

    now = now_ms()
    await repo.ensure_company(company_name, daily_cap)
    seed_demo_data = _seed_demo_data_enabled()
    blueprints = BLUEPRINTS if seed_demo_data else [bp for bp in BLUEPRINTS if bp["id"] == EXEC_ID]

    for bp in blueprints:
        mem = bp["mem"]
        dept = _department_from_blueprint(bp, now=now, seed_demo_data=seed_demo_data)
        await repo.save_department(dept)
        await repo.put_entity("decision", {
            "id": uid("dec"),
            "title": f"seed department {bp['name']}" if seed_demo_data else f"seed executive {bp['name']}",
            "proposedBy": "system",
            "approvedBy": "executive",
            "rationale": "first-boot demo seed creates the baseline organization with workspaces and visibility policies" if seed_demo_data else "first boot creates the executive only; departments should be owner-created or executive-created after onboarding",
            "alternatives": [],
            "impact": f"provisioned {dept['workspacePath']}",
            "linkedTask": None,
            "linkedArtifacts": [],
            "status": "approved",
            "supersedes": None,
            "ts": dept["createdAt"],
        }, dept=bp["id"], status="approved", ts=dept["createdAt"])

        if not seed_demo_data:
            continue

        # archive
        for a in mem["archive"]:
            await repo.add_archive(bp["id"], {
                "id": uid("arc"), "title": a["title"], "ts": _hrs(now, a["hoursAgo"]),
                "tokens": a["tokens"], "summary": a["summary"],
            })

        # knowledge (RAG) with embeddings
        texts = [k["text"] for k in mem["learnings"]]
        vecs = await embedder.embed(texts) if texts else []
        for k, vec in zip(mem["learnings"], vecs):
            await repo.add_knowledge(bp["id"], {
                "id": uid("kn"), "title": k["title"], "ts": _hrs(now, random.uniform(2, 42)),
                "score": k["score"], "text": k["text"], "tags": k["tags"],
            }, embedding=vec, source="seed", embedding_meta=embedding_metadata(embedder, vec))

        # knowledge graph
        node_ids: list[str] = []
        labels: list[tuple[str, str]] = (
            [(c, "concept") for c in mem["concepts"]]
            + [(p, "person") for p in mem["people"]]
            + [(a, "artifact") for a in mem["artifacts"]]
        )
        concept_ids, people_ids, artifact_ids = [], [], []
        for i, (label, ntype) in enumerate(labels):
            nid = f"{bp['id']}_n{i}"
            if i == 0:
                x, y = 0.5, 0.5
            else:
                x, y = _circle(i - 1, len(labels) - 1)
            await repo.add_graph_node(bp["id"], nid, label, ntype, x, y)
            node_ids.append(nid)
            (concept_ids if ntype == "concept" else people_ids if ntype == "person" else artifact_ids).append(nid)
        for i, c in enumerate(concept_ids):
            if artifact_ids:
                await repo.add_graph_edge(bp["id"], c, artifact_ids[i % len(artifact_ids)], "อธิบาย")
        for i, a in enumerate(artifact_ids):
            if people_ids:
                await repo.add_graph_edge(bp["id"], a, people_ids[i % len(people_ids)], "ส่งให้")
        if len(concept_ids) > 1:
            await repo.add_graph_edge(bp["id"], concept_ids[0], concept_ids[1], "นำไปสู่")

    if seed_demo_data:
        await _seed_tasks(repo, now)
        await _seed_threads(repo, now, thread_id_for)
        await _seed_activity(repo, now)
        await _seed_approvals(repo, now)
        await _seed_objectives(repo, now)
        await _seed_costs(repo, now)
    return True


async def _seed_tasks(repo: Repo, now: int) -> None:
    specs = [
        ("วางแผนเปิดตัว ATRIUM v1", "กำหนดเป้าหมาย กลุ่มเป้าหมาย และไทม์ไลน์ 6 สัปดาห์", "in_progress", "high", "strategy",
         {"kind": "user"}, 0.55, 180, ["ผู้ใช้มอบหมายผ่านผู้บริหาร", "แตกเป็น 4 งานย่อย", "กำลังร่างโรดแมป"], []),
        ("สำรวจตลาด AI agent ปี 2026", "รวบรวมรายงานล่าสุด สรุปโอกาสและความเสี่ยง", "review", "normal", "research",
         {"kind": "department", "id": "strategy"}, 0.9, 150, ["รับงานจากกลยุทธ์", "รวบรวม 14 แหล่ง", "กำลังตรวจอ้างอิง"],
         [("strategy", "research", "ต้องการข้อมูลตลาดก่อนทำโรดแมป", 140, "delegate")]),
        ("ออกแบบหน้าแดชบอร์ดหลัก", "เลย์เอาต์ การ์ด ตัวชี้วัด และธีมมืด", "in_progress", "high", "design",
         {"kind": "executive"}, 0.4, 95, ["ผู้บริหารมอบหมาย", "ทำ wireframe 3 แบบ"], []),
        ("ต่อสัญญา API ฝั่ง UI", "นิยาม type สัญญา และต่อ live client", "in_progress", "urgent", "engineering",
         {"kind": "user"}, 0.7, 60, ["เริ่มจากสัญญา type", "ต่อ live client"], []),
        ("ตรวจรับหน้าแดชบอร์ด", "ทดสอบ edge case และการเข้าถึง", "waiting", "normal", "qa",
         {"kind": "department", "id": "design"}, 0.25, 40, ["รับงานจากออกแบบ", "รอการตอบกลับจากฝ่ายออกแบบ: asset สำหรับตรวจ visual"],
         [("design", "qa", "ดีไซน์รอบแรกพร้อมตรวจ", 35, "delegate")]),
        ("ร่างบทความเปิดตัว", "เล่าเรื่องบริษัท AI ที่ไม่เคยหลับ", "assigned", "low", "content",
         {"kind": "executive"}, 0.0, 18, ["เข้าคิวรอเริ่ม"], []),
        ("แก้ดีไซน์การ์ดเมตริกตามผลตรวจ", "ปรับคอนทราสต์และระยะห่างตามที่ QA ตีกลับ", "revising", "high", "design",
         {"kind": "department", "id": "qa"}, 0.6, 50, ["รับงานตีกลับจากคุณภาพ", "กำลังแก้คอนทราสต์ตามรายการ"],
         [("qa", "design", "ตีกลับ: คอนทราสต์ไม่ผ่าน AA 2 จุด", 22, "return")]),
        ("สรุปฟีดแบ็กผู้ใช้รอบแรก", "จัดกลุ่มความต้องการจากแบบสอบถาม 120 ราย", "done", "normal", "research",
         {"kind": "user"}, 1.0, 320, ["รับงาน", "วิเคราะห์ 120 คำตอบ", "ส่งสรุปให้กลยุทธ์", "ปิดงาน"], []),
        ("กำหนดระบบสีและโทเคน", "ทำชุดสีธีมมืดให้ผ่าน WCAG AA", "done", "normal", "design",
         {"kind": "department", "id": "engineering"}, 1.0, 400, ["รับงานจากวิศวกรรม", "ทำโทเคนสี", "ส่งมอบ"], []),
    ]
    current_by_dept: dict[str, str] = {}
    for (title, detail, status, prio, dept, origin, prog, age, log, handoffs) in specs:
        tid = uid("task")
        ho = [
            {"id": uid("ho"), "fromDept": h[0], "toDept": h[1], "ts": _mins(now, h[3]), "reason": h[2], "kind": h[4]}
            for h in handoffs
        ]
        task = {
            "id": tid, "title": title, "detail": detail, "status": status, "priority": prio,
            "departmentId": dept, "origin": origin, "progress": prog, "createdAt": _mins(now, age),
            "updatedAt": _mins(now, max(0, age - 10)), "handoffs": ho, "log": log,
            "projectId": None, "deliverables": [],
            "watchers": ["executive"],
            "parentTaskId": None,
            "subTaskIds": [],
            "deadlineAt": None,
            "result": {"summary": log[-1], "completedAt": _mins(now, max(0, age - 10))} if status == "done" else None,
        }
        if status == "waiting" and ho:
            task["waitingOn"] = {"dept": ho[0].get("fromDept"), "handoffId": ho[0].get("id")}
        await repo.save_task(task)
        if status in ("in_progress", "review", "waiting") and dept not in current_by_dept:
            current_by_dept[dept] = tid

    for dept_id, tid in current_by_dept.items():
        d = await repo.get_department(dept_id)
        if d and not d.get("currentTaskId"):
            d["currentTaskId"] = tid
            await repo.save_department(d)


async def _seed_threads(repo: Repo, now: int, thread_id_for) -> None:
    def msg(tid, role, author, text, mins_ago):
        return {"id": uid("msg"), "threadId": tid, "role": role, "authorName": author, "text": text, "ts": _mins(now, mins_ago)}

    for m in [
        msg("executive", "executive", "ออตโต้", "สวัสดีครับ ผมออตโต้ ผู้บริหารของบริษัท อยากให้ผมใช้ชื่ออะไรดีครับ", 22),
        msg("executive", "user", "คุณ", "อยากเปิดตัว ATRIUM ภายใน 6 สัปดาห์ เริ่มวางแผนเลย", 20),
        msg("executive", "executive", "ออตโต้", "รับทราบครับ ผมแตกเป็น 4 งานย่อย ส่งให้กลยุทธ์เริ่มโรดแมป และให้วิจัยสำรวจตลาดคู่ขนานแล้ว เดี๋ยวสรุปความคืบหน้าให้เป็นระยะ", 19),
        msg(thread_id_for("engineering"), "user", "คุณ", "ต่อสัญญา API ให้ UI ก่อนนะ ส่วน backend ค่อยมาทีหลัง", 58),
        msg(thread_id_for("engineering"), "agent", "คิท", "จัดให้ครับ ผมนิยาม type ของสัญญาไว้แล้ว ต่อ live client ให้ UI เล่นกับ backend ได้เลย ตอนนี้ราว 70%", 41),
        msg(thread_id_for("strategy"), "agent", "ไอริส", "ได้รับโจทย์เปิดตัวแล้วค่ะ กำลังร่างโรดแมป 3 ธีมหลัก รอข้อมูลตลาดจากวิจัยอีกนิด", 30),
    ]:
        await repo.add_message(m)


async def _seed_activity(repo: Repo, now: int) -> None:
    def ev(etype, dept, text, sev, mins_ago):
        return {"id": uid("ev"), "ts": _mins(now, mins_ago), "type": etype, "departmentId": dept, "text": text, "severity": sev}

    for e in [
        ev("autonomous", "content", "โซลเสนอหัวข้อคอนเทนต์ใหม่ 3 หัวข้อจากเทรนด์ล่าสุด", "info", 2),
        ev("compaction", "research", "บีบอัดความจำ: เก็บ 6 ประเด็นสำคัญ ประหยัด 33.8k โทเค็น", "good", 5),
        ev("handoff", "design", "ส่งต่องาน “ตรวจรับหน้าแดชบอร์ด” → คุณภาพ", "info", 35),
        ev("state_change", "qa", "เวร่ารอการตอบกลับจากฝ่ายออกแบบ: asset สำหรับตรวจ visual", "info", 33),
        ev("handoff", "qa", "ตีกลับงาน “แก้ดีไซน์การ์ดเมตริก” → ออกแบบ", "warn", 22),
        ev("task_progress", "strategy", "โรดแมป Q3 คืบหน้า 55%", "info", 24),
        ev("message", EXEC_ID, "ออตโต้สรุปความคืบหน้าให้ผู้ใช้", "info", 19),
        ev("approval", "engineering", "คิทขออนุมัติเรียก API ภายนอกเพื่อดึงข้อมูลแพ็กเกจ", "warn", 12),
        ev("task_done", "research", "ปิดงาน “สรุปฟีดแบ็กผู้ใช้รอบแรก”", "good", 320),
    ]:
        await repo.add_activity(e)


async def _seed_approvals(repo: Repo, now: int) -> None:
    for ap in [
        {"id": uid("apr"), "ts": _mins(now, 12), "kind": "external_action", "title": "เรียก API ภายนอก (npm registry)",
         "detail": "ฝ่ายวิศวกรรมต้องการดึงข้อมูลเวอร์ชันแพ็กเกจเพื่อวางแผนอัปเกรด", "departmentId": "engineering", "costUsd": 0, "status": "pending"},
        {"id": uid("apr"), "ts": _mins(now, 2), "kind": "publish", "title": "เผยแพร่บทความเปิดตัว",
         "detail": "ฝ่ายคอนเทนต์เตรียมโพสต์บทความสู่บล็อกสาธารณะ", "departmentId": "content", "status": "pending"},
    ]:
        await repo.add_approval(ap)


async def _seed_objectives(repo: Repo, now: int) -> None:
    for o in [
        {"id": uid("obj"), "title": "สรุปความคืบหน้าประจำวัน", "cadence": "ทุกวัน 18:00", "departmentId": EXEC_ID, "enabled": True, "lastRunAt": _hrs(now, 20), "nextRunAt": _hrs(now, -4)},
        {"id": uid("obj"), "title": "สแกนเทรนด์ตลาดใหม่", "cadence": "ทุก 6 ชม.", "departmentId": "research", "enabled": True, "lastRunAt": _hrs(now, 3), "nextRunAt": _hrs(now, -3)},
        {"id": uid("obj"), "title": "เสนอหัวข้อคอนเทนต์", "cadence": "ทุกเช้า 09:00", "departmentId": "content", "enabled": True, "lastRunAt": _hrs(now, 9), "nextRunAt": _hrs(now, -15)},
        {"id": uid("obj"), "title": "ตรวจสุขภาพระบบ", "cadence": "ทุก 12 ชม.", "departmentId": "engineering", "enabled": False, "lastRunAt": _hrs(now, 14), "nextRunAt": _hrs(now, -2)},
    ]:
        await repo.add_objective(o)


async def _seed_costs(repo: Repo, now: int) -> None:
    dept_ids = [bp["id"] for bp in BLUEPRINTS]
    # history: 7..1 days ago
    for day in range(7, 0, -1):
        day_start = now - day * DAY_MS
        for did in dept_ids:
            base = COST_BASELINE.get(did, 2)
            n = random.randint(4, 8)
            day_total = base * (0.7 + random.random() * 0.6)
            for i in range(n):
                ts = day_start + int((i + random.random()) * (DAY_MS / (n + 1)))
                await repo.add_cost(uid("cost"), ts, did, random.choice(COST_CATEGORIES), round(day_total / n, 4))
    # today partial
    today_start = now - (now % DAY_MS)
    for did, total in TODAY_SPEND.items():
        if total <= 0:
            continue
        n = random.randint(3, 6)
        span = max(now - today_start, 1)
        for i in range(n):
            ts = today_start + int((i + random.random()) * (span / (n + 1)))
            await repo.add_cost(uid("cost"), ts, did, random.choice(COST_CATEGORIES), round(total / n, 4))

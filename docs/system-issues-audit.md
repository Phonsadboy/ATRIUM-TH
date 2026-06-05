# เอกสารตรวจสอบปัญหาระบบ ATRIUM — ฉบับตรวจสอบซ้ำ (System Issues Audit v2)

> วิธีตรวจสอบ: re-audit แบบหลาย agent + การยืนยันเชิงปฏิปักษ์ (adversarial verification) ทุกข้อค้นพบถูกเปิดโค้ดจริงตรวจซ้ำ — บางข้อใน v1 ถูก **หักล้าง/ลดระดับ** และพบปัญหาใหม่จำนวนมาก
> ขอบเขต: `system/app/` (backend), `ui/src/` (เฉพาะ flow ส่งงาน), `system/app/provider/`, `system/app/memory/`, `db/`
> สถานะอ้างอิง: branch `main` (commit `7cb2fd0`)
> เครื่องหมายสถานะ: ✅ ยืนยันแล้ว · ✏️ แก้จาก v1 · ❌ หักล้าง (ลบจากความเสี่ยง)
>
> สถาปัตยกรรมพื้นฐานที่ยืนยันแล้ว (สำคัญต่อการอ่านเอกสารนี้):
> - การเขียน DB หลายครั้งในหนึ่ง engine tick / หนึ่ง API request **รวมอยู่ใน `session_scope()` เดียว** ที่ commit ทีเดียวตอนจบ หรือ rollback ทั้งหมดถ้า exception (`db/base.py:179-188`) → งานหลายสเต็ป **เป็น atomic** มากกว่าที่ v1 เข้าใจ
> - แต่ engine เรียก `commit_and_release()` เพื่อ commit งานบางส่วน "กลางคัน" เพื่อคืน DB connection ระหว่างรอ provider (`engine.py:999, 1048, 1098, 1190`) → เฉพาะ job kind ที่ commit กลางคันเท่านั้นที่เสี่ยง partial state
> - Job เป็น durable + **at-least-once**: กู้งานค้าง `running` ตอน boot และมี in-flight reaper ระหว่างรันแล้ว; scheduled objective/trigger side effects มี deterministic idempotency แล้ว; retry ไม่มี hard cap/dead-letter ตาม Full Autonomy intent แต่มี high-attempt visibility และ timeout recovery/requeue ใน runtime แล้ว

---

## 0. บทสรุปผู้บริหาร

ATRIUM แข็งแรงในเรื่อง atomicity ของการเขียน DB (session เดียว/commit เดียว), การ dedup inbound, content-addressed object store, และ id ที่กันชนด้วย CSPRNG แต่การตรวจซ้ำพบ **ปัญหาที่ยืนยันแล้วมากกว่า 40 ข้อ** กระจายตั้งแต่บั๊กตรรกะที่ active อยู่จริง ไปจนถึงความเปราะบางเชิงปฏิบัติการและช่องโหว่ความปลอดภัยที่สำคัญเมื่อ agent ถูก prompt-injection

**Top 12 ที่ควรแก้ก่อน (ทั้งหมดยืนยันด้วยโค้ดจริง):**

| # | ปัญหา | ไฟล์:บรรทัด | ระดับ |
|---|------|------------|------|
| H1 | **แก้แล้วใน worktree:** Cron `*/N` ใน 5-field cron ใช้หน่วยตามตำแหน่ง field แล้ว (`*/5` นาที, `0 */2` ชั่วโมง, `0 0 */3` วัน) | `scheduling.py:17-62` + `tests/test_scheduling.py` | ✅ fixed |
| H2 | **แก้แล้วใน worktree:** Unrestricted MCP เป็น accepted capability และ status/audit ระบุชัดว่า `mcp_enabled_servers` เป็น visibility list ไม่ใช่ deny allowlist | `mcp_local.py:mcp_unrestricted_policy`, `main.py:_execute_mcp_call`, `chat_tools.py:_owner_execute_mcp_call` + `tests/test_mcp_unrestricted_visibility.py` | ✅ fixed |
| H3 | **แก้แล้วใน worktree:** Private/localhost fetch ยังเป็น accepted capability ผ่าน `allowPrivateHosts=true` แต่ผลลัพธ์และ audit log มี `networkAudit`/private-host warning/history แล้ว | `web_tools.py:execute_web_fetch`, `main.py:_web_fetch_private_audit_event`, `chat_tools.py:_record_chat_web_fetch_private_audit` + `tests/test_web_fetch_private_visibility.py` | ✅ fixed |
| H4 | **แก้แล้วใน worktree:** Full Autonomy เป็น effective policy ถาวร; คำขอ `ask/deny/critical_only` หรือ `agentFullAccess=false` ถูกเก็บเป็น requested metadata เท่านั้น และเพิ่ม status/audit/checkpoint visibility | `db/repo.py:447-680`, `main.py:_full_auto_tool_audit_event`, `chat_tools.py:_owner_full_auto_tool_audit_event` + `tests/test_full_autonomy_visibility.py` | ✅ fixed |
| H5 | **แก้แล้วใน worktree:** POST `/api/tasks` เช็ค client id ซ้ำก่อน `save_task` และตอบ `409` แทน upsert ทับงานเดิม | `main.py:9234-9245` + `tests/test_task_review_schedule.py` | ✅ fixed |
| H6 | **แก้แล้วใน worktree:** เพิ่ม rescanner ให้ task ที่มี `nextReviewAt` แต่ไม่มี queued/running reminder ถูก enqueue กลับมาเอง | `engine.py:_rescan_task_review_reminders` + `tests/test_task_review_schedule.py` | ✅ fixed |
| H7 | **แก้แล้วใน worktree:** edit knowledge ที่เปลี่ยน text จะ clear embedding เก่า และมี API route สำหรับ status/re-embed stale knowledge | `db/repo.py:1842-1858` + `main.py:/api/knowledge/reembed-stale` + `tests/test_knowledge_embedding_migration.py` | ✅ fixed |
| H8 | **แก้แล้วใน worktree:** video background jobs มี runtime cancel event + subprocess process-group signal; worker cancellation set event ให้ `_run` หยุด process | `video_editing.py:61-68, 265-409, 1388-1485` + `tests/test_video_job_cancellation.py` | ✅ fixed |
| H9 | **แก้แล้วใน worktree:** `video.cancel_job` signal runtime process และ `process_video_job` re-read cancelled record ก่อนเขียน done/failed จึงไม่ทับ cancelled | `video_editing.py:1234-1257, 1409-1420, 1454-1466` + `tests/test_video_job_cancellation.py` | ✅ fixed |
| H10 | **แก้แล้วใน worktree:** Telegram polling แยก error ต่อ update, บันทึก `telegram_update_error`, และเลื่อน offset ต่อ ทำให้ update เดียวไม่ขังทั้ง batch | `telegram_gateway.py:1762-1790` + `tests/test_telegram_channel_gateway.py` | ✅ fixed |
| H11 | **แก้แล้วใน worktree:** Telegram API parse `retry_after`/status code และ outbound retry ใช้ delay ที่ Telegram ขอแทน fixed delay | `telegram_gateway.py:71-81, 513-557, 1507-1534` + `tests/test_telegram_channel_gateway.py` | ✅ fixed |
| H12 | **แก้แล้วใน worktree:** UI ส่งงาน await backend, update state หลัง server ตอบ, แสดง error โดยไม่ล้างฟอร์ม และกัน submit ซ้ำ | `ui/src/contract/ApiClient.ts:555-564` + `ui/src/panels/AssignTaskModal.tsx:74-108, 203-235` | ✅ fixed |

---

## 1. การแก้ไข/หักล้างจากฉบับ v1 (โปร่งใส)

การตรวจซ้ำพบว่า v1 **เขียนเกินจริงหรือเข้าใจกลไกผิด** ในหลายข้อ — แก้ให้ถูกดังนี้:

| v1 | คำตัดสินใหม่ | ความจริงจากโค้ด |
|----|-------------|------------------|
| 1.1 handoff เขียนหลาย entity ไม่ atomic → งานค้าง waiting ตลอด | ❌ **หักล้าง** | `_create_handoff_task` (`engine.py:5214`) ทั้งชุดอยู่ใน `session_scope` เดียว commit ครั้งเดียว, ไม่มี `commit_and_release` คั่น → crash กลางคัน rollback ทั้งหมด ไม่เกิดงานค้างครึ่ง ๆ |
| 1.2 mark done คนละ transaction จาก work | ✏️ **แก้กลไก + fixed side-effect idempotency** | work + `mark_job("done")` อยู่ใน session เดียว commit พร้อมกัน; scheduled objective/trigger task creation ใช้ deterministic task id แล้ว จึง rerun แล้วไม่สร้าง task/activity/notification ซ้ำ |
| 1.6 artifact paging ข้ามหน้า → บริบทไม่ครบ | ❌ **หักล้าง** | cursor เป็นตำแหน่งที่เก็บไว้ ขยับเฉพาะหลัง turn สำเร็จ; turn ที่ถูกขัดคืน None → ไม่ขยับ → ส่งหน้าเดิมซ้ำ; index wrap modulo; full content เข้าถึงได้เสมอผ่าน API ref (`engine.py:3574-3626, 3511`) |
| 3.2 แผนกขนานแชร์ memory เขียนทับ | ✏️ **แก้** | แต่ละแผนก advance ใน `session_scope` ของตัวเอง + re-fetch จาก DB (`engine.py:5927-5939`); `departments` list ใช้ read-only เท่านั้น **ความเสี่ยงจริงคือ DB-level TOCTOU**: 2 handoff เป้าหมายแผนก idle เดียวกันอาจปลุกทับกัน (low-med) |
| 5.3 image retry วนไม่จบ | ❌ **หักล้าง** | บั๊กนี้ไม่มี — `attempts` เก็บบน run entity, gate `attempt < IMAGE_GENERATION_MAX_ATTEMPTS(=3)` (`image_generation.py:1175, 1209`); ครบ 3 → mark failed หยุด; delay = 60/180/300s ไม่ใช่ 60s คงที่ (หมายเหตุ: ChatGPT OAuth route มี retry ภายในอีกชั้นก่อนกลับมาเข้า run-level retry) |
| 5.4 subprocess timeout ไม่ kill child | ❌ **หักล้าง (false positive)** | `subprocess.run(timeout=)` ฆ่า child ให้อยู่แล้ว; path ที่ใช้ `Popen` ก็ทำ terminate→kill เอง (`chat_tools.py:3955-3968`) — ปัญหา orphan ที่พบจริงคือ render ใน `asyncio.to_thread` และแก้แล้วใน H8 |
| 5.1 ไม่มี retry provider | ✏️ **แก้ + accepted no-hard-breaker** | provider layer retry: Anthropic SDK `max_retries=2`, OpenAI 2 ครั้งพร้อม backoff+jitter และ `Retry-After`; hard circuit-breaker ระดับ engine ไม่ทำเป็น default เพราะลด Full Autonomy แต่ `provider.recoveryPolicy` expose retry/resume visibility แล้ว |
| 7.1 optional field ไม่ guard → crash | ✏️ **ลดเป็น latent** | ผู้บริโภคทุกจุดที่ตรวจใช้ `.get()/or {}/isinstance` แล้ว ไม่พบ consumer ที่ index ตรง ๆ → เป็นความเสี่ยงแฝงเท่านั้น |
| 4.2 chat history ตัด N ท้าย | ✏️ **กลับด้าน** | default `chat_history_messages = 0` = **ไม่ตัดเลย** ส่งทั้ง thread (ปัญหากลายเป็น cost/overflow ไม่ใช่ context หาย); การตัดเกิดเฉพาะตอน operator ตั้งค่าบวก |

ข้อ v1 ที่ **ยืนยันคงเดิมหรือยังเปิดอยู่**: 1.7, 1.8, 2.2, 3.3, 6.1, 6.2, 6.3 (low), 7.2, 7.3; ส่วน 1.4 timeout cleanup แก้ด้วย recovery/requeue แล้ว และ 2.3 ไม่มี cap ระดับคิวทั่วไปถูกจัดเป็น accepted no-hard-cap พร้อม visibility

---

## 2. ระบบส่งงาน & Handoff (Task lifecycle)

**H5 — สร้าง task ด้วย id จาก client → upsert ทับ task เดิม (งานหาย)** ✅ fixed
เดิม: `main.py:9245` `"id": input.id or uid("task")` + `db/repo.py:925-943` (`save_task` เป็น upsert)
แก้แล้วใน worktree: `main.py:9234-9245` ตั้ง `task_id` แล้วถ้า `input.id` ซ้ำกับ `repo.get_task(task_id)` จะ `HTTP 409 "task id already exists"` ก่อน `save_task`; test `ApiAssignTaskCollisionTest.test_assign_task_rejects_client_id_collision`
ผลกระทบเดิมก่อนแก้: `AssignTaskInput.id` เป็น optional ที่ client ส่งมาได้ และไม่มีการเช็คว่ามี id นี้อยู่แล้วหรือไม่ การ POST `/api/tasks` ด้วย id ที่ตรงกับ task เดิมจะ **เขียนทับทั้ง row** (status/progress/deliverables/handoffs/log/result) กลายเป็น task เปล่า `status:"assigned", progress:0` ไม่มี 409/เตือน
> เคสเดิมก่อนแก้: UI retry หลัง response แลค หรือ tool ที่ derive id ซ้ำ → task ที่มี handoff + draft อยู่ถูกล้างเป็นงานเปล่า

**H6 — ลูกโซ่ปลุกตรวจงาน (task_review_reminder) ขาดถาวรถ้า job เดียวล้ม** ✅ fixed
`engine.py:2538` (re-arm เป็นบรรทัดสุดท้าย) + `engine.py:2902-2903` (ล้ม → mark failed ไม่ requeue)
เดิม: reminder เป็นลูกโซ่ self-perpetuating: job ถัดไปถูก enqueue **เฉพาะเมื่อทุก side effect ก่อนหน้าสำเร็จ** (watch line, notify, save_task) ถ้า job raise/timeout ก่อนถึงบรรทัด 2538 → mark failed ไม่มี retry → **ไม่มี reminder ตัวถัดไป ลูกโซ่หยุดถาวร** ต่างจาก objective/trigger ที่มี rescanner ทุก tick กู้คืนให้ (`engine.py:6114-6116`)
แก้แล้วใน worktree: เพิ่ม `_rescan_task_review_reminders` ใน engine tick ก่อน `_process_due_jobs`; rescanner ดู active tasks ที่มี `reviewIntervalMs`/`reviewScheduleToken`/`nextReviewAt` และไม่มี queued/running reminder สำหรับ `(taskId, token)` เดิม แล้ว enqueue reminder กลับมาเอง; tests ยืนยันทั้ง path re-enqueue และไม่ enqueue ซ้ำเมื่อมี active reminder อยู่แล้ว
> เคส: DB blip/timeout ครั้งเดียวตอน reminder → executive เลิกถูกปลุกมาตรวจ task นั้นตลอดไป ไม่ self-heal

**M — empty/whitespace title ผ่านได้** ✅ fixed
เดิม: `schema.py:937` `title: str` (ไม่มี min_length, base Schema ไม่มี str_strip_whitespace) + `main.py:9651-9674`
`title=""` หรือ `"   "` ผ่าน Pydantic และถูกบันทึกตรง ๆ; `deliverables` สร้างตอน create ไม่ได้ (hardcode `[]`) → task ชื่อว่าง ไม่มี deliverable เป็น task ที่ valid

แก้แล้วใน worktree: `assign_task` strip title แล้ว reject string ว่างด้วย `HTTP 400 "title cannot be empty"` ก่อนสร้าง task; test `test_assign_task_rejects_empty_title` ยืนยัน path นี้
> เคส: client/agent ส่ง string เปล่า → งานชื่อว่างโผล่ในคิวแผนก ระบุไม่ได้

**1.2 — job at-least-once → rerun ทำ task/activity ซ้ำ** ✅ fixed
เดิม: crash ก่อน commit → row ค้าง `running` → `reset_stuck_jobs` (boot) requeue → `objective_run`/`trigger_run` สร้าง task ด้วย `uid()` ใหม่ → **งานซ้ำ** ไม่มี idempotency key ระดับ side effect
แก้แล้วใน worktree: เพิ่ม `_scheduled_task_id(...)` และให้ `objective_run`/`trigger_run` ใช้ deterministic task id จาก `(kind, sourceId, departmentId, scheduledFor, event)`; ถ้า rerun แล้ว task id เดิมมีอยู่ จะ skip activity/notification side effects ด้วย; log เก็บ `objective/trigger idempotencyKey=...`; tests `test_objective_run_uses_deterministic_task_id_and_skips_duplicate_side_effects` และ `test_trigger_run_uses_deterministic_task_id_per_assignee_and_skips_duplicates`

**1.5 — snapshot สำหรับ UI ตัดข้อมูล** ✅ fixed
`db/repo.py:65-88` — log เก็บแค่ N ท้าย + clip ต่อบรรทัด, detail clip, `draftDeliverableMarkdown` เคยเป็น `None` เมื่อ limit=0; C2 แก้แล้วให้ default แสดง draft แบบ clip ได้, และแก้ handoff `messages` ที่เคยถูก compact เป็น `[]` ทั้งก้อน (`db/repo.py:190`) แล้ว
แก้แล้วใน worktree: `_compact_handoff_for_snapshot(...)` เก็บ `messageCount`, `messagesTruncated`, และ recent message preview แบบ bounded (`state_task_handoff_messages`, `state_task_handoff_message_chars`) โดยไม่ส่งทั้งประวัติทุก state refresh; test `test_snapshot_handoff_keeps_recent_message_preview`

**L — parent_task_id ข้ามโปรเจกต์/ใต้ parent ที่ปิดแล้ว ไม่ถูก validate** ✅ fixed
เดิม: `main.py:9237-9243` เช็ค project membership ของ department แต่ `_link_child_task` (`main.py:1741-1753`) เช็คแค่ parent มีอยู่ ไม่เช็ค project ตรงกัน/parent ยังไม่ done → sub-task tree ข้ามโปรเจกต์/ห้อยใต้ parent ที่ปิดแล้วได้
แก้แล้วใน worktree: เพิ่ม `_validate_parent_task` ให้ reject parent ที่ `done/cancelled` และ reject parent/child project mismatch ก่อน link; tests `test_assign_task_rejects_closed_parent_task` และ `test_assign_task_rejects_cross_project_parent_task`

**1.7 — `append_handoff_message` เป็น pure function ต้อง save เอง** ✅ 🟡 (`handoffs.py:119-132`)
**1.8 — `enqueue_task_review_reminder` คืน None เงียบ / ไม่ retry** ✅ 🟡 (`task_review.py:68-92`) — ดู H6 ประกอบ

> สิ่งที่ตรวจแล้ว **ปลอดภัย**: create/assign/reassign/approval-resolve เป็น atomic (session เดียว), ไม่มี engine-before-commit race (engine อ่าน row ที่ commit แล้ว; `hub.pulse` ป้อนแค่ WebSocket ไม่ปลุก engine), approval มี guard กันแก้ซ้ำ

---

## 3. Job Queue, Durability, การกู้คืน

**1.3 — ไม่มี in-flight reaper** ✅ fixed
เดิม: `reset_stuck_jobs` เรียกตอน startup เท่านั้น; `job_runtime_summary` แค่ **รายงาน** `staleRunning` → งานค้าง `running` (worker ตายไม่ restart) ไม่มีใคร requeue จนกว่าจะ reboot

แก้แล้วใน worktree: เพิ่ม `Repo.requeue_stale_running_jobs(...)` และ engine helper `_requeue_stale_running_jobs(...)` ให้ tick ปกติดึง stale `running` กลับเป็น `queued` โดยใช้ stale window แบบ conservative (`max(engine_stale_after_s, engine_job_timeout_s, image_generation_timeout_s)`) และ exclude `image_generation` เมื่อตั้ง timeout เป็น unbounded; บันทึก activity warning เมื่อ requeue สำเร็จ; tests `test_requeue_stale_running_jobs_restores_only_stale_running_rows`, `test_engine_reaper_records_activity_and_uses_conservative_window`, และ `test_engine_reaper_excludes_unbounded_image_jobs`
หมายเหตุ: การแก้นี้ไม่เพิ่ม max-attempt/dead-letter และไม่หยุดงาน AI เอง; scheduled objective/trigger idempotency key แก้แล้วใน 1.2

**1.4 — timeout ไม่ rollback/cleanup** ✅ fixed recovery/requeue
เดิม: สาขา TimeoutError ของ job queue แค่ mark `failed`; สำหรับ kind ที่ commit เฉพาะตอนจบ `session_scope` จะ rollback ให้ แต่ kind ที่คืน connection ด้วย `commit_and_release` ระหว่างรอ provider อาจมี partial state ค้างใน DB แล้ว job จบเป็น failed

แก้แล้วใน worktree: เพิ่ม `_handle_job_timeout(...)` + `_record_job_timeout_recovery(...)` (`engine.py:1794-1884`) ให้ timeout ทุก job มี recovery record `job_timeout_recovery` และ activity warning; `chat_reply` ยังปิด pending bubble เป็น failed/retryable ผ่าน `_mark_chat_reply_timeout(...)` เพราะ requeue กับ reply ที่ปิดแล้วจะถูก guard ข้าม; non-chat timeout เปลี่ยนจาก `failed` เป็น `queued` พร้อม `run_after` จาก `engine_timeout_retry_delay_s` (`engine.py:3165-3188, 3239-3270`) เพื่อให้ AI ทำงานต่อได้แทนหยุดค้าง. Test `test_non_chat_job_timeout_requeues_and_records_recovery` ยืนยันว่า non-chat timeout requeue, บันทึก recovery, และไม่เก็บ payload ลับนอก allow keys

**2.1 — SQLite ไม่มี row-lock ตอน claim** ❌ หักล้าง / verified safe
ข้อกล่าวหาเดิมถูกลดออกจาก medium: `Repo.claim_due_jobs(...)` ใช้ `UPDATE jobs SET status='running' WHERE id=:id AND status='queued'` แล้วเช็ค `rowcount == 1` ต่อ row หลัง select candidate; แม้ SQLite ไม่มี `skip_locked`, concurrent session ที่เลือก id เดียวกันจะมีเพียง session เดียว update สำเร็จ อีก session rowcount เป็น 0 แล้วไม่ return job นั้น
ยืนยันด้วย test `test_sqlite_claim_due_jobs_does_not_double_claim_same_row` บน SQLite file DB สอง session concurrent claim job เดียวกัน → claimed ได้ครั้งเดียวและ row ลงท้ายเป็น `running`

**2.3 — ไม่มี max-attempts/dead-letter "ทั่วไป"** ✅ ⚪ accepted no-hard-cap + visibility fixed
ตรวจแล้ว: `mark_job` ยังทำแค่ `attempts += 1` เมื่อมี error (`db/repo.py:2378-2388`) และไม่มี cap ระดับคิวทั่วไป; image self-cap ที่ 3 เป็น logic เฉพาะของ image run ไม่ใช่ queue policy กลาง

ตัดสินใจตาม product intent: ไม่แก้ด้วย hard max-attempt/dead-letter ที่หยุดงาน AI หรือทำให้ `_RetryJobLater` หมดสิทธิ์ retry เอง เพราะจะลดความสามารถทำงานต่อเนื่องของ AI

แก้แล้วใน worktree: เพิ่ม `engine_retry_visibility_attempts` เป็น threshold แสดงผลเท่านั้น (`config.py:359-361`), ให้ `job_runtime_summary(...)` ส่ง `retryVisibility` + `highAttemptJobs` เฉพาะ queued/running ที่ attempts สูง (`db/repo.py:2390-2477`), และ expose ผ่าน `/health` + `/api/runtime` (`main.py:8124-8128, 8168-8172`); test `test_job_runtime_summary_reports_high_attempt_jobs_without_capping` ยืนยันว่า summary ไม่เปลี่ยน status/attempts ของ job

**2.2 — `mark_job` คืนเงียบเมื่อ row หาย/ถูก cancel** ✅ 🟡 `db/repo.py:2106-2111`

---

## 4. Scheduling / Cron / Objective / Trigger

**H1 — Cron `*/N` ถูกตีความเป็น "นาที" เสมอ ไม่ดูตำแหน่ง field** ✅ fixed
เดิม: `scheduling.py:27-30`
```python
cron_step = re.search(r"(?:^|\s)\*/(\d+(?:\.\d+)?)(?:\s|$)", text)
if cron_step:
    minutes = float(cron_step.group(1))
    return int(minutes * MINUTE_MS) if minutes > 0 else default
```
regex จับ `*/N` ตัวแรกแล้วถือเป็น "นาที" เสมอ ไม่สนว่าอยู่ field ไหน:
- `*/5 * * * *` → 5 นาที ✓
- `0 */2 * * *` → ได้ 2 **นาที** (ควรเป็น 2 ชม. = เร็วเกิน 60 เท่า)
- `0 0 */3 * *` → ได้ 3 **นาที** (ควรเป็น 3 วัน = เร็วเกิน 1440 เท่า)
ป้อนเข้า `_enqueue_due_objectives`/`_enqueue_due_triggers` → trigger/objective ที่ตั้งเป็นชั่วโมง/วันจะยิงเกือบทุก tick
แก้แล้วใน worktree: `scheduling.py` parse cron 5-field ตามตำแหน่ง field ก่อน fallback เดิม; test `tests/test_scheduling.py` ยืนยัน `*/5 * * * * = 5 นาที`, `0 */2 * * * = 2 ชั่วโมง`, `0 0 */3 * * = 3 วัน`
> เคสเดิมก่อนแก้: ผู้ใช้/agent สร้าง trigger cron แบบรายชั่วโมง → ยิงทุกไม่กี่นาที = เผา LLM cost + แจ้งเตือนถล่ม

**M — catch-up burst ได้สูงสุด 8×N task ใน tick เดียวหลัง downtime** ✅ fixed
เดิม: `_enqueue_due_objectives`/`_enqueue_due_triggers` สร้าง catch-up runs ได้สูงสุด `MAX_TRIGGER_CATCH_UP_RUNS=8` แต่ enqueue ทุก job ด้วย `run_after=now` — cap กันค้างแต่ไม่กระจายเวลา → ทุก task ลงพร้อมกัน

แก้แล้วใน worktree: เพิ่ม `CATCH_UP_RUN_SPACING_MS=60_000` และ `_due_run_after(...)` ให้ due batch ที่มีหลาย run ถูก enqueue ครบเท่าเดิมแต่ `runAfter` เรียงห่างกันทีละ 60 วินาที ทั้ง objective และ trigger; tests `test_objective_catch_up_jobs_are_staggered_without_dropping_runs` และ `test_trigger_catch_up_jobs_are_staggered_without_dropping_runs` ยืนยันว่าไม่ drop runs และไม่ยิงพร้อมกัน
> เคส: engine หยุด 2-3 ชม. บน objective cadence 15 นาที → กลับมาเจอ 8 task ค้าง + 8 แจ้งเตือน ทีเดียว

**L — `_enqueue_due_objectives` ไม่มี enqueue lock (ต่างจาก trigger)** ✅ fixed
เดิม: objective enqueue ไม่มี lock เทียบกับ trigger ที่มี `_TRIGGER_ENQUEUE_LOCK`; ปัจจุบันมัก serialize ด้วย single loop แต่ถ้า tick ซ้อน/เรียกซ้ำขนาน อาจ enqueue objective run เดิมก่อน `save_objective` ขยับ `nextRunAt`
แก้แล้วใน worktree: เพิ่ม `_OBJECTIVE_ENQUEUE_LOCK` ครอบ `_enqueue_due_objectives` แบบเดียวกับ trigger; test `test_concurrent_objective_enqueue_dedupes_with_lock` ยืนยัน concurrent enqueue ได้ job ชุดเดียว

**L — one-shot vs recurring แยกด้วย truthiness ของ `oneShotAt` เปราะ** ✅ fixed
เดิม: trigger create/update/tool/engine ใช้ truthiness (`not one_shot_at`, `trigger.get("oneShotAt")`) ทำให้ค่า one-shot ที่ explicit แต่ falsy เช่น `0` ถูกมองเป็นไม่มี one-shot และอาจกลายเป็น recurring
แก้แล้วใน worktree: เพิ่ม `has_one_shot_at(...)` แล้วใช้ใน `next_run_for_cadence`, trigger validation, scheduler tool, และ `_enqueue_due_triggers`; tests `test_one_shot_zero_is_explicit_schedule_value` และ `test_trigger_one_shot_zero_is_not_treated_as_recurring`
หมายเหตุ: ส่วนที่เอกสารเดิมพูดว่า objective ไม่มี one-shot field ถูกตรวจแล้วว่า `ScheduledObjective` เป็น recurring surface โดย schema บังคับ `cadence`; one-shot schedule ใช้ `Trigger.oneShotAt` ไม่ใช่ objective

> ตรวจแล้ว **ปลอดภัย**: catch-up ไม่ลูปไม่จบ + ไม่ drift (grid phase-aligned), `uid()` กันชนด้วย CSPRNG (`ids.py:28-49`), thread_id ไม่ชน (prefix แยก), `_TRIGGER_ENQUEUE_LOCK` กัน trigger ซ้อน และ `_OBJECTIVE_ENQUEUE_LOCK` กัน objective ซ้อนแล้ว

---

## 5. Concurrency & Races

**3.1 — task last-write-wins (ไม่มี optimistic lock)** ✅ fixed
เดิม: `_advance_department` อ่าน task เข้า memory (`engine.py`) → เรียกโมเดล (วินาที-นาที) → `save_task`; `save_task` set `data=task` ทั้งก้อน ไม่มี version/`updatedAt` compare → แก้จาก UI/API ระหว่างนั้นถูกเขียนทับเงียบ
แก้แล้วใน worktree: เพิ่ม `_save_engine_task_update(...)` ให้ engine re-read task สด (`Repo.get_task_fresh(..., populate_existing=True)`) ก่อน save และ merge เฉพาะ field ที่ engine เปลี่ยนลงบน current task; preserve owner edits เช่น `detail`/`priority`, merge `log`/`handoffs`, ไม่ถอย `progress`, และไม่ทับ terminal `done/cancelled`; tests `test_department_work_step_preserves_concurrent_task_edits` และ `test_engine_task_merge_preserves_concurrent_terminal_status`

**M — engine loop แย่ง `chat_reply` กับ chat-reply worker เฉพาะทาง** ✅ fixed
เดิม: `engine.py:117` `DEDICATED_WORKER_JOB_KINDS = {"image_generation", "trigger_run"}` (ไม่มี `chat_reply`) → main tick (`engine.py:6118`, kind=None) claim `chat_reply` ขนานกับ `run_chat_reply_loop` ได้ → **บายพาส** logic กันแผนกเดียวกันรันซ้อน (`_partition_parallel_chat_jobs`) → 1 แผนกอาจมี 2 turn พร้อมกัน เขียน state ทับ/ตอบสลับลำดับ (atomic claim กัน "ตอบซ้ำ" ได้ แต่ไม่กันรันซ้อน)
แก้แล้วใน worktree: เพิ่ม `chat_reply` เข้า `DEDICATED_WORKER_JOB_KINDS` เพื่อให้ main engine queue exclude งานนี้ และปล่อยให้ `run_chat_reply_loop`/`_partition_parallel_chat_jobs` เป็นผู้ claim; test `test_general_engine_queue_excludes_dedicated_worker_jobs` ยืนยัน exclude set มี `chat_reply`

**3.2 (แก้แล้ว) — TOCTOU ปลุกแผนก idle** ✅ 🟡 `engine.py:5384` 2 handoff อ่านสำเนา dept คนละชุดแล้วปลุกทับกัน (last-writer-wins)
**3.3 — global runtime dict ไม่มี lock** ✅ 🟡 `engine.py:209-231` กระทบแค่ telemetry

---

## 6. Provider / Chat reply / Cost

**M — chat_reply ที่ timeout ไม่ปิด bubble ทันที; snapshot reconcile กู้ภายหลัง** ✅ fixed
`engine.py:2828-2831` timeout → mark job failed แต่ timeout path ไม่เรียก `sink.finish()` เพื่อปิด `reply_message_id` ที่ตั้ง `pending=True` (`engine.py:1696`) ทันที (ต่างจาก provider-error ภายในฟังก์ชันที่ persist `status="failed"` ที่ `1918-1958`) → ผู้ใช้อาจเห็น bubble หมุนค้างจนกว่าจะ refresh/state snapshot

ข้อเดิมที่ว่า "ค้างถาวร" ถูกหักล้าง: `Repo.snapshot()` เรียก `reconcile_chat_reply_placeholders(limit=200)` ก่อนคืน state (`db/repo.py:2337-2339`, `1132-1187`) จึงสามารถเปลี่ยน pending placeholder ของ terminal `chat_reply` job เป็น failed/cancelled ภายหลังได้
แก้แล้วใน worktree: เพิ่ม `_mark_chat_reply_timeout(...)` ให้ timeout handler ของ main queue/chat worker ปิด `pending`, ตั้ง `status="failed"`, ใส่ `error.code="chat_reply_timeout"` และ pulse `msg_done` ทันที; test `test_chat_reply_timeout_marks_pending_message_failed`

**5.1 — ไม่มี circuit-breaker/resume ชั้น engine** ✅ ⚪ accepted no-hard-breaker + recovery visibility fixed
engine จับ exception → คืน partial `stop_reason="error"` (`engine.py:1918`); provider layer retry จริง (Anthropic SDK `max_retries=2` + backoff/jitter; OpenAI `_post_response_with_retry` 408/409/425/429/500-504)
แก้แล้วใน worktree: OpenAI Responses retry ที่เคย delay `0s` ตอนนี้ใช้ exponential backoff + jitter และ honor `Retry-After` สำหรับ HTTP retry ทั้ง non-stream/stream ก่อนมี output; tests `test_responses_provider_retry_delay_uses_backoff_with_jitter` และ `test_responses_provider_retry_sleep_honors_retry_after_header`

ตัดสินใจตาม product intent: ไม่เพิ่ม hard circuit-breaker ที่หยุด provider global/default เพราะจะลดความสามารถ AI เมื่อ provider/runtime กลับมาพร้อมเอง; ใช้ retry/recovery visibility และ manual retry/resume แทน

แก้เพิ่มใน worktree: `provider_health(...)` expose `recoveryPolicy` (`provider/registry.py:172-210`) ระบุ `hardCircuitBreaker=false`, retry layers, `Retry-After`, route `/api/messages/{thread_id}/retry`, non-chat timeout recovery/requeue, runtime degraded retry และ image worker retry/requeue; test `test_provider_health_exposes_recovery_policy_without_hard_circuit_breaker`

**L — crash หลัง provider/cost commit แต่ก่อน message terminal → rerun คิด cost ซ้ำได้** ✏️ 🟡
`engine.py:1651-1653` guard ข้ามเฉพาะ reply ที่ไม่ pending; ถ้า process ตายขณะ reply ยัง `pending=True` → boot requeue → rerun ได้ ความเสี่ยงคิดเงินซ้ำเกิดเฉพาะกรณี provider/cost ถูก commit แล้วแต่ message ยังไม่ terminal ไม่ใช่ทุก crash "กลาง stream"

**L — `_RetryJobLater` (runtime degraded) ไม่มี attempt cap** ✅ ⚪ accepted no-hard-cap + visibility fixed
ตรวจแล้วเป็นพฤติกรรมเดียวกับ 2.3: runtime (Letta) ล่มถาวรทำให้ reply ถูก requeue ต่อได้เรื่อย ๆ และมี message แจ้งผู้ใช้ จึงไม่เงียบ; ไม่แก้ด้วย cap/dead-letter ที่หยุด AI แต่เพิ่ม `highAttemptJobs`/`retryVisibility` ใน runtime summary ให้เห็นงาน active ที่ retry สูงแล้ว

**5.2 — suppress(Exception) 6 จุด (telegram progress×5 + runtime ledger×1)** ✅ fixed
แก้แล้วใน worktree: จุด side-effect ของ Telegram progress และ runtime event ledger ไม่กลืน exception เงียบแล้ว แต่บันทึก activity ผ่าน `_record_suppressed_engine_error(...)` แบบ best-effort โดยไม่ทำให้ chat turn fail; test `test_suppressed_engine_error_records_activity`
**4.1 — compaction async หลัง turn, turn ปัจจุบันใช้ context เต็ม** ⚪ accepted full-context design
ยืนยันจากโค้ด/เทสต์: `chat_history_messages=0` หมายถึงไม่ตัด history ที่ fetch มาใน prompt assembly และ engine path `_llm_chat_history(...)` ส่งข้อความทั้งหมดของ history ที่ให้มาเข้า prompt; compaction ถูก enqueue หลังประเมิน token/context เพื่อเป็น memory/retrieval เสริมรอบถัดไป ไม่ใช่การตัดบริบทของ turn ปัจจุบัน. ตาม product intent ห้ามแก้ด้วย default truncation/summary-only prompt; tests `test_engine_chat_history_zero_keeps_all_fetched_messages_in_prompt` และ `test_chat_history_zero_keeps_all_fetched_messages_in_prompt`
**4.3 — token count timeout 8s → fallback estimate** ✅ fixed
แก้แล้วใน worktree: `_chat_context_tokens_for_turn` ยัง fallback estimate เหมือนเดิม แต่ `contextTokenSource` ระบุเหตุผลชัดเจน เช่น `estimate:provider_timeout`, `estimate:provider_error:RuntimeError`, หรือ `estimate:provider_nonpositive`; tests `test_chat_context_token_source_labels_provider_timeout` และ `test_chat_context_token_source_labels_provider_error`
**4.4 — `_parse_json_object` คืน {} เงียบ → knowledge สกัดหายตอน compaction** ✅ fixed
แก้แล้วใน worktree: เพิ่ม `_parse_json_object_with_meta(...)` ที่คืน metadata `ok/source/error/detail`; fallback ของ compaction/work/review/autonomous เก็บ `jsonParse` หรือ `_jsonParse` เมื่อ parse ไม่สำเร็จ ทำให้เห็นว่าเป็น `no_json_object`, `json_decode_error`, หรือ `json_not_object`; test `test_json_object_parser_returns_visibility_metadata`

> ตรวจแล้ว **ปลอดภัย**: chat-reply worker + engine loop อยู่ event loop เดียว แชร์ `_JOB_CLAIM_LOCK`; partial stream persist ด้วย id เดิม (ไม่ซ้ำ); ChatGPT OAuth refresh + skew; Claude Code/OpenAI auth/429 surface เป็น error ชัด ไม่ retry ไม่จบ

---

## 7. Media (Image / Audio / Video)

**H8 — render วิดีโอใน `asyncio.to_thread` ยกเลิกไม่ได้ → ffmpeg/node orphan** ✅ fixed
เดิม: `engine.py:2823` (`asyncio.wait_for(timeout=1800s)`) + `video_editing.py:1762` (`asyncio.to_thread(_render_ffmpeg)`)
render หนึ่งยิง ffmpeg หลายครั้งต่อเนื่อง (per-segment 900s ฯลฯ) รวมเกิน 1800s ได้ง่าย `wait_for` ยกเลิกได้แค่ coroutine ที่ await **ยกเลิก thread ไม่ได้** → job = failed แต่ ffmpeg/node ยังรันต่อกิน CPU/disk; ยิ่งกว่านั้น remotion/transcribe ตั้ง subprocess timeout (1800/3600s) **สูงกว่า** worker timeout → ถูก worker ตัดก่อนเสมอ ขณะ process ลูกหลาน (node+headless browser) ยังค้าง
> เคส: render timeline 5 คลิป หรือ transcribe วิดีโอ 40 นาที → job failed แต่เครื่องยังโดน ffmpeg/whisper กินยาว, retry ซ้อน orphan สะสมจนเครื่องตัน
แก้แล้วใน worktree: เพิ่ม runtime registry ต่อ video job (`_CURRENT_VIDEO_JOB_ID`, cancel event, current subprocess); `_run` เปลี่ยนเป็น `Popen` + poll cancel event และส่ง SIGTERM/SIGKILL ให้ process group เมื่อ cancel/timeout; `process_video_job` set cancel event เมื่อ coroutine ถูก cancel จาก worker timeout; test `test_run_terminates_active_process_when_cancel_event_is_set` ยืนยัน signal path

**H9 — `video.cancel_job` ไม่หยุด render จริง + เขียนทับ cancelled→done** ✅ fixed
เดิม: `video_editing.py:1094-1123` (cancel แค่ flip สถานะ DB/manifest ไม่มี pid/signal/event) + `1277-1281`/`1294-1298` (process_video_job เขียน record ทับเป็น done/failed)
ยกเลิกแล้ว render ที่รันอยู่ทำต่อจนจบ แล้ว **เขียนทับสถานะ "cancelled" เป็น "done"** (ไม่มี compare-and-swap) → "ยกเลิก" ทั้งไม่คืน CPU และไม่ติดทน
> เคส: ยกเลิก 4K render เพื่อปลดเครื่อง — UI ขึ้น cancelled แต่ ffmpeg รันต่อหลายนาที แล้ว job รายงาน done พร้อม artifact จริง
แก้แล้วใน worktree: `video.cancel_job` เรียก `_cancel_video_job_runtime` แล้วเก็บ `runtimeCancelSignalled`; `process_video_job` re-read `video_job` ล่าสุดก่อน finalizing/done และก่อน failed write ถ้าเห็น `status=cancelled` จะ preserve cancelled และ raise `VideoJobCancelledError` แทนเขียนทับ; test `test_process_video_job_preserves_cancelled_record_after_render_returns` ยืนยัน record ไม่ถูกทับเป็น done

**M — render intermediates ไม่ถูกลบ (disk leak ทุก render + ตอน fail)** ✅ fixed
เดิม: `video_editing.py:1925/2057/3403/3505/3817` เขียน segment_NNN.mp4, base.mp4, overlays.mp4, text.mp4, audio.mp4 + PNG ลง `renders/<id>/` ไม่มี cleanup ทำให้ re-render สะสมหลาย GB
แก้แล้วใน worktree: เพิ่ม `_cleanup_video_render_intermediates(...)` และเรียกจาก `_render_edit` ทั้งหลัง persist artifact และตอน render fail; ลบเฉพาะ pattern ที่รู้จัก (`segment_*.mp4`, `base.mp4`, `concat.txt`, `overlays.mp4`, `text.mp4`, `audio.mp4`, `text_layer_*.png`) โดย preserve final render + manifest; test `test_cleanup_video_render_intermediates_preserves_outputs_and_unrelated_files` และ `test_render_edit_cleans_intermediates_after_persisting_artifact` ยืนยัน path นี้
**M — motion renderer working-dir/cache สะสมหลัง render** ✅ fixed
แก้แล้วใน worktree: เพิ่ม `_cleanup_motion_render_intermediates(...)` สำหรับ Remotion/HyperFrames หลัง persist render artifact แล้ว โดยลบเฉพาะ cache/temp dirs และ extra render outputs ที่ไม่ได้ preserve เป็น output หลัก; ตั้งใจ **ไม่ลบ** package source, `assets`, `node_modules`, หรือ final output path เพื่อไม่ลดความสามารถของ AI ในการ preview/rerun package; cleanup summary ถูกเก็บใน render manifest และ test `test_hyperframes_render_uses_cli_and_persists_artifact` ยืนยันว่า `.cache`/output สำรองถูกลบ แต่ `index.html`, `node_modules`, และ final output ยังอยู่
**M — render_motion ขอ render แล้ว render ล้ม แต่ package สำเร็จอาจถูกมองเป็น ok/done** ✅ fixed
เดิม: `_render_motion` สร้าง package สำเร็จแล้วถ้า `render=true` แต่ Remotion/HyperFrames render ล้ม จะยังคืน top-level `ok=True` เพราะ package ถูกสร้างแล้ว ทำให้ background video job มีโอกาสจบเป็น done ทั้งที่ render artifact ไม่มีจริง
แก้แล้วใน worktree: เมื่อ `render=true` และ render result `ok=False`, manifest จะเป็น `status=render_failed/render_skipped`, เก็บ `renderFailure`, และผลรวมคืน `ok=False` พร้อม `packageOk=True` เว้นแต่ระบุ `allowPartialRender`; test `test_render_motion_reports_requested_render_failure_as_not_ok` ยืนยันว่า package ยังอยู่แต่สถานะไม่ถูกมองเป็น render สำเร็จ
**M — `sourcePath`/`source`/font อ่าน host path ได้เต็มสิทธิ์ (accepted Full Autonomy file access)** ✅ ⚪ accepted
`video_editing.py:3433-3444` (และ `1979`, `3831`, `3739`) absolute path ส่งตรงเข้า ffmpeg; relative join project dir แล้ว `.resolve()` ได้ path นอก project ได้ → ไฟล์ถูก mux เข้า artifact ที่ผู้ขอ download ได้ (ไม่ใช่ shell injection — เป็น host-file access ตาม Full Autonomy) จะ **ไม่** แก้ด้วย filesystem sandbox/sourcePath containment
แก้แล้วใน worktree: เพิ่ม visibility-only `pathUsage`/`hostPathAudit` ใน render_edit, render_motion, และ transcribe manifests; project audit ของ `render.done` เก็บ `hostPathCount` และ `hostPaths` โดยไม่ block path; test `test_render_edit_records_host_path_audit_without_blocking` ยืนยันว่า host `sourcePath` ยัง render ได้และถูกบันทึก audit
**M — image worker timeout สั้นกว่า retry window ภายใน → fail ถาวร บายพาส fallback** ✅ fixed
`engine.py:2982-2997` + `image_generation.py` outer `wait_for(timeout=image_generation_timeout_s)` ครอบ routine ที่ retry เอง 3 ครั้ง → timeout ชั้นนอก mark **failed ถาวร** (`mark_image_generation_job_failed`) ข้าม retry/OpenAI fallback
แก้แล้วใน worktree: เพิ่ม `handle_image_generation_worker_timeout(...)` ให้ timeout ชั้น worker requeue `image_generation_run` เป็น `queued` พร้อม `retryAfter` เมื่อยังเหลือ attempt และให้ engine mark job กลับ `queued` แทน `failed`; เมื่อ attempt หมดแล้วจึง fail ตามเดิม; tests `test_image_worker_timeout_requeues_run_when_attempts_remain` และ `test_image_worker_timeout_branch_requeues_job_instead_of_failing_when_retryable`
**M — image double cost ตอน retry/rerun หลังสำเร็จ (ไม่มี idempotency)** ✅ fixed
เดิม: ถ้า image job ถูก rerun หลัง `image_generation_run` สำเร็จแล้ว code จะตั้ง `running`, เพิ่ม `attempts`, และเรียก paid provider ใหม่ ทำให้สร้าง/คิดเงินซ้ำแทนใช้ artifact เดิม

แก้แล้วใน worktree: `process_image_generation_job(...)` เพิ่ม early idempotency skip เมื่อ run มี `status="succeeded"` และมี artifacts แล้ว (`image_generation.py:1170-1221`) โดย update `idempotencySkipCount/lastIdempotencySkipAt`, บันทึก activity, และคืน artifacts เดิมด้วย provider `idempotency_cache` ก่อนแตะ provider/attempt ใหม่; test `test_image_generation_job_skips_completed_run_without_regenerating` ยืนยันว่า `generate_image_assets` ไม่ถูกเรียก
**L — audio transcription ไม่มี retry เลย** ✅ fixed
แก้แล้วใน worktree: `transcribe_audio_bytes` retry เฉพาะ transient HTTP `408/409/425/429/500/502/503/504/529` และ `httpx.RequestError` โดยใช้ `Retry-After` ถ้ามีหรือ exponential delay; 4xx ทั่วไป/config/source/JSON error ยัง fail ตรงเหมือนเดิม; `audio_transcription_status` แสดง retry policy; tests `test_transcribe_audio_bytes_retries_transient_http_error` และ `test_transcribe_audio_bytes_does_not_retry_non_transient_http_error`
**L — OAuth bearer อาจหลุดลง error text บน 4xx รูปแบบแปลก** ✅ 🟡 `image_generation.py:1507-1519` (ขึ้นกับ proxy base-url ที่ operator ตั้ง)
**L — `n>1` ถูกทิ้งเงียบบน ChatGPT OAuth image route** ❌
หักล้างข้อกล่าวหาเดิม: โค้ดไม่ได้ทิ้งรูปที่ parse ได้ เพราะเก็บทุก `result.images` ที่ upstream คืน (`image_generation.py:2291-2325`) และมี requested count อยู่ใน prompt (`image_generation.py:1431, 1461-1484`) ข้อจำกัดจริงคือ route นี้ไม่ได้ส่ง field `n` แบบ native จึงอาจได้จำนวนน้อยกว่าที่ขอจาก upstream แต่ ATRIUM ไม่ได้ silently drop รูปเอง
**L — video.resume_job = restart ใหม่ทั้งหมด ไม่ใช่ resume** ✅ 🟡 `video_editing.py:1126-1211` เผา compute ซ้ำ + ทิ้ง intermediates ชุดที่สอง
**L — transcribe ใช้ dir ร่วม → re-run/ขนานเขียนทับกัน** ✅ fixed
เดิม: project transcript เขียนลง `_project_dir(...)/transcripts` ด้วยชื่อไฟล์คงที่ (`audio.wav`, `transcript.json`, `transcript.normalized.json`, `transcript.srt/vtt/ass`) ทำให้ re-run หรือรันขนานบน project เดียวกันเขียนทับกันได้
แก้แล้วใน worktree: `_transcribe` สร้าง `transcript_id` ตั้งแต่ต้น และเขียนลง `transcripts/<transcript_id>/` ต่อ run พร้อมบันทึก `workDir`/`isolatedWorkDir`; test `test_transcribe_uses_isolated_output_dir_per_run` ยืนยันว่า run ซ้ำบน project เดียวกันได้คนละ dir และไม่มี `transcripts/transcript.json` กลาง

> ตรวจแล้ว **ปลอดภัย**: object store เขียน atomic (mkstemp+fsync+replace, content-addressed), image retry cap = 3 จริง, ไม่มี shell injection (list argv, ไม่มี shell=True), `render_motion render=true` ที่ render ล้มไม่ถูกนับเป็น render done แล้ว แต่ยังคืน package ที่สร้างสำเร็จให้ใช้งานต่อได้

---

## 8. Telegram

**H10 — polling: update ที่พังก่อนถูก record ทำทั้งคิวค้าง (offset starvation)** ✅ fixed
เดิม: `telegram_gateway.py:1689-1703` — `await handle_telegram_update(...)` **ไม่ถูกห่อ suppress** (ต่างจากการ bump offset ที่ห่อ); ถ้า update กลาง batch raise ก่อน `_record_update`/offset bump (เช่น media import fail, ไฟล์เกิน maxFileBytes, route ปลายทางมีปัญหาก่อนบันทึก update) → loop หลุด → `put_entity` offset ถูกข้าม → poll รอบหน้า Telegram ส่ง batch เดิมซ้ำจาก offset ค้าง → **update เสียวนซ้ำไม่จบ และข้อความทุกคนหลังจากนั้นไม่ถูกประมวลผล**

ขอบเขตที่ตรวจแล้ว: ถ้า update นั้นถูก `_record_update` สำเร็จแล้ว รอบ retry จะเจอ duplicate-skip จึงไม่ค้างถาวรจาก update เดิม ข้อนี้จึงเป็น high เฉพาะ path ที่ exception เกิดก่อนบันทึก update
> เคส: ผู้ใช้คนหนึ่งส่งรูปใหญ่เกิน limit แล้ว fail ก่อนบันทึก update → ทั้งบอทหยุดรับข้อความจากทุกคนจนกว่าจะล้าง update นั้น

แก้แล้วใน worktree: `run_telegram_polling_loop` ห่อ `handle_telegram_update` ต่อ update, บันทึก `telegram_update_error`/activity เมื่อ update ใดพัง, และเลื่อน offset ใน `finally`; test `test_polling_loop_isolates_failed_update_and_advances_offset` ยืนยันว่า update แรกพัง แต่ update ถัดไปยังถูกประมวลผลและ state offset ไปถึง update ถัดไป

**H11 — HTTP 429 ถือเป็น error ตาย ไม่อ่าน `retry_after`** ✅ fixed
เดิม: `telegram_gateway.py:513-515` raise `TelegramGatewayError` เหมือนกันหมดทุก non-2xx; ไม่ parse `retry_after`, ไม่ sleep, ไม่แยก 429 → ฝั่งส่ง: นับเป็น delivery fail เผา retry (default 3, fixed 8s) จน **คำตอบไม่ถึงผู้ใช้**; ฝั่ง getUpdates: 429 ทริป §H10
> เคส: คำตอบยาวแตกหลาย chunk ส่งติด ๆ → 429 กลางทาง → retry ที่ 8s (อาจสั้นกว่าที่ Telegram ขอ) ครบ cap → ผู้ใช้ไม่ได้คำตอบ

แก้แล้วใน worktree: `TelegramGatewayError` เก็บ `retry_after_s`/`status_code`; `_telegram_api_request` และ `_telegram_api_multipart` parse `parameters.retry_after`; outbound retry ใช้ `retry_after_s` เป็น `nextRetryAt` เมื่อ Telegram ส่งมา; tests `test_telegram_api_request_exposes_retry_after_from_http_429` และ `test_outbound_retry_uses_telegram_retry_after` ยืนยัน path นี้

**M — คำตอบยาว: progress-repair finalize ส่งแค่ chunk[0] ทิ้งที่เหลือ** ✅ fixed
เดิม: `telegram_gateway.py:1184-1186` แตก chunk แล้ว edit แค่ `chunks[0]` เข้า progress message ทิ้ง `chunks[1:]` (ต่างจาก path หลัก `send_telegram_payload` ที่ loop ส่งทุก chunk) → คำตอบ >3800 ตัวถูกตัดเงียบถ้า path นี้ชนะ race

แก้แล้วใน worktree: `_repair_completed_telegram_progress_message` edit progress message เป็น chunk แรก แล้วส่ง `sendMessage` ต่อสำหรับ `chunks[1:]`, เก็บ `receipts`/`chunkCount`, และไม่ตั้ง `reply_parameters` ซ้ำบน chunk ต่อเนื่อง; test `test_completed_progress_job_sends_remaining_chunks_after_progress_final_edit` ยืนยัน 3 chunks ถูกส่งครบ
**M — webhook ingest แบบ synchronous บล็อก response** ✅ fixed
เดิม: `main.py:9532-9537` `await handle_telegram_update` ทำ getFile+download ในคำขอ → media ใหญ่ทำ response ช้า → Telegram retry/ส่งซ้ำ; raise (ไฟล์เกิน) → 500 แทนผลลัพธ์ "ignored"
แก้แล้วใน worktree: `/api/telegram/webhook` ตรวจ secret/JSON แล้ว enqueue `telegram_update` job ทันที (`webhookAsync=true`); `process_telegram_update_job(...)` ประมวลผล update ใน worker และบันทึก `telegram_update_error` + activity ถ้า ingest ล้ม โดยไม่ทำให้ webhook request fail; registered `set_telegram_file_store(_store_file_artifact)` ทำให้ media attachment ยังเข้า artifact pipeline ได้; tests `test_webhook_ingest_enqueues_update_without_synchronous_processing` และ `test_telegram_update_job_records_failure_without_raising`
**L — getUpdates long-poll timeout coupling → error ปลอมตอน idle** ✅ 🟡 `telegram_gateway.py:1678-1683, 505-511`

> ตรวจแล้ว **ปลอดภัย**: webhook secret ใช้ `hmac.compare_digest`; inbound dedup (telegram_update/client-message-id/outbox receipt) แน่น; ส่ง fail ปกติ requeue `telegram_outbound` และ 429 ใช้ `retry_after` แล้ว; ถ้า retry attempt หมดจะเหลือเป็น failed receipt ให้ตรวจย้อนหลังได้

---

## 9. Security (Auth / SSRF / MCP / Secrets / Auto-approve)

> หมายเหตุ: ATRIUM เป็น local-first single-user (default bind `127.0.0.1:8787`) — calibrate ความรุนแรงตามนั้น แต่ **เมื่อ agent ถูก prompt-injection ตัว agent เองคือผู้โจมตีในเครื่อง** ช่องด้านล่างจึงสำคัญแม้ไม่เปิด network

**H2 — unrestricted MCP เป็น accepted capability; ไม่บังคับ allowlist แบบ deny** ✅ fixed
เดิม: `mcp_runtime_block_reason(...)` รับ `enabled_servers` แต่ไม่ enforce และ status ใช้ชื่อ `mcpEnabledServers` ทำให้เหมือนเป็น allowlist จริง
แก้แล้วใน worktree: เพิ่ม `mcp_unrestricted_policy(...)` ที่ประกาศ `mode=unrestricted`, `allowlistEnforced=false`, `configuredServersPurpose=status_visibility_only_not_a_deny_gate`; `/api/runtime` และ credential readiness expose `mcp.policy`; ผล `mcp.call` ทั้ง `/api/tools/run` และ chat tool มี `policy/audit`; และเพิ่ม activity audit `mcp.call <server>.<tool> ในโหมด unrestricted` โดยยังไม่ block server นอก config
> เคสที่ยอมรับ: ตั้ง allowlist = github เท่านั้น แต่ agent เรียก `mcp.call server="drive"` หรือ server อื่นที่ gateway/local executor รองรับได้ (server unknown จะได้เฉพาะ status/unsupported ตาม executor)

**H3 — private/localhost fetch ผ่าน `allowPrivateHosts` เป็น accepted capability; ไม่ปิด flag นี้** ✅ fixed
เดิม: `web.fetch` อนุญาต private/localhost เมื่อส่ง `allowPrivateHosts=true` แต่ไม่มี owner-visible marker/audit ว่า fetch นั้นเป็น private-network action
แก้แล้วใน worktree: `execute_web_fetch(...)` คืน `networkAudit` พร้อม `allowPrivateHosts`, `privateNetwork`, `resolvedAddresses/resolvedPrivateAddresses`, trace ต่อ redirect และ warning; `/api/tools/run` กับ chat tool เพิ่ม activity audit `web.fetch private-host visibility: <host> (allowPrivateHosts=true)` เฉพาะ private fetch โดยไม่ block หรือปิด flag
**M — DNS-rebinding / re-resolve visibility gap** ✅ fixed visibility
เดิม: ตรวจ host แล้ว แต่ `opener.open` re-resolve ตอน connect → record TTL สั้นสลับ host ได้

แก้แล้วใน worktree: `_bounded_get(...)` เก็บ post-fetch network audit หลังอ่าน response แล้ว และ `execute_web_fetch(...)` เพิ่ม `networkAudit.dnsReResolve`, `postFetchResolvedAddresses`, `postFetchResolvedPrivateAddresses` เพื่อเทียบ pre-fetch กับ post-fetch resolve โดยไม่ pin IP, ไม่ block private host, และไม่ปิด `allowPrivateHosts`; test `test_fetch_records_post_fetch_dns_reresolve_visibility`
**L — header (Authorization/Cookie) ถูก replay ข้าม host ตอน redirect** ✅ 🟡 `web_tools.py:316-335`
**L — `extract_text_from_uri`/`bytes_from_uri` อ่านไฟล์ local ผ่าน `file://`/path เปล่า (latent)** ✅ 🟡 `file_intake.py:232-238, 266-274` (ยังไม่พบ sink ที่ agent คุม `uri` ได้โดยตรง — เป็น defense-in-depth)

**H4 — `permission_mode="full_auto"` + `agentFullAccess=True` เป็น design intent ถาวร** ✅ fixed
แก้แล้วใน worktree: `_normalize_permission_mode` บังคับ effective mode เป็น `full_auto` เสมอ, `set_permission_policy(..., agentFullAccess=false)` ยังคง `agentFullAccess=True` และเก็บ `requestedAgentFullAccess=false` ไว้ตรวจสอบ; `/api/policy` และ `/api/runtime.v2.fullAutonomy` คืน `fullAutonomyStatus`; `/api/tools/run` และ chat `run_owner_tool` ใส่ `fullAutonomy` metadata + activity audit สำหรับ risky auto-approved actions; tool checkpoint มี `rollbackPlan`; test `tests/test_full_autonomy_visibility.py` ยืนยันทั้งหมดโดยไม่เพิ่ม approval gate หรือปิด entitlement

**M — API ไม่มี auth ทุก endpoint** ✅ ⚪ accepted local-first + exposure visibility fixed
ตรวจแล้ว: ไม่มี `Depends/Security/dependencies` บังคับ auth จริง และ `x-atrium-actor` เป็น actor label เท่านั้น; default bind ยังเป็น `127.0.0.1:8787`

ตัดสินใจตาม product intent: ไม่แก้ด้วย mandatory API token/shared-secret gate เป็น default เพราะจะลดสิทธิ์ local AI/UI และขัด Full Autonomy; ถ้าจะเปิดออกนอก loopback ให้ทำเป็น owner-controlled boundary/reverse proxy แยก ไม่ใช่ gate ที่ปิด local runtime

แก้แล้วใน worktree: เพิ่ม `_api_exposure_status(...)` (`main.py:3496-3539`) และ expose `apiExposure` ใน `/health` + `/api/runtime` (`main.py:8135-8138, 8199-8202`) เพื่อบอก `host/port`, loopback/network-exposed, CORS wildcard, warning, และ `auth.enforcement="not_enforced"` แบบ visibility-only; tests `test_loopback_api_exposure_is_local_visibility_only` และ `test_network_exposed_api_reports_warning_without_enforcing_auth`
**M — chat attachment ไม่ควรใช้ byte upload เป็น path หลัก; ให้ใช้ local path reference + optional copy** ✅ fixed
แก้ใน worktree: เพิ่ม `POST /api/attachments/reference` + `AttachmentReferenceInput.copyToWorkspace=false` เป็นค่า default, backend สร้าง artifact `storage=external`, `referenceKind=local_path`, `copyStatus=not_copied`, เก็บ `sourcePath/sourceSizeBytes/sampledBytes` และมี marker เมื่อ context ถูกตัด; UI Composer เปิดช่องแนบ path เป็นทางหลักและยังคง `/api/attachments/upload` เป็น fallback/native-browser case โดยไม่ปิดความสามารถ upload จริง
**L — CORS `allow_credentials=True` + wildcard methods/headers** ✅ 🟡 `main.py:756-762` (origins เป็น allowlist localhost จึงปลอดภัยตามสภาพ แต่เสี่ยงถ้า operator ขยาย `cors_origins`)
**6.3 — redact token ด้วย regex** ✅ 🟡 (downgraded — regex แน่นพอ ช่องหลบแคบ) `chat_tools.py:322`

> ตรวจแล้ว **ปลอดภัย**: `/api/runtime` คืนแค่ bool presence (ไม่ใช่ค่า secret), `/api/provider-auth/env` mask secret, ไม่มี endpoint dump `os.environ`, WebSocket cleanup ถูกต้อง (bounded queue + unsubscribe ใน finally)

---

## 10. Database (Schema / Index / Query)

**H — `_detect_anomalies` full-scan `cost_records` ทั้งประวัติ/แผนกในทุก `cost_report` path** ✅ fixed
เดิม: `db/repo.py:1469-1477` `SELECT * FROM cost_records WHERE department_id=:did` **ไม่มี ts lower bound, ไม่มี LIMIT** (กรอง 7 วันใน Python หลังโหลดหมด) + N+1 ต่อแผนก; เรียกจาก `cost_report` ใน engine cost-forecast path (`engine.py:749`; ชื่อฟังก์ชันปัจจุบันยังมีคำว่า budget), command `/cost` (`main.py:1589`), API (`main.py:10719`) และ finance tool (`chat_tools.py:7330`); index `ix_cost_dept_ts` ช่วยไม่ได้เพราะ query ไม่มี ts predicate
> เคส: บริษัทรัน 24/7 สะสม cost หลายเดือน → ทุกครั้งที่เปิด/เรียก cost report หรือ cost forecast จะโหลด cost ทั้งหมดต่อแผนก latency/RAM โตตามประวัติ × จำนวนแผนก

แก้แล้วใน worktree: `_detect_anomalies` query เฉพาะ `(department_id, ts, usd)` โดยมี `T.CostRecordRow.ts >= window_start` และ filter department ใน SQL (`department_id == dept_id` หรือ `IN (ids)`), แล้ว aggregate today/prior ใน memory จากข้อมูล 7 วันเท่านั้น; test `tests/test_cost_report_queries.py` ยืนยันว่า cost_records SELECT ใน `cost_report` มี `ts >=` และ anomaly ยังทำงาน

**M — entities query กรอง type+status/dept แต่ order by ts → ไม่ใช้ index** ✅ fixed
เดิม: `db/repo.py:list_entities` index มีแค่ `(type, ts)`; caller กรอง `type+status` / `type+dept+project+status` (`tool_run` ปริมาณสูง, limit 1000-2000) → scan/sort เกินผลลัพธ์มาก

แก้แล้วใน worktree: เพิ่ม composite indexes `ix_entities_type_status_ts`, `ix_entities_type_dept_ts`, `ix_entities_type_dept_status_ts`, และ `ix_entities_type_dept_project_status_ts` ทั้งใน ORM metadata และ SQLite additive index list; test `test_db_schema_indexes.py` ยืนยัน index metadata + SQLite additive path
**M — `all_threads` N+1 + aggregation ไม่มี LIMIT** ✅ fixed
เดิม: `db/repo.py:all_threads` GROUP BY ทั้งตาราง messages ทุก snapshot แล้ว query ต่อ thread ด้วย `thread_messages(...)` ทำให้ snapshot ใช้ N+1 query และโหลดรายชื่อ thread เกิน `state_thread_count`

แก้แล้วใน worktree: เมื่อ caller ส่ง `max_threads`, `all_threads` จำกัด recent thread query ตาม `max_threads`, preserve preferred threads ที่มีข้อความอยู่, แล้ว batch-fetch ข้อความล่าสุดของทุก selected thread ด้วย window function แทนการเรียก `thread_messages` ทีละ thread; test `test_all_threads_limits_threads_preserves_preferred_and_batches_messages` ยืนยัน limit/preferred/batch path
**M — SQLite migration เป็น additive-only + แยกกลไกจาก Postgres (Alembic) → drift เงียบ** ✅ fixed
เดิม: `db/base.py` เพิ่ม nullable column/index ได้เท่านั้น เปลี่ยน type/NOT NULL/rename/drop ไม่ได้ และไม่มี version stamp → schema change ที่เกิน "add column" จะลง Postgres แต่ข้าม SQLite เงียบ

แก้แล้วใน worktree: เพิ่ม SQLite schema metadata table + expected/actual schema fingerprint, stamp version, `matchesExpected`, missing table/column/index report, และ expose ผ่าน `Repo.database_schema_health()` ใน `/health` + `/api/runtime`; tests `test_sqlite_schema_stamp_records_current_fingerprints` และ `test_repo_database_schema_health_exposes_sqlite_status`
หมายเหตุ: ยังตั้งใจเป็น additive-only เพื่อไม่เสี่ยงทำลายข้อมูล local SQLite; type/NOT NULL/rename/drop ยังต้อง explicit migration แต่จะไม่เงียบใน runtime status แล้ว
**L-M — hot read หลายจุดคืนทั้งตาราง (ไม่มี LIMIT)** ✅ 🟡 `list_objectives`(1323), `list_departments`(653), `delete_department` โหลด `select(T.Task)` ทั้งตาราง (773), `reembed_stale_knowledge`/`migration_status` โหลด memory_knowledge ทั้งหมด (1737/1789); `T.Objective` ไม่มี index `department_id`
**L — `cost_records.category` ไม่มี index/CHECK และ typo category หลุดรายงาน** ✅ fixed
เดิม: `Repo.add_cost` เก็บ `category` string ตรง ๆ; `FinancePanel` แสดงเฉพาะ 6 category ตาม contract ทำให้ typo เช่น `toool` ถูกนับใน DB/report raw แต่ไม่เห็นใน breakdown หลัก และตารางไม่มี category index/check สำหรับ DB ใหม่
แก้แล้วใน worktree: เพิ่ม `normalize_cost_category(...)` ให้ category นอก contract ถูก map เข้า bucket `tool` พร้อมแนบ `rawCategory=...` ใน detail/ledger, เพิ่ม `CHECK` สำหรับ DB ใหม่, และเพิ่ม `ix_cost_category_ts`/`ix_cost_dept_category_ts` รวมถึง SQLite additive index; tests `test_add_cost_normalizes_unknown_category_to_visible_tool_bucket` และ `test_cost_record_category_has_check_and_query_indexes`
หมายเหตุ: FK-hardening ทั้ง schema ยังเป็นงานแยกเชิง schema governance ไม่ใช่ข้อ `cost category` นี้โดยตรง

> ตรวจแล้ว **ปลอดภัย**: ไม่พบ cost double-count ใน cost_report; `expire_on_commit=False` + `commit_and_release` ใช้กับ dict (`r.data`) เป็นหลัก จึงไม่ค่อยเจอ stale ORM object

---

## 11. Memory / Knowledge / Embeddings

**H7a — knowledge ที่ถูกแก้ไขยังใช้ embedding เก่าตลอดไป** ✅ fixed
เดิม: `db/repo.py:1842-1853` `edit_knowledge` แก้ `row.text` แต่ **ไม่ re-embed/ไม่แตะ embedding**; ตัวตรวจ stale (`1709-1727`) ดูแค่ provider/model/dim ไม่มี content hash → entry ที่แก้แล้วถือว่า "current" ไม่ถูก re-embed → RAG จัดอันดับด้วยเวกเตอร์ของข้อความเก่า
แก้แล้วใน worktree: เมื่อ patch มี `text` ใหม่ที่ต่างจากเดิม จะ clear `embedding` + embedding metadata และ clear pgvector column (`_set_pgvector_embedding(row.id, None)`) ทำให้ `reembed_stale_knowledge` เห็นเป็น stale และ search ไม่ใช้ vector เก่า
**H7b — โค้ด re-embed/migration เป็น dead code** ✅ fixed
เดิม: `db/repo.py:1781` `reembed_stale_knowledge` + `1729` `migration_status` ไม่มี caller/route/cron เลย → เปลี่ยน embedding provider (hash→ollama:bge-m3, dim 256→1024) แล้วไม่มีอะไร rebuild; pgvector กรอง `vector_dims=:dims` → knowledge dim เก่า **หายจากผลค้นเงียบ ๆ**
แก้แล้วใน worktree: เพิ่ม `GET /api/knowledge/embedding-migration` สำหรับตรวจสถานะ และ `POST /api/knowledge/reembed-stale` สำหรับ rebuild stale embeddings แบบกำหนด `limit`/`batchSize` ได้; test `tests/test_knowledge_embedding_migration.py` ครอบทั้ง clear embedding และ route wiring
> เคสเดิมก่อนแก้: รัน offline ก่อน (hash embedder) แล้วเปิด Ollama ภายหลัง → knowledge เก่าทั้งหมดหายจาก search หรือคิดคะแนนมั่ว ไม่มี migration อัตโนมัติ

**M — graph edge ไม่มี unique constraint → ทวีคูณซ้ำ** ✅ fixed
เดิม: `add_graph_edge` insert เสมอ ไม่ upsert; dedup แค่ภายใน extraction เดียว → compaction ซ้ำ ๆ สะสม edge `(from,to,rel)` เดิมไม่จบ ทำ graph context/knowledge-debt เพี้ยน
แก้แล้วใน worktree: `Repo.add_graph_edge` query edge เดิมตาม `(department_id, from_id, to_id, rel)` แล้ว update metadata แทน insert ซ้ำ; graph mirror ถูกเรียกเฉพาะตอน insert ใหม่; tests `test_add_graph_edge_updates_existing_edge_instead_of_inserting_duplicate` และ `test_add_graph_edge_inserts_when_edge_is_new`
**M — embed fail ตอน compaction ทิ้ง compaction ทั้งก้อน** ✅ fixed
เดิม: `engine.py` เรียก `resolve_embedder().embed(...)` ตรง ๆ หลัง Ollama probe; ถ้า embed จริง fail จะ exception → job failed/rollback archive+ledger และ knowledge ของ thread นั้นไม่ถูกสกัด
แก้แล้วใน worktree: `_compact_department` retry primary embedder สั้น ๆ แล้ว fallback เป็น `HashEmbedder` โดยไม่ทิ้ง compaction; archive/hub pulse เก็บ `embeddingFallback`, knowledge ติด tag `embedding:fallback`, และ activity เตือน severity `warn`; test `test_compaction_falls_back_to_hash_embedding_when_primary_embed_fails`
**L — warehouse import ไม่ dedup → re-import = knowledge ซ้ำ** ✅ fixed
เดิม: `memory/warehouse.py` ใช้ `uid("wh")` ทุกครั้ง ทำให้ import source+text เดิมซ้ำแล้วสร้างทั้ง warehouse entity และ knowledge embedding ซ้ำ
แก้แล้วใน worktree: สร้าง deterministic `dedupeKey` จาก `departmentId/sourceKind/sourceUri/text`, ใช้ `wh_<hash>` เป็น id, ตรวจ duplicate ก่อน embed/add knowledge และรองรับ legacy warehouse entity ที่ยังไม่มี `dedupeKey` ด้วยการเทียบ source/text; tests `test_import_text_source_dedupes_same_source_and_text`, `test_import_text_source_dedupes_legacy_entry_without_dedupe_key`, และ `test_import_text_source_allows_changed_text_from_same_source`

> ตรวจแล้ว **ปลอดภัย**: object store atomic + content-addressed; `events.py` bounded queue (drop เก่า) แต่ notify persist ลง DB ก่อน → ไม่หายถาวร; extraction มี fallback กัน {} ไม่ให้ crash

---

## 12. UI — Flow ส่งงาน (Assign Task)

**H12a — server fail ถูกกลืนเงียบ → phantom task โผล่แล้วหาย** ✅ fixed
เดิม: `ui/src/contract/ApiClient.ts:1347-1357` (`command()` `.catch` แค่ `console.error` + `refresh()`) + `ui/src/contract/ApiClient.ts:604-606` (optimistic push task เข้า state ก่อน POST)
assignTask push task เข้า state ทันที แล้ว fire POST แบบ fire-and-forget; ถ้า server 500/validation fail → ไม่มี toast/error state, task โผล่แล้วหายตอน snapshot ถัดไป **ผู้ใช้เข้าใจว่าส่งงานสำเร็จ ทั้งที่ไม่มีอะไรเกิด**
แก้แล้วใน worktree: `CompanyClient.assignTask` เปลี่ยนเป็น `Promise<Task>`; `ApiClient.assignTask` await `POST /api/tasks` ก่อน update state และใช้ task ที่ backend ส่งกลับเท่านั้น จากนั้น refresh แบบ best-effort

**H12b — submit ไม่ await server, modal ปิด+navigate ก่อนยืนยัน, input ที่พิมพ์หาย** ✅ fixed
เดิม: `ui/src/panels/AssignTaskModal.tsx:86-100` `client.assignTask(...)` (sync, ไม่ await) แล้ว `onClose()` ทันที → form (key ตาม open) ถูก unmount ทำลาย state title/detail → ถ้า fail ข้อความที่พิมพ์หาย ไม่มีให้ retry
แก้แล้วใน worktree: `submit` เป็น async, ตั้ง `submitting`, await `client.assignTask`, ปิด modal เฉพาะเมื่อสำเร็จ; เมื่อ fail จะแสดง `role="alert"` และคง title/detail/review/dept state ให้ retry ได้

**M — ไม่มี disable-on-submit/idempotency → ดับเบิลคลิกได้ task ซ้ำ** ✅ fixed
เดิม: `ui/src/panels/AssignTaskModal.tsx:203-211` ปุ่ม disable แค่ `!canSubmit`; แต่ละครั้ง mint id ใหม่ → 2 คลิกเร็ว = 2 task
แก้แล้วใน worktree: `submitting` disable ปุ่ม และ `submittingRef` กัน submit ซ้ำก่อน React render disabled state
**L — `reviewMinutes=0` ถูกบีบเป็น 5 (ปิด reminder จาก modal ไม่ได้)** ✅ fixed
เดิม: `ui/src/panels/AssignTaskModal.tsx:88` `Math.max(1, Number(reviewMinutes)||5)` ขัด API contract (`0`=ปิด)
แก้แล้วใน worktree: modal ยอมรับ `min=0` และ submit แปลงเลขติดลบเป็น 0 แต่ไม่บีบ 0 เป็น 1; backend test `test_new_task_review_interval_allows_explicit_disable` ยืนยัน `0` ปิด reminder

> ตรวจแล้ว **ปลอดภัย**: required-field gate ตรง server (title+dept); ทุก field ที่ modal เก็บถูกส่งจริง; transport layer (`request()`) throw error ชัด redacted — H12 และ `reviewMinutes=0` แก้แล้วใน worktree

---

## 13. Config defaults ที่เสี่ยง

| รหัส | default | ผล | ไฟล์ |
|------|--------|----|------|
| C1 | `permission_mode="full_auto"` + `agentFullAccess=True` + entitlements ON | ตั้งใจใช้เสมอใน Full Autonomy; status/audit/checkpoint แก้แล้วใน H4 และยังไม่ลดสิทธิ์ | `config.py:310, 230-235` + `db/repo.py:447-680` |
| C2 | `state_task_deliverable_chars=12_000` + handoff snapshot preview limits | ✅ แก้แล้ว: snapshot แสดง `draftDeliverableMarkdown` ได้ตาม default และ handoff มี recent message preview/จำนวนข้อความ โดยยัง clip เพื่อคุม payload | `config.py` → `db/repo.py:_compact_task_for_snapshot` |
| C3 | `chat_history_messages=0` | ⚪ accepted: ส่งทั้ง thread เป็น default เพื่อไม่ลดบริบท/ความสามารถ AI; ไม่แก้ด้วย default truncation ให้ใช้ visibility/compaction แทน | `config.py:323` → prompt assembly |
| C4 | `max_upload_bytes=0` | accepted เฉพาะในฐานะ fallback endpoint; chat UI ใช้ local path reference + optional backend copy แล้ว (= §9) | `config.py:220`, `main.py:/api/attachments/reference`, `Composer.tsx` |
| C5 | `MAX_ATTACHMENT_CONTEXT_CHARS=10_000` (default) | ✅ marker แล้วเมื่อถูกตัด และเพิ่ม per-attachment `contextMaxChars`/`includeFullContext` + `open_local_file.maxChars` ให้ AI ขยายบริบทไฟล์ได้ | `chat_input.py`, `chat_tools.py`, `schema.py` |

(เพิ่ม) **L — streaming sink reconcile เป็น `result.text`** ✅ fixed
เดิม: `ChatMessageStreamSink.finish` เขียนทับข้อความ live deltas ด้วย `result.text` ตอนจบ ทำให้ UI เห็นข้อความระหว่าง stream คนละชุดกับ persisted final ได้โดยไม่มี metadata
แก้แล้วใน worktree: ถ้า final text เป็น suffix ต่อจาก streamed text จะ emit delta ต่อให้ครบ; ถ้า final text diverge จะคง streamed text ที่ผู้ใช้เห็นไว้, mutate `result.text` ให้ตรง persisted text, และติด `streamReconcile` ใน message/msg_done; tests `test_finish_appends_final_suffix_instead_of_silent_rewrite` และ `test_finish_keeps_streamed_text_when_final_text_diverges`
**L — work_visibility dedup ดูแค่ 120 ข้อความท้าย** ✅ fixed
เดิม: `work_visibility.py:90` scan แค่ 120 ข้อความท้าย (thread ยาว → event ซ้ำได้)
แก้แล้วใน worktree: เก็บ `work_visibility_event` entity จาก hash ของ `visibilityEventKey` หลัง emit สำเร็จ และเช็ค entity ก่อน fallback ไป scan recent messages; test `test_work_status_notice_dedupes_after_thread_window_rolls_off`
**L — attachment context override/expand full context** ✅ fixed
เดิม: attachment context ถูก hardcode ที่ 10,000 chars แม้เป็น path-reference ที่ backend รู้ source path/source size แล้ว; AI เห็น marker แต่ยังไม่มี per-message override เพื่อดึงบริบทเพิ่มทันที
แก้แล้วใน worktree: เพิ่ม `contextMaxChars`/`includeFullContext` ใน `MessageAttachment`, preserve override ตอน normalize artifact attachments, `attachment_context` ใช้ known source/content size เมื่อขอ full context, และ `open_local_file` รับ `maxChars`/`includeFullContext`; tests `test_attachment_context_respects_context_max_chars_override`, `test_attachment_context_include_full_context_uses_known_source_size`, `test_normalize_chat_attachments_preserves_context_override_for_artifact`, และ `test_open_local_file_tool_respects_max_chars_for_artifact_preview`
**L — `efforts_for_model(...)[0]` IndexError แฝง** ✅ fixed
เดิม: `catalog.py:271-284` ถ้าเพิ่ม model ที่ `supportedEfforts` ไม่อยู่ใน `EFFORT_ORDER` → `efforts_for_model` คืน list ว่าง แล้ว `default_thinking_effort_for_model(...)[0]` crash ตอนสร้างแผนก/ทุก turn
แก้แล้วใน worktree: explicit `supportedEfforts` ที่ไม่มีค่าถูกต้องจะ fallback ไป effort order ปกติ และ default effort มี fallback `"high"` ชั้นสุดท้าย; test `test_invalid_explicit_supported_efforts_falls_back_without_crashing`

---

## 14. เคสใช้งานจริง (Real-World Scenarios)

- **S1 ปิดฝา Mac/ปิด service กลางงาน** → job ค้าง `running`, boot/reaper กู้แล้ว **rerun**; scheduled objective/trigger task duplication แก้แล้วด้วย deterministic idempotency, reminder chain เดิมขาดถาวรแก้ด้วย rescanner, และ timeout กลาง provider ของ non-chat job กลับคิวพร้อม recovery record แล้ว
- **S2 worker coroutine ตายเงียบไม่ restart** → แก้แล้วใน worktree ให้มี in-flight reaper ดึง stale `running` กลับ queue ระหว่างรัน; scheduled objective/trigger side effects มี idempotency แล้ว
- **S3 ตั้ง cron รายชั่วโมง/วัน** → เดิมยิงทุกไม่กี่นาที; แก้แล้วใน worktree ให้ 5-field cron ใช้หน่วยตาม field (H1)
- **S4 prompt injection ในเนื้องาน** → agent เรียก `web.fetch allowPrivateHosts:true` ดึง cloud metadata, `mcp.call` ไปยัง server/tool ที่ gateway/local executor รองรับแม้นอก allowlist, อ่าน host path ผ่าน video/file route, หรือรัน host shell ใน Full Autonomy — พฤติกรรมนี้เป็น accepted design; เพิ่ม audit/visibility แล้วสำหรับ H2/H3/H4 และ video host paths โดยไม่ใช้ deny/block/approval gate
- **S5 ผู้ใช้แก้ knowledge** → เดิม RAG ยังคืนเวกเตอร์ของข้อความเก่าและ migration dead code; แก้แล้วใน worktree ให้ clear embedding ตอน text เปลี่ยนและมี route re-embed stale knowledge (H7)
- **S6 render วิดีโอยาว / ยกเลิก** → เดิม ffmpeg/node orphan กิน CPU/disk ต่อและยกเลิกไม่ติด; แก้แล้วใน worktree ให้ cancel/timeout signal process, preserve cancelled status, cleanup FFmpeg `render_edit` intermediates, และ cleanup motion cache/extra outputs โดยคง package source ไว้ (§7)
- **S7 ผู้ใช้ Telegram คนหนึ่งส่งไฟล์ใหญ่/route พังก่อน update ถูกบันทึก** → เดิมทั้งบอทหยุดรับข้อความทุกคนและคำตอบยาวเจอ 429 แล้ว retry สั้นเกิน; แก้แล้วใน worktree ให้ polling isolate error ต่อ update และ outbound retry ใช้ `retry_after` (H10, H11)
- **S8 มอบงานผ่าน UI ตอนเน็ตสะดุด** → เดิม task โผล่แล้วหาย, ผู้ใช้คิดว่าส่งแล้ว, ข้อความที่พิมพ์หาย; แก้แล้วใน worktree ให้ await backend, แสดง error โดยคงฟอร์ม, และกัน submit ซ้ำ (H12)
- **S9 ผู้ใช้แก้ task ผ่าน UI ระหว่างแผนกทำงาน** → แก้แล้วใน worktree ให้ engine re-read/merge task ก่อน save จึงไม่ทับ owner edits ด้วยภาพเก่า (3.1)
- **S10 reuse task id / UI retry create** → เดิม upsert ทับ task ที่กำลังทำ; แก้แล้วใน worktree ให้ POST `/api/tasks` ตอบ 409 เมื่อ client id ซ้ำ (H5)
- **S11 บริษัทรันยาวหลายเดือน** → เดิม cost_records full-scan ทุก cost report/cost forecast path; แก้แล้วใน worktree ให้ anomaly query bound 7 วันด้วย `ts >= window_start`, เพิ่ม entities composite indexes, แก้ snapshot `all_threads` ให้ bound/batch, และเพิ่ม SQLite schema stamp/status แล้ว
- **S12 ไม่มี budget limit เป็น design intent** → ระบบต้องไม่หยุดงานเพราะ cost/budget; ให้มีได้เฉพาะ cost telemetry/audit เพื่อดูย้อนหลัง ไม่ใช่ตัวกำหนดการทำงาน

---

## 15. ตารางสรุป Severity (master)

**🔴 High (open):** ไม่มี Top 12 high ที่ยังเปิดอยู่ใน worktree ปัจจุบัน; DB ที่เหลือเป็น medium/low ตาม §10

**✅ Fixed/verified in current worktree:** H1 cron field units · H2 MCP unrestricted visibility/audit · H3 private-host fetch visibility/audit · DNS post-fetch re-resolve visibility · H4 Full Autonomy status/audit/checkpoint visibility · API exposure visibility without auth gate · provider recoveryPolicy without hard breaker · H5 task id collision/409 · H6 task review reminder rescanner · H7 knowledge stale embedding/re-embed route · H8/H9 video cancel/runtime process guard · FFmpeg `render_edit` intermediate cleanup · motion render cache/extra-output cleanup · render_motion partial-render status guard · video host-path audit/visibility · video transcribe per-run dir · image worker timeout retry/requeue · image completed-run idempotency skip · audio transient retry · scheduled job side-effect idempotency · SQLite atomic claim verified · job retry high-attempt visibility without cap · non-chat job timeout recovery/requeue · chat_reply timeout pending-bubble close · token-count fallback source labeling · JSON parse failure visibility · suppressed side-effect error logging · OpenAI retry backoff/jitter · task concurrent-edit merge · graph edge upsert/dedup · warehouse import dedup · compaction embedding retry/hash fallback · streaming sink reconcile · attachment context override/full context · cost category normalization/index · entities composite indexes · all_threads bound/batch snapshot query · SQLite schema stamp/status · in-flight job reaper · catch-up run staggering · objective enqueue lock · trigger one-shot explicit handling · task draft deliverable snapshot default · handoff message snapshot preview · Telegram webhook async ingest · H10 Telegram polling offset starvation · H11 Telegram 429 retry_after · Telegram progress-final remaining chunks · H12 UI assign-task await/error/duplicate guard · DB cost anomaly query bound · UI review reminder disable · chat_reply dedicated-worker isolation · parent-task validation · assign-task empty-title validation · work_visibility persistent dedup · catalog effort fallback

**⚪ Accepted Full Autonomy:** H4 full_auto/agentFullAccess เป็นโหมดถาวรของ product และแก้ด้าน status/audit/checkpoint แล้วใน worktree; งานต่อไปยังเป็น recovery/undo ที่ลึกขึ้น ไม่ใช่การลดสิทธิ์

**⚪ Accepted Unrestricted Tool/Network/File Access:** H2 unrestricted MCP, H3 `allowPrivateHosts`, และ video host-file `sourcePath` access เป็น visibility/audit แล้วใน worktree; external-send/credential use ไม่แก้ด้วย deny/block/sandbox/approval gate; ให้แก้ด้วย status/audit/history/badge/recovery

**⚪ Accepted No-Budget / No-Hard-Retry-Cap Runtime:** ไม่มี budget limit/budget hard stop และไม่มี hard max-attempt/dead-letter default เพราะอาจทำให้ระบบหยุดทำงานหรือหยุด AI ก่อน runtime/provider ฟื้น; ใช้ได้เฉพาะ telemetry/audit/forecast/high-attempt visibility เพื่อดูย้อนหลังหรือแจ้งเตือน

**⚪ Accepted Full-Context Chat History:** `chat_history_messages=0` และ 4.1 compaction-after-turn เป็น default ที่ตั้งใจเพื่อไม่ตัดบริบท AI; ห้ามแก้ด้วย default truncation/summary-only prompt ให้ใช้ compaction, token-source visibility, และ retrieval เสริมแทน

**✅ Medium (open):** ไม่มีข้อ medium ที่ยังเปิดอยู่ใน worktree ปัจจุบัน; รายการที่กระทบสิทธิ์/ความสามารถ AI ถูกจัดเป็น accepted visibility/recovery ตาม §16

**🟡 Low (~16):** 1.7 · 1.8 · 2.2 · 3.2 TOCTOU · 3.3 · 6.3 · 7.1 latent · 7.2 · 7.3 · crash-after-cost double cost · OAuth token leak · resume=restart · SSRF rebinding · header replay · file:// latent · CORS

---

## 16. แยกตามผลกระทบต่อสิทธิ์/ความสามารถ AI

> นิยามในส่วนนี้: "สิทธิ์" = สิทธิ์เรียก tool/MCP/network/filesystem/credential/host shell; "ความสามารถ" = ความสามารถทำงานต่อเนื่อง อัตโนมัติ ใช้บริบทครบ และส่งงานถึงผู้ใช้จริง
> Product intent ที่ยืนยันแล้ว: ใช้ **Owner Trusted / Full Autonomy** เสมอ; ห้ามแก้ด้วยการลดสิทธิ์/approval gate เป็น default ให้แก้ด้วย audit/visibility/recovery/explicit mode แทน

**A. แก้แล้วกระทบสิทธิ์หรือความสามารถ AI (แก้ด้วย visibility/audit เท่านั้น ไม่ลดสิทธิ์)**
1. **H2 MCP allowlist** — ✅ แก้แล้วใน worktree: เปลี่ยนจาก "allowlist ภาพลวง" เป็น `unrestricted_mcp` policy/status + audit log โดยไม่ enforce deny
2. **H3 private/localhost fetch** — ✅ แก้แล้วใน worktree: ยังให้ AI ใช้ `allowPrivateHosts` ได้ และเพิ่ม `networkAudit`, warning, private-host visibility audit, owner-visible history, และ DNS post-fetch re-resolve visibility
3. **H4 full_auto/agentFullAccess** — ✅ แก้แล้วใน worktree: คง Full Autonomy เสมอ, บันทึกคำขอลดสิทธิ์เป็น requested metadata, เพิ่ม `fullAutonomyStatus`, recent risky actions, risky-action audit และ checkpoint `rollbackPlan`
4. **API auth/CORS hardening** — ✅ แก้ด้วย visibility แล้ว: `apiExposure` รายงาน loopback/network-exposed/CORS/auth-not-enforced โดยไม่บังคับ token gate; ถ้า bind ออกนอก loopback ให้ทำ owner-controlled boundary/reverse proxy แยก โดย local AI/UI ต้องยังใช้งานเต็มสิทธิ์
5. **chat attachment path-reference mode** — ✅ แก้แล้วใน worktree: UI แนบ local path แบบ reference-only เป็นทางหลัก ไม่ต้อง upload bytes จริงเป็น default; backend จำ path/source metadata และมี `copyToWorkspace` สำหรับ copy เข้า workspace/object store เมื่อ owner/AI ต้องใช้ไฟล์ต่อ โดยไม่ปิด upload fallback
6. **provider circuit-breaker/resume** — ✅ แก้ด้วย visibility/recovery แล้ว: OpenAI retry backoff/jitter และ `provider.recoveryPolicy` รายงาน retry layers/manual retry/job recovery โดยไม่เพิ่ม hard circuit-breaker; hard `_RetryJobLater` cap/dead-letter ถูกตัดออกในหมวด B
7. **video cancel/timeout/process cleanup** — ✅ H8/H9 แก้แล้วใน worktree: กระทบเฉพาะงาน render ที่ runaway เพราะถูกหยุดจริงแต่ไม่ลดความสามารถ; FFmpeg `render_edit` intermediates cleanup และ motion render cache/extra-output cleanup แก้แล้ว โดยไม่ลบ package source/`node_modules`/final output
8. **video host-file path visibility** — ✅ แก้แล้วใน worktree: ยังคงอ่าน host `sourcePath`/font ได้เต็มสิทธิ์ แต่ manifest/audit ระบุ `pathUsage`/`hostPathAudit` ให้ตรวจย้อนหลังได้

**B. สิ่งที่ตัดออกชัดเจน: ห้ามทำเป็นแผนแก้**
1. **บังคับ MCP allowlist แบบ deny**
2. **ปิด `allowPrivateHosts` จาก LLM หรือ block localhost/private/metadata ทั้งหมด**
3. **เปลี่ยน `permission_mode` จาก `full_auto` เป็น ask/limited หรือปิด entitlements**
4. **hard budget enforcement / budget limit**
5. **hard max-attempt/dead-letter ที่หยุดงาน AI เป็น default**
6. **filesystem sandbox/sourcePath containment ใด ๆ**
7. **upload hard cap โดยไม่มี path-reference/copy fallback**
8. **external-send/credential approval gate แบบบังคับ**

**C. แก้แล้วไม่กระทบสิทธิ์หรือความสามารถ AI ใด ๆ (แก้ได้ทันที เป็น bug/reliability/performance ล้วน)**
1. **H1 cron parser** — ✅ แก้แล้วใน worktree และมี test
2. **H5 task id upsert** — ✅ แก้แล้วใน worktree และมี test
3. **H6 reminder rescanner** — ✅ แก้แล้วใน worktree และมี test
4. **H7 stale embedding/re-embed dead code** — ✅ แก้แล้วใน worktree และมี test
5. **H10/H11 + Telegram progress-final chunks + webhook async ingest** — ✅ แก้แล้วใน worktree: parse `retry_after`, แยก error ต่อ update, ไม่ทำให้ทั้งคิวค้าง, progress-final path ส่ง chunk ที่เหลือต่อจนคำตอบยาวครบ, และ webhook enqueue งานก่อนตอบ Telegram โดยยังใช้ media artifact pipeline ได้
6. **H12 UI fire-and-forget** — ✅ แก้แล้วใน worktree: await server, error state, preserve input, disable-on-submit
7. **job durability** — ✅ in-flight reaper, scheduled objective/trigger side-effect idempotency, high-attempt retry visibility, และ non-chat timeout recovery/requeue แก้แล้วโดยไม่เพิ่ม hard stop; งานที่เหลือคือ `mark_job` visibility
8. **scheduling/objective locks** — ✅ objective enqueue lock, trigger one-shot explicit handling, และ catch-up run staggering แก้แล้วใน worktree
9. **task concurrency** — ✅ แยก main engine ไม่ claim `chat_reply` ซ้อน worker เฉพาะทางแล้ว และ engine re-read/merge task ก่อน save เพื่อ preserve owner edits ระหว่าง AI ทำงาน
10. **provider/chat correctness** — ✅ ปิด pending bubble ตอน timeout, token-count fallback source labeling, JSON parse error visibility, suppressed side-effect error logging, OpenAI retry backoff/jitter, และ provider recoveryPolicy visibility แล้ว
11. **media reliability** — ✅ image worker timeout retry/requeue, image completed-run idempotency skip, audio transient retry, FFmpeg `render_edit` cleanup, motion render cache/extra-output cleanup, render_motion partial-render status guard, และ video transcribe per-run dir แก้แล้วใน worktree
12. **DB performance/schema** — ✅ bound `_detect_anomalies` ด้วย ts predicate, เพิ่ม entities composite indexes, แก้ `all_threads` snapshot เป็น bound/batch, เพิ่ม SQLite schema stamp/status, และยืนยัน SQLite atomic job claim แล้ว; งานที่เหลือคือ FK/CHECK/index cleanup
13. **memory/knowledge dedup/fallback** — ✅ graph edge upsert/dedup, warehouse import dedup, และ compaction embed retry/hash fallback แก้แล้วใน worktree
14. **config/UI correctness** — ✅ `reviewMinutes=0`, parent task validation, assign-task empty-title validation, work_visibility persistent dedup, catalog effort fallback, streaming sink reconcile, attachment context override/full context, task draft deliverable snapshot default, และ handoff message snapshot preview แก้แล้วใน worktree; `chat_history_messages=0` เป็น accepted full-context default ไม่ลดความสามารถ AI

**D. แก้แล้วเพิ่มสิทธิ์หรือความสามารถ AI**
1. **เปิดทางปิด reminder จาก UI (`reviewMinutes=0`)** — ✅ แก้แล้วใน worktree: ให้ owner/AI เลือกงานที่ไม่ต้องปลุกซ้ำได้จริง
2. **re-embed + migration/reindex route** — AI ใช้ knowledge ที่แก้ล่าสุดได้จริง และกู้ knowledge เก่าที่ไม่มี embedding ได้
3. **Telegram 429 retry + long-answer progress repair** — ✅ H11 และ progress-final chunk repair แก้แล้วใน worktree: AI สื่อสารกับผู้ใช้ Telegram ได้ครบขึ้นเมื่อเจอ rate limit และคำตอบยาวไม่ถูกตัดเหลือ chunk แรก
4. **true video resume / job resume UI** — AI กลับมาทำงานยาวต่อจาก partial state ได้ แทน restart ทั้งหมด
5. **path-reference attachment + optional backend copy + attachment context marker/override** — ✅ แก้แล้วใน worktree: AI ใช้ไฟล์ใหญ่ได้เร็วขึ้นโดยไม่ต้อง upload ผ่าน HTTP, backend จำ path ไว้และ copy-on-use เข้า workspace/object store ได้เมื่อจำเป็น, รู้ชัดเมื่อบริบท attachment ถูกตัด, และขยายบริบทผ่าน `contextMaxChars`/`includeFullContext` หรือ `open_local_file.maxChars` ได้
6. **artifact/content recovery tooling** — เพิ่มปุ่ม/route ให้ AI ดึง full content, re-run extraction, หรือ repair missing refs ได้เอง
7. **audit + undo/checkpoint ใน full autonomy mode** — ✅ H4 แก้แล้วใน worktree สำหรับ status/audit/checkpoint/rollbackPlan metadata; งานต่อไปคือ undo/recovery tooling ที่ execute rollback ได้ลึกขึ้น
8. **provider fallback/resume ที่เห็นสถานะ** — ✅ image worker timeout retry/requeue, non-chat timeout recovery/requeue, OpenAI retry backoff/jitter, provider recoveryPolicy visibility, และ job high-attempt visibility แก้แล้ว ทำให้งานกลับคิว/รอ provider ได้ดีขึ้นแทน fail เงียบ โดยไม่เพิ่ม hard circuit breaker

---

## 17. ข้อเสนอแนะจัดลำดับ

**แก้ทันที (บั๊ก active / ความปลอดภัย):**
1. ✅ แก้ cron parser ให้ดูตำแหน่ง fieldแล้ว (H1)
2. ✅ ทำ MCP mode ให้ตรงความจริงแล้ว: ประกาศ unrestricted + audit log แทนการ enforce `enabled_servers` แบบ deny (H2)
3. ✅ คง private-host fetch ได้ใน Owner Trusted mode และเพิ่ม audit/private-host badge/history + resolved-address trace + post-fetch DNS re-resolve visibility โดยไม่ block (H3)
4. ✅ คง `full_auto`/`agentFullAccess` เสมอและเพิ่ม Full Autonomy status/risky-action audit/checkpoint แล้ว (H4); API exposure visibility (`apiExposure`) บอก host/CORS/auth-not-enforced แล้วโดยไม่บังคับ shared-secret ที่ทำให้ local AI/UI เสียสิทธิ์
5. ✅ เช็ค existence ก่อน `save_task` ใน POST `/api/tasks` แล้ว: duplicate client id ตอบ 409 (H5)

**แก้รอบถัดไป (durability / งานหาย):**
6. ✅ ใส่ rescanner ปลุก reminder จาก `nextReviewAt` แล้ว (H6)
7. ✅ in-flight reaper ดึง stale `running` กลับ queue แล้ว และ scheduled objective/trigger task creation มี idempotency key แล้ว (1.2)
8. ✅ video: เพิ่ม runtime cancel event + process-group signal, cancelled-status guard, FFmpeg `render_edit` intermediates cleanup, motion render cache/extra-output cleanup, render_motion partial-render status guard, transcribe per-run dir, และ host-path audit แล้ว (H8, H9) โดยไม่ทำ filesystem sandbox/sourcePath containment (§7)
9. ✅ telegram: ห่อ `handle_telegram_update` ต่อ updateไม่ให้ batch ค้าง, parse `retry_after`, ให้ progress-final path ส่ง chunk ต่อจนครบ, และเปลี่ยน webhook ingest เป็น async queued job แล้ว (H10, H11, long-answer repair, webhook sync)
10. ✅ UI: await ผล assignTask, แสดง error, คง input, disable-on-submit แล้ว (H12)
11. ✅ chat attachment: เพิ่ม local path reference เป็น flow หลักของ UI แล้ว; backend จำ path แบบ reference-only เป็น default และมี optional copy/copy-on-use ให้ AI ใช้ต่อโดยไม่ปิด upload fallback
12. ✅ knowledge: clear stale embedding ตอน edit + ต่อ `reembed_stale_knowledge` เข้า API route แล้ว (H7)
13. ✅ image generation: worker timeout requeue งานที่ยังเหลือ attempt แล้ว และ completed-run idempotency skip เพื่อไม่ยิง paid provider ซ้ำหลัง run สำเร็จ
14. ✅ state snapshot: handoff ไม่ทิ้ง `messages` เป็น `[]` ทั้งก้อนแล้ว; snapshot มี recent message preview + count/truncated marker แบบ bounded

**แก้เชิงโครงสร้าง:**
15. ✅ task concurrent-edit merge (3.1): engine re-read/merge task ก่อน save แล้ว และไม่ทับ terminal `done/cancelled`; chat_reply dedicated-worker isolation ก็แก้แล้ว
16. ✅ index `cost_records(department_id, ts)` ใช้จริง + composite entities indexes + bound/batch `all_threads` + SQLite schema stamp/status แล้ว; งานถัดไปคือ query/table-wide scan อื่น ๆ (§10)
17. ✅ provider recoveryPolicy รอบ provider (5.1) รายงาน retry/resume โดยไม่ใช้ cost/budget/hard breaker เป็น hard stop; OpenAI retry backoff+jitter แก้แล้ว

---

## ภาคผนวก — วิธีตรวจสอบเพิ่ม
- `/api/runtime`, `/api/health` → `staleRunning`, `retryVisibility`, `highAttemptJobs`, `jobTimeoutS`, cost telemetry; `job_runtime_summary` (`db/repo.py:2390`) ดูงานค้าง/คิวบวม/งาน active ที่ retry สูงโดยไม่หยุดงาน; entity `job_timeout_recovery` ใช้ตรวจ timeout recovery/requeue ย้อนหลัง
- test ที่เกี่ยวข้อง: `system/tests/test_scheduling.py`, `system/tests/test_runtime_job_stability.py`, `system/tests/test_task_review_schedule.py`
- คำเตือนระเบียบวิธี: เลขบรรทัดอ้างอิงจากการตรวจซ้ำ ณ commit `7cb2fd0` — โค้ดที่แก้ภายหลังควรเปิดยืนยันซ้ำ; ข้อ "ตรวจแล้วปลอดภัย" คือผ่านการตรวจเชิงปฏิปักษ์แล้ว ไม่ใช่ไม่ได้ดู

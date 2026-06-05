# Task Control Modal

เอกสารนี้เป็นสเปกสำหรับทำ modal ควบคุมงานจากหน้าต่างงานของ ATRIUM

## คำศัพท์ที่ต้องไม่สับสน

- `ผู้ใช้` คือมนุษย์ที่ใช้งาน UI และกดปุ่มในระบบ
- `ผู้บริหาร` คือ AI executive ของ ATRIUM ไม่ใช่มนุษย์
- `แผนก` คือ AI department/agent ที่รับงานไปทำ
- `งาน` คือ `Task` ที่อยู่ในบอร์ดงานหรือแท็บงานของแผนก

ตัวอย่าง:

- ถ้า `task.origin.kind = "user"` แปลว่า `ผู้ใช้` เป็นคนสั่งงาน
- ถ้า `task.origin.kind = "executive"` แปลว่า `ผู้บริหาร AI` เป็นคนสร้างหรือมอบหมายงาน
- ถ้า modal ส่งคำสั่ง `cancel` จาก UI ต้องบันทึกว่า `requestedBy = "user"` แม้งานนั้นจะถูกสร้างโดย `ผู้บริหาร AI`

## เป้าหมาย

ให้ผู้ใช้กดดูรายละเอียดงานและจัดการงานได้จาก modal เดียว โดยไม่ต้องเดาว่างานไหนควรยกเลิกหรือปิด โดยเฉพาะกรณีที่ผู้บริหาร AI สร้างงานซ้ำหรือแตกงานไว้หลายรายการ

## Non-goals

- ไม่ทำระบบ approval ใหม่ทั้งระบบ
- ไม่เปลี่ยนความหมายของ `ผู้บริหาร` เป็นมนุษย์
- ไม่บังคับให้ทุก action ต้องผ่านผู้บริหาร AI
- ไม่รวมกับ modal มอบหมายงานใหม่

## สถานะระบบปัจจุบัน

ระบบมีส่วนที่ใช้ต่อได้แล้ว:

- `TaskCard` แสดงชื่องาน สถานะ แผนก progress waiting/blocked details และรอบปลุกตรวจงาน
- `DeptTasks` แสดงงานของแผนก
- `TaskBoard` แสดงงานทั้งบริษัท
- Backend มี `POST /api/tasks/{task_id}/request-close` สำหรับขอปิดงานผ่าน flow เดิม
- Backend มี `POST /api/tasks/{task_id}/reassign` สำหรับย้ายงาน
- Backend มี `PATCH /api/tasks/{task_id}/review-schedule` สำหรับตั้งรอบตรวจงาน
- Backend มีสถานะ `cancelled` อยู่แล้ว แต่ยังไม่มี endpoint ให้ผู้ใช้กดยกเลิกงานจาก task modal โดยตรง

## UX behavior

### เปิด modal

คลิก `TaskCard` แล้วเปิด `TaskControlModal`

ตำแหน่งที่ต้องเปิดได้:

- แท็บงานของแผนก
- บอร์ดงานทั้งบริษัท
- รายการงานใน executive monitor ถ้ามี task id ชัดเจน

ถ้าต้องการให้คลิก card ยังเลือกแผนกเหมือนเดิม ให้เพิ่มปุ่ม icon `ดูรายละเอียดงาน` บน card แทน แต่ UX ที่แนะนำคือคลิก card เพื่อเปิด modal เพราะผู้ใช้ feedback ว่าต้องการจัดการจากหน้าต่างงาน

### Header ของ modal

ต้องแสดง:

- ชื่องาน
- สถานะปัจจุบัน
- แผนกเจ้าของงาน
- ผู้สร้างงาน:
  - `ผู้ใช้` ถ้า `origin.kind = "user"`
  - `ผู้บริหาร AI` ถ้า `origin.kind = "executive"`
  - `ฝ่าย{name}` ถ้า `origin.kind = "department"`

ห้ามเขียน copy ที่ทำให้เข้าใจว่า `ผู้บริหาร` คือคนกด UI

คำที่ควรใช้:

- `สร้างโดยผู้บริหาร AI`
- `ผู้ใช้กดหยุดงานนี้`
- `รอผู้บริหาร AI ตรวจงาน`

คำที่ไม่ควรใช้:

- `รอคุณผู้บริหาร`
- `ผู้บริหารกดปิดงาน`
- `ส่งให้คุณอนุมัติ` ถ้าหมายถึง AI executive

## Modal layout

### Section 1: Summary

แสดงข้อมูลหลัก:

- งาน: `task.title`
- รายละเอียด: `task.detail`
- สถานะ: `task.status`
- แผนก: `task.departmentId`
- ความสำคัญ: `task.priority`
- progress: `task.progress`
- สร้างเมื่อ: `task.createdAt`
- อัปเดตล่าสุด: `task.updatedAt`
- origin: แปลเป็นภาษาคนตาม rules ด้านบน

### Section 2: Current state

ถ้างาน `waiting`:

- แสดง `waitingOn.dept`
- แสดง `waitingOn.reason`
- แสดง `approvalId`, `decisionRequestId`, `handoffId` ถ้ามี
- copy ควรเป็น `รอการตอบกลับจากฝ่าย...` หรือ `รอผู้บริหาร AI ตรวจงาน`

ถ้างาน `blocked`:

- แสดง `blockedLastReason`
- แสดง `blockedRetryCount`
- แสดง `blockedRetryGuard`

ถ้างานกำลังทำ:

- แสดง progress
- แสดง log ล่าสุด 5-10 บรรทัด
- แสดง draft/result ล่าสุดถ้ามี

### Section 3: Actions

ปุ่มหลักมี 4 ปุ่ม:

1. `ยกเลิกงาน`
2. `หยุดงาน`
3. `ส่งเท่าที่มี`
4. `ปิดงาน`

ทุก action ต้องมี confirmation สั้นๆ พร้อม reason optional ยกเว้น `ส่งเท่าที่มี` อาจไม่ต้อง confirm ถ้าไม่มีผลทำลายข้อมูล

## Action semantics

### 1. ยกเลิกงาน

ใช้เมื่อผู้ใช้ต้องการยกเลิกงานที่ไม่ต้องทำแล้ว เช่น งานซ้ำ งานผิดแผนก หรืองานที่ผู้บริหาร AI สร้างผิด

ผลลัพธ์:

- `task.status = "cancelled"`
- `task.progress` คงค่าเดิม ไม่ต้อง set เป็น 0 หรือ 1
- clear `task.waitingOn`
- clear `task.pendingCloseApprovalId` ถ้ามี
- ถ้าแผนกเจ้าของงานมี `currentTaskId = task.id` ให้ clear เป็น `null`
- เพิ่ม log: `ผู้ใช้ยกเลิกงาน: {reason}`
- ยกเลิก review reminder/job ที่ผูกกับ task นี้
- best effort ยกเลิก queued runtime/tool jobs ที่ผูกกับ task นี้
- ไม่ลบ artifact หรือ memory ที่สร้างไปแล้ว

คำเตือนใน modal:

`ยกเลิกแล้วงานจะไม่กลับมาทำต่อ แต่ข้อมูลและชิ้นงานที่สร้างไว้จะยังอยู่`

### 2. หยุดงาน

ใช้เมื่อผู้ใช้ต้องการหยุดชั่วคราว แต่ยังไม่อยากยกเลิกหรือปิดงาน

แนะนำเพิ่มสถานะใหม่:

```ts
type TaskStatus = ... | "paused"
```

ถ้ายังไม่อยากเพิ่ม enum ในรอบแรก ให้ใช้ fallback:

- `task.status = "blocked"`
- `task.statusReason = "paused_by_user"`

แต่ระยะยาวควรมี `paused` เพราะ `blocked` แปลว่าติดปัญหา ไม่ใช่ผู้ใช้สั่งพัก

ผลลัพธ์เมื่อใช้ `paused`:

- `task.status = "paused"`
- clear `task.waitingOn`
- ถ้าแผนกเจ้าของงานมี `currentTaskId = task.id` ให้ clear เป็น `null`
- เพิ่ม log: `ผู้ใช้หยุดงานชั่วคราว: {reason}`
- pause/revoke review reminder
- best effort cancel queued runtime/tool jobs ที่ผูกกับ task นี้
- ไม่ถือว่างานเสร็จ
- เพิ่มปุ่ม `ทำต่อ` สำหรับ resume

คำเตือนใน modal:

`หยุดงานจะพักการทำงานของแผนกไว้ก่อน สามารถกดทำต่อได้ภายหลัง`

### 3. ส่งเท่าที่มี

ใช้เมื่อผู้ใช้ต้องการให้ระบบส่งผลลัพธ์ปัจจุบัน แม้งานยังไม่สมบูรณ์

ความหมาย:

- ไม่ใช่การปิดงานทันที
- เป็นการสร้าง deliverable จากสิ่งที่มีอยู่
- แล้วส่งเข้า flow ตรวจ/ปิดงานตามระบบ

แหล่งข้อมูลที่ใช้สร้าง deliverable ตามลำดับ:

1. `task.draftDeliverableMarkdown`
2. `task.result.summary`
3. `task.result`
4. log ล่าสุดของงาน
5. ถ้าไม่มีข้อมูลเลย ให้ตอบ error ว่า `ยังไม่มีผลลัพธ์พอให้ส่ง`

ผลลัพธ์:

- สร้าง artifact หรือ deliverable snapshot
- เรียก flow เดิมเทียบเท่า `POST /api/tasks/{task_id}/request-close`
- `requestedBy = "user"`
- `source = "user_submit_partial"`
- `task.status = "review"` หรือ status ตาม `request_task_close_approval`
- ถ้า flow ต้องให้ผู้บริหาร AI ตรวจ ให้ copy ว่า `ส่งให้ผู้บริหาร AI ตรวจจากผลลัพธ์เท่าที่มี`

คำเตือนใน modal:

`ระบบจะส่งผลลัพธ์ปัจจุบันให้ผู้บริหาร AI ตรวจ งานอาจถูกปิดหรือถูกส่งกลับให้แก้ต่อ`

### 4. ปิดงาน

ใช้เมื่อผู้ใช้ต้องการ mark งานว่าเสร็จหรือจบแล้วจากฝั่ง UI

ต้องแยกจาก `ส่งเท่าที่มี`:

- `ส่งเท่าที่มี` = ส่งผลงานปัจจุบันเข้า review flow
- `ปิดงาน` = ผู้ใช้สั่งจบงานโดยตรง

ผลลัพธ์:

- `task.status = "done"`
- `task.progress = 1`
- clear `task.waitingOn`
- clear `task.pendingCloseApprovalId`
- ถ้าแผนกเจ้าของงานมี `currentTaskId = task.id` ให้ clear เป็น `null`
- เพิ่ม `task.result.reviewStatus = "closed_by_user"`
- เพิ่ม `task.result.completedAt = now`
- เพิ่ม `task.result.closedBy = "user"`
- เพิ่ม log: `ผู้ใช้ปิดงาน: {reason}`
- ยกเลิก review reminder/job ที่ผูกกับ task นี้
- enqueue reflection ได้ แต่ต้องระบุว่า outcome มาจาก user close ไม่ใช่ผู้บริหาร AI อนุมัติ

คำเตือนใน modal:

`ปิดงานจะถือว่างานนี้จบแล้วทันที ไม่ส่งให้ผู้บริหาร AI ตรวจซ้ำ`

## API design

แนะนำให้เพิ่ม endpoint เดียวสำหรับ action จาก modal:

```http
POST /api/tasks/{task_id}/control
```

Request:

```json
{
  "action": "cancel",
  "reason": "งานซ้ำกับ task_xxx",
  "requestedBy": "user"
}
```

Allowed actions:

```ts
type TaskControlAction =
  | "cancel"
  | "pause"
  | "resume"
  | "submit_partial"
  | "close"
```

Response:

```json
{
  "ok": true,
  "task": {},
  "approval": null,
  "artifact": null,
  "executed": true
}
```

เหตุผลที่ใช้ endpoint เดียว:

- UI เรียกง่าย
- audit log กลางทำง่าย
- validation ของ terminal state ทำครั้งเดียว
- ลดการทำ logic ซ้ำระหว่าง `cancel`, `pause`, `close`

ยังคง endpoint เดิมไว้:

- `/api/tasks/{task_id}/request-close` ใช้สำหรับแผนกหรือ AI ขอปิดงานตาม lifecycle เดิม
- `/api/tasks/{task_id}/reassign` ใช้สำหรับย้ายงาน
- `/api/tasks/{task_id}/review-schedule` ใช้สำหรับตั้งรอบตรวจ

## Backend validation

ทุก action ต้องตรวจ:

- task มีจริง
- ถ้า task เป็น `done` หรือ `cancelled` แล้ว ห้าม `cancel`, `pause`, `submit_partial`, `close` ซ้ำ
- ถ้า action คือ `resume` อนุญาตเฉพาะ task ที่ `paused`
- ถ้า action คือ `submit_partial` ต้องมี content พอสร้าง deliverable
- `requestedBy` จาก modal ต้องเป็น `"user"` เท่านั้น

ห้ามให้ client ส่ง `requestedBy = "executive"` จาก modal เพราะจะทำให้สับสนว่า AI executive เป็นคนกด UI

## State transition table

| Current status | cancel | pause | resume | submit_partial | close |
|---|---|---|---|---|---|
| backlog | cancelled | paused | - | error | done |
| assigned | cancelled | paused | - | maybe error | done |
| in_progress | cancelled | paused | - | review | done |
| waiting | cancelled | paused | - | review ถ้ามี output | done |
| blocked | cancelled | paused | - | review ถ้ามี output | done |
| review | cancelled | paused | - | no-op หรือ refresh deliverable | done |
| revising | cancelled | paused | - | review | done |
| paused | cancelled | no-op | assigned/in_progress | review ถ้ามี output | done |
| done | error | error | error | error | no-op |
| cancelled | no-op | error | error | error | error |

## Frontend implementation checklist

1. เพิ่ม `taskControlTaskId` ใน UI store
2. เพิ่ม actions:
   - `openTaskControl(taskId)`
   - `closeTaskControl()`
3. สร้าง `TaskControlModal.tsx`
4. ให้ `TaskCard` เปิด modal
5. ใน `TaskBoard` ไม่ควรปิด board ทันทีตอนคลิก card ถ้า modal ซ้อนทับได้
6. เพิ่ม `client.controlTask(taskId, input)`
7. เพิ่ม loading/error state ต่อปุ่มแต่ละปุ่ม
8. หลัง action สำเร็จให้ `client.refresh()`
9. ปิด modal เฉพาะ action terminal สำเร็จ เช่น `cancel`, `close`
10. `submit_partial` สำเร็จแล้วให้ modal ยังเปิดอยู่และโชว์ว่า `ส่งให้ผู้บริหาร AI ตรวจแล้ว`

## Backend implementation checklist

1. เพิ่ม schema:

```py
TaskControlAction = Literal["cancel", "pause", "resume", "submit_partial", "close"]

class TaskControlInput(Schema):
    action: TaskControlAction
    reason: Optional[str] = None
    requested_by: Literal["user"] = "user"
```

2. เพิ่ม endpoint:

```py
@app.post("/api/tasks/{task_id}/control")
async def control_task(task_id: str, input: TaskControlInput) -> TaskControlResponse:
    ...
```

3. แยก helper:

```py
async def apply_user_task_control(repo, task, input, now):
    ...
```

4. ใน helper ต้อง clear `currentTaskId` ของแผนกเมื่อ action เป็น terminal หรือ paused
5. ต้อง save task และ department ใน transaction เดียวกัน
6. ต้องเพิ่ม activity log เป็นภาษาไทย
7. ต้อง pulse hub ด้วย `taskId` และ `departmentId`
8. ต้อง cancel review reminders/jobs ที่เกี่ยวข้อง
9. สำหรับ `submit_partial` ให้ reuse `request_task_close_approval(...)`
10. สำหรับ `close` ให้บันทึก outcome ว่า `closed_by_user` ไม่ใช่ `approved_by_executive`

## Audit log wording

ตัวอย่าง log:

- `ผู้ใช้ยกเลิกงาน: งานซ้ำกับ task_123`
- `ผู้ใช้หยุดงานชั่วคราว: รอข้อมูลเพิ่มจากลูกค้า`
- `ผู้ใช้ส่งผลลัพธ์เท่าที่มีให้ผู้บริหาร AI ตรวจ`
- `ผู้ใช้ปิดงานทันที: ไม่ต้องทำต่อแล้ว`

ห้ามใช้:

- `ผู้บริหารยกเลิกงาน` เมื่อ action มาจาก modal
- `ผู้บริหารปิดงาน` เมื่อ action มาจาก modal

ใช้ `ผู้บริหาร AI` เฉพาะกรณี AI executive เป็น actor จริง เช่น ตรวจงานหรือสร้างงาน

## Acceptance criteria

- ผู้ใช้เปิด modal จาก task card ได้
- modal แสดงชัดว่างานสร้างโดย `ผู้ใช้`, `ผู้บริหาร AI`, หรือ `ฝ่าย...`
- กดยกเลิกงานแล้ว task เป็น `cancelled` และแผนกไม่ถือ task นี้เป็น `currentTaskId`
- กดหยุดงานแล้ว task ไม่ถูกรันต่อ และ resume ได้ถ้าใช้สถานะ `paused`
- กดส่งเท่าที่มีแล้วมี deliverable/approval flow ชัดเจน และ copy ระบุว่าให้ `ผู้บริหาร AI` ตรวจ
- กดปิดงานแล้ว task เป็น `done` โดยระบุ `closed_by_user`
- งานที่ terminal แล้วไม่สามารถถูกควบคุมซ้ำแบบผิดสถานะ
- ไม่มีข้อความ UI ที่ทำให้เข้าใจว่า `ผู้บริหาร` คือมนุษย์

## Suggested copy

ปุ่ม:

- `ยกเลิกงาน`
- `หยุดงาน`
- `ส่งเท่าที่มี`
- `ปิดงาน`
- `ทำต่อ`

ข้อความแสดง origin:

- `สร้างโดยผู้ใช้`
- `สร้างโดยผู้บริหาร AI`
- `สร้างโดยฝ่าย{name}`

ข้อความสถานะ:

- `ผู้ใช้หยุดงานนี้ไว้ชั่วคราว`
- `ส่งผลลัพธ์เท่าที่มีให้ผู้บริหาร AI ตรวจแล้ว`
- `ผู้ใช้ปิดงานนี้แล้ว`


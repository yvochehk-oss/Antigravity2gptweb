import re

with open("app/routers/rag_sync.py", "r") as f:
    content = f.read()

# We need to replace `def _do_sync` and `sync_single` / `sync_batch`

new_do_sync = """def _do_sync_background(
    sync_log_id: int,
    project_id: int,
    rag_project_id: int,
    rag_url: str,
    rag_api_key: str,
    extract_type: str,
    period_start: str | None,
    period_end: str | None,
    top_k: int,
    request_id: str | None = None,
):
    \"\"\"后台执行一次同步。\"\"\"
    db = SessionLocal()
    try:
        sync_log = db.get(SyncLog, sync_log_id)
        if not sync_log:
            return

        # 1. 调用 RAG 抽取
        try:
            rag_data = _call_rag_extract(
                rag_url, rag_api_key, rag_project_id, extract_type,
                period_start, period_end, top_k, request_id=request_id,
                validation_db=db,
            )
        except Exception as e:
            sync_log.status = "FAILED"
            sync_log.errors_json = json.dumps([f"RAG 服务连接或抽取失败: {e}"])
            db.commit()
            return

        extracted_items = rag_data.get("extracted_items") or []
        errors = rag_data.get("errors") or []

        sync_log.rag_chunk_ids_json = json.dumps([i.get("source_chunk_id", 0) for i in extracted_items])
        sync_log.rag_document_ids_json = json.dumps(list({i.get("source_document_id", 0) for i in extracted_items}))
        sync_log.total_chunks = rag_data.get("total_chunks", 0)
        sync_log.total_extracted = len(extracted_items)
        
        imported_ids: list[int] = []
        pending_ids: list[int] = []
        duplicate_count = 0
        failed_count = 0

        def _pending_item(item: dict, fields: dict, confidence: Decimal, reason: str) -> None:
            pending_fields = dict(fields)
            pending_fields["_review_reason"] = reason
            with db.begin_nested():
                pending = SyncPending(
                    sync_log_id=sync_log.id,
                    project_id=project_id,
                    sync_type=extract_type,
                    source_chunk_id=item.get("source_chunk_id", 0),
                    source_document_id=item.get("source_document_id", 0),
                    filename=item.get("filename") or "",
                    page_start=item.get("page_start"),
                    confidence=confidence,
                    fields_json=json.dumps(pending_fields, ensure_ascii=False),
                    status="pending",
                    note=reason,
                )
                db.add(pending)
                db.flush()
                pending_ids.append(pending.id)

        # 3. 按置信度分流
        for item in extracted_items:
            fields = item.get("fields") or {}
            try:
                confidence = Decimal(str(item.get("confidence", 0)))
            except Exception:
                confidence = Decimal("0")
            if confidence < 0 or confidence > 1:
                confidence = Decimal("0")

            try:
                mapped = _map_fields(db, project_id, extract_type, fields)
                if _dedup_check(db, None, project_id, extract_type, fields, mapped):
                    duplicate_count += 1
                    continue
                if confidence < AUTO_CONF_THRESHOLD:
                    _pending_item(item, fields, confidence, "抽取置信度不足，需人工确认")
                    continue
                with db.begin_nested():
                    record = _import_record(
                        db, project_id, extract_type, fields, mapped=mapped,
                    )
                    db.flush()
                    imported_ids.append(record.id)
            except SyncReviewRequired as exc:
                try:
                    _pending_item(item, fields, confidence, exc.reason)
                except Exception as pending_exc:
                    failed_count += 1
                    errors.append(
                        f"待确认记录写入失败 chunk={item.get('source_chunk_id', 0)}: {pending_exc}"
                    )
            except Exception as exc:
                failed_count += 1
                errors.append(
                    f"入库失败 chunk={item.get('source_chunk_id', 0)}: {exc}"
                )

        # 4. 更新同步记录
        if pending_ids and not errors:
            status = "PENDING_REVIEW"
        elif errors and (imported_ids or pending_ids):
            status = "PARTIAL"
        elif errors:
            status = "FAILED"
        else:
            status = "SUCCESS"
            
        sync_log.status = status
        sync_log.tax_record_ids_json = json.dumps(imported_ids)
        sync_log.total_imported = len(imported_ids)
        sync_log.total_pending = len(pending_ids)
        sync_log.errors_json = json.dumps(errors)
        db.commit()

    finally:
        db.close()
"""

# Let's replace _do_sync entirely.
start_idx = content.find("def _do_sync(")
end_idx = content.find("def _map_fields(", start_idx)

if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + new_do_sync + "\n\n" + content[end_idx:]
else:
    print("Could not find _do_sync")

# Now replace sync_single
sync_single_old = """@router.post("/sync", response_model=SyncResponse)
def sync_single(body: SyncRequest, request: Request):
    \"\"\"触发单类型同步：从 RAG 抽取指定类型数据并入库。\"\"\"
    actor = current_actor(request)

    db = SessionLocal()
    try:
        # 验证项目存在
        proj = db.get(Project, body.project_id)
        if not proj:
            raise HTTPException(404, f"税务系统项目 {body.project_id} 不存在")

        rag_project_id, rag_url, rag_api_key = _resolve_rag_project(
            db, body.project_id, body.rag_project_id,
        )

        return _do_sync(
            db=db,
            project_id=body.project_id,
            rag_project_id=rag_project_id,
            rag_url=rag_url,
            rag_api_key=rag_api_key,
            extract_type=body.extract_type,
            period_start=body.period_start,
            period_end=body.period_end,
            top_k=body.top_k,
            note=body.note,
            actor=actor,
            request_id=get_request_id(),
        )
    finally:
        db.close()"""

sync_single_new = """@router.post("/sync", response_model=SyncResponse)
def sync_single(body: SyncRequest, request: Request, background_tasks: BackgroundTasks):
    \"\"\"触发单类型同步：从 RAG 抽取指定类型数据并入库。\"\"\"
    actor = current_actor(request)

    db = SessionLocal()
    try:
        # 验证项目存在
        proj = db.get(Project, body.project_id)
        if not proj:
            raise HTTPException(404, f"税务系统项目 {body.project_id} 不存在")

        rag_project_id, rag_url, rag_api_key = _resolve_rag_project(
            db, body.project_id, body.rag_project_id,
        )

        sync_log = SyncLog(
            project_id=body.project_id,
            sync_type=body.extract_type,
            rag_project_id=rag_project_id,
            rag_chunk_ids_json="[]",
            rag_document_ids_json="[]",
            tax_record_ids_json="[]",
            status="RUNNING",
            total_chunks=0,
            total_extracted=0,
            total_imported=0,
            total_pending=0,
            errors_json="[]",
            synced_at=_now(),
            synced_by=actor,
            note=body.note,
        )
        db.add(sync_log)
        db.commit()
        db.refresh(sync_log)

        background_tasks.add_task(
            _do_sync_background,
            sync_log_id=sync_log.id,
            project_id=body.project_id,
            rag_project_id=rag_project_id,
            rag_url=rag_url,
            rag_api_key=rag_api_key,
            extract_type=body.extract_type,
            period_start=body.period_start,
            period_end=body.period_end,
            top_k=body.top_k,
            request_id=get_request_id(),
        )

        return SyncResponse(
            sync_log_id=sync_log.id,
            sync_type=body.extract_type,
            status="RUNNING",
            total_extracted=0,
            total_imported=0,
            total_pending=0,
            imported_ids=[],
            pending_ids=[],
            errors=[],
        )
    finally:
        db.close()"""

content = content.replace(sync_single_old, sync_single_new)

sync_batch_old = """@router.post("/sync-batch")
def sync_batch(body: SyncBatchRequest, request: Request):
    \"\"\"批量同步：按类型列表逐一同步。\"\"\"
    actor = current_actor(request)
    results: list[SyncResponse] = []

    db = SessionLocal()
    try:
        proj = db.get(Project, body.project_id)
        if not proj:
            raise HTTPException(404, f"税务系统项目 {body.project_id} 不存在")

        rag_project_id, rag_url, rag_api_key = _resolve_rag_project(
            db, body.project_id, body.rag_project_id,
        )

        for extract_type in body.extract_types:
            try:
                result = _do_sync(
                    db=db,
                    project_id=body.project_id,
                    rag_project_id=rag_project_id,
                    rag_url=rag_url,
                    rag_api_key=rag_api_key,
                    extract_type=extract_type,
                    period_start=body.period_start,
                    period_end=body.period_end,
                    top_k=30,
                    note=body.note,
                    actor=actor,
                    request_id=get_request_id(),
                )
                results.append(result)
            except Exception as e:
                results.append(SyncResponse(
                    sync_log_id=0,
                    sync_type=extract_type,
                    status="FAILED",
                    total_extracted=0,
                    total_imported=0,
                    total_pending=0,
                    imported_ids=[],
                    pending_ids=[],
                    errors=[str(e)],
                ))

        return {"project_id": body.project_id, "results": [r.model_dump() for r in results]}
    finally:
        db.close()"""

sync_batch_new = """@router.post("/sync-batch")
def sync_batch(body: SyncBatchRequest, request: Request, background_tasks: BackgroundTasks):
    \"\"\"批量同步：按类型列表逐一同步。\"\"\"
    actor = current_actor(request)
    results: list[SyncResponse] = []

    db = SessionLocal()
    try:
        proj = db.get(Project, body.project_id)
        if not proj:
            raise HTTPException(404, f"税务系统项目 {body.project_id} 不存在")

        rag_project_id, rag_url, rag_api_key = _resolve_rag_project(
            db, body.project_id, body.rag_project_id,
        )

        for extract_type in body.extract_types:
            sync_log = SyncLog(
                project_id=body.project_id,
                sync_type=extract_type,
                rag_project_id=rag_project_id,
                rag_chunk_ids_json="[]",
                rag_document_ids_json="[]",
                tax_record_ids_json="[]",
                status="RUNNING",
                total_chunks=0,
                total_extracted=0,
                total_imported=0,
                total_pending=0,
                errors_json="[]",
                synced_at=_now(),
                synced_by=actor,
                note=body.note,
            )
            db.add(sync_log)
            db.commit()
            db.refresh(sync_log)

            background_tasks.add_task(
                _do_sync_background,
                sync_log_id=sync_log.id,
                project_id=body.project_id,
                rag_project_id=rag_project_id,
                rag_url=rag_url,
                rag_api_key=rag_api_key,
                extract_type=extract_type,
                period_start=body.period_start,
                period_end=body.period_end,
                top_k=30,
                request_id=get_request_id(),
            )

            results.append(SyncResponse(
                sync_log_id=sync_log.id,
                sync_type=extract_type,
                status="RUNNING",
                total_extracted=0,
                total_imported=0,
                total_pending=0,
                imported_ids=[],
                pending_ids=[],
                errors=[],
            ))

        return {"project_id": body.project_id, "results": [r.model_dump() for r in results]}
    finally:
        db.close()"""

content = content.replace(sync_batch_old, sync_batch_new)

with open("app/routers/rag_sync.py", "w") as f:
    f.write(content)

print("Patched successfully")

from __future__ import annotations

import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.aaa_parser import AaaParser
from backend.config import ConfigLoader
from backend.decompressor import Decompressor
from backend.identifier import Identifier
from backend.log_parser import LogParser
from backend.metadata import MetadataGenerator
from backend.models import ParseResult, TaskInfo, TaskStatus
from backend.scanner import Scanner

app = FastAPI(title="日志解析维护工具", version="0.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

config_loader = ConfigLoader()
decompressor = Decompressor(config_loader)
scanner = Scanner(config_loader)
log_parser = LogParser(config_loader)
identifier = Identifier()
aaa_parser = AaaParser(config_loader)
metadata_gen = MetadataGenerator()

tasks: dict[str, TaskInfo] = {}


def _get_output_dir(task_id: str) -> Path:
    cfg = config_loader.get_config()
    return Path(cfg.output.base_dir) / task_id


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """上传并解析日志压缩包。"""
    task_id = uuid.uuid4().hex[:12]
    task_info = TaskInfo(task_id=task_id, status=TaskStatus.PENDING)
    tasks[task_id] = task_info

    try:
        output_dir = _get_output_dir(task_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / (file.filename or "upload.zip")
        with open(file_path, "wb") as f:
            while chunk := await file.read(8 * 1024 * 1024):
                f.write(chunk)

        extract_dir = output_dir / "extracted"
        errors: list[str] = []

        def _safe_step(message, fn):
            try:
                return fn()
            except Exception as e:
                errors.append(f"{message}: {e}")
                return None

        task_info.status = TaskStatus.EXTRACTING
        task_info.message = "正在解压外层压缩包..."
        _safe_step("解压外层包", lambda: decompressor.extract_all(file_path, extract_dir))

        task_info.status = TaskStatus.SCANNING
        task_info.message = "正在扫描诊断日志目录 diag/ ..."
        diag_slots = _safe_step("扫描 diag", lambda: scanner.scan_diag(extract_dir)) or []

        task_info.message = "正在扫描私有日志目录 varlog/ ..."
        private_slots = _safe_step("扫描 varlog", lambda: scanner.scan_private(extract_dir)) or []

        result = ParseResult(
            task_id=task_id,
            package_name=file.filename or "",
            extracted_root=str(extract_dir),
            diagnostic_slots=diag_slots,
            private_slots=private_slots,
            errors=errors,
        )

        task_info.message = "正在解压槽位内诊断日志..."
        _safe_step("解压诊断日志内容", lambda: _extract_diag_contents(result, extract_dir, output_dir))

        # 机制模块日志解析（优先）
        task_info.message = "正在解析机制模块日志..."
        aaa_results = _safe_step("AAA 解析", lambda: aaa_parser.parse_all(result))
        if aaa_results:
            for aaa_result in aaa_results.values():
                aaa_parser.apply_to_identifier(aaa_result, result)
                _safe_step(f"落盘 {aaa_result.module_name}",
                           lambda ar=aaa_result: aaa_parser.write_output(ar, output_dir))
            result.aaa_results = list(aaa_results.values())

        # 兜底：时间戳提取 + 目录判定
        task_info.status = TaskStatus.IDENTIFYING
        task_info.message = "正在提取时间戳并兜底识别..."
        _safe_step("时间戳提取", lambda: log_parser.build_all_periods(result.diagnostic_slots))
        _safe_step("兜底识别", lambda: identifier.analyze(result))

        cfg = config_loader.get_config()
        if cfg.output.generate_metadata:
            metadata_gen.generate(result, output_dir)

        task_info.result = result
        task_info.status = TaskStatus.DONE
        task_info.progress = 1.0
        task_info.message = "解析完成"

    except Exception as e:
        task_info.status = TaskStatus.ERROR
        task_info.message = str(e)
        task_info.result = ParseResult(task_id=task_id, errors=[traceback.format_exc()])

    return {"task_id": task_id}


def _extract_diag_contents(result: ParseResult, extract_dir: Path, output_dir: Path) -> None:
    """解压每个槽位下的诊断日志压缩包内容 (.zip)。"""
    for slot in result.diagnostic_slots:
        for entry in slot.diagnostic_logs:
            if not entry.compressed:
                continue
            src = Path(entry.path)
            if not src.exists():
                continue
            dest = output_dir / "contents" / slot.name / entry.name.removesuffix(
                "".join(suffix for suffix in [".zip", ".gz", ".tar.gz", ".tgz"] if entry.name.endswith(suffix))
            )
            try:
                decompressor.extract_all(src, dest)
                entry.extracted_path = str(dest)
            except Exception:
                pass


@app.get("/api/task/{task_id}")
async def get_task(task_id: str):
    task = tasks.get(task_id)
    if task is None:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    return task.model_dump(mode="json")


@app.get("/api/task/{task_id}/metadata")
async def get_metadata(task_id: str):
    metadata_file = _get_output_dir(task_id) / "metadata.json"
    if not metadata_file.exists():
        return JSONResponse({"error": "元数据不存在"}, status_code=404)
    return FileResponse(metadata_file, media_type="application/json")


@app.get("/api/task/{task_id}/file")
async def get_file(task_id: str, path: str = ""):
    """获取解压后的具体文件内容。"""
    base = _get_output_dir(task_id) / "extracted"
    file_path = (base / path).resolve()
    if not str(file_path).startswith(str(base.resolve())):
        return JSONResponse({"error": "路径越界"}, status_code=403)
    if not file_path.exists():
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    if file_path.is_dir():
        return JSONResponse({"error": "路径是目录"}, status_code=400)
    return FileResponse(file_path)


@app.get("/api/task/{task_id}/log-content")
async def get_log_content(task_id: str, slot: str, filename: str):
    """获取诊断日志解压后的文本内容。"""
    contents_dir = _get_output_dir(task_id) / "contents" / slot / filename
    if not contents_dir.exists():
        # 尝试列出目录中的所有文件
        if contents_dir.parent.exists():
            files = list(contents_dir.parent.glob(f"{filename}*/**/*"))
            if not files:
                files = list(contents_dir.parent.rglob("*"))
            # 返回第一个文本文件
            for f in files:
                if f.is_file() and f.suffix in (".log", ".txt", ".diag", ""):
                    try:
                        return {"content": f.read_text(encoding="utf-8", errors="replace")[:500000]}
                    except Exception:
                        continue
        return JSONResponse({"error": "日志内容不存在"}, status_code=404)

    # 目录存在，返回目录下所有文件的内容
    texts = []
    for f in sorted(contents_dir.rglob("*")):
        if f.is_file():
            try:
                texts.append(f"\n--- {f.name} ---\n{f.read_text(encoding='utf-8', errors='replace')[:100000]}")
            except Exception:
                texts.append(f"\n--- {f.name} ---\n[二进制文件]")
    return {"content": "\n".join(texts)}


@app.get("/api/task/{task_id}/aaa-slots")
async def aaa_slots(task_id: str):
    task = tasks.get(task_id)
    if task is None or task.result is None or not task.result.aaa_results:
        return JSONResponse({"error": "AAA 结果不存在"}, status_code=404)
    aaa = task.result.aaa_results[0]
    return {
        "module_name": aaa.module_name,
        "active_master_slots": aaa.active_master_slots,
        "slots": [
            {
                "slot_id": s.slot_id,
                "board_cycles": len(s.board_cycles),
                "total_processes": sum(len(c.processes) for c in s.board_cycles),
                "total_logs": sum(cp.total_count for c in s.board_cycles for cp in c.processes),
            }
            for s in aaa.slots
        ],
    }


@app.get("/api/task/{task_id}/aaa-lifecycles")
async def aaa_lifecycles(task_id: str, slot: str = ""):
    task = tasks.get(task_id)
    if task is None or task.result is None or not task.result.aaa_results:
        return JSONResponse({"error": "AAA 结果不存在"}, status_code=404)
    aaa = task.result.aaa_results[0]
    for s in aaa.slots:
        if s.slot_id == slot or not slot:
            return {
                "slot_id": s.slot_id,
                "board_cycles": [
                    {
                        "dir_name": c.dir_name,
                        "start_time": c.start_time.isoformat() if c.start_time else None,
                        "end_time": c.end_time.isoformat() if c.end_time else None,
                        "processes": [
                            {
                                "process_name": p.process_name,
                                "pid": p.pid,
                                "total_count": p.total_count,
                                "missing_sequences": p.missing_sequences,
                            }
                            for p in c.processes
                        ],
                    }
                    for c in s.board_cycles
                ],
            }
    return JSONResponse({"error": "slot 不存在"}, status_code=404)


@app.get("/api/task/{task_id}/aaa-logs")
async def aaa_logs(task_id: str, slot: str = "", dir_name: str = "", proc: str = ""):
    task = tasks.get(task_id)
    if task is None or task.result is None or not task.result.aaa_results:
        return JSONResponse({"error": "AAA 结果不存在"}, status_code=404)
    aaa = task.result.aaa_results[0]
    for s in aaa.slots:
        if s.slot_id != slot:
            continue
        for c in s.board_cycles:
            if c.dir_name != dir_name:
                continue
            for p in c.processes:
                if f"{p.process_name}-{p.pid}" == proc:
                    return {
                        "process_name": p.process_name,
                        "pid": p.pid,
                        "total_count": p.total_count,
                        "missing_sequences": p.missing_sequences,
                        "logs": [
                            {
                                "source": l.source,
                                "sequence": l.sequence,
                                "timestamp": l.timestamp.isoformat() if l.timestamp else None,
                                "context": l.context,
                                "is_active_signal": l.is_active_signal,
                            }
                            for l in p.logs
                        ],
                    }
    return JSONResponse({"error": "未找到"}, status_code=404)


@app.get("/api/config")
async def get_config():
    return config_loader.get_config().model_dump(mode="json")


frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

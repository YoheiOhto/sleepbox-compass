from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .localization import normalize_individual

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".heic", ".webp"}
VIDEO_SUFFIXES = {".mov", ".mp4", ".m4v"}


def discover_inputs(inbox: Path) -> List[Path]:
    return sorted(p for p in inbox.iterdir() if p.is_file()
                  and p.suffix.lower() in IMAGE_SUFFIXES | VIDEO_SUFFIXES)


def extract_frames(source: Path, out: Path, interval: float = 1.0) -> List[Path]:
    out.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() in IMAGE_SUFFIXES:
        target = out / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return [target]
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("動画の処理にはffmpegが必要です")
    pattern = out / f"{source.stem}-%06d.jpg"
    proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(source),
                           "-vf", f"fps=1/{interval}", "-q:v", "2", str(pattern)],
                          capture_output=True, text=True, check=False)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip())
    return sorted(out.glob(f"{source.stem}-*.jpg"))


def sidecar_extract(frame: Path, ocr_command: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Read deterministic OCR/VLM output next to a frame.

    This keeps private images local and lets users plug in any local VLM. Both
    `frame.jpg.json` and `frame.json` are accepted.
    """
    candidates = [Path(str(frame) + ".json"), frame.with_suffix(".json")]
    for path in candidates:
        if path.exists():
            return normalize_individual(json.loads(path.read_text(encoding="utf-8")))
    if ocr_command:
        proc = subprocess.run([ocr_command, str(frame)], capture_output=True, text=True, check=False)
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip() or f"OCRに失敗しました: {frame}")
        return normalize_individual(json.loads(proc.stdout))
    return None


def ingest_path(path: Path, frames_dir: Path, ocr_command: Optional[str] = None) -> List[Dict[str, Any]]:
    result = []
    for source in ([path] if path.is_file() else discover_inputs(path)):
        for frame in extract_frames(source, frames_dir / source.stem):
            item = sidecar_extract(frame, ocr_command) or sidecar_extract(source, ocr_command)
            if item:
                item.setdefault("capture_sha256", hashlib.sha256(frame.read_bytes()).hexdigest())
                item.setdefault("confidence", 1.0)
                item.setdefault("verified", False)
                result.append(item)
    # Stable core fields deduplicate adjacent/video frames without unsafe image phash.
    unique = {}
    for item in result:
        key = json.dumps({k: item.get(k) for k in
                          ("species", "nature", "ingredients", "subskills", "main_skill", "skill_level")},
                         ensure_ascii=False, sort_keys=True)
        unique[key] = item
    return list(unique.values())


def audit(items: Iterable[Dict[str, Any]], path: Path) -> Dict[str, int]:
    rows = list(items)
    low = [x for x in rows if float(x.get("confidence", 0)) < .995]
    unverified = [x for x in rows if not x.get("verified")]
    lines = ["# 取り込み監査レポート", "", f"- 総個体数: {len(rows)}",
             f"- 低信頼: {len(low)}", f"- 未検証: {len(unverified)}", "",
             "未検証個体は博士へ送る候補になりません。", ""]
    for item in low or unverified:
        lines.append(f"- BOX {item.get('box_index', '-')} / {item.get('species_ja', item.get('species'))}: 要確認")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"total": len(rows), "low_confidence": len(low), "unverified": len(unverified)}


def render_review(items: Iterable[Dict[str, Any]], path: Path) -> None:
    """Create an offline review page; corrected JSON is downloaded, never uploaded."""
    data = json.dumps(list(items), ensure_ascii=False).replace("</", "<\\/")
    page = f'''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>取り込みレビュー</title><style>body{{font-family:sans-serif;max-width:760px;margin:auto;padding:20px;background:#f4f7f3;color:#183026}}article{{background:white;padding:16px;margin:12px 0;border-radius:14px}}label{{display:grid;gap:4px;margin:8px 0}}input,textarea,button{{font:inherit;padding:10px}}textarea{{min-height:130px}}button{{background:#267553;color:white;border:0;border-radius:10px}}</style>
<h1>取り込みレビュー</h1><p>低信頼・未検証の個体を確認してください。画像や内容は外部送信されません。</p><main></main><button id="save">修正JSONを保存</button>
<script>const rows={data},main=document.querySelector('main');const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}}[c]));
main.innerHTML=rows.map((x,i)=>`<article><strong>BOX ${{x.box_index??'-'}} · ${{esc(x.species_ja||x.species)}}</strong><label>ニックネーム<input data-i="${{i}}" data-k="display_name" value="${{esc(x.display_name||'')}}"></label><label>抽出JSON<textarea data-i="${{i}}">${{esc(JSON.stringify(x,null,2))}}</textarea></label></article>`).join('');
document.querySelector('#save').onclick=()=>{{document.querySelectorAll('textarea').forEach(t=>rows[+t.dataset.i]=JSON.parse(t.value));const b=new Blob([JSON.stringify({{individuals:rows}},null,2)],{{type:'application/json'}}),a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='reviewed_individuals.json';a.click();URL.revokeObjectURL(a.href)}};</script></html>'''
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")

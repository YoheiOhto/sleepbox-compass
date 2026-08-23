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


def ingest_path(path: Path, frames_dir: Path, ocr_command: Optional[str] = None,
                vision: bool = False, interval: float = .8) -> List[Dict[str, Any]]:
    result = []
    for source in ([path] if path.is_file() else discover_inputs(path)):
        if vision:
            from .ocr import scan
            scanned = scan(source, interval)
            for item in scanned:
                item.setdefault("ocr_sources", []).append(source.name)
            result.extend(scanned)
            continue
        for frame in extract_frames(source, frames_dir / source.stem):
            item = sidecar_extract(frame, ocr_command) or sidecar_extract(source, ocr_command)
            if item:
                item.setdefault("capture_sha256", hashlib.sha256(frame.read_bytes()).hexdigest())
                item.setdefault("confidence", 1.0)
                item.setdefault("verified", False)
                result.append(item)
    # The same individual can appear in a production video and a later nature
    # video. SP + species joins those complementary captures without image
    # matching or uploading private frames.
    joined = {}
    for item in result:
        key = ((item.get("species"), item.get("sp")) if item.get("sp") is not None
               else (item.get("species"), item.get("box_index"), item.get("ocr_seconds")))
        if key not in joined:
            joined[key] = item
            continue
        base = joined[key]
        base_missing = set(base.get("ocr_missing", []))
        item_missing = set(item.get("ocr_missing", []))
        for field in ("nature", "level", "main_skill", "skill_level"):
            if field not in item_missing and (field in base_missing or not base.get(field)):
                base[field] = item.get(field)
        for field in ("ingredients", "subskills"):
            if field not in item_missing and len(item.get(field, [])) >= len(base.get(field, [])):
                base[field] = item[field]
        base["confidence"] = max(float(base.get("confidence", 0)), float(item.get("confidence", 0)))
        base["ocr_sources"] = sorted(set(base.get("ocr_sources", [])) | set(item.get("ocr_sources", [])))
        base["ocr_missing"] = sorted(base_missing & item_missing)
    result = list(joined.values())

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
        missing = "、".join(item.get("ocr_missing", []))
        suffix = f"（不足: {missing}）" if missing else ""
        lines.append(f"- BOX {item.get('box_index', '-')} / {item.get('species_ja', item.get('species'))}: 要確認{suffix}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"total": len(rows), "low_confidence": len(low), "unverified": len(unverified)}


def render_review(items: Iterable[Dict[str, Any]], path: Path) -> None:
    """Create an offline review page; corrected JSON is downloaded, never uploaded."""
    data = json.dumps(list(items), ensure_ascii=False).replace("</", "<\\/")
    from .localization import names
    nature_data = json.dumps(names()["natures"], ensure_ascii=False).replace("</", "<\\/")
    page = f'''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>取り込みレビュー</title><style>body{{font-family:sans-serif;max-width:760px;margin:auto;padding:20px;background:#f4f7f3;color:#183026}}article{{background:white;padding:16px;margin:12px 0;border-radius:14px}}label{{display:grid;gap:4px;margin:8px 0}}input,textarea,button{{font:inherit;padding:10px}}textarea{{min-height:130px}}button{{background:#267553;color:white;border:0;border-radius:10px}}</style>
<h1>取り込みレビュー</h1><p>低信頼・未検証の個体を確認してください。画像や内容は外部送信されません。</p><main></main><button id="save">修正JSONを保存</button>
<script>const rows={data},NATURES={nature_data},main=document.querySelector('main');const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}}[c]));
const ingredientFields=(x,i)=>(x.ingredient_options||[]).map((slot,j)=>`<label>Lv.${{slot.level}} 食材<select data-i="${{i}}" data-slot="${{j}}"><option value="">画像を見て選択</option>${{slot.choices.map(c=>`<option value="${{esc(JSON.stringify(c.slice(0,2)))}}" ${{x.ingredients?.[j]?.[0]===c[0]?'selected':''}}>${{esc(c[2])}} ×${{c[1]}}</option>`).join('')}}</select></label>`).join('');
const natureOptions=x=>Object.entries(NATURES).map(([v,n])=>`<option value="${{v}}" ${{x.nature===v?'selected':''}}>${{esc(n)}}</option>`).join('');
main.innerHTML=rows.map((x,i)=>`<article><strong>BOX ${{x.box_index??'-'}} · ${{esc(x.species_ja||x.species)}} · きのみ ${{esc(x.berry_ja||x.berry||'-')}}</strong><p>${{x.ocr_missing?.length?`要確認: ${{x.ocr_missing.map(esc).join('、')}}`:'主要項目を取得済み'}}${{x.ocr_sources?.length?` · ${{x.ocr_sources.length}}本の動画をSPで統合`:''}}</p><label>ニックネーム<input data-i="${{i}}" data-k="display_name" value="${{esc(x.display_name||'')}}"></label><label>現在レベル<input type="number" min="1" max="100" data-i="${{i}}" data-k="level" value="${{x.level??''}}"></label><label>画面のSP<input type="number" data-i="${{i}}" data-k="sp" value="${{x.sp??''}}"></label><label>性格<select data-i="${{i}}" data-k="nature">${{natureOptions(x)}}</select></label><label>メインスキルLv<input type="number" min="1" max="7" data-i="${{i}}" data-k="skill_level" value="${{x.skill_level??1}}"></label>${{ingredientFields(x,i)}}<label><span><input type="checkbox" data-i="${{i}}" data-k="verified"> 内容を動画と照合済みにする</span></label><details><summary>抽出JSONを確認・編集</summary><label>抽出JSON<textarea data-i="${{i}}">${{esc(JSON.stringify(x,null,2))}}</textarea></label></details></article>`).join('');
document.querySelector('#save').onclick=()=>{{document.querySelectorAll('textarea').forEach(t=>rows[+t.dataset.i]=JSON.parse(t.value));document.querySelectorAll('[data-slot]').forEach(s=>{{if(s.value){{const i=+s.dataset.i,j=+s.dataset.slot;rows[i].ingredients??=[];rows[i].ingredients[j]=JSON.parse(s.value)}}}});document.querySelectorAll('[data-k]').forEach(n=>{{const x=rows[+n.dataset.i],k=n.dataset.k;if(k==='verified')x[k]=n.checked;else if(k==='level'||k==='sp'||k==='skill_level')x[k]=Number(n.value);else x[k]=n.value}});const b=new Blob([JSON.stringify({{individuals:rows}},null,2)],{{type:'application/json'}}),a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='reviewed_individuals.json';a.click();URL.revokeObjectURL(a.href)}};</script></html>'''
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Collection, Dict, Iterable, List, Optional

from .localization import normalize_individual

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".heic", ".webp"}
VIDEO_SUFFIXES = {".mov", ".mp4", ".m4v"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
                vision: bool = False, interval: float = .8,
                on_progress: Optional[Callable[[str], None]] = None,
                qwen: Any = None, skip_hashes: Collection[str] = (),
                on_source_done: Optional[Callable[[Path, str, int], None]] = None,
                ) -> List[Dict[str, Any]]:
    """Read captures into candidate individuals.

    ``qwen`` is a ``vlm.QwenReader``. Sources whose SHA-256 is in
    ``skip_hashes`` were already synced and are not read again, and
    ``on_source_done`` is told about every source that was read.
    """
    result = []
    sources = [path] if path.is_file() else discover_inputs(path)
    total_sources = len(sources)
    for source_index, source in enumerate(sources, start=1):
        prefix = f"[{source_index}/{total_sources}] {source.name}"
        source_hash = file_sha256(source)
        if source_hash in skip_hashes:
            if on_progress:
                on_progress(f"{prefix}: 取り込み済みのためスキップ")
            continue
        if vision or qwen is not None:
            if on_progress:
                on_progress(f"{prefix}: 開始")

            def report(message: str, prefix=prefix) -> None:
                on_progress(f"{prefix}: {message}")

            if qwen is not None:
                from .vlm import scan as vlm_scan
                scanned = vlm_scan(source, qwen, frames_dir, interval,
                                   report if on_progress else None)
            else:
                from .ocr import scan
                scanned = scan(source, interval, report if on_progress else None)
            for item in scanned:
                item.setdefault("ocr_sources", []).append(source.name)
                if source.suffix.lower() in IMAGE_SUFFIXES:
                    # Two helpers can legitimately have the same species and
                    # build. Keep still captures distinct while making a
                    # repeated import of the same screenshot idempotent. A
                    # later re-capture of a stored individual is matched to
                    # it in `core.import_individuals`.
                    capture_hash = source_hash
                    item.setdefault("capture_sha256", capture_hash)
                    item.setdefault("uid", f"capture-{capture_hash[:20]}")
                    item["_still_capture"] = True
            result.extend(scanned)
            if on_source_done:
                on_source_done(source, source_hash, len(scanned))
            if on_progress:
                on_progress(f"{prefix}: 完了（{len(scanned)}件抽出）")
            continue
        if on_progress:
            on_progress(f"{prefix}: 開始")
        before = len(result)
        for frame in extract_frames(source, frames_dir / source.stem):
            item = sidecar_extract(frame, ocr_command) or sidecar_extract(source, ocr_command)
            if item:
                item.setdefault("capture_sha256", hashlib.sha256(frame.read_bytes()).hexdigest())
                item.setdefault("confidence", 1.0)
                item.setdefault("verified", False)
                result.append(item)
        if on_source_done:
            on_source_done(source, source_hash, len(result) - before)
        if on_progress:
            on_progress(f"{prefix}: 完了")
    # The same individual can appear in a production video and a later nature
    # video. SP + species joins those complementary captures without image
    # matching or uploading private frames.
    joined = {}
    for item in result:
        key = (("still", item.get("capture_sha256")) if item.get("_still_capture")
               else (item.get("species"), item.get("sp")) if item.get("sp") is not None
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
        key = (item.get("capture_sha256") if item.get("_still_capture") else
               json.dumps({k: item.get(k) for k in
                           ("species", "nature", "ingredients", "subskills", "main_skill", "skill_level")},
                          ensure_ascii=False, sort_keys=True))
        item.pop("_still_capture", None)
        unique[key] = item
    return list(unique.values())


def audit(items: Iterable[Dict[str, Any]], path: Path) -> Dict[str, int]:
    rows = list(items)
    low = [x for x in rows if float(x.get("confidence", 0)) < .995]
    unverified = [x for x in rows if not x.get("verified")]
    recaptured = [x for x in rows if x.get("recaptured")]
    duplicates = [x for x in rows if x.get("possible_duplicates")]
    mismatched = [x for x in rows if x.get("sp_check") == "mismatch"]
    auto_verified = [x for x in rows if x.get("auto_verified")]
    lines = ["# 取り込み監査レポート", "", f"- 総個体数: {len(rows)}",
             f"- 低信頼: {len(low)}", f"- 未検証: {len(unverified)}",
             f"- 登録済み個体の更新（再取り込み）: {len(recaptured)}",
             f"- 重複の可能性: {len(duplicates)}"]
    if any("sp_check" in x for x in rows):
        lines += [f"- SP一致で自動確定: {len(auto_verified)}", f"- SP不一致: {len(mismatched)}"]
    lines += ["", "未検証個体は博士へ送る候補になりません。", ""]
    # SP mismatches first; an SP-confirmed individual needs no further check.
    listed = low or unverified
    listed = mismatched + [x for x in listed if x not in mismatched and not x.get("auto_verified")]
    for item in listed:
        # After an SP check, only the fields the SP could not confirm remain.
        missing = "、".join(item.get("review_fields", item.get("ocr_missing", [])))
        suffix = f"（不足: {missing}）" if missing else ""
        flag = f" {item['sp_flag']}" if item.get("sp_flag") else ""
        lines.append(f"- BOX {item.get('box_index', '-')} / {item.get('species_ja', item.get('species'))}: 要確認{flag}{suffix}")
    for item in duplicates:
        lines.append(f"- BOX {item.get('box_index', '-')} / {item.get('species_ja', item.get('species'))}: "
                     f"同じ構成の登録済み個体が{item['possible_duplicates']}体あり、どれの再取り込みか"
                     "決められないため新しい個体として登録しました（重複の可能性）")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"total": len(rows), "low_confidence": len(low), "unverified": len(unverified),
            "recaptured": len(recaptured), "possible_duplicates": len(duplicates),
            "sp_mismatch": len(mismatched), "auto_verified": len(auto_verified)}


def render_review(items: Iterable[Dict[str, Any]], path: Path) -> None:
    """Create an offline review page; corrected JSON is downloaded, never uploaded.

    On save, each row gets ``human_edited``: the fields (keys of the page's
    FIELD_NAMES) whose values differ from the scan, which the SP check then
    treats as resolved (see ``spcheck.unresolved_fields``).
    """
    data = json.dumps(list(items), ensure_ascii=False).replace("</", "<\\/")
    from .localization import names
    nature_data = json.dumps(names()["natures"], ensure_ascii=False).replace("</", "<\\/")
    skill_data = json.dumps(names()["mainskills"], ensure_ascii=False).replace("</", "<\\/")
    page = f'''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>取り込みレビュー</title><style>body{{font-family:sans-serif;max-width:760px;margin:auto;padding:20px;background:#f4f7f3;color:#183026}}article{{background:white;padding:16px;margin:12px 0;border-radius:14px}}label{{display:grid;gap:4px;margin:8px 0}}input,textarea,button{{font:inherit;padding:10px}}textarea{{min-height:130px}}button{{background:#267553;color:white;border:0;border-radius:10px}}</style>
<h1>取り込みレビュー</h1><p>低信頼・未検証の個体を確認してください。画像や内容は外部送信されません。</p><main></main><button id="save">修正JSONを保存</button>
<script>const rows={data},ORIG=rows.map(x=>JSON.stringify(x)),NATURES={nature_data},MAINSKILLS={skill_data},FIELD_NAMES={{species:'ポケモン',level:'レベル',nature:'性格',ingredients:'食材',subskills:'サブスキル',main_skill:'メインスキル',skill_level:'スキルLv'}},main=document.querySelector('main');const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}}[c]));
const ingredientFields=(x,i)=>(x.ingredient_options||[]).map((slot,j)=>`<label>Lv.${{slot.level}} 食材<select data-i="${{i}}" data-slot="${{j}}"><option value="">画像を見て選択</option>${{slot.choices.map(c=>`<option value="${{esc(JSON.stringify(c.slice(0,2)))}}" ${{x.ingredients?.[j]?.[0]===c[0]?'selected':''}}>${{esc(c[2])}} ×${{c[1]}}</option>`).join('')}}</select></label>`).join('');
const natureOptions=x=>Object.entries(NATURES).map(([v,n])=>`<option value="${{v}}" ${{x.nature===v?'selected':''}}>${{esc(n)}}</option>`).join('');
main.innerHTML=rows.map((x,i)=>`<article><strong>BOX ${{x.box_index??'-'}} · ${{esc(x.species_ja||x.species)}} · きのみ ${{esc(x.berry_ja||x.berry||'-')}} · メインスキル ${{esc(MAINSKILLS[x.main_skill]||x.main_skill||'-')}}</strong><p>${{x.sp_flag?`<strong style="color:#b3261e">${{esc(x.sp_flag)}}</strong> · `:''}}${{x.auto_verified?'SP一致で自動確定 · ':''}}${{(x.review_fields??x.ocr_missing)?.length?`要確認: ${{(x.review_fields??x.ocr_missing).map(k=>esc(FIELD_NAMES[k]||k)).join('、')}}`:'主要項目を取得済み'}}${{x.ocr_sources?.length?` · ${{x.ocr_sources.length}}本の動画をSPで統合`:''}}</p><label>ニックネーム<input data-i="${{i}}" data-k="display_name" value="${{esc(x.display_name||'')}}"></label><label>現在レベル<input type="number" min="1" max="100" data-i="${{i}}" data-k="level" value="${{x.level??''}}"></label><label>画面のSP<input type="number" data-i="${{i}}" data-k="sp" value="${{x.sp??''}}"></label><label>性格<select data-i="${{i}}" data-k="nature">${{natureOptions(x)}}</select></label><label>メインスキルLv<input type="number" min="1" max="7" data-i="${{i}}" data-k="skill_level" value="${{x.skill_level??1}}"></label>${{ingredientFields(x,i)}}<label><span><input type="checkbox" data-i="${{i}}" data-k="verified"> 内容を動画と照合済みにする</span></label><details><summary>抽出JSONを確認・編集</summary><label>抽出JSON<textarea data-i="${{i}}">${{esc(JSON.stringify(x,null,2))}}</textarea></label></details></article>`).join('');
document.querySelector('#save').onclick=()=>{{document.querySelectorAll('textarea').forEach(t=>rows[+t.dataset.i]=JSON.parse(t.value));document.querySelectorAll('[data-slot]').forEach(s=>{{if(s.value){{const i=+s.dataset.i,j=+s.dataset.slot;rows[i].ingredients??=[];rows[i].ingredients[j]=JSON.parse(s.value)}}}});document.querySelectorAll('[data-k]').forEach(n=>{{const x=rows[+n.dataset.i],k=n.dataset.k;if(k==='verified'||k==='review_confirmed')x[k]=n.checked;else if(k==='level'||k==='sp'||k==='skill_level')x[k]=Number(n.value);else x[k]=n.value}});rows.forEach((x,i)=>{{const o=JSON.parse(ORIG[i]);x.human_edited=[...new Set([...(o.human_edited||[]),...Object.keys(FIELD_NAMES).filter(k=>JSON.stringify(x[k])!==JSON.stringify(o[k]))])]}});const b=new Blob([JSON.stringify({{individuals:rows}},null,2)],{{type:'application/json'}}),a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='reviewed_individuals.json';a.click();URL.revokeObjectURL(a.href)}};</script></html>'''
    page = page.replace('data-k="verified"> 内容を動画と照合済みにする',
                        'data-k="review_confirmed"> ボックス画面と照合済み（以後「!」を表示しない）')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")

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
                    # repeated import of the same screenshot idempotent.
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
    page = f'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark">
<title>取り込みレビュー</title><style>
:root{{color-scheme:light dark;--bg:#f5f7f5;--surface:#fff;--surface-2:#eff3f0;--ink:#15231c;--muted:#56665d;--line:#dce4de;--line-strong:#c3cfc7;--brand:#13774e;--brand-soft:#dff1e7;--on-brand:#fff;--warn:#8a5a00;--warn-soft:#fdf1d6;--warn-line:#e7c675;--focus:#1b7a52;--shadow:0 1px 2px rgba(20,40,30,.05),0 3px 10px rgba(20,40,30,.04)}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0e1411;--surface:#171f1b;--surface-2:#202a25;--ink:#e6eee9;--muted:#a0b1a7;--line:#2b3731;--line-strong:#3c4a43;--brand:#62cc99;--brand-soft:#1b3829;--on-brand:#05140c;--warn:#f0c060;--warn-soft:#352b12;--warn-line:#6d5a25;--focus:#86e0b4;--shadow:0 1px 2px rgba(0,0,0,.3)}}}}
*{{box-sizing:border-box;min-width:0}}body{{margin:0;background:var(--bg);color:var(--ink);font:400 15px/1.6 -apple-system,BlinkMacSystemFont,"Hiragino Sans","Hiragino Kaku Gothic ProN","Noto Sans JP","Segoe UI",Roboto,sans-serif;font-variant-numeric:tabular-nums;overflow-wrap:break-word;-webkit-font-smoothing:antialiased}}
header,main{{max-width:760px;margin:auto;padding:0 16px}}header{{padding-top:24px}}h1{{font-size:26px;font-weight:800;letter-spacing:-.02em;line-height:1.25;margin:0 0 6px}}header p{{color:var(--muted);margin:0 0 6px;font-size:14px}}
.card{{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:16px;margin:14px 0;box-shadow:var(--shadow)}}.card.needs{{border-left:4px solid var(--warn-line)}}
.card-head{{display:flex;align-items:center;gap:12px}}.box-no{{flex:0 0 auto;padding:4px 9px;border-radius:9px;background:var(--surface-2);color:var(--muted);font-size:12px;font-weight:700}}.card-head h2{{margin:0;font-size:18px;line-height:1.3}}.card-head .meta{{color:var(--muted);font-size:13px}}
.status{{margin:12px 0 4px;padding:9px 12px;border-radius:12px;font-size:13px;background:var(--brand-soft);color:var(--ink)}}.status.warn{{background:var(--warn-soft);border:1px solid var(--warn-line)}}.status.warn strong{{color:var(--warn)}}
.fields{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px 10px;margin-top:12px}}.fields .wide{{grid-column:1/-1}}
label{{display:grid;gap:5px;align-content:end}}label>span{{font-size:12px;font-weight:700;color:var(--muted)}}
.flag{{display:inline-block;margin-left:6px;padding:0 7px;border-radius:99px;background:var(--warn-soft);color:var(--warn);border:1px solid var(--warn-line);font-size:11px;font-weight:700}}label.need input,label.need select{{border-color:var(--warn-line);box-shadow:0 0 0 2px var(--warn-soft)}}
input,select,textarea,button{{font:inherit;color:inherit}}input:not([type=checkbox]),select{{width:100%;min-height:44px;border:1px solid var(--line-strong);border-radius:12px;padding:9px 12px;background:var(--surface);color:var(--ink)}}
select{{appearance:none;-webkit-appearance:none;padding-right:34px;background:var(--surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%237f8f86' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E") no-repeat right 11px center}}
:focus-visible{{outline:2px solid var(--focus);outline-offset:2px}}
.check{{display:flex;align-items:center;gap:10px;margin-top:12px;min-height:48px;padding:10px 12px;border-radius:12px;background:var(--surface-2);font-weight:600;cursor:pointer}}.check input{{flex:0 0 auto;width:22px;height:22px;margin:0;accent-color:var(--brand)}}
details{{margin-top:8px}}summary{{cursor:pointer;padding:10px 0;color:var(--brand);font-weight:650;font-size:14px}}textarea{{width:100%;min-height:160px;border:1px solid var(--line-strong);border-radius:12px;padding:10px;background:var(--surface-2);color:var(--ink);font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}}
.savebar{{position:sticky;bottom:0;padding:12px 16px calc(12px + env(safe-area-inset-bottom));background:linear-gradient(transparent,var(--bg) 30%)}}#save{{display:block;width:100%;max-width:728px;margin:auto;min-height:50px;border:0;border-radius:14px;background:var(--brand);color:var(--on-brand);font-weight:700;font-size:16px;box-shadow:0 6px 18px rgba(0,0,0,.18);cursor:pointer}}
@media(max-width:420px){{.fields{{gap:10px 8px}}label>span{{font-size:11.5px}}}}
</style></head><body>
<header><h1>取り込みレビュー</h1><p>低信頼・未検証の個体を確認してください。画像や内容は外部送信されません。</p></header><main></main><div class="savebar"><button id="save">修正JSONを保存</button></div>
<script>const rows={data},NATURES={nature_data},main=document.querySelector('main');const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}}[c]));
const MISSING_LABELS={{species:'種族',nature:'性格',ingredients:'食材',subskills:'サブスキル',main_skill:'メインスキル'}},need=(x,k)=>x.ocr_missing?.includes(k)?' need':'',flag=(x,k)=>x.ocr_missing?.includes(k)?'<b class="flag">要確認</b>':'';
const ingredientFields=(x,i)=>(x.ingredient_options||[]).map((slot,j)=>`<label class="wide${{need(x,'ingredients')}}"><span>Lv.${{slot.level}} 食材${{flag(x,'ingredients')}}</span><select data-i="${{i}}" data-slot="${{j}}"><option value="">画像を見て選択</option>${{slot.choices.map(c=>`<option value="${{esc(JSON.stringify(c.slice(0,2)))}}" ${{x.ingredients?.[j]?.[0]===c[0]?'selected':''}}>${{esc(c[2])}} ×${{c[1]}}</option>`).join('')}}</select></label>`).join('');
const natureOptions=x=>Object.entries(NATURES).map(([v,n])=>`<option value="${{v}}" ${{x.nature===v?'selected':''}}>${{esc(n)}}</option>`).join('');
main.innerHTML=rows.map((x,i)=>`<article class="card${{x.ocr_missing?.length?' needs':''}}"><div class="card-head"><span class="box-no">BOX ${{x.box_index??'-'}}</span><div><h2>${{esc(x.species_ja||x.species)}}</h2><div class="meta">きのみ ${{esc(x.berry_ja||x.berry||'-')}}</div></div></div><p class="status${{x.ocr_missing?.length?' warn':''}}">${{x.ocr_missing?.length?`<strong>要確認:</strong> ${{x.ocr_missing.map(k=>esc(MISSING_LABELS[k]||k)).join('、')}}`:'主要項目を取得済み'}}${{x.ocr_sources?.length?` · ${{x.ocr_sources.length}}本の動画をSPで統合`:''}}</p><div class="fields"><label class="wide"><span>ニックネーム</span><input data-i="${{i}}" data-k="display_name" value="${{esc(x.display_name||'')}}"></label><label><span>現在レベル</span><input type="number" inputmode="numeric" min="1" max="100" data-i="${{i}}" data-k="level" value="${{x.level??''}}"></label><label><span>画面のSP</span><input type="number" inputmode="numeric" data-i="${{i}}" data-k="sp" value="${{x.sp??''}}"></label><label><span>メインスキルLv</span><input type="number" inputmode="numeric" min="1" max="7" data-i="${{i}}" data-k="skill_level" value="${{x.skill_level??1}}"></label><label class="wide${{need(x,'nature')}}"><span>性格${{flag(x,'nature')}}</span><select data-i="${{i}}" data-k="nature">${{natureOptions(x)}}</select></label>${{ingredientFields(x,i)}}</div><label class="check"><input type="checkbox" data-i="${{i}}" data-k="verified"> 内容を動画と照合済みにする</label><details><summary>抽出JSONを確認・編集</summary><label><span>抽出JSON</span><textarea data-i="${{i}}">${{esc(JSON.stringify(x,null,2))}}</textarea></label></details></article>`).join('');
document.querySelector('#save').onclick=()=>{{document.querySelectorAll('textarea').forEach(t=>rows[+t.dataset.i]=JSON.parse(t.value));document.querySelectorAll('[data-slot]').forEach(s=>{{if(s.value){{const i=+s.dataset.i,j=+s.dataset.slot;rows[i].ingredients??=[];rows[i].ingredients[j]=JSON.parse(s.value)}}}});document.querySelectorAll('[data-k]').forEach(n=>{{const x=rows[+n.dataset.i],k=n.dataset.k;if(k==='verified'||k==='review_confirmed')x[k]=n.checked;else if(k==='level'||k==='sp'||k==='skill_level')x[k]=Number(n.value);else x[k]=n.value}});const b=new Blob([JSON.stringify({{individuals:rows}},null,2)],{{type:'application/json'}}),a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='reviewed_individuals.json';a.click();URL.revokeObjectURL(a.href)}};</script></body></html>'''
    page = page.replace('data-k="verified"> 内容を動画と照合済みにする',
                        'data-k="review_confirmed"> ボックス画面と照合済み（以後「!」を表示しない）')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")

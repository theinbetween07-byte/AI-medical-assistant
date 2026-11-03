"""把 医学术语库_v2.xls 编译成集成层 + 报告引擎可直接使用的词表。

用法（唯一可用的 Python 是 ASR 项目里的便携发行版）：
    python scripts/import_terms.py --xls "<path>/医学术语库_v2.xls"

产物（默认写到 integration/terms/）：
    seed_terms.json      给报告引擎用（结构对齐 report-engine/terms/seed_terms.json）
    glossary_full.json   全量 602 条 + auto_replace / skip_reasons，供界面查阅与审计
    import_report.json   导入统计（版本号、条数、丢弃原因分布），满足 NFR-10 变更可追溯

注意：引擎的 TermsStore 要求 terms_dir 里同时存在 extraction_rules.json，
本脚本会把仓库里的 extraction_rules.json 一并复制过去，避免抽取规则静默退化。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]            # integration/
REPO = ROOT.parent                                    # workspace root
sys.path.insert(0, str(ROOT))

from app.terms import compile_terms, read_xls, write_terms  # noqa: E402

DEFAULT_RULES = (
    REPO / "report-produce" / "书面报告生成插件（含术语纠正）" / "terms" / "extraction_rules.json"
)


def ensure_xlrd(extra: str) -> None:
    try:
        import xlrd  # noqa: F401
        return
    except ImportError:
        pass
    candidates = [Path(extra)] if extra else []
    candidates.append(REPO / ".deepworks" / "tmp" / "pylibs")
    for candidate in candidates:
        if (candidate / "xlrd").is_dir():
            sys.path.insert(0, str(candidate))
            return
    raise SystemExit(
        "缺少 xlrd。请先离线安装：\n"
        "  python -m pip download xlrd -d .deepworks\\tmp\\whl --no-deps\n"
        "  然后解压到 .deepworks\\tmp\\pylibs，并用 --pylib 指向该目录"
    )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


_ALIAS_FIELD_SPLIT = re.compile(r"[、,，;；|]+")


def _alias_parts(value: str) -> list[str]:
    return [part.strip() for part in _ALIAS_FIELD_SPLIT.split(value or "") if part.strip()]


def _alias_tokens(entry: dict) -> set[str]:
    return {
        entry.get("zh", "").strip(),
        *_alias_parts(entry.get("yue", "")),
        *_alias_parts(entry.get("synonyms", "")),
    } - {""}


def load_overrides(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"覆盖文件不是合法 JSON：{path}（{exc}）") from exc


def apply_overrides(entries: list[dict], data: dict) -> dict:
    """把本地补丁合并进 xls 条目。

    支持 `add_terms` / `add_aliases` / `drop_aliases`。源文件保持原样，
    所有改动都进 import_report，满足 NFR-10 的变更可追溯。
    """
    stats: dict[str, list[str]] = {
        "added_terms": [], "added_aliases": [], "dropped_aliases": [], "warnings": [],
    }
    if not data:
        return stats

    by_id = {str(e.get("term_id") or "").upper(): e for e in entries}

    for term in data.get("add_terms") or []:
        tid = str(term.get("term_id") or "").strip()
        if not tid or not term.get("zh") or not term.get("en"):
            stats["warnings"].append(f"add_terms 缺少 term_id/zh/en：{term}")
            continue
        if tid.upper() in by_id:
            stats["warnings"].append(f"add_terms 与已有 term_id 冲突，已跳过：{tid}")
            continue
        entry = {
            "term_id": tid,
            "category": term.get("category", ""),
            "zh": term.get("zh", ""),
            "en": term.get("en", ""),
            "yue": term.get("yue", ""),
            "jyutping": term.get("jyutping", ""),
            "type_code": term.get("type_code", ""),
            "synonyms": term.get("synonyms", ""),
            "note": term.get("note", ""),
        }
        entries.append(entry)
        by_id[tid.upper()] = entry
        stats["added_terms"].append(tid)

    for item in data.get("add_aliases") or []:
        entry = by_id.get(str(item.get("term_id") or "").upper())
        alias = str(item.get("alias") or "").strip()
        if not entry or not alias:
            stats["warnings"].append(f"add_aliases 未匹配到词条：{item}")
            continue
        if alias in _alias_tokens(entry):
            continue
        merged = [part for part in (entry.get("synonyms") or "", alias) if part]
        entry["synonyms"] = "、".join(merged)
        stats["added_aliases"].append(f"{entry['term_id']}:{alias}")

    for item in data.get("drop_aliases") or []:
        entry = by_id.get(str(item.get("term_id") or "").upper())
        alias = str(item.get("alias") or "").strip()
        if not entry or not alias:
            stats["warnings"].append(f"drop_aliases 未匹配到词条：{item}")
            continue
        hit = False
        for key in ("yue", "synonyms"):
            parts = _alias_parts(entry.get(key) or "")
            if alias in parts:
                hit = True
                entry[key] = "、".join(part for part in parts if part != alias)
        if hit:
            stats["dropped_aliases"].append(f"{entry['term_id']}:{alias}")
        else:
            stats["warnings"].append(f"drop_aliases 未找到别名：{entry['term_id']}:{alias}")

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="导入医学术语库")
    parser.add_argument("--xls", required=True, help="医学术语库_v2.xls 路径")
    parser.add_argument("--out", default=str(ROOT / "terms"), help="输出目录")
    parser.add_argument("--rules", default=str(DEFAULT_RULES), help="extraction_rules.json 来源")
    parser.add_argument("--pylib", default="", help="xlrd 所在目录（可选）")
    parser.add_argument("--version", default="", help="词表版本号，默认今天")
    parser.add_argument(
        "--overrides",
        default=str(ROOT / "terms" / "overrides.json"),
        help="本地术语补丁（默认 terms/overrides.json，不存在则跳过）",
    )
    args = parser.parse_args()

    ensure_xlrd(args.pylib)

    xls_path = Path(args.xls)
    if not xls_path.is_file():
        raise SystemExit(f"找不到术语库文件：{xls_path}")

    version = args.version or date.today().isoformat()
    entries = read_xls(xls_path)
    overrides_path = Path(args.overrides)
    overrides = load_overrides(overrides_path)
    override_stats = apply_overrides(entries, overrides)
    engine_payload, glossary = compile_terms(entries, version)
    engine_payload["source_sha256"] = sha256_of(xls_path)

    out_dir = Path(args.out)
    stats = write_terms(engine_payload, glossary, out_dir)

    rules_copied = False
    rules_src = Path(args.rules)
    if rules_src.is_file():
        shutil.copyfile(rules_src, out_dir / "extraction_rules.json")
        rules_copied = True

    report = {
        "version": version,
        "source": xls_path.name,
        "source_rows": len(entries) - len(override_stats["added_terms"]),
        "rules_copied": rules_copied,
        **stats,
        "overrides": {
            "file": overrides_path.name if overrides else "",
            "version": overrides.get("version", ""),
            **override_stats,
        },
        "note": "entries_auto_replace 为参与自动替换的词条数；其余仅作词表查阅（保守策略）",
    }
    (out_dir / "import_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n产物目录：{out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

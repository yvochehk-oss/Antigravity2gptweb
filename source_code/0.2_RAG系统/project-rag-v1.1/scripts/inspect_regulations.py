import os, sys, re, json
from pathlib import Path

reg_dir = Path('/Users/yvoche/AI开发/073_成都建工/0.2 RAG系统/project-rag-v1.1/regulations_data')
lib_03_dir = Path('/Users/yvoche/AI开发/073_成都建工/0.3 涉税法律法规库')

FRONTMATTER_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)

def parse_md(path):
    text = path.read_text(encoding='utf-8')
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text.strip(), len(text)
    raw = m.group(1)
    body = text[m.end():].strip()
    meta = {}
    for line in raw.splitlines():
        if ':' in line:
            k, _, v = line.partition(':')
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, body, len(text)

files = [p for p in reg_dir.rglob('*.md') if p.name not in ('INDEX.md', 'README.md', 'DATA_SOURCES.md')]

report = {
    'total_files': len(files),
    'skeleton_files': [],
    'missing_frontmatter': [],
    'missing_required_meta': [],
    'small_body_files': [],
    'duplicate_files': {},
    'by_category': {},
    'by_status': {},
    'comparison_with_03': {'missing_in_rag': [], 'in_both': []},
    'file_details': []
}

req_keys = ['title', 'doc_no', 'status', 'tax_type', 'jurisdiction', 'legal_level']

title_map = {}
docno_map = {}

for f in sorted(files):
    rel = str(f.relative_to(reg_dir))
    cat = rel.split('/')[0]
    report['by_category'][cat] = report['by_category'].get(cat, 0) + 1
    
    meta, body, total_len = parse_md(f)
    
    is_skeleton = '(空，由' in body or 'regulations_data/scripts/build_regulation_md.py' in body or len(body) < 100
    
    report['file_details'].append({
        'path': rel,
        'title': meta.get('title', f.stem),
        'doc_no': meta.get('doc_no', ''),
        'status': meta.get('status', 'MISSING_STATUS'),
        'legal_level': meta.get('legal_level', ''),
        'jurisdiction': meta.get('jurisdiction', ''),
        'tax_type': meta.get('tax_type', ''),
        'industry': meta.get('industry', ''),
        'body_length': len(body),
        'is_skeleton': is_skeleton
    })
    
    if not meta:
        report['missing_frontmatter'].append(rel)
    else:
        for k in req_keys:
            if not meta.get(k):
                report['missing_required_meta'].append({'file': rel, 'missing_field': k})
        st = meta.get('status', '未知')
        report['by_status'][st] = report['by_status'].get(st, 0) + 1
        
        t = meta.get('title', f.stem)
        d = meta.get('doc_no', '')
        if t:
            title_map.setdefault(t, []).append(rel)
        if d and d != '无':
            docno_map.setdefault(d, []).append(rel)

    if is_skeleton:
        report['skeleton_files'].append({
            'file': rel,
            'title': meta.get('title', f.stem),
            'doc_no': meta.get('doc_no', ''),
            'body_len': len(body),
        })
    elif len(body) < 500:
        report['small_body_files'].append({
            'file': rel,
            'title': meta.get('title', f.stem),
            'body_len': len(body)
        })

# Comparison with 0.3 (if directory exists)
files_03 = [p for p in lib_03_dir.rglob('*.md') if p.name != 'README.md'] if lib_03_dir.exists() else []
rag_filenames = {p.name for p in files}

for f03 in files_03:
    if f03.name not in rag_filenames:
        report['comparison_with_03']['missing_in_rag'].append(str(f03.relative_to(lib_03_dir)))
    else:
        report['comparison_with_03']['in_both'].append(str(f03.relative_to(lib_03_dir)))


# Duplicates
for t, lst in title_map.items():
    if len(lst) > 1:
        report['duplicate_files'][f'Duplicate Title: {t}'] = lst

for d, lst in docno_map.items():
    if len(lst) > 1:
        report['duplicate_files'][f'Duplicate DocNo: {d}'] = lst

print(json.dumps(report, ensure_ascii=False, indent=2))

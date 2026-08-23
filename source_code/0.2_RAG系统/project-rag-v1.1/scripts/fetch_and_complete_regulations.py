import os, sys, re, json, subprocess, time
from pathlib import Path

BASE_DIR = Path('/Users/yvoche/AI开发/073_成都建工/0.2 RAG系统/project-rag-v1.1')
REG_DIR = BASE_DIR / 'regulations_data'

def call_pkulaw(args):

    cmd = ['pkulaw-mcp'] + args + ['--json']
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return None
    stdout = res.stdout
    idx = stdout.find('{')
    if idx == -1:
        idx = stdout.find('[')
    if idx != -1:
        try:
            return json.loads(stdout[idx:])
        except Exception:
            return None
    return None

def fetch_all_articles_of_law(title, max_articles=80):
    articles = []
    meta = {}
    consecutive_misses = 0
    for i in range(1, max_articles + 1):
        data = call_pkulaw(['fatiao', 'get_law_item_content', '--title', title, '--tiao_num', str(i)])
        if data and data.get('Message') == '成功':
            d = data.get('Data', {})
            text = d.get('FullText', '').strip()
            if text:
                articles.append((i, text))
                if not meta:
                    meta = {
                        'title': d.get('Title', title),
                        'document_no': d.get('DocumentNO', ''),
                        'issuer': '、'.join(d.get('IssueDepartment', [])) if isinstance(d.get('IssueDepartment'), list) else str(d.get('IssueDepartment', '')),
                        'publish_date': d.get('IssueDate', '').replace('.', '-'),
                        'effective_date': d.get('ImplementDate', '').replace('.', '-'),
                        'legal_level': d.get('EffectivenessDic', ['规范性文件'])[0] if isinstance(d.get('EffectivenessDic'), list) and d.get('EffectivenessDic') else '规范性文件',
                        'status': d.get('TimelinessDic', ['现行有效'])[0] if isinstance(d.get('TimelinessDic'), list) and d.get('TimelinessDic') else '现行有效',
                        'url': d.get('Url', ''),
                    }
                consecutive_misses = 0
                continue
        consecutive_misses += 1
        if consecutive_misses >= 2:
            break
    return meta, articles

def build_markdown(meta, body_content, note="", summary=""):
    fm_lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, list):
            fm_lines.append(f"{k}:")
            for it in v:
                fm_lines.append(f"  - {it}")
        else:
            clean_v = str(v).replace('"', '\\"')
            fm_lines.append(f'{k}: "{clean_v}"')
    if note and "note" not in meta:
        clean_n = note.replace('"', '\\"')
        fm_lines.append(f'note: "{clean_n}"')
    fm_lines.append("---\n")
    
    parts = ["\n".join(fm_lines)]
    if summary:
        parts.append(f"> {summary.strip()}\n")
    parts.append(body_content.strip() + "\n")
    return "\n".join(parts)

print("Starting regulation completion pipeline...")

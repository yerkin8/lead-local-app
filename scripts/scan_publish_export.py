"""Check the exact public export file set and obvious secret/PII patterns."""
from pathlib import Path
import hashlib,re,subprocess,csv,sys
root=Path(__file__).resolve().parents[1]
export=root/'publish_export'
tracked=set(subprocess.check_output(['git','-C',str(export),'ls-files','-z']).decode().split('\0'))-{''}
allow=tracked|{'README.md'}
files={p.relative_to(export).as_posix():p for p in export.rglob('*') if p.is_file() and '.git' not in p.relative_to(export).parts}
issues=[]
if set(files)!=allow:
    issues += [f'unexpected: {sorted(set(files)-allow)}',f'missing: {sorted(allow-set(files))}']
patterns={
 'credential':r'ghp_[A-Za-z0-9]{20}|github_pat_[A-Za-z0-9_]{20}|sk-[A-Za-z0-9]{20}|AIza[A-Za-z0-9_-]{20}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
 'email':r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
 'private-path':r'(?i)C:\\Users\\[^\\\s]+\\',
 'telegram-token':r'\b\d{8,10}:[A-Za-z0-9_-]{35}\b',
}
rows=[]
for name,path in sorted(files.items()):
    data=path.read_bytes()
    if len(data)>5*1024*1024: issues.append(f'oversize: {name}')
    if path.suffix.lower() not in {'.woff2','.ico','.png'}:
        try: content=data.decode('utf-8')
        except UnicodeDecodeError: issues.append(f'non-utf8: {name}'); continue
        for label,regex in patterns.items():
            if re.search(regex,content): issues.append(f'{label}: {name}')
    rows.append((name,len(data),hashlib.sha256(data).hexdigest()))
out=root/'verification/publish-stage8';out.mkdir(parents=True,exist_ok=True)
with (out/'stage8-export-manifest.csv').open('w',newline='',encoding='utf-8') as handle:
    writer=csv.writer(handle);writer.writerow(['path','bytes','sha256']);writer.writerows(rows)
(out/'stage8-export-files.txt').write_text('\n'.join(name for name,_,_ in rows)+'\n',encoding='utf-8')
print('SCAN_PASS' if not issues else 'SCAN_FAIL')
print('files',len(rows))
for item in issues:print(item)
sys.exit(bool(issues))

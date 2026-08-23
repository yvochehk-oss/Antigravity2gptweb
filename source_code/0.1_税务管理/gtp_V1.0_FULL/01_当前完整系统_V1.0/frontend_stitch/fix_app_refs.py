# Fix App.tsx and DashboardView.tsx references
files_and_replacements = [
    ('src/App.tsx', [("'proj-01'", "'proj-02'")]),
    ('src/components/DashboardView.tsx', [
        ("onSelectProject('proj-01')", "onSelectProject('proj-02')"),
        ("proj.id === 'proj-01'", "proj.id === 'proj-02'"),
    ]),
]

for filepath, replacements in files_and_replacements:
    with open(filepath, 'r') as f:
        content = f.read()
    for old, new in replacements:
        content = content.replace(old, new)
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Updated {filepath}")

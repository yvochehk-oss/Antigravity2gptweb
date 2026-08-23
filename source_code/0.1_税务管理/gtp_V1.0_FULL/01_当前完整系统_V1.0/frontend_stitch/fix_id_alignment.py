# Fix 1: Update mockData.ts - change proj-01~05 to proj-02~06 to align with DB
with open('src/data/mockData.ts', 'r') as f:
    content = f.read()

# Rename IDs in reverse order to avoid double-replace
for old, new in [("'proj-05'", "'proj-06'"), ("'proj-04'", "'proj-05'"), 
                 ("'proj-03'", "'proj-04'"), ("'proj-02'", "'proj-03'"), 
                 ("'proj-01'", "'proj-02'")]:
    content = content.replace(old, new)

with open('src/data/mockData.ts', 'w') as f:
    f.write(content)

print("mockData.ts updated")

# Fix 2: Update any hardcoded selectedProjectId or useState defaults in TaxPlanningView.tsx
with open('src/components/TaxPlanningView.tsx', 'r') as f:
    content = f.read()

# Fix initial project ID references
content = content.replace("'proj-01'", "'proj-02'")

with open('src/components/TaxPlanningView.tsx', 'w') as f:
    f.write(content)

print("TaxPlanningView.tsx updated")

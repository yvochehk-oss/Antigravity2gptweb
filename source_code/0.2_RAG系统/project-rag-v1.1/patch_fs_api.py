import sys

with open("app/main.py", "r") as f:
    content = f.read()

api_code = """
class FsListRequest(BaseModel):
    path: str

@app.post("/api/v1/fs/list-dirs")
def api_fs_list_dirs(body: FsListRequest):
    \"\"\"List directories for a given path (admin/operator only).\"\"\"
    import os
    target_path = body.path
    if not target_path:
        target_path = "/Volumes" if os.path.exists("/Volumes") else "/"
    elif not os.path.exists(target_path) or not os.path.isdir(target_path):
        return {"error": "路径不存在或不是文件夹"}
    
    dirs = []
    try:
        for entry in os.scandir(target_path):
            if entry.is_dir() and not entry.name.startswith("."):
                dirs.append({"name": entry.name, "path": entry.path})
    except Exception as e:
        return {"error": str(e)}
        
    parent_path = os.path.dirname(target_path) if target_path != "/" else "/"
    return {
        "current": target_path,
        "parent": parent_path,
        "dirs": sorted(dirs, key=lambda x: x["name"].lower())
    }

@app.post("/api/v1/documents/import-folder")
"""

content = content.replace("@app.post(\"/api/v1/documents/import-folder\")", api_code)

with open("app/main.py", "w") as f:
    f.write(content)

print("Patched main.py")

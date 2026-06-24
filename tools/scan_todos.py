import os

def scan_todos(root_dir):
    todos = []
    fixmes = []
    
    exclude_dirs = {'.git', 'node_modules', '__pycache__', '.idea', 'dist', 'build'}
    
    for root, dirs, files in os.walk(root_dir):
        # Modify dirs in-place to skip excluded directories
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        
        for file in files:
            if file.endswith(('.py', '.vue', '.js', '.ts', '.css', '.md', '.html')):
                filepath = os.path.join(root, file)
                try:
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        for i, line in enumerate(f, 1):
                            if "TODO" in line:
                                todos.append(f"TODO: {filepath}:{i} - {line.strip()}")
                            if "FIXME" in line:
                                fixmes.append(f"FIXME: {filepath}:{i} - {line.strip()}")
                except Exception as e:
                    print(f"Error reading {filepath}: {e}")

    print("--- TODOs ---")
    for todo in todos:
        print(todo)
    
    print("\n--- FIXMEs ---")
    for fixme in fixmes:
        print(fixme)

if __name__ == "__main__":
    scan_todos(".")

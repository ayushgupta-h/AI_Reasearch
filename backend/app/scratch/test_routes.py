"""
Minimal route test without TestClient - just check routes are registered.
"""
import sys, os, time

BACKEND = r'c:\Users\aaayu\OneDrive\Desktop\Reasearch_AI\backend'
sys.path.insert(0, r'c:\Users\aaayu\OneDrive\Desktop\Reasearch_AI\.venv\Lib\site-packages')
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

t = time.time()
from app.factory import create_app
app = create_app()
print(f"App created in {time.time()-t:.2f}s")

# Check routes without HTTP calls
from starlette.routing import Route, Mount

def collect_routes(routes, prefix=""):
    result = []
    for r in routes:
        if hasattr(r, 'methods') and hasattr(r, 'path'):
            result.append((sorted(r.methods), prefix + r.path))
        if hasattr(r, 'routes'):
            result.extend(collect_routes(r.routes, prefix))
    return result

all_routes = collect_routes(app.routes)
print(f"\nRegistered routes ({len(all_routes)}):")
for methods, path in sorted(all_routes, key=lambda x: x[1]):
    print(f"  {methods} {path}")

# Validate key routes exist
expected = ['/health', '/providers', '/auth/config', '/auth/me',
            '/sessions', '/research', '/chat', '/export']
found_paths = [p for _, p in all_routes]
missing = [e for e in expected if not any(e in p for p in found_paths)]
if missing:
    print(f"\n❌ Missing routes: {missing}")
    sys.exit(1)
else:
    print(f"\n✅ All expected routes present")


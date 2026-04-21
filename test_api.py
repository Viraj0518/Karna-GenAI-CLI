from fastapi.testclient import TestClient

import api

c = TestClient(api.app)
for path in ("/opportunities", "/search?q=construction"):
    r = c.get(path)
    print(path, r.status_code, str(r.json())[:200])

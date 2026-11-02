import json
import sys
from .controller import main

try:
    sys.exit(main())
except Exception as exc:
    print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
    sys.exit(1)

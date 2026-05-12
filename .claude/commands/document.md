# /document — Auto-Documentation Generator

Generate comprehensive documentation for `$ARGUMENTS`.

## Execution Plan

### Step 1 — Discover Public API Surface

```bash
# Extract all public symbols
TARGET="$ARGUMENTS"

if [[ "$TARGET" == *.py ]] || find "$TARGET" -name "*.py" -q 2>/dev/null; then
  # Python: extract module docstrings, class/function signatures
  python3 -c "
import ast, sys, os

def extract_api(path):
    with open(path) as f:
        tree = ast.parse(f.read())
    items = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith('_'):
                items.append({
                    'type': type(node).__name__,
                    'name': node.name,
                    'lineno': node.lineno,
                    'docstring': ast.get_docstring(node) or ''
                })
    return items

import json
if os.path.isfile('$TARGET'):
    print(json.dumps(extract_api('$TARGET'), indent=2))
else:
    result = []
    for root, _, files in os.walk('$TARGET'):
        for f in files:
            if f.endswith('.py') and not f.startswith('_'):
                result.extend(extract_api(os.path.join(root, f)))
    print(json.dumps(result, indent=2))
"
fi
```

### Step 2 — Spawn Doc-Writing Subagent

```
You are a technical writer. Given this API surface:
<output from step 1>

Write complete documentation including:

1. MODULE / PACKAGE OVERVIEW
   - What does this code do? (1 paragraph)
   - Key concepts and terminology
   - Architecture decision rationale

2. INSTALLATION & QUICKSTART
   - Prerequisites
   - Installation steps (from pyproject.toml / package.json)
   - Minimal working example (< 10 lines)

3. API REFERENCE
   For each public function/class:
   - Signature with type annotations
   - Parameters table (name | type | default | description)
   - Returns
   - Raises
   - Example usage
   - ≥1 edge case note

4. USAGE EXAMPLES
   - 3 realistic end-to-end examples covering common use cases
   - Each example runnable as-is

5. CHANGELOG ENTRY
   - Conventional commit format for any recent changes

Output as a single Markdown document ready to be saved as docs/API.md
```

### Step 3 — Write Files

Save the output:
- `docs/API.md` — full API reference
- `docs/QUICKSTART.md` — quickstart guide (extract from above)
- Update `README.md` — refresh "Usage" and "API" sections only (preserve rest)

### Step 4 — Verify

```bash
# Ensure all public functions are documented
python3 -c "
import ast, sys
# Check that every public function has a docstring
# Report any missing
"
```

Report: N functions documented, N missing docstrings.

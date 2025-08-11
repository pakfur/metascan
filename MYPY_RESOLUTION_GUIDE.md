# MyPy Resolution Guide for Metascan

This guide provides a systematic approach to resolving the 148 mypy type checking errors in your codebase.

## Current Status
✅ **Completed:**
- Installed type stubs for PIL and requests
- Created mypy.ini configuration file
- Fixed some Optional type annotations in prompt_tokenizer.py

⚠️ **Remaining:** 148 errors across 19 files

## Priority Order (High to Low Impact)

### 1. **Critical Missing Methods (HIGH PRIORITY)**
**Files:** `metascan/core/media.py`, `metascan/core/database_sqlite.py`

**Issues:**
- Missing `to_json()` and `from_json()` methods on Media class
- These are required for database serialization

**Solution:**
```python
# The @dataclass_json decorator should provide these methods automatically
# If not working, verify dataclasses-json installation and imports:
pip install dataclasses-json

# Or add manual methods:
def to_json(self) -> str:
    return json.dumps(asdict(self), default=str, indent=2)

@classmethod
def from_json(cls, json_str: str) -> 'Media':
    data = json.loads(json_str)
    # Convert string paths back to Path objects
    data['file_path'] = Path(data['file_path'])
    if data.get('thumbnail_path'):
        data['thumbnail_path'] = Path(data['thumbnail_path'])
    return cls(**data)
```

### 2. **Collection Type Issues (MEDIUM-HIGH PRIORITY)**
**Files:** All extractor files (`swarmui.py`, `fooocus.py`, `comfyui.py`, etc.)

**Issues:**
- `Collection[str]` cannot be assigned to or have items appended
- Should be `List[str]` for mutable operations

**Solution:**
```python
# Change from:
from typing import Collection
def func() -> Collection[str]:
    result: Collection[str] = []  # ❌ Wrong

# To:
from typing import List
def func() -> List[str]:
    result: List[str] = []  # ✅ Correct
```

### 3. **Python 3.8 Compatibility (MEDIUM PRIORITY)**
**Files:** Multiple files with `list[str]`, `dict[str, Any]`, `tuple[int, int]`

**Issues:**
- Built-in generics not available in Python 3.8
- Need to import from `typing` module

**Solution:**
```python
# Change from:
def func() -> list[str]:  # ❌ Python 3.9+ only
    return []

# To:
from typing import List
def func() -> List[str]:  # ✅ Python 3.8 compatible
    return []
```

### 4. **Missing Type Annotations (MEDIUM PRIORITY)**
**Files:** Most core modules

**Issues:**
- Functions missing return type annotations
- Variables needing explicit type hints

**Solution:**
```python
# Add return type annotations:
def _init_database(self):  # ❌ Missing return type
def _init_database(self) -> None:  # ✅ With return type

# Add variable type hints:
extracted = {}  # ❌ Needs annotation
extracted: Dict[str, Any] = {}  # ✅ With annotation
```

### 5. **PyQt6 Event Handler Overrides (LOW-MEDIUM PRIORITY)**
**Files:** UI modules (`media_viewer.py`, `virtual_thumbnail_view.py`)

**Issues:**
- Event handlers expect `Optional[QEvent]` but receive `QEvent`

**Solution:**
```python
# Change from:
def keyPressEvent(self, event: QKeyEvent):  # ❌ Should be Optional

# To:
def keyPressEvent(self, event: Optional[QKeyEvent]):  # ✅ Correct
    if event is None:
        return
    # Handle event...
```

### 6. **None Safety Issues (LOW PRIORITY)**
**Files:** UI modules

**Issues:**
- Accessing attributes on potentially None objects

**Solution:**
```python
# Change from:
self.widget().width()  # ❌ widget() might return None

# To:
widget = self.widget()
if widget is not None:
    width = widget.width()  # ✅ Safe access
```

## Execution Plan

### Phase 1: Critical Fixes (1-2 hours)
1. Fix Media class serialization methods
2. Fix Collection → List type issues in extractors
3. Test that the application still works

### Phase 2: Python 3.8 Compatibility (1 hour)
1. Replace all built-in generic types with typing imports
2. Run mypy again to verify reduction in errors

### Phase 3: Type Annotations (2-3 hours)
1. Add missing return type annotations to all functions
2. Add explicit type hints to variables flagged by mypy

### Phase 4: UI and Advanced Issues (2-4 hours)
1. Fix PyQt6 event handler signatures
2. Add None safety checks
3. Handle remaining edge cases

## Useful Commands

```bash
# Run mypy on specific file
mypy metascan/core/media.py

# Run mypy with less strict settings for testing
mypy --ignore-missing-imports --no-strict-optional metascan/

# Check specific error type
mypy metascan/ 2>&1 | grep "assignment"

# Count remaining errors
mypy metascan/ 2>&1 | grep "error:" | wc -l
```

## Quick Wins (Start Here)

Here are the easiest fixes you can make immediately:

1. **Fix imports in files with Python 3.9+ syntax:**
   ```bash
   # Find files that need typing imports
   grep -r "list\[" metascan/ --include="*.py"
   grep -r "dict\[" metascan/ --include="*.py" 
   grep -r "tuple\[" metascan/ --include="*.py"
   ```

2. **Add missing return types to simple functions:**
   ```bash
   # Find functions missing return types
   mypy metascan/ 2>&1 | grep "missing a return type annotation"
   ```

3. **Fix variable annotations:**
   ```bash
   # Find variables needing type hints
   mypy metascan/ 2>&1 | grep "Need type annotation"
   ```

## Progress Tracking

Track your progress by running:
```bash
mypy metascan/ --config-file mypy.ini 2>&1 | grep "Found.*errors" | tail -1
```

Target: Reduce from 148 → 100 → 50 → 10 → 0 errors

## When to Use `# type: ignore`

Use sparingly and only for:
- Complex third-party library interactions
- PyQt6 quirks that are known to be safe
- Temporary suppressions while focusing on critical issues

```python
# Example of acceptable usage:
result = complex_third_party_function()  # type: ignore[return-value]
```

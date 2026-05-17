# quicksight-gen (deprecated shim)

This package was **renamed** to [`recon-gen`](https://pypi.org/project/recon-gen/) in v11.0.0.

## Migrate

```bash
# Replace
pip install quicksight-gen
# With
pip install recon-gen
```

And update imports in your code:

```python
# Replace
from quicksight_gen.common.tree import App
# With
from recon_gen.common.tree import App
```

## Why the rename

"QuickSight" is an AWS trademark, and the tool's scope grew beyond just
QuickSight artifact generation — it also produces an HTMX-rendered
dashboard alternative and a regulator-ready PDF audit report. The new
name `recon-gen` (short for "reconciliation generator") captures the
actual scope.

## Shim drop timeline

This shim package will be removed in the first `recon-gen` release
published ~30-60 days after v11.0.0 (calendar-anchored, not
release-count-anchored). The exact drop date is in v11.0.0
RELEASE_NOTES.

Until then, `pip install quicksight-gen` continues to work transparently
— installing it pulls in `recon-gen` as a transitive dependency. On
import it fires a one-time `DeprecationWarning` pointing at the new
name.

## Source

The shim's source lives at
[github.com/chotchki/recon-gen](https://github.com/chotchki/recon-gen)
under `quicksight-gen-shim/`.

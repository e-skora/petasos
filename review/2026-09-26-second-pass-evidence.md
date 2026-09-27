# Second-pass review: offline evidence

This accompanies `2026-09-26-second-pass.md`. The probes ran against PR #3 head
`19ef3d29254ed1dfe77b74add12431f9e3fc1150`, not against an implementation of change 001.
They use local fixtures and do not contact GitHub.

## Reproduce

In a disposable checkout of the reviewed head, install the locked dependencies with
`uv sync --locked`. Save the Python block below outside the checkout, then run it with
that checkout as the working directory and its Python environment. The script imports
`tests/test_merge_gate.py` to reuse the existing passing fixture. Every assertion confirms
an observed behavior at the reviewed head, including the unsafe permissive outcomes.
After a fix, those assertions should fail and the corresponding restrictive expectations
should become regression tests in the gate's actual test suite.

The Claude-origin fixture shows that no reviewer-run evidence is required. It does not
post a forged comment. The rename fixture supplies the old and new paths explicitly;
the gate drops the old path. The merge fixture replaces the entire GitHub client, so its
branch-deletion request is only an item in a local list.

```python
import contextlib
import dataclasses
import importlib.util
import io
import os
from pathlib import Path
from unittest.mock import patch

root = Path.cwd()
spec = importlib.util.spec_from_file_location('gate_tests', root / 'tests/test_merge_gate.py')
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
g = t.merge_gate

def show(label, facts):
    v = g.evaluate(facts)
    print(f'{label}: ready={v.ready}, reasons={v.reasons}')
    assert v.ready

show('late reaction from old review', t.ready_facts(reviews=[g.Review(t.CODEX_LOGIN, 'COMMENTED', 'Old review', t.OLD_SHA)], reactions=[g.Reaction(t.CODEX_LOGIN, '+1', '2026-09-26T10:05:00Z')]))
show('conflicting Claude summaries', t.ready_facts(issue_comments=t.ready_facts().issue_comments + [g.Comment(t.CLAUDE_LOGIN, f'Petasos review: claude\nCommit: {t.SHA}\nBlockers: 1\nblocker | file:1 | unresolved')]))
show('builder-shaped Claude summary without reviewer run', t.ready_facts())
show('questions file may be replaced or deleted', t.ready_facts(changed_files=t.DEFAULT_CHANGED_FILES + ['changes/QUESTIONS.md']))
show('plan-specific prohibition ignored', t.ready_facts(plan_text=t.DEFAULT_PLAN_TEXT + '\nwall_forbidden:\n- `src/petasos/trust/grants.py`\n'))

class FakeGitHub:
    repo = 'example/project'
    reads = []
    def paged(self, path, key=None):
        if path.endswith('/check-runs'):
            return [dict(id=i, name=n, conclusion='success', status='completed', started_at='2026-09-26T10:00:00Z', app={'slug': 'untrusted-check-app'}) for i, n in enumerate(g.REQUIRED_CHECKS)]
        if path.endswith('/files'):
            return [dict(filename='src/petasos/trust/copied_review.md', previous_filename='review/trusted-review.md', status='renamed')]
        if path.startswith('/issues/') and path.endswith('/comments'):
            return [dict(user={'login': t.CLAUDE_LOGIN}, body=t.ready_facts().issue_comments[0].body)]
        if path.endswith('/reviews'):
            return [dict(user={'login': t.CODEX_LOGIN}, state='COMMENTED', body='Looks fine.', commit_id=t.SHA)]
        return []
    def file_at(self, path, ref):
        self.reads.append((path, ref))
        if path.endswith('/plan.md'):
            return t.DEFAULT_PLAN_TEXT
        if path.endswith('/report.md'):
            return t.ready_facts().report_text
        raise AssertionError(path)

pr = dict(number=1, title='[build] 001-trust-core', draft=False, base={'ref': 'main'}, head={'sha':t.SHA, 'ref':'build/001-trust-core', 'repo':{'full_name':'example/project'}}, labels=[], body='## Wall check\nPASS')
api = FakeGitHub()
facts = g.gather(api, pr)
show('rename removes protected review file; foreign check app accepted', facts)
assert 'review/trusted-review.md' not in facts.changed_files
assert not any(path.endswith('/proposal.md') for path, ref in api.reads)
print('eligibility: gather never reads proposal status, dependencies, or grounded commit')

class MergeAPI:
    calls = []
    def __init__(self, *a): pass
    def paged(self, path): return [pr]
    def request(self, method, path, body=None):
        self.calls.append((method,path,body))
        if path.endswith('/merge'):
            return {'merged':False, 'message':'not merged'}
        return {}

with patch.object(g,'GitHub',MergeAPI), patch.object(g,'gather',return_value=t.ready_facts()), patch.dict(os.environ, {'GITHUB_TOKEN':'local-fixture','GITHUB_REPOSITORY':'example/project','MERGE_GATE_DRY_RUN':''}):
    with patch.object(g,'summary') as output:
        g.main()
    assert any(method=='DELETE' for method,path,body in MergeAPI.calls)
    assert any('merged at head' in line for line in output.call_args.args[0])
    assert MergeAPI.calls[0][2]['sha'] == t.SHA
    print('merge response merged=false: still reports merged and deletes branch')
    print('positive control: merge request carries evaluated head SHA')
print('All probes completed offline. No GitHub writes were performed.')

```

## Observed output

```text
late reaction from old review: ready=True, reasons=()
conflicting Claude summaries: ready=True, reasons=()
builder-shaped Claude summary without reviewer run: ready=True, reasons=()
questions file may be replaced or deleted: ready=True, reasons=()
plan-specific prohibition ignored: ready=True, reasons=()
rename removes protected review file; foreign check app accepted: ready=True, reasons=()
eligibility: gather never reads proposal status, dependencies, or grounded commit
merge response merged=false: still reports merged and deletes branch
positive control: merge request carries evaluated head SHA
All probes completed offline. No GitHub writes were performed.
```

The questions-file probe passes a changed path: because file status and content are absent
from `PullFacts`, replacement and deletion cannot be distinguished from an allowed append.
The eligibility probe records all file reads. Neither proposal status nor dependency or
grounding evidence is fetched. The successful fixture can therefore be identical for an
authorized and unauthorized change.

## Other independently executed checks

At the reviewed head, Python 3.13.12, before any review document was committed:

```text
uv sync --locked: installed the locked dependencies successfully
pytest -q: 72 passed, 1 skipped in 0.69s
ruff check .: All checks passed!
ruff format --check .: 30 files already formatted
uv pip check: All installed packages are compatible
uv run --no-sync pip --version: Failed to spawn: pip; No such file or directory
```

The identifier test skipped locally because its secret was unavailable. The matching
GitHub check passed; no secret value was obtained for these probes.

## Read-only live inspection

- PR #3 remained open at the exact base and head named above when rechecked.
- The head's required checks were produced by `github-actions` and all passed.
- The branch rule API returned required checks with no pinned integration and
  `strict_required_status_checks_policy: false`.
- The repository-secret name listing contained only `PRIVATE_DENYLIST`.
- Builder run `36282155669` on the base commit failed after obtaining its GitHub App token.
  The error said no supported Claude authentication credential had been supplied.
- Vendor plugin source was checked at commit
  `7779afb12e3635f46f56ec823979d68350ae000b`; the Claude Action `v1` ref resolved to
  `756cc22e19660d20e8cc9496b4f242475a7f7790` during this review.

These observations concern the inspected state, not a promise that external settings or
moving vendor tags will stay unchanged.

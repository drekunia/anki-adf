# Local QA on Windows

These checks use synthetic data and never open the user's Anki collection.

Run the portable regression suite from the add-on directory:

```powershell
python -B -m unittest discover -s . -v
```

The smoke tests use the Python 3.13 and Qt libraries bundled with the installed
Anki at `C:\Program Files\Anki`. Build the small console embedding host:

```powershell
$qa = Join-Path (Get-Location).Path '.qa'
& 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe' /nologo "/out:$qa\host.exe" "$qa\host.cs"
& "$qa\host.exe" "$qa\qt_smoke.py"
& "$qa\host.exe" "$qa\anki_service_smoke.py"
```

`qt_smoke.py` runs actual Qt widgets offscreen, supplies an in-memory fake Anki
collection and settings, and checks configurations, counts, preset changes,
source filters, tri-state selection, tag targets/counts, tag-state filters,
side-by-side details, single-click Browse routing, main-menu Browse ownership,
cache invalidation and ignored overlaps. It can generate `main.png`/`results.png`.

`anki_service_smoke.py` creates a new synthetic collection in a `collection-*`
subdirectory here and checks the actual installed Anki backend: normalized
matching, deck search, multiple decks, sibling suspension, undoable tags, live
index updates and ignored pairs. Generated collections can be removed after
inspection; they contain test data only.

The host, screenshots and generated collection directories are build/test outputs
and are not required to run the add-on.

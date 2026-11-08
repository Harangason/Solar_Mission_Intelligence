"""One process version for API health, route results, and saved calculations."""
from pathlib import Path
import hashlib
ROOT=Path(__file__).resolve().parents[1]
FILES=[ROOT/'main.py',*ROOT.joinpath('planner').glob('*.py'),*ROOT.joinpath('solver').glob('*.py'),
    *ROOT.joinpath('services').glob('*.py'),*ROOT.joinpath('models').glob('*.py'),
    *ROOT.joinpath('web','src').rglob('*.ts'),*ROOT.joinpath('web','src').rglob('*.tsx'),ROOT/'kernels'/'manifest.json']
CALCULATION_BUILD=hashlib.sha256(b''.join(p.relative_to(ROOT).as_posix().encode()+b'\0'+p.read_bytes() for p in sorted(FILES) if p.is_file())).hexdigest()[:16]

"""Select synthetic data before importing the UI; never inherit a live backend."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
os.environ['FEDERATION_CATALOG'] = str(ROOT / 'examples/minimal/CATALOG.md')
os.environ.pop('KNOWLEDGE_CATALOG', None)
for key in ('MEILI_URL', 'MEILI_API_KEY', 'OLLAMA_EMBED_URL', 'KNOWLEDGE_AUTO_INDEX', 'KNOWLEDGE_ENABLE_LIVE_MARIMO'):
    os.environ.pop(key, None)
sys.path.insert(0, str(ROOT / 'foundation/tools/knowledge-ui'))
sys.path.insert(0, str(ROOT / 'foundation/integrations/federation-core'))

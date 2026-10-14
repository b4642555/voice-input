r"""Wikidata items that a term pack does not list yet, most linked first, to review by hand when updating a pack
(voiceinput/packs/<id>.txt is curated: Wikidata misses tools such as Cursor or Ollama and lists noise such as
DeviantArt). Labels with digits are usually versions (GPT-4.5); textfmt already writes spoken numbers, so packs keep
the base name.

  .venv\Scripts\python.exe dev\pack_candidates.py ai [min_sitelinks]
"""
import csv
import io
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from voiceinput import packs  # noqa: E402

# P31 classes per pack: AI model, large language model, chatbot, generative pre-trained transformer, generative AI,
# generative AI chatbot, generative AI website, text-to-video model
CLASSES = {
    "ai": "Q117349473 Q115305900 Q870780 Q116777014 Q117246174 Q133284163 Q119018971 Q115517227",
}
QUERY = """SELECT DISTINCT ?label (COUNT(?link) AS ?sites) WHERE {
  VALUES ?cls { %s }
  ?item wdt:P31 ?cls ; rdfs:label ?label . FILTER(LANG(?label) = "en")
  OPTIONAL { ?link schema:about ?item }
} GROUP BY ?label ORDER BY DESC(?sites)"""


def main():
    pack_id = sys.argv[1]
    least = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    query = QUERY % " ".join("wd:" + c for c in CLASSES[pack_id].split())
    url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": query})
    req = urllib.request.Request(url, headers={"Accept": "text/csv", "User-Agent": "VoiceInput-dev/0.1 (term packs)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        rows = list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))
    have = {w.casefold() for w in packs._load(packs.SHIPPED / f"{pack_id}.txt").words}   # the curated list
    for row in rows:
        if int(row["sites"]) >= least and row["label"].casefold() not in have:
            print(f"{row['sites']:>4}  {row['label']}")


if __name__ == "__main__":
    main()

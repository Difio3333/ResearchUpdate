# AI Research Radar

A personal feed of new AI/ML papers and trending repos. Runs entirely on GitHub, no server and no cost for public repos.

- **Sources:**
  - arXiv **AI/ML**: cs.AI, cs.LG, cs.CL, cs.CV, stat.ML
  - arXiv **Math & algorithms**: numerical analysis, optimization, algorithms and data structures, mathematical and symbolic software, computational geometry, information theory, statistical computing, discrete math. A keyword filter keeps the papers that look applicable to software.
  - Hugging Face daily papers
  - GitHub repos, new and fast-starred, by topic
- **Collector:** `collector/collect.py`, Python standard library only
- **Schedule:** GitHub Actions, every 6 hours (`.github/workflows/update.yml`)
- **Storage:** `docs/data/items.json`, committed by the workflow
- **Frontend:** static page in `docs/`, served by GitHub Pages

## Setup

1. Create a new GitHub repo and push these files to `main`.
2. **Settings → Pages:** Source = "Deploy from a branch", Branch = `main`, folder = `/docs`.
3. **Settings → Actions → General → Workflow permissions:** select "Read and write permissions".
4. **Actions → Update feed → Run workflow** once to fill the first data.
5. Your site is at `https://<your-username>.github.io/<repo-name>/`.

## Configure

Edit `config.json`:

- `arxiv.feeds` holds a list of feeds. Each one has a `name` (which becomes a topic in the page's dropdown), arXiv `categories`, `max_results`, and optional `keywords`. When keywords are set, a paper is kept only if its title or abstract contains at least one of them, case-insensitive. Remove `keywords` to keep everything from that feed. To add another area, add another feed, for example `{"name": "crypto", "categories": ["cs.CR"], "max_results": 100}`.
- `github` sets the topics, minimum stars and how recent repos must be.
- `retention_days` and `max_items` limit how much history the page loads.

Other arXiv categories that may be worth adding: `cs.CC` (complexity), `cs.LO` (logic and verification), `cs.CR` (cryptography), `quant-ph` (quantum algorithms), `math.CO` (combinatorics), `math.PR` (probability).

## Run locally

```bash
python collector/collect.py
python -m http.server -d docs 8000   # then open http://localhost:8000
```

## Notes

- GitHub has no official trending API. "Trending" here means recently created repos with the most stars, per topic.
- Items that appear on both arXiv and Hugging Face are merged into one card.
- Ideas for later: LLM relevance scoring and summaries, Semantic Scholar enrichment, RSS/Atom output, email or Telegram digest.

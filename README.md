# Market Brief Agent

Bloomberg Terminal (TOP <GO>) -tyylinen päiväraportti: markkinadata (yfinance) + uutiset (RSS), suodatus Claude Sonnet 5.5:llä.

## Asennus

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

## Käyttö

```bash
python agent.py
```

Raportti tulostuu terminaaliin ja tallentuu tiedostoon `latest_brief.txt`.

## GitHub Actions

1. Pushaa repo GitHubiin.
2. Lisää `Settings → Secrets and variables → Actions` -kohtaan secret `ANTHROPIC_API_KEY`.
3. Workflow `.github/workflows/daily_brief.yml` ajaa agentin arkisin klo 06:00 UTC ja commitoi `latest_brief.txt`:n. Manuaalinen ajo: `Actions → Daily Market Brief → Run workflow`.

## Huomio

- Claude Sonnet 5.5 ei hyväksy `temperature`-parametria, joten sitä ei lähetetä.
- Pyynnössä on päällä palvelinpuolen fallback (`fallbacks="default"`), joka ohjaa pyynnön toiselle mallille, jos turvallisuusluokittelija hylkää sen.

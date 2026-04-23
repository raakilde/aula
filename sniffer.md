# Sniffer: Udtræk Aula tokens og deviceId

For at kunne spoofe Aula-sessioner og automatisere adgang til Aula API’et, skal du bruge access_token, refresh_token og deviceId fra Aula-appen. Dette kan gøres med en sniffer, fx `sniffer.py` baseret på mitmproxy.

## Sådan bruger du sniffer.py

1. **Start mitmproxy** og opsæt din mobil til at bruge proxyen.
2. Kør `sniffer.py` mens du logger ind i Aula-appen på din mobil.
3. Snifferen gemmer automatisk:
   - access_token
   - refresh_token
   - deviceId
   - relevante cookies og headers
   i en fil (fx `.aula_tokens.json`).
4. Brug disse værdier i din Home Assistant-integration eller til curl-requests.

## Eksempel på brug

```sh
python3 sniffer.py --mitmproxy-port 8080 --output .aula_tokens.json
```

## Output-format

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "device_id": "IOS-private-...",
  "expires_at": 1234567890
}
```

## Tips
- Sørg for at deviceId er den fulde streng (fx `IOS-private-...`).
- Opdater tokens og deviceId løbende, hvis du logger ud eller får nye værdier.
- Brug altid de nyeste tokens og deviceId i din integration.

---
Se README.md for integration med Home Assistant.

# Aula til Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=plastic)](https://github.com/hacs/integration)

Home Assistant-integration til [Aula](https://www.aula.dk) — den danske skoleplatform. Giver adgang til skemaer, ugeplaner, beskeder, fravær og meget mere direkte i dit Home Assistant-dashboard.

## Funktioner

| Kategori | Sensorer |
|----------|----------|
| **Skema** | Ugentligt skoleskema (denne uge + næste uge) |
| **Ugeplaner** | Ugeplan, huskeliste |
| **Beskeder** | Opslag, beskeder (Aula-mail) |
| **Fravær** | Fremmøde-status, tilstedeværelse |
| **Lukkedage** | Helligdage og skolefridage |
| **Uddannelse** | Min Uddannelse — forløb og ugenoter |
| **Bibliotek** | Systematic A/S skolebibliotek |
| **Kalender** | Aula-kalenderbegivenheder |

### Dashboard-eksempler

Ugeplaner og ugenoter ligger som sensor-attributter og kan vises med et markdown-kort:

```yaml
type: markdown
content: '{{ state_attr("sensor.skolen_barn", "ugeplan") }}'
title: Ugeplan
```

## Installation

### HACS (anbefalet)

1. Åbn HACS i Home Assistant.
2. Søg efter **Aula** og installér.
3. Genstart Home Assistant.

### Manuel installation

1. Download seneste release fra GitHub.
2. Kopiér mappen `custom_components/aula` til din `custom_components`-mappe.
3. Genstart Home Assistant.

## Opsætning

[![Tilføj integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=aula)

**Settings → Integrations → Add Integration → Aula**

### MitID-login

Integrationen logger ind via MitID direkte fra Home Assistant — ingen browser eller cookies krævet på serveren.

1. Indtast dit **MitID brugernavn** og **adgangskode**.
2. Vælg godkendelsesmetode:
   - **APP** — godkend via MitID-appen på din telefon (QR-kode vises i HA).
   - **TOKEN** — indtast engangskode fra din MitID-kodelæser.
3. Integrationen henter automatisk OAuth-tokens og holder sessionen kørende.

> Tokens fornyes automatisk — du skal normalt kun logge ind én gang.

### Sådan virker autentificeringen

```
MitID-login → OAuth 2.0 PKCE → SAML-udveksling → Aula API-token
```

- **Access token** udløber efter ca. 1 time og fornyes automatisk via refresh token.
- **Refresh token** er gyldigt i ca. 30 dage og roteres ved hver fornyelse.
- Ingen brugernavne eller adgangskoder gemmes efter login — kun tokens opbevares lokalt i Home Assistant.

## Kompatibilitet

Integrationen kræver ingen browser, GUI eller speciel hardware på HA-serveren.

- Home Assistant OS, Container, Core, Supervised
- Alle arkitekturer (x86_64, ARM64, ARMv7)
- Headless-servere, Docker, VM'er, cloud-instanser

## Fejlfinding

| Problem | Løsning |
|---------|---------|
| Login fejler | Kontrollér MitID brugernavn og adgangskode |
| Token udløbet | Integrationen fornyer automatisk — vent et par minutter og tjek loggen |
| QR-kode vises ikke | Sørg for at godkendelsesmetoden er sat til **APP** |
| Engangskode afvist | Tjek at koden fra kodelæseren er aktuel (ikke udløbet) |
| Sensorer opdaterer ikke | Tjek Home Assistant-loggen for fejl fra `aula`-integrationen |

## Privatlivspolitik og GDPR

Denne integration behandler personoplysninger i henhold til GDPR (EU 2016/679). Da al data forbliver lokalt i din Home Assistant-instans, fungerer du som **dataansvarlig** for de oplysninger, integrationen henter.

### Data der behandles

| Datakategori | Eksempler | GDPR-grundlag |
|---|---|---|
| Login-oplysninger | MitID brugernavn, adgangskode (kun under login) | Art. 6(1)(a) — samtykke |
| OAuth-tokens | Access token, refresh token | Art. 6(1)(b) — nødvendigt for funktion |
| Barnets data | Navn, skema, ugeplan, fravær | Art. 6(1)(a) — samtykke |
| Beskeder | Opslag, Aula-mail, afsendere | Art. 6(1)(a) — samtykke |

### Dataopbevaring

- **Ingen cloud-tjenester** — al data opbevares udelukkende lokalt i Home Assistant.
- **Ingen tredjeparter** — data sendes kun mellem din HA-instans og Aulas API.
- **Credentials** — brugernavn og adgangskode bruges kun i login-øjeblikket og gemmes ikke.
- **Tokens** — opbevares krypteret i Home Assistants konfiguration og kan fjernes ved at slette integrationen.

### Anbefalinger

- Begræns adgang til din Home Assistant-instans.
- Brug HTTPS og stærke adgangskoder til Home Assistant.
- Fjern integrationen under **Settings → Integrations** hvis du ikke længere ønsker at bruge den — alle tokens slettes automatisk.

## Sikker opbevaring af tokens og deviceId

Fra og med version X.X.X gemmer integrationen automatisk dine tokens og deviceId i Home Assistants **hemmelige lager** (secret storage). Dette betyder:

- Tokens og deviceId opbevares krypteret og utilgængeligt for andre integrationer eller brugere.
- Ingen tokens skrives længere til configuration.yaml, config entry eller ukrypterede filer.
- Ved opdatering eller fornyelse af tokens sker al lagring automatisk og sikkert.

**Du behøver ikke selv gøre noget** — integrationen håndterer alt automatisk.

> Hvis du tidligere har brugt .aula_tokens.json fra sniffer.py, kan du kopiere værdierne ind første gang. Herefter håndteres alt via secret storage.

Se [sniffer.md](sniffer.md) for hvordan du udtrækker tokens/deviceId første gang.

---

## Support

Har du spørgsmål eller problemer? Opret et [issue på GitHub](https://github.com/raakilde/aula/issues).

## Licens

Se [LICENSE](LICENSE) for detaljer.

## Udtræk tokens og deviceId med sniffer.py

Hvis du vil bruge eksisterende Aula-session fra mobil-appen, kan du udtrække access_token, refresh_token og deviceId med en sniffer (fx `sniffer.py` baseret på mitmproxy).

Se [sniffer.md](sniffer.md) for guide og eksempler.

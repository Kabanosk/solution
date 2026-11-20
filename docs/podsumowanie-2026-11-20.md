# Podsumowanie: Autonomous CFO, stan na piątek 20.11.2026 ok. 16:30

Collabothon 2026, wyzwanie Red Hat „Autonomous CFO”. Termin oddania: niedziela 22.11, 10:00 (cel: 9:00).
Szczegółowe wymagania, architektura i lista luk: [`docs/requirements.md`](requirements.md). API: [`docs/api.md`](api.md).
Klaster: [`docs/cluster-setup.md`](cluster-setup.md). Decyzje: `.house/DECISIONS.md`.

## 1. Co budujemy (w jednym akapicie)

Agent-„dyrektor finansowy” dla małej firmy (demo: Pizzeria Amalfi). Łączy dane z całej firmy: wyciąg bankowy,
faktury, arkusz HR, metryki (zamówienia, klienci) i firmowe foldery na komputerach pracowników (przez MCP).
Sam paruje przelewy z fakturami, a brakujących dokumentów szuka u księgowej Grażyny albo prosi o nie na Slacku.
Wyłapuje anomalie kosztów (np. chmura +50% przy zamówieniach +3%) i prognozuje płynność (czerwcowe zamknięcie lokalu).
Ważne rzeczy (kredyt, lokata, nowy dostawca, duże kwoty) tylko proponuje i czeka na zgodę właściciela na Slacku.
Każde „wykonanie” jest symulowane. Każda decyzja ma poziom: AUTO / NOTIFY / APPROVAL / SIMULATED.

## 2. Co działa na klastrze (namespace `cfo-agent-uwur`)

| Element | Stan |
|---|---|
| API (FastAPI) | `https://api-cfo-agent-uwur.apps.ocp.mwpzc.sandbox39.opentlc.com` ✅ |
| Worker (agent + symulator + bot Slack, `cfo.run`) | działa, połączenie Socket Mode ze Slackiem nawiązane ✅ |
| Serwery MCP: `company-db`, `workstations`, `hr` | działają ✅ |
| Postgres + dane | schemat zmigrowany, seed (`cfo-seed`) wykonany ✅ |
| MLflow | `https://mlflow-cfo-agent-uwur.apps.ocp.mwpzc.sandbox39.opentlc.com` ✅ |
| Próbnik metryk vLLM (`usage-sampler`) | co 60 s zapisuje `/metrics` Granite ✅ |
| Model | Granite 4.0 H Tiny, KServe InferenceService w `my-first-model`, HTTP `…-predictor…:8080/v1` (D-020). Llama 3.2 3B wyłączona |
| Serwer plików z danymi organizatora (`files`) | tylko wewnątrz klastra, bez Route (dane „for business use only”) |

Sekrety: tokeny Slacka w Secrecie `slack` (`SLACK_APP_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_USER_MAP`), hasło bazy w `postgres`.
Nic z tego nie jest w repo.

## 3. Co jest w kodzie (`backend/cfo`)

| Moduł | Co robi | Zadanie |
|---|---|---|
| `config.py`, `domain.py`, `db/` | konfiguracja z env, typy domenowe, schemat Postgresa | T001 |
| `data/` | generator danych spójnych z wyciągiem (faktury AS/FLV, folder Grażyny, HR, reklamy, chmura, zamówienia) i loader | T002 |
| `agent/policy.py` | autonomia: rutynowe kontra nietypowe, progi 1% / 5% wpływów, nowy dostawca nigdy AUTO, reguły CEO | T003 (D-005, D-011) |
| `mcp_servers/` | baza firmy, komputery pracowników (tylko foldery firmowe, każdy dostęp w `access_log`), HR z łańcuchem eskalacji | T004 (D-004) |
| `agent/graph.py` | LangGraph: parowanie faktur, eskalacja z pomijaniem urlopów, zgody CEO (interrupt/resume), pod-agent do anomalii | T005 |
| `analysis/cost_metrics.py` | koszt kontra metryka (jedzenie/zamówienia, reklamy/klienci, chmura/zamówienia online) | T007 (D-006) |
| `channels/` | bot Slack: DM, przyciski Zatwierdź/Odrzuć, czat CEO | T008 |
| `sim/` | symulator grudnia ze sterowaniem tempem | T010 |
| `run.py` | jeden proces: symulator, agent, Slack | T011 |
| `forecast/` | cechy, XGBoost (MAE 207 €/dzień vs 1 415 € dla naiwnej średniej), bufor bezpieczeństwa, „co jeśli”, propozycje kredytu/lokaty, rejestr w MLflow | T014–T016, T026 (D-013) |
| `notebooks/forecast.ipynb` | notebook na workbench RHOAI (bonus Jupyter + MLflow) | T017 |
| `api/` | REST: KPI, koszty, prognoza, decyzje, reguły, zgody, zdarzenia SSE, symulator, zużycie LLM | T018–T020, T032 |
| `observability.py` | tracing agenta w MLflow | T027 |
| `usage/` | koszt działania agenta z metryk vLLM | T032 (D-019) |
| `Containerfile`, `deploy/` | obraz UBI, manifesty OpenShift (kustomize) | T021–T024 + infrastruktura Grzesia (PR #2) |

## 4. API: zużycie modelu (nowe, T032)

- `GET /v1/llm/usage?from&to&group_by=day|hour|event_type|purpose`: żądania, tokeny (też z cache), p50/p95 czasu,
  koszt dwiema metodami (zajętość GPU i amortyzacja wg udziału w tokenach), koszt na akcję agenta i porównanie
  z hostowanym API. Stawki są w bloku `assumptions`, ze źródłem i datą.
- `GET /v1/llm/usage/live`: bieżący stan vLLM (żądania w toku i w kolejce, KV-cache, tokeny/s).
- Dane: tabela `llm_usage` (każde wywołanie modelu przez agenta, z krokiem i zdarzeniem) oraz `vllm_metrics_samples`
  (próbki `/metrics`).
- Założenia cenowe (do powiedzenia jako założenia, nie fakty): L4 ≈ 0,70 €/h (AWS g6.xlarge 0,80 $/h, kurs 0,87);
  gpt-5-mini 0,25/2,00 $, gpt-5-nano 0,05/0,40 $ za 1M tokenów wejścia/wyjścia.
- Sprawdzone na klastrze 20.11 16:16: `/live` zwraca świeżą próbkę Granite; pierwsze wywołanie kosztowało
  ok. 0,00025 € (zajętość GPU) albo 0,016 € (amortyzacja).

## 5. Najważniejsze decyzje

- **D-002:** scenariusz to rozbudowana Pizzeria Amalfi; historia od stycznia do listopada 2025, grudzień leci na żywo z symulatora.
- **D-003:** faktury tylko po stronie wydatków. Brak dokumentu to ryzyko podatkowe (Belegpflicht).
- **D-004:** agent ma dostęp tylko do danych firmowych. Każdy dostęp jest logowany, a pracownik dostaje DM, że agent zajrzał.
- **D-005, D-011:** autonomia według reguły „rutynowe kontra nietypowe”. Nowy dostawca nigdy nie jest AUTO.
- **D-008, D-017, D-020:** Python, LangGraph, MCP i Postgres. Model ustawia się w configu; domyślnie Granite 4.0 H Tiny.
- **D-009, D-015:** bonusy Red Hata, w które celujemy: vLLM, NeMo Guardrails (robi Wojtek), MLflow (tracing, ewaluacja, rejestr modelu) i Jupyter.
- **D-013:** alarm płynności to spadek poniżej bufora, a nie poniżej zera.
  - Na prawdziwych danych saldo w czerwcu zostaje ok. 45 tys. €, więc agent proponuje lokatę 24,5 tys. €.
  - W scenariuszu „co jeśli” z piecem za **30 tys. €** (nie 25 tys.) w marcu saldo przebija bufor i agent proponuje linię kredytową na 7 tys. €.
- **D-019:** koszt działania agenta liczony z metryk vLLM. Zastępuje odrzucone T030/T031.

Uwaga porządkowa: w `.house/DECISIONS.md` są po dwa wpisy D-016 i D-018 z różnych sesji. Moje przenumerowałem na
D-019 i D-020; pozostałe dwa duplikaty warto przenumerować tam, skąd pochodzą.

## 6. Co zostało (stan zadań w `.house/tasks/`)

| Zadanie | Stan | Uwagi |
|---|---|---|
| T013: Slack, klaster, NeMo | Slack ✅, klaster ✅, **NeMo Guardrails: Wojtek** | bonus; bramka płatności jest już w kodzie (LangGraph) |
| T025: przewodnik wdrożenia `docs/deploy.md` | do zrobienia | |
| T028: ewaluacja agenta w MLflow | do zrobienia | drugi człon bonusu MLflow („tracing and evaluating”) |
| T029: MLflow na klastrze (workbench, trening, model `champion`, trace'y) | do zrobienia | dowód dla jury |
| T040, T042, T043, T044: poprawki po review | w review | |
| T041: odporność agenta na mały model | do zrobienia | ważne: Granite Tiny, brak auto tool-choice w vLLM |
| T046: zabezpieczenie API (zapis, SSE) | do zrobienia | Route jest publiczny: każdy może dziś zatwierdzić wniosek jako `vincenzo` |

## 7. Ryzyka do demo (pełna lista: `docs/requirements.md` §8)

1. **Golden path na prawdziwym modelu nie był jeszcze przejechany end-to-end.** Testy biegną na atrapie modelu. Trzeba odpalić
   `POST /v1/sim:start` na klastrze i przejść kroki z `.house/demo-flow.md`.
2. **`demo-flow.md` rozjeżdża się z danymi.**
   - FLV-42026 (29.12) jest przed AS-69664 (30.12) i obie wypadają w urlopie Grażyny.
   - Alert chmury przychodzi dopiero 31.12.
   - Kredyt pojawia się tylko w scenariuszu „co jeśli” z piecem za 30 tys. €.
3. **Dashboard Pawła (PR #4) i API.** Trzeba sprawdzić, czy dashboard woła API, a nie fixture'y.
   Na starszym branchu `feature/dashboard` leżał CSV organizatora: musi zniknąć z publicznej historii.
4. **Publiczne API bez autoryzacji** (T046).
5. **Termin zgłoszenia bonusów** u Georga i Karstena (Red Hat) jest nieznany: zapytać na Discordzie.

## 8. Jak to obejrzeć

```bash
curl -s https://api-cfo-agent-uwur.apps.ocp.mwpzc.sandbox39.opentlc.com/v1/kpis
curl -s https://api-cfo-agent-uwur.apps.ocp.mwpzc.sandbox39.opentlc.com/v1/llm/usage/live
curl -s -X POST https://api-cfo-agent-uwur.apps.ocp.mwpzc.sandbox39.opentlc.com/v1/sim:start -H 'content-type: application/json' -d '{"speed": 3600}'
curl -sN https://api-cfo-agent-uwur.apps.ocp.mwpzc.sandbox39.opentlc.com/v1/events   # zdarzenia na żywo
```

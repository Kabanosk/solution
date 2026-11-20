# Postęp

Ślad dla drużyny. Krótkie wpisy po każdym skończonym zadaniu, dopisywane na końcu (najnowsze na dole).
Format: `- YYYY-MM-DD HH:MM · <kto> · <co jest gotowe / co się zmieniło> · <link do raportu>`
- 2026-11-20 13:50 intake + grill zakończone; decyzje D-002…D-010; 13 zadań (T001–T013) zamrożonych; Q-002 o CSV otwarte
- 2026-11-20 14:10 · coder · T001 gotowe: szkielet backendu (config, typy domenowe, schemat DB, klient LLM + FakeLLM), check GREEN · reports/T001.md
- 2026-11-20 13:57 T001 cofnięte po scaleniu: regresja w T001: init.sh
- 2026-11-20 14:40 · coder · T003 gotowe: silnik polityki autonomii (classify, reguły CEO, parse_rule), 38 testów, check GREEN · reports/T003.md
- 2026-11-20 15:00 · coder · T004 gotowe: serwery MCP company-db, workstations (tylko foldery firmowe, log dostępu), hr; 27 testów, check GREEN · reports/T004.md
- 2026-11-20 15:10 · coder · T008 gotowe: Notifier + adapter Slack (Socket Mode, przyciski Zatwierdź/Odrzuć, czat CEO), manifest i instrukcja, 26 testów, check GREEN · reports/T008.md
- 2026-11-20 14:23 T008 cofnięte po scaleniu: regresja w T004: AC-1 (exit 1)
- 2026-11-20 15:30 · coder · T002 gotowe: generator danych syntetycznych (faktury AS/FLV, PDF/eml, HR, chmura +50%, grudzień jako zdarzenia) + loader Postgres; 91 testów, check GREEN · reports/T002.md
- 2026-11-20 16:00 · coder · T005 gotowe: agent LangGraph (rekoncyliacja faktur, eskalacja po łańcuchu HR, zatwierdzanie przez interrupt + resume, pod-agent do anomalii kosztów), 23 testy, check GREEN · reports/T005.md
- 2026-11-20 16:30 · coder · T014 gotowe: cechy prognozy (przepływ dzienny + kalendarz, flaga przerwy czerwcowej, dni płatności, koszt miesięczny na zespół), 12 testów, check GREEN · reports/T014.md
- 2026-11-20 16:00 · coder · T018 gotowe: szkielet API (FastAPI, /v1 stuby w finalnym kształcie z flagą stub, model błędów, paginacja, CORS, OpenAPI, docs/api.md), 27 testów, check GREEN · reports/T018.md
- 2026-11-20 17:00 · coder · T010 gotowe: symulator grudnia (start/pauza/krok/skok, stan w Postgresie, CLI python -m cfo.sim, subskrybenci zamiast wywołań agenta), 17 testów · reports/T010.md
- 2026-11-20 17:30 · coder · T015 gotowe: model XGBoost prognozy płynności (zapis/odczyt, MLflow opcjonalnie, ważności cech i SHAP dla T016), transze partnera jako znany harmonogram; dołek czerwca 2026 widoczny (-14 tys.), ale saldo od końca listopada nie schodzi poniżej 0 (min ~45 tys.), 15 testów, check GREEN · reports/T015.md
- 2026-11-20 18:00 · coder · T007 gotowe: analiza koszt kontra metryka (3 pary, koszt na jednostkę, wykrywanie anomalii z pozycjami faktur i zdaniem wyjaśnienia), flaguje tylko chmurę grudnia +50%; podpięty pod subagenta z T005; 16 testów, check GREEN · reports/T007.md
- 2026-11-20 18:10 · coder · T021 gotowe: Containerfile (UBI 9, uv, non-root, UID OpenShift) + dispatcher `cfo` (api, mcp, worker, migrate, generate, load); kontener startuje, /healthz 200 · reports/T021.md

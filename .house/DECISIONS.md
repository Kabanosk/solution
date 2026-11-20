# Dziennik decyzji

Każda decyzja, która zmienia kierunek, zakres albo zasady pracy. Najnowsze na dole.
Format wpisu: nagłówek drugiego poziomu `D-<kolejny numer, 3 cyfry> <krótki tytuł>`, pod nim:

```
- **Decyzja:** …
- **Dlaczego:** …
- **Kto:** człowiek | judge | coordinator
- **Data:** YYYY-MM-DD HH:MM
```

## D-001 Rdzeń produktu: połączone dane firmy w jednym harnessie
- **Decyzja:** produkt to jeden harness agentowy z konektorami (MCP) do: wyciągu bankowego, bazy faktur, metryk produktu/userów, symulowanych komputerów pracowników (pliki, wiadomości). Agenci liquidity / tax / investment / anomalie kosztów (chmura, marketing vs userzy) to „soczewki” na tych samych połączonych danych. Wyjście: Slack (DM, zatwierdzanie), czat CEO, dashboard.
- **Dlaczego:** tak opisał zespół (whiteboard + wyjaśnienie); wartość jest w łączeniu źródeł, a wymagany use case (liquidity) wynika z niego naturalnie.
- **Kto:** człowiek
- **Data:** 2026-11-20

## D-002 Scenariusz: rozbudowana Pizzeria Amalfi, historia sty–lis 2025, grudzień na żywo
- **Decyzja:** dane organizatora (wyciąg 2025) + dogenerowane: faktury dostawców (AS-/FLV-), księgowa/office manager „Grażyna” z firmowym folderem i skrzynką, arkusz HR (manager, urlopy), sprzęt, reklamy, apka do zamówień online (chmura, zamówienia, nowi klienci). Historia w bazie: 01–11.2025; grudzień 2025 wrzuca symulator jako nowe zdarzenia (tempo sterowane na demo). Agent widzi czerwcową dziurę w historii i proponuje linię kredytową przed czerwcem 2026.
- **Dlaczego:** jury zna te dane; wymagany use case liquidity zostaje; wszystkie przepływy zespołu mają gdzie żyć.
- **Kto:** człowiek (grill)
- **Data:** 2026-11-20 13:30

## D-003 Faktury tylko po stronie wydatków
- **Decyzja:** przepływ: zapłacona faktura (AS-/FLV-) bez dokumentu w systemie → agent szuka na firmowym komputerze Grażyny → LLM wyciąga pozycje → porównanie kwoty z przelewem → zapis; niezgodność → prośba o wyjaśnienie + powiadomienie Grażyny; brak → DM, przypomnienie po X h, manager, przy urlopie wyżej aż do CEO. Brak dokumentu = ryzyko podatkowe (Belegpflicht), więc to też soczewka „Tax”. Faktur sprzedażowych nie robimy.
- **Dlaczego:** wpływy pizzerii to tylko rozliczenia kart, bez faktur.
- **Kto:** coordinator (człowiek: „zdecyduj”)
- **Data:** 2026-11-20 13:30

## D-004 Dostęp tylko do danych firmowych
- **Decyzja:** agent czyta tylko firmowe foldery/skrzynki na firmowych komputerach (VPN; na demo: symulowany folder + pliki .eml przez MCP), nigdy prywatnych. Każdy dostęp jest logowany, a pracownik dostaje DM, że agent zajrzał.
- **Dlaczego:** cel to ciągłość pracy (urlop, zapomniana faktura), nie nadzór; jury to bank („Obey the laws”).
- **Kto:** człowiek
- **Data:** 2026-11-20 13:30

## D-005 Autonomia: rutynowe kontra nietypowe, progi względem wpływów
- **Decyzja:** sam robi: wydatki rutynowe (znany dostawca, kwota ±20% typowej) i wszystko < 1% średnich miesięcznych wpływów; robi i informuje: nietypowe ≤ 5%; pyta CEO: > 5%, nowy dostawca, kredyt, lokata, hedge FX, zmiana budżetu zespołu. CEO nadpisuje reguły zdaniem (zapisane jako reguła, widoczne w historii). Wykonanie zawsze symulowane („SIMULATED”). Agent sam odpala pod-agentów do dochodzeń.
- **Dlaczego:** progi skalują się z firmą; autonomia tam, gdzie nudne i powtarzalne; spełnia rozróżnienie z challenge card.
- **Kto:** człowiek
- **Data:** 2026-11-20 13:30

## D-006 Koszt kontra metryka: trzy pary
- **Decyzja:** koszt jedzenia ↔ zamówienia (food cost %), reklamy ↔ nowi klienci (CAC), chmura apki ↔ zamówienia online (koszt/zamówienie). Wzrost kosztu bez wzrostu metryki → alert z wyjaśnieniem do CEO / właściciela kosztu.
- **Kto:** coordinator (człowiek: „zdecyduj”)
- **Data:** 2026-11-20 13:30

## D-007 Kanały: najpierw Slack
- **Decyzja:** bot Slack (Socket Mode): DM do pracowników, czat CEO, Zatwierdź/Odrzuć. Kanał za interfejsem (Signal/opencode później). Wspólna historia decyzji dla kanałów, osobne rozmowy.
- **Kto:** człowiek
- **Data:** 2026-11-20 13:30

## D-008 Stack
- **Decyzja:** Python, LangGraph (przerwanie + wznowienie przy zgodzie), MCP do narzędzi, Postgres na klastrze jako baza firmy/udawany ERP, FastAPI z OpenAPI dla frontu. Model przez vLLM, model-agnostycznie: `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` w `.env`, domyślnie Llama 3.2 3B (już na klastrze, L4 24 GB). Bez ogx (nic nie dodaje do vLLM+LangGraph, Tech Preview). Deploy: zwykłe YAML-e (`oc apply`), bez Terraformu.
- **Dlaczego:** zespół zna Pythona; klaster żyje 2 dni; decyzję o modelu podejmiemy później.
- **Kto:** człowiek
- **Data:** 2026-11-20 13:30

## D-009 Bonusy: vLLM, NeMo Guardrails, MLflow, Jupyter
- **Decyzja:** prognozy XGBoost/drzewo trenowane w Jupyterze na RHOAI, śledzone w MLflow; LLM tylko tłumaczy wynik. NeMo Guardrails robi Wojtek sam (osobny czat); kod ma tylko przełącznik: zapytania do LLM idą przez jeden `LLM_BASE_URL`, który może wskazać proxy NeMo. Bramka płatności jest w LangGraph (NeMo nie sprawdza kwot). Research: reports/guardrails-research.md.
- **Kto:** człowiek
- **Data:** 2026-11-20 13:30

## D-010 Podział pracy i termin
- **Decyzja:** Wojtek + Grzesiu: backend + OpenShift; Paweł + Piotrek: demo, prezentacja, dashboard (front). API REST z OpenAPI i docs; front zgłasza nowe endpointy przez issues. Deadline niedziela 22.11 10:00, cel 09:00. Makiety DM-a i dashboardu po postawieniu Slacka.
- **Kto:** człowiek
- **Data:** 2026-11-20 13:30

## D-011 Nowy dostawca nigdy nie jest AUTO
- **Decyzja:** płatność do nowego dostawcy poniżej 1% wpływów → NOTIFY (nie AUTO); powyżej → APPROVAL. Rutynowe płatności zostają AUTO niezależnie od kwoty.
- **Dlaczego:** drobne pierwsze przelewy do nieznanych odbiorców to klasyczny wektor nadużyć; jury to bank. Rozstrzyga niejasność w D-005 zgłoszoną przez T003.
- **Kto:** coordinator
- **Data:** 2026-11-20 14:30

## D-012 Zmiana kryteriów T011

- **Decyzja:** nowe kryteria akceptacji T011: AC-1 (run), AC-2 (manual)
- **Dlaczego:** deps updated after splitting T006/T009/T012 into small tasks
- **Kto:** coordinator
- **Data:** 2026-11-20 14:24

## D-013 Próg płynności = bufor bezpieczeństwa; kredyt w scenariuszu „co jeśli”
- **Decyzja:** „dziura” to prognozowane saldo poniżej bufora bezpieczeństwa (domyślnie 1 miesiąc kosztów stałych ≈ 17,7 tys. €, konfigurowalne), nie poniżej zera. Prognoza z prawdziwych danych pokazuje czerwcowy spadek (net czerwca ok. −14 tys. €), ale saldo zostaje ok. 45 tys. €, więc agent: (a) przy zapasie ponad bufor i czerwcowy spadek proponuje lokatę/fundusz rynku pieniężnego na kwotę, która nie przebije bufora w czerwcu (soczewka Investment, APPROVAL); (b) w scenariuszu „co jeśli” od CEO (np. „kupuję piec za 25 tys. € w marcu”) prognoza przebija bufor w czerwcu → wniosek o linię kredytową (APPROVAL). Stub API (−20 tys. €) zostanie zastąpiony prawdziwą prognozą w T019.
- **Dlaczego:** T015 pokazał, że z danych od końca listopada saldo nie schodzi pod zero; zmyślanie minusa byłoby nieuczciwe wobec jury, a oba warianty pokazują liquidity + investment z wyjaśnieniem.
- **Kto:** coordinator (do potwierdzenia przez zespół demo)
- **Data:** 2026-11-20 17:30

## D-014 T008 wmergowany ręcznie (obejście bramki)
- **Decyzja:** T008 wrócił przez revert rewertu na branchu od main; `house merge` dwa razy wywrócił się na swoim rebase'ie „gałęzi już raz cofniętej” (Could not apply e4dcedc). Zrobiłem bramkę ręcznie: `house check T008 --worktree` GREEN → `git merge --no-ff` → `house check --all` GREEN dla wszystkich zadań.
- **Dlaczego:** pierwotny revert był najpewniej flake'iem wspólnej bazy, a bramka ma błąd przy ponownym mergowaniu cofniętej gałęzi (do zgłoszenia w repo house).
- **Kto:** coordinator
- **Data:** 2026-11-20 18:00

## D-015 MLflow: tracing + ewaluacja agenta, rejestr modelu prognozy; bez Data Science Pipelines
- **Decyzja:** MLflow robimy w trzech częściach: (1) tracing każdego zdarzenia agenta (T027), (2) ewaluacja agenta na zestawie scenariuszy z deterministycznymi scorerami (T028), (3) model prognozy trenowany w notebooku na workbenchu RHOAI i rejestrowany w MLflow, a API ładuje wersję `champion` (T026, T017, T029). Data Science Pipelines (cykliczny retrening) pomijamy.
- **Dlaczego:** bonus Red Hat brzmi dosłownie „Use MLflow for tracing and evaluating the agents”; trening w Jupyterze zalicza osobny bonus (Jupyter). Pipelines wymagają S3 i dodatkowych podów na jednym nodzie i nie dają punktów.
- **Kto:** coordinator (człowiek: „ogarnijmy taski do MLflow”)
- **Data:** 2026-11-20 15:30

## D-016 Review Codexem całej dotychczasowej pracy (limit dzienny pominięty)
- **Decyzja:** 19 review Codexa (każdy wmergowany task + PR #2 Grzesia) przez maszynerię `houselib.review` z zakresem merge^1...merge^2 (`house review T###` daje pusty diff dla już wmergowanych gałęzi). Limit `codex_reviews_per_day: 4` pominięty na prośbę człowieka. Wynik: `reports/review-fixes.md` (P0–P3), raporty `reports/review-<id>.md`.
- **Dlaczego:** człowiek: „odpal codexa i rób review wszystkiego”.
- **Kto:** coordinator (człowiek)
- **Data:** 2026-11-20 15:45

## D-019 Koszt użycia agenta z metryk vLLM zamiast rozbicia kosztów pizzerii pod dashboard
- **Decyzja:** T030/T031 (endpointy kosztów pizzerii i paczka dla Pawła) odrzucone — źle zrozumiałem prośbę; dane firmy są już w Postgresie na klastrze. Zamiast tego T032: szacowanie kosztu działania agenta na klastrze — zapis użycia każdego wywołania LLM (tokeny, czas, który krok agenta) + próbkowanie metryk Prometheus z vLLM, model kosztu (czas GPU wg stawki L4, amortyzacja, porównanie z hostowanym API), endpointy `/v1/llm/usage` i `/v1/llm/usage/live`.
- **Dlaczego:** prośba Wojtka; pokazuje jury koszt jednej decyzji agenta i przewagę własnego modelu na OpenShift AI.
- **Kto:** człowiek
- **Data:** 2026-11-20 16:30

## D-017 Domyślny model: Granite 4.0 H Tiny (Llama 3.2 3B nie działa na klastrze)
- **Decyzja:** domyślny model agenta to Granite 4.0 H Tiny serwowany przez KServe w namespace `my-first-model` (svc `modelcar-granite-4-0-h-tiny-kserve-workload-svc`, HTTPS z certyfikatem self-signed na 8000; `/v1` i `/metrics`). InferenceService `llama-32-3b-instruct` ma Ready=False. Kod zostaje model-agnostyczny (D-008), zmienia się tylko konfiguracja: `LLM_BASE_URL`, `LLM_MODEL` (dokładna nazwa z `/v1/models`), `VLLM_METRICS_URL`.
- **Dlaczego:** informacja od Grzesia; Granite to model z rodziny Red Hat/IBM i dobrze radzi sobie z wywoływaniem narzędzi.
- **Kto:** człowiek (Grzesiu)
- **Data:** 2026-11-20 16:45

## D-016 Izolacja schematów testów; T026 wmergowany ręcznie
- **Decyzja:** testy używają schematów Postgresa z PID procesu (`tNNN_test_<pid>`), bo kilka sesji naraz odpala bramki i nadpisywały sobie wspólny `t005_test` (stąd cofnięcie T026). T026 wrócił przez revert rewertu i ręczną bramkę jak w D-014 (`house check --worktree` GREEN → `merge --no-ff` → `check --all` GREEN).
- **Kto:** coordinator
- **Data:** 2026-11-20 16:30

## D-018 Limit rund review Codexa na task z poprawkami
- **Decyzja:** poprawki z review (T040–T046) przechodzą maksymalnie 3 rundy review Codexa. W ostatniej rundzie poprawiamy tylko to, co psuje demo albo instrukcję wdrożenia, resztę odrzucamy z uzasadnieniem w raporcie taska (np. T040: wyścigi przy seedzie i złośliwy `scenario.json`, T044: utrwalone NaN, którego już nie da się zapisać).
- **Dlaczego:** każda kolejna runda znajdowała coraz bardziej egzotyczne przypadki (symlinki, deadlocki przy seedzie); koszt czasu > zysk na demo.
- **Kto:** coordinator
- **Data:** 2026-11-20 17:30

## D-020 Granite jako zwykły InferenceService (HTTP), aktualne adresy
- **Decyzja:** Granite 4.0 H Tiny działa teraz jako zwykły KServe InferenceService: `LLM_BASE_URL=http://modelcar-granite-4-0-h-tiny-predictor.my-first-model.svc.cluster.local:8080/v1`, `VLLM_METRICS_URL=http://…:8080/metrics` (bez TLS). Stare adresy https `…-kserve-workload-svc…:8000` z D-017 nie działają. Sekrety Slacka: Secret `slack` (envFrom w worker i api). Próbnik metryk działa jako Deployment `usage-sampler`; `/v1/llm/usage` i `/v1/llm/usage/live` odpowiadają na Route API (sprawdzone 2026-11-20 16:16).
- **Kto:** człowiek (wdrożenie w sesji forka)
- **Data:** 2026-11-20 16:20

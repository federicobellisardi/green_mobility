# Output/analisi mancanti — consolidato

Elenco unico di tutto ciò che manca per portare i pannelli PARTIAL/BLOCKED a READY.
Nessuna voce qui sotto è stata stimata o simulata per produrre questo documento: è
un audit di stato, non un'analisi.

## 1. Car dose-benefit (mai calcolato, per nessuna città)

Riguarda: Fig.2 B, Fig.3 A/C, Fig.4 A/B, Fig.5 C.

- Esiste solo estrazione di flusso/congestione grezza per Palma
  (`weekday_full_car_edge_bins.parquet`, colonna proxy `person_seconds_est`),
  dalla fase a 4 città precedente a questo lavoro — non un dose-benefit termico.
- Nessuna città ha una pipeline car → shade → UTCI → dose eseguita.
- Il baseline car di Palma stesso è STALE (manifest datati 18/07, prima del fix
  teleport-rate; teleport rate osservato 1.28–1.87% su quei run) — da rilanciare
  prima di poterlo anche solo usare come proxy provvisorio.
- Per sbloccare: eseguire simulazioni car (o quantomeno routing/flow car) per le
  date rilevanti, poi applicare la stessa pipeline shade/UTCI già validata per
  walk/bike, con conversione veicolo-ore → persona-ore e sensitivity di
  occupancy (1.0/1.2/1.5), senza scegliere un valore "vero".

## 2. Vulnerability index (mai costruito, per nessuna città)

Riguarda: Fig.1 D, Fig.5 (tutti i pannelli), Fig.6 (tutti i pannelli).

- Reddito/povertà/Gini INE disponibili solo per 4/11 province (Barcellona
  esclusa a monte).
- Popolazione MITMA disponibile per tutte e 11.
- Nessun indice composito è mai stato definito né calcolato.
- Per sbloccare: reperire i dati INE mancanti per le province restanti,
  definire esplicitamente la formula dell'indice composito (pesi, normalizzazione),
  documentarla come scelta metodologica dichiarata.

## 3. Edges table combinata per l'ottimizzazione (mai costruita)

Riguarda: Fig.5 (tutti i pannelli), a cascata Fig.6 (tutti i pannelli).

- `interventions/strategies.py` contiene funzioni di ranking reali e testate,
  ma richiede una tabella per-edge (`edge_id, length_m, flow_total, flow_active,
  exposure_score, vulnerability_index, utci_peak_c`) mai assemblata per nessuna
  città.
- `gm intervene` fallisce immediatamente ("file not found") su ogni città provata.
- Dipende da (1) car dose-benefit, (2) vulnerability_index, (3) una definizione
  esplicita di `exposure_score`/`utci_peak_c` dalla pipeline v2.
- Una volta costruita: eseguire `allocate_budget` per q=1/5/10/20% sulle 6
  strategie nominate, incluso un baseline random multi-realizzazione con seed
  registrati.

## 4. Copertura cross-city della pipeline v2 (canopy fusion + building shadow + four-state)

Riguarda: Fig.3 D (già ok, 11 città), Fig.4 D, Fig.6 (tutti i pannelli).

- Stato al momento della scrittura di questi notebook: **solo Palma** ha la
  pipeline v2 completa (canopy fusion corretta e validata, building shadow,
  decomposizione a quattro stati).
- Fase 2 (Bilbao + Sevilla, canopy_v2/building_shadow/car per 3 nuove date) è
  stata lanciata (`scripts/launch_phase2_bilbao_sevilla.sh`) ma il suo
  completamento non è stato riverificato in questa sessione.
- Barcellona resta BLOCKED/PARTIAL: rete e OD ora esistono, ma nessuno dei tre
  modi ha ancora una pipeline termica completa.
- Per Fig.6 servono realisticamente 6-8 città con pipeline v2 completa per
  statistiche cross-city difendibili (Spearman con bootstrap CI, procedura
  leave-one-city-out) — quest'ultima procedura, inoltre, non è mai stata
  progettata né implementata in nessuna forma.

## 5. Normalizzazione mancante in Fig.3 A

- v1 (tree-only, senza edifici) non ha una colonna persona-ore persistita per
  la stessa aggregazione mensile, quindi il pannello mostra dose assoluta
  invece che normalizzata (/1000 persona-ore) — la forma preferita per il
  confronto cross-city dichiarato nelle istruzioni.
- Per sbloccare: ripetere l'aggregazione mensile salvando anche il denominatore
  persona-ore per mese/città.

## 6. Building-coefficient sensitivity cross-city (Fig.4 D)

- Calcolata finora solo per Palma (griglia 0.25/0.50/0.75/1.00×).
- Estensione ad altre città in attesa del completamento della Fase 2.

## Riepilogo per figura

| Figura | Bloccanti principali |
|---|---|
| 1 | vulnerability_index (D) |
| 2 | car dose-benefit non riverificato (B) |
| 3 | car dose-benefit (A, C); persona-ore v1 mancanti (A); mode-dose overlay oltre Palma (B) |
| 4 | building-coefficient cross-city (D); car dose-benefit (A, B) |
| 5 | edges table combinata; vulnerability_index; car dose-benefit; allocate_budget mai eseguito |
| 6 | tutto quanto sopra + pipeline v2 completa per 6-8 città + procedura leave-one-city-out da progettare |

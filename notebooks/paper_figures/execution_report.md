# Report di esecuzione — notebook figure del paper

Data build/esecuzione: 2026-07-19/20. Ambiente: `/home/fbellisardi/.conda/envs/geo_flow`
(kernel usato da `jupyter nbconvert --execute`).

## Cosa è stato eseguito

Tutti e sei i notebook sono stati costruiti (via helper `nbformat`) ed eseguiti
end-to-end con `jupyter nbconvert --to notebook --execute --inplace`, uno per
uno. Verifica post-hoc appena eseguita su ciascun file `.ipynb`: **0 celle con
output di tipo `error` in tutti e 6 i notebook**. Nessun notebook ha quindi
lanciato eccezioni non gestite durante l'esecuzione registrata nel file
salvato su disco.

Nessuna simulazione, download, estrazione canopy, building-shadow o ray-casting
è stata rilanciata per produrre questi notebook: usano esclusivamente output
già esistenti su disco più analisi leggere (aggregazioni, join, ri-espressioni
di quantità già calcolate), come richiesto.

| # | Notebook | Dimensione | Stato esecuzione |
|---|---|---|---|
| 1 | `01_figure1_data_landscape.ipynb` | 581 KB | eseguito, 0 errori |
| 2 | `02_figure2_multimodal_thermal_framework.ipynb` | 135 KB | eseguito, 0 errori |
| 3 | `03_figure3_dynamic_thermal_exposure.ipynb` | 244 KB | eseguito, 0 errori |
| 4 | `04_figure4_shade_and_phenology.ipynb` | 176 KB | eseguito, 0 errori |
| 5 | `05_figure5_canopy_optimization.ipynb` | 321 KB | eseguito, 0 errori (tutto BLOCKED, documentato) |
| 6 | `06_figure6_cross_city_generalization.ipynb` | 245 KB | eseguito, 0 errori (tutto BLOCKED, documentato) |

## Output prodotti su disco

- `figures/paper/main/figure{1..6}/panel_{a,b,c,d}.{pdf,png}` — pannelli singoli,
  PDF vettoriale + PNG 300dpi, font embedded, palette color-blind-safe, label A-D.
- `figures/paper/main/figure{1..6}/figure{1..6}_composite.{pdf,png}` — anteprima
  composita 2×2 per figura.
- `reports/paper_figures/figure{1..6}/panel_*_source.csv` — tabella dati sorgente
  per ogni pannello effettivamente disegnato (non prodotta per pannelli BLOCKED,
  dove non esiste alcun dato sorgente da salvare).
- `reports/paper_figures/figure{1..6}/manifest.json` — stato READY/PARTIAL/BLOCKED
  per pannello, per ciascuna figura.
- `reports/paper_figures/figure_input_audit.csv` — audit consolidato 24 righe
  (6 figure × 4 pannelli) con stato e nota.
- `notebooks/paper_figures/plotting_utils.py` — stile condiviso (colori modali
  car/walk/bike/active/multimodal, colori climatici, funzioni panel/composite/
  blocked_panel/save_source_table, seed=42 fisso).
- `notebooks/paper_figures/figure_plan.md` — claim, tabella pannelli, didascalia
  provvisoria per ciascuna delle 6 figure.
- `notebooks/paper_figures/missing_outputs.md` — lista consolidata di cosa manca
  per sbloccare i pannelli PARTIAL/BLOCKED (car dose-benefit, vulnerability_index,
  edges table per l'ottimizzazione, copertura cross-city della pipeline v2).
- `notebooks/paper_figures/run_all.sh` — esegue in sequenza tutti e 6 i notebook
  (`chmod +x` applicato).

## Stato sintetico pannelli (24 totali)

- READY: 13
- PARTIAL: 7
- BLOCKED: 4 (tutti in Figura 5) + i 4 di Figura 6 sono BLOCKED per dipendenza
  diretta da Figura 5, quindi in totale 8 pannelli su 24 sono interamente
  bloccati (Figure 5 e 6), coerentemente con quanto già anticipato nell'audit
  prima di iniziare la costruzione dei notebook.

Dettaglio completo per figura/pannello in `reports/paper_figures/figure_input_audit.csv`
e nella tabella di `figure_plan.md`.

## Come rieseguire

```bash
cd notebooks/paper_figures
./run_all.sh
# oppure, per un singolo notebook:
JUPYTER=/home/fbellisardi/.conda/envs/geo_flow/bin/jupyter ./run_all.sh
```

Ogni notebook risolve `REPO_ROOT` da sé risalendo la directory fino a trovare
`configs/`, quindi è eseguibile anche da una copia del repo in un altro path,
purché la struttura interna sia intatta.

## Limiti noti di questo report

Questo report attesta l'assenza di errori di esecuzione registrati nei file
`.ipynb` salvati. Non sostituisce una revisione visiva dei pannelli (colori,
leggibilità, sovrapposizioni testo/dati), che resta da fare prima dell'uso nel
manoscritto. I contenuti scientifici (claim, didascalie) sono quelli riportati
in `figure_plan.md` e vanno intesi come provvisori fino a revisione editoriale.

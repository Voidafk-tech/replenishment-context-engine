# Replenishment Context Engine

Prototype and evaluation for a context engine that gives an AI copilot clean, cited replenishment
facts for grocery retail, instead of raw tables. Built for an SAP-focused case competition.

> We are not building a better AI model. We build the information environment an AI copilot needs
> before it recommends a stock move.

## See the demo

Open `index.html` in any browser. It has two tabs:

- **Live demo**: a planner asks whether bottled water can be restocked in Vancouver by morning. It
  compares a plain copilot answer with one built on the context engine, and walks through the
  engine's five steps (resolve, gather, verify, constrain, package). All demo data is synthetic.
- **Pitch fixes**: how each piece of judge feedback is addressed, the SAP architecture, an ROI
  calculator, the pilot plan and the evaluation results.

## Run the evaluation

Needs Python 3.9 or newer. No packages to install.

```bash
cd eval
python3 download_data.py      # gets the M5 data (about 50 MB download)
python3 find_spikes.py        # finds 20 real demand spikes
python3 build_scenarios.py    # builds scenarios, correct answers and both prompt versions
python3 grade.py              # scores the saved model answers
```

The model answers from our run are saved in `eval/out/answers/`, so `grade.py` works without
calling any model.

### Get fresh model answers (optional)

`run_llm.py` asks the model every question through the Claude Code command line, so it uses a
Claude plan's usage allowance and never the paid API. Log in first (run `claude`, then `/login`):

```bash
cd eval
python3 run_llm.py sonnet
python3 grade.py
```

## How the evaluation works

| File | What it does |
|---|---|
| `eval/find_spikes.py` | Finds item-state-days in M5 where sales jumped far above the 28-day average in at least two stores. |
| `eval/build_scenarios.py` | Adds synthetic warehouse, order and truck tables with traps: an out-of-date snapshot, shipped and cancelled orders, stock counted in single units, trucks in maintenance or arriving late, limited truck capacity, and approval thresholds. Writes the correct answer and two prompts per scenario. |
| `eval/engine.py` | The context engine. Builds the packet from the raw tables only; `build_scenarios.py` checks its numbers against the correct answers. |
| `eval/grade.py` | An answer is correct when the case total (within 1), coverage and approval verdicts all match. It is unsafe when it ships more than a warehouse can free up and transport, or skips a required approval. |
| `eval/out/` | Generated scenarios, prompts, model answers and `results.json`. |

## Results (Claude Sonnet, 19 scenarios)

| Model input | Correct | Unsafe plans | Input tokens (est.) |
|---|---|---|---|
| Raw tables | 19/19 | 0 | ~4,600 |
| Context packet | 19/19 | 0 | ~760 |

With every relevant table in front of it, a strong model reasons correctly. The packet reaches the
same answers with about 6× less input. Token counts are estimated as characters ÷ 4. Two of the 40
runs (s16 with the packet, s20 with raw tables) were not completed.

## Data

M5 Forecasting competition data (Walmart store sales), downloaded from
[Nixtla's public copy](https://github.com/Nixtla/m5-forecasts). It is not stored in this repository.
All warehouse, order, truck, product-name and policy data is synthetic.

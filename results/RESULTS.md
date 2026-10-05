## Results with 95% bootstrap confidence intervals

| run | RCA exact (all, n=162) | RCA exact (hard, n=68) | dir hit (all) | dir hit (hard) |
|---|---|---|---|---|
| baseline (no RAG) | 81% [75%, 88%] | 65% [53%, 75%] | 62% [55%, 70%] | 53% [41%, 65%] |
| baseline (RAG k=5) | 80% [73%, 86%] | 63% [51%, 75%] | 67% [59%, 73%] | 56% [44%, 68%] |
| baseline (RAG k=5, 24h comments only) | 73% [67%, 80%] | 53% [41%, 65%] | 61% [54%, 69%] | 53% [41%, 65%] |
| graph v1 | 78% [72%, 84%] | 63% [51%, 75%] | 70% [63%, 77%] | 63% [51%, 75%] |
| graph v2 | 81% [75%, 87%] | 65% [53%, 76%] | 75% [68%, 81%] | 65% [53%, 76%] |
| graph v3 | 81% [75%, 87%] | 65% [53%, 75%] | 79% [72%, 85%] | 74% [63%, 84%] |

## Paired comparisons (same tickets): difference [95% CI], wins/losses, McNemar p

| comparison | metric | slice | diff | wins / losses | p |
|---|---|---|---|---|---|
| graph v3 vs baseline (RAG k=5) | RCA exact | all | +1.2% [-3.7%, +6.2%] | 10 / 8 | 0.815 |
| graph v3 vs baseline (RAG k=5) | RCA exact | hard | +1.5% [-7.4%, +10.3%] | 5 / 4 | 1.000 |
| graph v3 vs baseline (RAG k=5) | dir hit | all | +12.3% [+6.2%, +18.5%] | 25 / 5 | 0.000 * |
| graph v3 vs baseline (RAG k=5) | dir hit | hard | +17.6% [+7.4%, +27.9%] | 13 / 1 | 0.002 * |
| baseline (RAG k=5) vs baseline (no RAG) | RCA exact | all | -1.9% [-7.4%, +3.7%] | 9 / 12 | 0.664 |
| baseline (RAG k=5) vs baseline (no RAG) | RCA exact | hard | -1.5% [-11.8%, +8.8%] | 6 / 7 | 1.000 |
| baseline (RAG k=5) vs baseline (no RAG) | dir hit | all | +4.3% [-1.2%, +9.9%] | 14 / 7 | 0.189 |
| baseline (RAG k=5) vs baseline (no RAG) | dir hit | hard | +2.9% [-4.4%, +10.3%] | 4 / 2 | 0.688 |
| graph v3 vs graph v2 | RCA exact | all | +0.0% [-5.6%, +5.6%] | 11 / 11 | 1.000 |
| graph v3 vs graph v2 | RCA exact | hard | +0.0% [-10.3%, +10.3%] | 7 / 7 | 1.000 |
| graph v3 vs graph v2 | dir hit | all | +4.3% [-0.6%, +9.3%] | 12 / 5 | 0.143 |
| graph v3 vs graph v2 | dir hit | hard | +8.8% [+0.0%, +17.6%] | 8 / 2 | 0.109 |
| baseline (RAG k=5, 24h comments only) vs baseline (RAG k=5) | RCA exact | all | -6.2% [-11.1%, -1.2%] | 4 / 14 | 0.031 * |
| baseline (RAG k=5, 24h comments only) vs baseline (RAG k=5) | RCA exact | hard | -10.3% [-19.1%, -1.5%] | 2 / 9 | 0.065 |
| baseline (RAG k=5, 24h comments only) vs baseline (RAG k=5) | dir hit | all | -5.6% [-11.1%, -0.6%] | 5 / 14 | 0.064 |
| baseline (RAG k=5, 24h comments only) vs baseline (RAG k=5) | dir hit | hard | -2.9% [-11.8%, +5.9%] | 4 / 6 | 0.754 |

\* p < 0.05

# Label lift (2026-11-08)

- db: `/home/mustafa/Downloads/Odin/NLP/cinematch.ai/backend/cinematch.db`
- interactions: 85,152 (49,513 positive, base rate 58.1%)
- positive threshold: rating ≥ 4.0, min support 30

## Subniches — strongest

| label | support | positive rate | lift |
|---|---:|---:|---:|
| Western frontier dramas with a touch of humor | 1215 | 68.3% | 1.17 |
| Humanity and the world around us | 11994 | 68.3% | 1.17 |
| Dreamlike, quirky, and surreal storytelling | 5839 | 65.5% | 1.13 |
| Fascinating, emotional stories and documentaries | 1847 | 64.3% | 1.11 |
| Gritty crime and ruthless gangsters | 7272 | 64.2% | 1.10 |
| Captivating relationships and charming romance | 7920 | 62.5% | 1.07 |
| Emotional life of renowned artists | 2136 | 62.1% | 1.07 |
| Enduring stories of family and marital drama | 5041 | 62.0% | 1.07 |
| Challenging or sexual themes & twists | 4109 | 61.8% | 1.06 |
| Emotional and captivating fantasy storytelling | 6999 | 61.7% | 1.06 |
| Intriguing and suspenseful murder mysteries | 7085 | 61.6% | 1.06 |
| Emotional and touching family dramas | 10319 | 61.5% | 1.06 |

## Subniches — weakest

| label | support | positive rate | lift |
|---|---:|---:|---:|
| Bollywood emotional dramas | 479 | 54.1% | 0.93 |
| Catchy songs and hilarious musical comedy | 5186 | 53.0% | 0.91 |
| High speed and special ops | 10168 | 52.8% | 0.91 |
| Epic heroes | 5724 | 52.3% | 0.90 |
| Relationship comedy | 11431 | 51.9% | 0.89 |
| Adorable animals and heartwarming families | 8029 | 50.9% | 0.88 |
| Amusing jokes and witty satire | 1853 | 49.9% | 0.86 |
| Action comedy and silly heroics | 6562 | 47.2% | 0.81 |

## Themes — strongest

| label | support | positive rate | lift |
|---|---:|---:|---:|
| texas | 38 | 97.4% | 1.67 |
| live performance | 66 | 97.0% | 1.67 |
| husband wife relationship | 44 | 95.5% | 1.64 |
| concert | 78 | 94.9% | 1.63 |
| concert film | 91 | 94.5% | 1.63 |
| kung fu | 35 | 94.3% | 1.62 |
| feminism | 58 | 89.7% | 1.54 |
| los angeles, california | 57 | 89.5% | 1.54 |
| pop star | 49 | 87.8% | 1.51 |
| music video | 89 | 83.1% | 1.43 |
| martial arts | 51 | 82.4% | 1.42 |
| making of | 35 | 80.0% | 1.38 |

## Themes — weakest

| label | support | positive rate | lift |
|---|---:|---:|---:|
| christmas | 78 | 46.2% | 0.79 |
| dog | 65 | 46.2% | 0.79 |
| based on true story | 81 | 45.7% | 0.79 |
| space | 33 | 45.5% | 0.78 |
| Romance | 57 | 40.4% | 0.69 |
| family | 30 | 40.0% | 0.69 |
| sequel | 80 | 37.5% | 0.64 |
| holiday | 61 | 34.4% | 0.59 |

Lift > 1.0 means films carrying the label are more likely to be
positively received than the average rated film. The measured tables
exclude labels below the support floor; the JSON keeps everything.
